// qdbridge: test bridge for Queen's Domain lab copies. Our own code, injected next to the co-op mod; it does not
// modify the mod. Serves the hub's coopbridge protocol (tools/bridge/PROTOCOL.md) on tcp://127.0.0.1:<port>.
//
// Game thread: requests run inside the game's ProcessEvent (see "pump" below). A WH_GETMESSAGE hook on the game
// window's thread is used once, to install those hooks on the game thread.
//
// Command line (read from the game process): -qdbridge-port=N (default 27990), -qdcoop-profile=N (to find the mod's
// dev command file).
#include "SDK.hpp"
#include "SDK/TitleScreenUI_classes.hpp"
#include "SDK/TitleScreenUI_parameters.hpp"
#include "SDK/FS_PlayerChar_classes.hpp"
#include "SDK/FS_PlayerChar_parameters.hpp"
#include "SDK/FS_SaveSlot_classes.hpp"
#include "SDK/EnemyBase_classes.hpp"
#include "SDK/ItemPickup_Base_classes.hpp"
#include "SDK/ItemPickup_Base_parameters.hpp"
#include "SDK/CoreItemPickupActor_classes.hpp"
#include "SDK/SavableActor_classes.hpp"
#include "SDK/SavableActorState_structs.hpp"
#include "SDK/Checkpoint_classes.hpp"
#include "SDK/BreakableBarricade_classes.hpp"

#include <algorithm>
#include <cstring>

#include <coopbridge/server.h>

#include <Windows.h>

#include <atomic>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

namespace cb = coopbridge;
using cb::json::Value;

namespace {

constexpr WPARAM kPumpMarker = 0x51D9B71D;
HMODULE g_self = nullptr;
DWORD g_gameThread = 0;
HHOOK g_hook = nullptr;
std::atomic<bool> g_run{true};
cb::Server g_server;

std::wstring CmdLine() { return GetCommandLineW(); }

int ArgInt(const wchar_t* key, int def) {
    const std::wstring c = CmdLine();
    const size_t p = c.find(key);
    if (p == std::wstring::npos) return def;
    return _wtoi(c.c_str() + p + wcslen(key));
}

// ------------------------------------------------------------------ game helpers (game thread only)

SDK::UWorld* World() { return SDK::UWorld::GetWorld(); }

SDK::AFS_PlayerChar_C* Player() {
    SDK::UWorld* w = World();
    if (!w) return nullptr;
    SDK::ACharacter* c = SDK::UGameplayStatics::GetPlayerCharacter(w, 0);
    if (!c || !c->IsA(SDK::AFS_PlayerChar_C::StaticClass())) return nullptr;
    return static_cast<SDK::AFS_PlayerChar_C*>(c);
}

SDK::UFS_GameInstanceBase* GameInstance() {
    SDK::UWorld* w = World();
    if (!w || !w->OwningGameInstance) return nullptr;
    if (!w->OwningGameInstance->IsA(SDK::UFS_GameInstanceBase::StaticClass())) return nullptr;
    return static_cast<SDK::UFS_GameInstanceBase*>(w->OwningGameInstance);
}

template <class T>
T* FirstInstance() {
    auto& objs = SDK::UObject::GObjects;
    SDK::UClass* cls = T::StaticClass();
    if (!cls) return nullptr;
    for (int i = 0; i < objs->Num(); ++i) {
        SDK::UObject* o = objs->GetByIndex(i);
        if (o && !o->IsDefaultObject() && o->IsA(cls)) return static_cast<T*>(o);
    }
    return nullptr;
}

SDK::UFunction* FindFn(SDK::UObject* obj, const std::string& name) {
    for (SDK::UStruct* s = obj ? obj->Class : nullptr; s; s = s->SuperStruct)
        for (SDK::UField* f = s->Children; f; f = f->Next)
            if (f->IsA(SDK::UFunction::StaticClass()) && f->GetName() == name) return static_cast<SDK::UFunction*>(f);
    return nullptr;
}

// Blueprint functions are called by name, never through the SDK's generated BP wrappers: the Dumper-7 build used
// here generates those with the class name in place of the function name, and ProcessEvent(nullptr) is fatal.
bool CallNoParams(SDK::UObject* obj, const std::string& fn) {
    SDK::UFunction* f = FindFn(obj, fn);
    if (!f) return false;
    alignas(16) unsigned char buf[1024] = {};
    obj->ProcessEvent(f, buf);
    return true;
}

template <class P>
bool CallWith(SDK::UObject* obj, const std::string& fn, P& params) {
    SDK::UFunction* f = FindFn(obj, fn);
    if (!f) return false;
    alignas(16) unsigned char buf[1024] = {};
    static_assert(sizeof(P) <= sizeof(buf));
    std::memcpy(buf, &params, sizeof(P));
    obj->ProcessEvent(f, buf);
    std::memcpy(&params, buf, sizeof(P));
    return true;
}

void Need(bool ok, const std::string& what) {
    if (!ok) throw cb::Error(cb::errc::kInvalidParams, "function not found: " + what);
}

std::string GuidStr(const SDK::FGuid& g) {
    char b[40];
    std::snprintf(b, sizeof b, "%08X%08X%08X%08X", static_cast<uint32_t>(g.A), static_cast<uint32_t>(g.B),
                  static_cast<uint32_t>(g.C), static_cast<uint32_t>(g.D));
    return b;
}

std::string ObjName(SDK::UObject* o) { return o ? o->GetName() : std::string("null"); }

Value Vec(const SDK::FVector& v) {
    Value a = Value::array();
    a.push(std::round(v.X)); a.push(std::round(v.Y)); a.push(std::round(v.Z));
    return a;
}

std::wstring KdDevCmdPath() {
    wchar_t buf[MAX_PATH * 2];
    const DWORD n = GetEnvironmentVariableW(L"LOCALAPPDATA", buf, MAX_PATH * 2);
    std::wstring d = std::wstring(buf, n) + L"\\QueensDomain\\Saved\\KnightsDomain";
    const int profile = ArgInt(L"-qdcoop-profile=", 1);
    if (profile >= 2) d += L"\\DevProfile" + std::to_wstring(profile);
    return d + L"\\devcmd.txt";
}

// ------------------------------------------------------------------ state

// Live widgets whose class name contains `match` (Blueprint UI that loads after the title screen is not in the SDK).
std::vector<SDK::UUserWidget*> WidgetsMatching(const std::string& match) {
    std::vector<SDK::UUserWidget*> out;
    auto& objs = SDK::UObject::GObjects;
    SDK::UClass* cls = SDK::UUserWidget::StaticClass();
    for (int i = 0; i < objs->Num(); ++i) {
        SDK::UObject* o = objs->GetByIndex(i);
        if (!o || o->IsDefaultObject() || !o->IsA(cls)) continue;
        // class name, or the object's own name (runtime widgets: "<Class>_<number>")
        if (!match.empty() && ObjName(o->Class).find(match) == std::string::npos && ObjName(o).rfind(match, 0) != 0) continue;
        out.push_back(static_cast<SDK::UUserWidget*>(o));
    }
    return out;
}

Value PlayerState() {
    Value v = Value::object();
    SDK::AFS_PlayerChar_C* p = Player();
    v.set("present", p != nullptr);
    if (!p) return v;
    v.set("class", ObjName(p->Class));
    v.set("pos", Vec(p->K2_GetActorLocation()));
    v.set("health", p->Health);
    v.set("dead", static_cast<bool>(p->IsDead));
    v.set("cutscene", p->CurrentCutsceneManager ? ObjName(p->CurrentCutsceneManager) : std::string());
    return v;
}

Value SaveState() {
    Value v = Value::object();
    SDK::UFS_GameInstanceBase* gi = GameInstance();
    SDK::UFS_SaveGame* save = gi ? gi->BaseActiveSave : nullptr;
    v.set("present", save != nullptr);
    if (!save) return v;
    Value inv = Value::object();
    for (auto& kv : save->InventoryContents) inv.set(ObjName(kv.Key()), kv.Value());
    v.set("inventory", inv);
    if (save->IsA(SDK::UFS_SaveSlot_C::StaticClass())) {
        auto* s = static_cast<SDK::UFS_SaveSlot_C*>(save);
        Value savable = Value::object();
        for (auto& kv : s->SavableActorStates) savable.set(GuidStr(kv.Key()), kv.Value());
        v.set("savableActorStates", savable);
        Value named = Value::object();
        for (auto& kv : s->NamedSaveData) named.set(kv.Key().ToString(), kv.Value());
        v.set("namedSaveData", named);
        Value drops = Value::array();
        for (auto& kv : s->ItemDrops) drops.push(GuidStr(kv.Key()));
        v.set("itemDrops", drops);
        Value tele = Value::array();
        for (SDK::UFS_LocationData* t : s->UnlockedTeleporters) tele.push(ObjName(t));
        v.set("unlockedTeleporters", tele);
        Value kills = Value::object();
        for (auto& kv : s->KilledEnemyCounts) kills.set(ObjName(kv.Key()), kv.Value());
        v.set("killedEnemyCounts", kills);
        v.set("xp", s->XPAmount);
        v.set("level", s->SavedPlayerLevel);
        v.set("lastCheckpoint", ObjName(s->LastTouchedCheckpoint));
        v.set("savePoint", ObjName(s->SavePointID));
        Value visited = Value::array();
        for (SDK::UFS_LocationData* l : s->SavePointsVisited) visited.push(ObjName(l));
        v.set("savePointsVisited", visited);
    }
    return v;
}

Value EnemyState(double radiusCm, bool includeHidden = false) {
    Value list = Value::array();
    SDK::AFS_PlayerChar_C* p = Player();
    const SDK::FVector me = p ? p->K2_GetActorLocation() : SDK::FVector{};
    auto& objs = SDK::UObject::GObjects;
    SDK::UClass* base = SDK::AFS_EnemyCharacterBase::StaticClass();
    SDK::UClass* bp = SDK::AEnemyBase_C::StaticClass();
    for (int i = 0; i < objs->Num(); ++i) {
        SDK::UObject* o = objs->GetByIndex(i);
        if (!o || o->IsDefaultObject() || !o->IsA(base)) continue;
        auto* e = static_cast<SDK::AFS_EnemyCharacterBase*>(o);
        if (e->bSpawnerPreviewInstance || e->bActorIsBeingDestroyed) continue;
        // only actors that live in a level of the current world (GObjects also holds actors of unloaded levels)
        SDK::UObject* outer = e->Outer;
        if (!outer || !outer->IsA(SDK::ULevel::StaticClass()) || static_cast<SDK::ULevel*>(outer)->OwningWorld != World()) continue;
        if (e->bHidden && !includeHidden) continue;
        const SDK::FVector at = e->K2_GetActorLocation();
        const double d = std::sqrt((at.X - me.X) * (at.X - me.X) + (at.Y - me.Y) * (at.Y - me.Y) + (at.Z - me.Z) * (at.Z - me.Z));
        if (p && radiusCm > 0 && d > radiusCm) continue;
        Value ev = Value::object();
        ev.set("name", ObjName(e));
        ev.set("class", ObjName(e->Class));
        ev.set("pos", Vec(at));
        ev.set("dist", std::round(d));
        ev.set("dead", static_cast<bool>(e->bDead));
        ev.set("aiDisabled", static_cast<bool>(e->bIsAIDisabled));
        ev.set("hidden", static_cast<bool>(e->bHidden));
        if (bp && e->IsA(bp)) ev.set("hp", static_cast<SDK::AEnemyBase_C*>(e)->HP);
        list.push(ev);
    }
    return list;
}

// Actors of `cls` that live in a level of the current world (GObjects also holds actors of unloaded levels).
std::vector<SDK::AActor*> WorldActors(SDK::UClass* cls, bool keepOrigin = false) {
    std::vector<SDK::AActor*> out;
    if (!cls) return out;
    SDK::UWorld* w = World();
    auto& objs = SDK::UObject::GObjects;
    for (int i = 0; i < objs->Num(); ++i) {
        SDK::UObject* o = objs->GetByIndex(i);
        if (!o || o->IsDefaultObject() || !o->IsA(cls)) continue;
        auto* a = static_cast<SDK::AActor*>(o);
        if (a->bActorIsBeingDestroyed) continue;
        SDK::UObject* outer = a->Outer;
        if (!outer || !outer->IsA(SDK::ULevel::StaticClass()) || static_cast<SDK::ULevel*>(outer)->OwningWorld != w) continue;
        // actors of streamed-out sublevels have no registered root and report exactly the origin
        if (!a->RootComponent) continue;
        const SDK::FVector p = a->K2_GetActorLocation();
        if (!keepOrigin && p.X == 0 && p.Y == 0 && p.Z == 0) continue;
        out.push_back(a);
    }
    return out;
}

double Dist(const SDK::FVector& a, const SDK::FVector& b) {
    return std::sqrt((a.X - b.X) * (a.X - b.X) + (a.Y - b.Y) * (a.Y - b.Y) + (a.Z - b.Z) * (a.Z - b.Z));
}

// Actor names repeat across sublevels (each sublevel numbers its own actors), so a name means the loaded actor of
// that name nearest to the local player.
SDK::AActor* ActorByName(const std::string& name) {
    SDK::AFS_PlayerChar_C* me = Player();
    const SDK::FVector at = me ? me->K2_GetActorLocation() : SDK::FVector{};
    SDK::AActor* best = nullptr;
    double bestD = 1e30;
    for (SDK::AActor* a : WorldActors(SDK::AActor::StaticClass(), true)) {
        if (a->GetName() != name) continue;
        const double d = Dist(a->K2_GetActorLocation(), at);
        if (d < bestD) { bestD = d; best = a; }
    }
    return best;
}

// Every player character in the world: the local one and the co-op mod's puppets of remote players.
Value PlayersState() {
    Value list = Value::array();
    SDK::AFS_PlayerChar_C* me = Player();
    for (SDK::AActor* a : WorldActors(SDK::AFS_PlayerChar_C::StaticClass())) {
        Value v = Value::object();
        v.set("name", ObjName(a));
        v.set("local", a == me);
        v.set("pos", Vec(a->K2_GetActorLocation()));
        v.set("hidden", static_cast<bool>(a->bHidden));
        list.push(v);
    }
    return list;
}

// Saved world objects (doors, chests, elevators, barricades, secret walls, quest state, enemy spawners): each holds
// GUIDs that key into the save's SavableActorStates. `live` is the object's own in-memory copy (MySaveData).
Value SavablesState(double radiusCm, bool includeSpawners) {
    Value list = Value::array();
    SDK::AFS_PlayerChar_C* me = Player();
    const SDK::FVector at = me ? me->K2_GetActorLocation() : SDK::FVector{};
    SDK::UClass* spawner = SDK::UObject::FindClassFast("EnemySpawner_C");
    for (SDK::AActor* a : WorldActors(SDK::ASavableActor_C::StaticClass())) {
        if (!includeSpawners && spawner && a->IsA(spawner)) continue;
        const SDK::FVector p = a->K2_GetActorLocation();
        const double d = Dist(p, at);
        if (me && radiusCm > 0 && d > radiusCm) continue;
        auto* s = static_cast<SDK::ASavableActor_C*>(a);
        Value v = Value::object();
        v.set("name", ObjName(a));
        v.set("class", ObjName(a->Class));
        v.set("pos", Vec(p));
        v.set("dist", std::round(d));
        Value guids = Value::array();
        for (const SDK::FGuid& g : s->GUIDs) guids.push(GuidStr(g));
        v.set("guids", guids);
        Value live = Value::array();
        for (const SDK::FSavableActorState& st : s->MySaveData)
            live.push(st.IsSet_1_52B3D8FF4C3CC8B0DAAD90BE9BA2A2B7 ? Value(st.Value_4_81204DA949BB8DCF4D35D4B4419EC024) : Value());
        v.set("live", live);
        v.set("hasInteract", FindFn(a, "Interact") != nullptr);
        list.push(v);
    }
    return list;
}

Value CheckpointsState() {
    Value list = Value::array();
    SDK::AFS_PlayerChar_C* me = Player();
    const SDK::FVector at = me ? me->K2_GetActorLocation() : SDK::FVector{};
    for (SDK::AActor* a : WorldActors(SDK::ACheckpoint_C::StaticClass())) {
        Value v = Value::object();
        v.set("name", ObjName(a));
        v.set("pos", Vec(a->K2_GetActorLocation()));
        v.set("dist", std::round(Dist(a->K2_GetActorLocation(), at)));
        list.push(v);
    }
    return list;
}

// Knights' Domain draws each remote player as a SkeletalMeshActor with the knight mesh (Knight_mesh).
Value PuppetsState() {
    Value list = Value::array();
    for (SDK::AActor* a : WorldActors(SDK::ASkeletalMeshActor::StaticClass())) {
        auto* sk = static_cast<SDK::ASkeletalMeshActor*>(a)->SkeletalMeshComponent;
        if (!sk || ObjName(sk->SkeletalMesh) != "Knight_mesh") continue;
        // the level also places dead knights with this mesh; the mod's are spawned at runtime with default names
        if (ObjName(a).rfind("SkeletalMeshActor_", 0) != 0) continue;
        Value v = Value::object();
        v.set("name", ObjName(a));
        v.set("pos", Vec(a->K2_GetActorLocation()));
        v.set("hidden", static_cast<bool>(a->bHidden));
        // the mod drives puppets in single-node mode: the playing asset tells idle / walk / attack / death apart
        SDK::UAnimInstance* ai = sk->AnimScriptInstance;
        std::string anim;
        if (ai && ai->IsA(SDK::UAnimSingleNodeInstance::StaticClass()))
            anim = ObjName(static_cast<SDK::UAnimSingleNodeInstance*>(ai)->CurrentAsset);
        v.set("anim", anim);
        list.push(v);
    }
    return list;
}

Value PickupsState(double radiusCm) {
    Value list = Value::array();
    SDK::AFS_PlayerChar_C* me = Player();
    const SDK::FVector at = me ? me->K2_GetActorLocation() : SDK::FVector{};
    for (SDK::AActor* a : WorldActors(SDK::AItemPickup_Base_C::StaticClass())) {
        const SDK::FVector p = a->K2_GetActorLocation();
        const double d = Dist(p, at);
        if (me && radiusCm > 0 && d > radiusCm) continue;
        Value v = Value::object();
        v.set("name", ObjName(a));
        v.set("class", ObjName(a->Class));
        v.set("pos", Vec(p));
        v.set("dist", std::round(d));
        v.set("hidden", static_cast<bool>(a->bHidden));
        // a loot drop (spawned by an enemy, chest or pot), as opposed to a pickup placed in the level
        SDK::UClass* core = SDK::ACoreItemPickupActor_C::StaticClass();
        v.set("drop", core && a->IsA(core) && static_cast<bool>(static_cast<SDK::ACoreItemPickupActor_C*>(a)->IsADrop));
        list.push(v);
    }
    return list;
}

// Loaded actors whose class (or a parent class) or object name contains `word`: arena walls, boss doors, fight
// controllers, reward pickups. `collision` is the actor's own switch (SetActorEnableCollision); arena walls that a
// fight turns on and off use it or hide the actor.
std::string ActorsJson(const std::string& word, double radiusCm) {
    Value list = Value::array();
    SDK::AFS_PlayerChar_C* me = Player();
    const SDK::FVector at = me ? me->K2_GetActorLocation() : SDK::FVector{};
    // some level actors keep their root at the origin and place their parts as offset children (the Necronomicon's
    // door): the name or class match is enough here
    for (SDK::AActor* a : WorldActors(SDK::AActor::StaticClass(), true)) {
        bool match = ObjName(a).find(word) != std::string::npos;
        for (SDK::UStruct* s = a->Class; s && !match; s = s->SuperStruct) match = s->GetName().find(word) != std::string::npos;
        if (!match) continue;
        const SDK::FVector p = a->K2_GetActorLocation();
        const double d = Dist(p, at);
        if (me && radiusCm > 0 && d > radiusCm) continue;
        Value v = Value::object();
        v.set("name", ObjName(a));
        v.set("class", ObjName(a->Class));
        v.set("pos", Vec(p));
        v.set("dist", std::round(d));
        v.set("hidden", static_cast<bool>(a->bHidden));
        v.set("collision", static_cast<bool>(a->bActorEnableCollision));
        list.push(v);
    }
    return list.dump();
}

// Blueprint variable names carry a GUID suffix (Phase_12_0123...); `var` matches the name before it, or the full name.
bool ReadVar(SDK::UObject* obj, const std::string& var, std::string& out) {
    auto lower = [](std::string s) { for (char& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c))); return s; };
    const std::string want = lower(var);
    const uint8_t* base = reinterpret_cast<const uint8_t*>(obj);
    for (SDK::UStruct* st = obj->Class; st; st = st->SuperStruct) {
        for (SDK::FField* f = st->ChildProperties; f; f = f->Next) {
            const std::string name = lower(f->Name.ToString());
            const bool hit = name == want || (name.size() > want.size() && name.compare(0, want.size(), want) == 0 &&
                                              name[want.size()] == '_' && std::isdigit(static_cast<unsigned char>(name[want.size() + 1])));
            if (!hit || !f->ClassPrivate) continue;
            const std::string type = f->ClassPrivate->Name.ToString();
            auto* p = static_cast<SDK::FProperty*>(f);
            const uint8_t* at = base + p->Offset;
            char buf[64];
            if (type == "BoolProperty") {
                auto* b = static_cast<SDK::FBoolProperty*>(f);
                out = (base[p->Offset + b->ByteOffset] & b->ByteMask) ? "true" : "false";
            } else if (type == "IntProperty") {
                int32_t v; std::memcpy(&v, at, 4); out = std::to_string(v);
            } else if (type == "FloatProperty") {
                float v; std::memcpy(&v, at, 4); std::snprintf(buf, sizeof buf, "%g", v); out = buf;
            } else if (type == "ByteProperty" || type == "EnumProperty") {
                out = std::to_string(static_cast<int>(*at));
            } else if (type == "NameProperty") {
                out = reinterpret_cast<const SDK::FName*>(at)->ToString();
            } else if (type == "ObjectProperty") {
                SDK::UObject* o = nullptr; std::memcpy(&o, at, 8); out = o ? ObjName(o) : "None";
            } else {
                out = "<" + type + ">";
            }
            return true;
        }
    }
    return false;
}

// ------------------------------------------------------------------ watch (attack recorder)
// Enemy projectiles and area-attack actors live for a second or less, too short to catch by polling `state` from
// Python. `exec watch start` records, on the game thread every ~50 ms: every actor near the local player whose class
// (or a parent class) name contains one of the watch words, with its closest approach, plus the local player's health
// (with the mod's `kd god` on, health sits near 1e6 and every hit leaves a dent, so drops count hits). The classes are
// matched by name at runtime because most enemy Blueprints load after the title screen, where the SDK was dumped.

struct WatchRec {
    std::string cls, owner;
    ULONGLONG firstMs = 0, lastMs = 0;
    double minDist = 1e30;
    int samples = 0;
};

struct Watch {
    bool on = false;
    ULONGLONG t0 = 0, lastSample = 0;
    double radiusCm = 6000;
    std::vector<std::string> words{"Projectile", "Arrow", "Splash", "Vortex", "Whirl", "Wave", "AOE", "Aoe", "Spit",
                                   "Puddle", "Fireball", "Creep", "Explosion", "Shockwave"};
    std::map<std::string, WatchRec> seen;          // by actor name (names carry a unique number)
    std::map<SDK::UClass*, bool> classMatch;       // cache: does this class match a watch word
    double hpStart = 0, hpMin = 0, hpLast = 0, damage = 0;
    int drops = 0;
    bool hpValid = false;
    struct Drop { ULONGLONG ms; double amount, nearestDist; std::string nearest; };
    std::vector<Drop> dropLog;                     // each hit: when, how much, closest watched actor at that moment
    double prevNearestDist = 1e30;                 // previous sample's closest (a projectile is gone the frame it hits)
    std::string prevNearest;
} g_watch;

bool WatchClassMatches(SDK::UClass* cls) {
    auto it = g_watch.classMatch.find(cls);
    if (it != g_watch.classMatch.end()) return it->second;
    bool m = false;
    for (SDK::UStruct* s = cls; s && !m; s = s->SuperStruct) {
        const std::string n = s->GetName();
        for (const auto& w : g_watch.words) if (n.find(w) != std::string::npos) { m = true; break; }
    }
    g_watch.classMatch[cls] = m;
    return m;
}

void WatchTick() {
    if (!g_watch.on) return;
    const ULONGLONG now = GetTickCount64();
    if (now - g_watch.lastSample < 50) return;
    g_watch.lastSample = now;
    SDK::AFS_PlayerChar_C* me = Player();
    if (!me) return;
    const SDK::FVector at = me->K2_GetActorLocation();
    SDK::UWorld* w = World();
    auto& objs = SDK::UObject::GObjects;
    SDK::UClass* actorCls = SDK::AActor::StaticClass();
    double nearestDist = 1e30;
    std::string nearest;
    for (int i = 0; i < objs->Num(); ++i) {
        SDK::UObject* o = objs->GetByIndex(i);
        if (!o || o->IsDefaultObject() || !o->Class || !o->IsA(actorCls) || !WatchClassMatches(o->Class)) continue;
        auto* a = static_cast<SDK::AActor*>(o);
        if (a->bActorIsBeingDestroyed || !a->RootComponent) continue;
        SDK::UObject* outer = a->Outer;
        if (!outer || !outer->IsA(SDK::ULevel::StaticClass()) || static_cast<SDK::ULevel*>(outer)->OwningWorld != w) continue;
        const double d = Dist(a->K2_GetActorLocation(), at);
        if (d > g_watch.radiusCm) continue;
        WatchRec& r = g_watch.seen[a->GetName()];
        if (r.samples == 0) {
            r.cls = ObjName(a->Class);
            r.owner = ObjName(a->Instigator ? static_cast<SDK::UObject*>(a->Instigator) : static_cast<SDK::UObject*>(a->Owner));
            r.firstMs = now - g_watch.t0;
        }
        r.lastMs = now - g_watch.t0;
        r.minDist = (std::min)(r.minDist, d);
        ++r.samples;
        if (d < nearestDist) { nearestDist = d; nearest = a->GetName(); }
    }
    const double hp = me->Health;
    if (!g_watch.hpValid) {
        g_watch.hpStart = g_watch.hpMin = g_watch.hpLast = hp;
        g_watch.hpValid = true;
    } else {
        if (hp < g_watch.hpLast - 0.01) {
            ++g_watch.drops;
            g_watch.damage += g_watch.hpLast - hp;
            const bool usePrev = g_watch.prevNearestDist < nearestDist;
            g_watch.dropLog.push_back({now - g_watch.t0, g_watch.hpLast - hp, usePrev ? g_watch.prevNearestDist : nearestDist,
                                       usePrev ? g_watch.prevNearest : nearest});
        }
        g_watch.hpMin = (std::min)(g_watch.hpMin, hp);
        g_watch.hpLast = hp;
    }
    g_watch.prevNearestDist = nearestDist;
    g_watch.prevNearest = nearest;
}

Value WatchState() {
    Value v = Value::object();
    v.set("on", g_watch.on);
    v.set("ms", g_watch.t0 ? static_cast<double>(GetTickCount64() - g_watch.t0) : 0.0);
    Value list = Value::array();
    for (const auto& [name, r] : g_watch.seen) {
        Value e = Value::object();
        e.set("name", name);
        e.set("class", r.cls);
        e.set("owner", r.owner);
        e.set("firstMs", static_cast<double>(r.firstMs));
        e.set("lastMs", static_cast<double>(r.lastMs));
        e.set("minDist", std::round(r.minDist));
        e.set("samples", r.samples);
        list.push(e);
    }
    v.set("actors", list);
    Value h = Value::object();
    h.set("start", g_watch.hpStart);
    h.set("min", g_watch.hpMin);
    h.set("last", g_watch.hpLast);
    h.set("drops", g_watch.drops);
    h.set("damage", g_watch.damage);
    Value hits = Value::array();
    for (const auto& d : g_watch.dropLog) {
        Value e = Value::object();
        e.set("ms", static_cast<double>(d.ms));
        e.set("amount", d.amount);
        e.set("nearest", d.nearest.empty() ? Value() : Value(d.nearest));
        e.set("nearestDist", d.nearest.empty() ? Value() : Value(std::round(d.nearestDist)));
        hits.push(e);
    }
    h.set("hits", hits);
    v.set("health", h);
    return v;
}

// ------------------------------------------------------------------ reflection
// A Blueprint variable by name (classes that load after the title screen are not in the SDK): bool, int, float, byte,
// or an object (its name). Null when the actor has no such variable.
Value ReadProp(SDK::UObject* obj, const std::string& name) {
    for (SDK::UStruct* st = obj ? obj->Class : nullptr; st; st = st->SuperStruct) {
        for (SDK::FField* f = st->ChildProperties; f; f = f->Next) {
            if (!f->ClassPrivate || f->Name.ToString() != name) continue;
            auto* p = static_cast<SDK::FProperty*>(f);
            const uint8_t* at = reinterpret_cast<const uint8_t*>(obj) + p->Offset;
            const std::string type = f->ClassPrivate->Name.ToString();
            if (type == "BoolProperty") {
                auto* b = static_cast<SDK::FBoolProperty*>(f);
                return (at[b->ByteOffset] & b->ByteMask) != 0;
            }
            if (type == "IntProperty") return *reinterpret_cast<const int32_t*>(at);
            if (type == "FloatProperty") return *reinterpret_cast<const float*>(at);
            if (type == "ByteProperty" || type == "EnumProperty") return static_cast<int>(*at);
            if (type == "ObjectProperty") {
                SDK::UObject* o = *reinterpret_cast<SDK::UObject* const*>(at);
                return o ? Value(ObjName(o)) : Value();
            }
            return "unsupported " + type;
        }
    }
    return Value();
}

// Loaded actors whose class (or a parent class) name contains `match`, nearest first.
Value FindActors(const std::string& match, double radiusCm) {
    Value list = Value::array();
    SDK::AFS_PlayerChar_C* me = Player();
    const SDK::FVector at = me ? me->K2_GetActorLocation() : SDK::FVector{};
    std::vector<std::pair<double, SDK::AActor*>> hits;
    for (SDK::AActor* a : WorldActors(SDK::AActor::StaticClass())) {
        bool m = false;
        for (SDK::UStruct* st = a->Class; st && !m; st = st->SuperStruct) m = st->GetName().find(match) != std::string::npos;
        if (!m) continue;
        const double d = Dist(a->K2_GetActorLocation(), at);
        if (radiusCm > 0 && d > radiusCm) continue;
        hits.push_back({d, a});
    }
    std::sort(hits.begin(), hits.end(), [](auto& x, auto& y) { return x.first < y.first; });
    for (auto& [d, a] : hits) {
        Value v = Value::object();
        v.set("name", ObjName(a));
        v.set("class", ObjName(a->Class));
        v.set("pos", Vec(a->K2_GetActorLocation()));
        v.set("dist", std::round(d));
        list.push(v);
    }
    return list;
}

// ------------------------------------------------------------------ adapter

class Adapter : public cb::Adapter {
public:
    cb::Capabilities capabilities() const override {
        cb::Capabilities c; c.exec = c.eval = c.state = true; return c;
    }

    cb::ExecResult Exec(const std::string& line, const std::string&) override {
        std::istringstream in(line);
        std::string cmd; in >> cmd;
        if (cmd == "newgame" || cmd == "loadslot") {
            int slot = 0; in >> slot;
            auto* ui = FirstInstance<SDK::UTitleScreenUI_C>();
            if (!ui) throw cb::Error(cb::errc::kInvalidParams, "no title screen");
            Need(CallNoParams(ui, "SkipIntroSplashes"), "SkipIntroSplashes");
            Need(CallNoParams(ui, "ConfirmLanguage"), "ConfirmLanguage");
            if (cmd == "newgame") {
                SDK::Params::TitleScreenUI_C_SaveSlotSelected p{};
                p.Index_0 = slot; p.Backup = false; p.BackupSlot = 0;
                Need(CallWith(ui, "SaveSlotSelected", p), "SaveSlotSelected");
                Need(CallNoParams(ui, "NewGameOnNewSlotConfirmed"), "NewGameOnNewSlotConfirmed");
            } else {
                SDK::Params::TitleScreenUI_C_Load_Slot p{};
                p.Slot_0 = slot; p.Backup = false; p.BackupSlot = 0;
                Need(CallWith(ui, "Load Slot", p), "Load Slot");
            }
            return {true, cmd + " " + std::to_string(slot)};
        }
        if (cmd == "skipintro") {
            auto* p = Player();
            if (!p) throw cb::Error(cb::errc::kInvalidParams, "no player");
            Need(CallNoParams(p, "SkipIntroCutscene"), "SkipIntroCutscene");
            return {true, "ok"};
        }
        if (cmd == "tp") {
            float x = 0, y = 0, z = 0, yaw = 0; in >> x >> y >> z >> yaw;
            auto* p = Player();
            if (!p) throw cb::Error(cb::errc::kInvalidParams, "no player");
            p->K2_SetActorLocation(SDK::FVector{x, y, z}, false, nullptr, true);
            if (auto* pc = SDK::UGameplayStatics::GetPlayerController(World(), 0)) {
                SDK::FRotator r{}; r.Yaw = yaw; pc->SetControlRotation(r);
            }
            return {true, "ok"};
        }
        if (cmd == "tpto") {   // tpto <actor name> [distance cm]: stand next to an actor, facing it
            std::string name; float d = 150; in >> name >> d;
            SDK::AActor* a = ActorByName(name);
            auto* p = Player();
            if (!a || !p) throw cb::Error(cb::errc::kInvalidParams, "no actor " + name + " or no player");
            const SDK::FVector t = a->K2_GetActorLocation();
            const SDK::FVector me = p->K2_GetActorLocation();
            const float dx = me.X - t.X, dy = me.Y - t.Y, len = std::max(1.0f, std::sqrt(dx * dx + dy * dy));
            p->K2_SetActorLocation(SDK::FVector{t.X + dx / len * d, t.Y + dy / len * d, t.Z + 60}, false, nullptr, true);
            if (auto* pc = SDK::UGameplayStatics::GetPlayerController(World(), 0)) {
                SDK::FRotator r{}; r.Yaw = std::atan2(-dy, -dx) * 180.f / 3.14159265f; pc->SetControlRotation(r);
            }
            return {true, "ok"};
        }
        if (cmd == "interact") {   // interact <actor name>: the local player uses it (pickup, door, chest, checkpoint ...)
            std::string name; in >> name;
            SDK::AActor* a = ActorByName(name);
            auto* p = Player();
            if (!a || !p) throw cb::Error(cb::errc::kInvalidParams, "no actor " + name + " or no player");
            SDK::Params::ItemPickup_Base_C_Interact ip{};
            ip.PlayerInteracting = p;
            Need(CallWith(a, "Interact", ip), "Interact");
            return {true, "interacted with " + name};
        }
        if (cmd == "fns") {   // fns <actor>: Blueprint functions on the actor's class chain (down to AActor)
            std::string name; in >> name;
            SDK::AActor* a = ActorByName(name);
            if (!a) throw cb::Error(cb::errc::kInvalidParams, "no actor " + name);
            std::string out;
            for (SDK::UStruct* s = a->Class; s && s != SDK::AActor::StaticClass(); s = s->SuperStruct) {
                out += "[" + s->GetName() + "]";
                for (SDK::UField* f = s->Children; f; f = f->Next)
                    if (f->IsA(SDK::UFunction::StaticClass())) out += " " + f->GetName() + ";";
                out += "\n";
            }
            return {true, out};
        }
        if (cmd == "actors") {   // actors <word> [radius cm]: loaded actors whose class or name contains <word>, as JSON
            std::string word; double radius = 0; in >> word >> radius;
            if (word.empty()) throw cb::Error(cb::errc::kInvalidParams, "actors <word> [radius cm]");
            return {true, ActorsJson(word, radius)};
        }
        if (cmd == "getvar") {   // getvar <actor> <variable>: a Blueprint variable's value (name match ignores the GUID suffix)
            std::string name, var; in >> name >> var;
            SDK::AActor* a = ActorByName(name);
            if (!a) throw cb::Error(cb::errc::kInvalidParams, "no actor " + name);
            std::string out;
            // <Component>.collision: the collision of a component variable (a gate's blocker box): "none" / "query" /
            // "physics" / "both", as the component reports it now
            if (const size_t dot = var.find('.'); dot != std::string::npos && var.substr(dot + 1) == "collision") {
                if (!ReadVar(a, var.substr(0, dot), out)) throw cb::Error(cb::errc::kInvalidParams, "no variable " + var);
                SDK::UObject* comp = nullptr;
                for (SDK::UActorComponent* c : a->BlueprintCreatedComponents)
                    if (c && ObjName(c) == out) comp = c;
                if (!comp || !comp->IsA(SDK::UPrimitiveComponent::StaticClass()))
                    throw cb::Error(cb::errc::kInvalidParams, var + " is not a primitive component");
                static const char* kNames[] = {"none", "query", "physics", "both"};
                const auto e = static_cast<int>(static_cast<SDK::UPrimitiveComponent*>(comp)->GetCollisionEnabled());
                return {true, e >= 0 && e < 4 ? kNames[e] : std::to_string(e)};
            }
            if (!ReadVar(a, var, out)) throw cb::Error(cb::errc::kInvalidParams, "no readable variable " + var + " on " + name);
            return {true, out};
        }
        if (cmd == "callon") {   // callon <actor> <function with no params>
            std::string name, fn; in >> name;
            std::getline(in, fn);
            if (!fn.empty() && fn[0] == ' ') fn.erase(0, 1);
            SDK::AActor* a = ActorByName(name);
            if (!a) throw cb::Error(cb::errc::kInvalidParams, "no actor " + name);
            Need(CallNoParams(a, fn), fn);
            return {true, "called " + fn + " on " + name};
        }
        if (cmd == "activate" || cmd == "deactivate") {   // Activate(ByLoad=false, ActorThatTriggered=local player)
            std::string name; in >> name;
            SDK::AActor* a = ActorByName(name);
            auto* p = Player();
            if (!a || !p) throw cb::Error(cb::errc::kInvalidParams, "no actor " + name + " or no player");
            struct { bool byLoad; uint8_t pad[7]; SDK::AActor* by; } prm{false, {}, p};
            const std::string fn = cmd == "activate" ? "Activate" : "Deactivate";
            Need(CallWith(a, fn, prm), fn);
            return {true, fn + " " + name};
        }
        if (cmd == "sweep") {   // sweep x y z: move the player with collision; reports where it stopped and what it hit
            float x = 0, y = 0, z = 0; in >> x >> y >> z;
            auto* p = Player();
            if (!p) throw cb::Error(cb::errc::kInvalidParams, "no player");
            SDK::FHitResult hit{};
            p->K2_SetActorLocation(SDK::FVector{x, y, z}, true, &hit, false);
            const SDK::FVector at = p->K2_GetActorLocation();
            SDK::AActor* by = hit.Actor.Get();
            char b[256];
            std::snprintf(b, sizeof b, "{\"pos\":[%.0f,%.0f,%.0f],\"blocked\":%s,\"by\":\"%s\"}", at.X, at.Y, at.Z,
                          hit.bBlockingHit ? "true" : "false", by ? by->GetName().c_str() : "");
            return {true, b};
        }
        if (cmd == "tpside") {   // tpside <actor> <cm along its forward axis, negative = behind> [up]: stand there, facing it
            std::string name; float d = 150, up = 60; in >> name >> d >> up;
            SDK::AActor* a = ActorByName(name);
            auto* p = Player();
            if (!a || !p) throw cb::Error(cb::errc::kInvalidParams, "no actor " + name + " or no player");
            const SDK::FVector c = a->K2_GetActorLocation();
            const SDK::FVector f = a->GetActorForwardVector();
            p->K2_SetActorLocation(SDK::FVector{c.X + f.X * d, c.Y + f.Y * d, c.Z + up}, false, nullptr, true);
            if (auto* pc = SDK::UGameplayStatics::GetPlayerController(World(), 0)) {
                SDK::FRotator r{}; r.Yaw = std::atan2(-f.Y * d, -f.X * d) * 180.f / 3.14159265f; pc->SetControlRotation(r);
            }
            return {true, "ok"};
        }
        if (cmd == "breakhp") {   // breakhp <barricade>: HP to 0, then the game's own TookHit (what a final blow runs)
            std::string name; in >> name;
            SDK::AActor* a = ActorByName(name);
            SDK::UClass* cls = SDK::UObject::FindClassFast("BreakableBarricade_C");
            if (!a || !cls || !a->IsA(cls)) throw cb::Error(cb::errc::kInvalidParams, "no barricade " + name);
            static_cast<SDK::ABreakableBarricade_C*>(a)->HP = 0;
            Need(CallNoParams(a, "TookHit"), "TookHit");
            return {true, "broke " + name};
        }
        if (cmd == "clearpath") {   // clearpath <actor> [cm]: turn off collision of enemies near it (test setup only)
            std::string name; float r = 1500; in >> name >> r;
            SDK::AActor* a = ActorByName(name);
            if (!a) throw cb::Error(cb::errc::kInvalidParams, "no actor " + name);
            const SDK::FVector c = a->K2_GetActorLocation();
            int n = 0;
            for (SDK::AActor* e : WorldActors(SDK::AFS_EnemyCharacterBase::StaticClass()))
                if (Dist(e->K2_GetActorLocation(), c) <= r) { e->SetActorEnableCollision(false); ++n; }
            return {true, "collision off on " + std::to_string(n) + " enemies"};
        }
        if (cmd == "passthrough") {   // passthrough <actor> [cm] [up]: can the player walk through it along its forward axis?
            std::string name; float d = 250, up = 100; in >> name >> d >> up;
            SDK::AActor* a = ActorByName(name);
            auto* p = Player();
            if (!a || !p) throw cb::Error(cb::errc::kInvalidParams, "no actor " + name + " or no player");
            const SDK::FVector c = a->K2_GetActorLocation();
            const SDK::FVector f = a->GetActorForwardVector();
            const SDK::FVector from{c.X - f.X * d, c.Y - f.Y * d, c.Z + up};
            const SDK::FVector to{c.X + f.X * d, c.Y + f.Y * d, c.Z + up};
            auto tryPass = [&](const SDK::FVector& s, const SDK::FVector& e) {
                p->K2_SetActorLocation(s, false, nullptr, true);
                SDK::FHitResult hit{};
                p->K2_SetActorLocation(e, true, &hit, false);
                const SDK::FVector at = p->K2_GetActorLocation();
                const double left = Dist(at, e);
                SDK::AActor* by = hit.Actor.Get();
                char b[200];
                std::snprintf(b, sizeof b, "{\"passed\":%s,\"leftCm\":%.0f,\"by\":\"%s\"}", left < 30 ? "true" : "false",
                              left, by ? by->GetName().c_str() : "");
                return std::string(b);
            };
            // both directions: a wall right behind the start point on one side must not fake a block
            const std::string fwd = tryPass(from, to), back = tryPass(to, from);
            return {true, "{\"forward\":" + fwd + ",\"backward\":" + back + "}"};
        }
        if (cmd == "console") {   // console <command line>: run an engine console command (e.g. HighResShot 1)
            std::string rest; std::getline(in, rest);
            if (!rest.empty() && rest[0] == ' ') rest.erase(0, 1);
            SDK::UKismetSystemLibrary::ExecuteConsoleCommand(World(), SDK::FString(std::wstring(rest.begin(), rest.end()).c_str()),
                                                            SDK::UGameplayStatics::GetPlayerController(World(), 0));
            return {true, "ran " + rest};
        }
        if (cmd == "streamlevel") {   // streamlevel <sublevel name>: load and show it (as walking into it would)
            std::string lvl; in >> lvl;
            SDK::FLatentActionInfo li{};
            li.Linkage = -1;
            li.CallbackTarget = Player();   // the latent action manager keys actions by this object
            li.UUID = static_cast<int32_t>(GetTickCount64() & 0x7FFFFFFF);
            SDK::UGameplayStatics::LoadStreamLevel(World(), SDK::UKismetStringLibrary::Conv_StringToName(
                SDK::FString(std::wstring(lvl.begin(), lvl.end()).c_str())), true, false, li);
            return {true, "streaming " + lvl};
        }
        if (cmd == "find") {   // find <class match> [radius cm]: loaded actors of that class, nearest first (JSON)
            std::string match; double r = 0; in >> match >> r;
            return {true, FindActors(match, r).dump()};
        }
        if (cmd == "prop") {   // prop <actor> <variable> [variable ...]: Blueprint variables by name (JSON object)
            std::string name; in >> name;
            SDK::AActor* a = ActorByName(name);
            if (!a) throw cb::Error(cb::errc::kInvalidParams, "no actor " + name);
            Value v = Value::object();
            for (std::string pn; in >> pn;) v.set(pn, ReadProp(a, pn));
            return {true, v.dump()};
        }
        if (cmd == "call") {
            std::string fn; in >> fn;
            auto* p = Player();
            if (!p || !CallNoParams(p, fn)) throw cb::Error(cb::errc::kInvalidParams, "cannot call " + fn);
            return {true, "called " + fn};
        }
        if (cmd == "widgets") {   // widgets [class match]: live widget classes; with a match, also their own functions
            std::string match; in >> match;
            std::map<std::string, int> seen;
            std::string fns;
            for (auto* w : WidgetsMatching(match)) {
                ++seen[ObjName(w->Class)];
                if (!match.empty() && fns.empty())
                    for (SDK::UField* f = w->Class->Children; f; f = f->Next)
                        if (f->IsA(SDK::UFunction::StaticClass())) fns += ObjName(f) + " ";
            }
            std::string txt;
            for (auto& [k, n] : seen) txt += k + "x" + std::to_string(n) + " ";
            return {true, txt + (fns.empty() ? "" : "| functions: " + fns)};
        }
        if (cmd == "uicall") {   // uicall <widget class match> <function>: call a no-parameter function on it
            std::string match, fn; in >> match >> fn;
            auto ws = WidgetsMatching(match);
            for (auto* w : ws)
                if (CallNoParams(w, fn)) return {true, "called " + fn + " on " + ObjName(w)};
            throw cb::Error(cb::errc::kInvalidParams, "no widget " + match + " with " + fn);
        }
        if (cmd == "die") {   // die: the local player dies the normal way (the player Blueprint's Kill)
            auto* p = Player();
            if (!p) throw cb::Error(cb::errc::kInvalidParams, "no player");
            SDK::Params::FS_PlayerChar_C_Kill k{};
            Need(CallWith(p, "Kill", k), "Kill");
            return {true, "killed the local player"};
        }
        if (cmd == "watch") {   // watch start [radius cm] [word ...] | watch stop: see WatchTick; read with state "watch"
            std::string sub; in >> sub;
            if (sub == "start") {
                double r = 6000; in >> r;
                std::vector<std::string> words;
                for (std::string w; in >> w;) words.push_back(w);
                g_watch = Watch{};
                if (!words.empty()) g_watch.words = words;   // default: the list in Watch
                g_watch.radiusCm = r > 0 ? r : 6000;
                g_watch.t0 = GetTickCount64();
                g_watch.on = true;
                return {true, "watching"};
            }
            if (sub == "stop") { g_watch.on = false; return {true, "stopped"}; }
            throw cb::Error(cb::errc::kInvalidParams, "watch start [radius] | watch stop");
        }
        if (cmd == "kd") {   // pass the rest of the line to the co-op mod's own dev command file
            std::string rest; std::getline(in, rest);
            if (!rest.empty() && rest[0] == ' ') rest.erase(0, 1);
            std::ofstream f(KdDevCmdPath(), std::ios::app);
            if (!f) throw cb::Error(cb::errc::kInvalidParams, "cannot write the mod's devcmd.txt");
            f << rest << "\n";
            return {true, "queued for the mod: " + rest};
        }
        throw cb::Error(cb::errc::kInvalidParams, "unknown command '" + cmd + "'");
    }

    Value Eval(const std::string& expr, const std::string&) override {
        if (expr == "world") return World() ? ObjName(World()) : std::string("none");
        if (expr == "title") return FirstInstance<SDK::UTitleScreenUI_C>() != nullptr;
        if (expr == "loading") { auto* gi = GameInstance(); return gi ? static_cast<bool>(gi->bLoadingMap) : true; }
        if (expr == "inworld") { auto* gi = GameInstance(); return Player() != nullptr && gi && !gi->bLoadingMap; }
        if (expr == "health") { auto* p = Player(); return p ? Value(p->Health) : Value(); }
        throw cb::Error(cb::errc::kInvalidParams, "unknown expression '" + expr + "'");
    }

    Value State(const std::vector<std::string>& sections) override {
        auto want = [&](const char* s) {
            if (sections.empty()) return true;
            for (auto& x : sections) if (x == s) return true;
            return false;
        };
        Value v = Value::object();
        v.set("world", World() ? ObjName(World()) : std::string("none"));
        if (want("player")) v.set("player", PlayerState());
        if (want("save")) v.set("save", SaveState());
        if (want("enemies")) v.set("enemies", EnemyState(6000));
        if (want("players")) v.set("players", PlayersState());
        if (want("pickups")) v.set("pickups", PickupsState(6000));
        if (want("puppets")) v.set("puppets", PuppetsState());
        if (want("savables")) v.set("savables", SavablesState(6000, false));
        if (!sections.empty() && want("savables_all")) v.set("savables_all", SavablesState(0, false));
        if (want("checkpoints")) v.set("checkpoints", CheckpointsState());
        if (want("watch")) v.set("watch", WatchState());
        return v;
    }
};
Adapter g_adapter;

// ------------------------------------------------------------------ pump
// Requests run inside the game's own ProcessEvent (the path every Blueprint event, widget tick and button click takes),
// so a request may open a level exactly like a title-screen button does. We swap the ProcessEvent slot in the vtables
// of a few native classes whose objects tick through Blueprint events every frame (title widget, HUD widgets, player),
// keeping whatever was in the slot before (the co-op mod's own hook, if any) as the next link. Our DLL only.

using ProcessEventFn = void (*)(SDK::UObject*, SDK::UFunction*, void*);
struct VHook { void** slot = nullptr; ProcessEventFn next = nullptr; };
VHook g_vhooks[4];
int g_vhookCount = 0;
thread_local bool t_inPump = false;
ULONGLONG g_lastPump = 0;

template <int I>
void HookedProcessEvent(SDK::UObject* obj, SDK::UFunction* fn, void* parms) {
    if (!t_inPump && GetCurrentThreadId() == g_gameThread) {
        const ULONGLONG now = GetTickCount64();
        if (now - g_lastPump >= 8) {
            g_lastPump = now;
            t_inPump = true;
            g_server.Pump();
            WatchTick();
            t_inPump = false;
        }
    }
    g_vhooks[I].next(obj, fn, parms);
}

template <int I>
bool HookVtable(SDK::UObject* cdo) {
    if (!cdo) return false;
    void** vtable = *reinterpret_cast<void***>(cdo);
    void** slot = &vtable[SDK::Offsets::ProcessEventIdx];
    for (int k = 0; k < g_vhookCount; ++k) if (g_vhooks[k].slot == slot) return true;   // shared vtable
    DWORD old = 0;
    if (!VirtualProtect(slot, sizeof(void*), PAGE_READWRITE, &old)) return false;
    g_vhooks[I].slot = slot;
    g_vhooks[I].next = reinterpret_cast<ProcessEventFn>(*slot);
    *slot = reinterpret_cast<void*>(&HookedProcessEvent<I>);
    VirtualProtect(slot, sizeof(void*), old, &old);
    g_vhookCount = I + 1;
    return true;
}

LRESULT CALLBACK GetMsgProc(int code, WPARAM wp, LPARAM lp) {
    // Installs the ProcessEvent hooks once, on the game thread (CDO lookups walk GObjects).
    if (code >= 0 && wp == PM_REMOVE) {
        MSG* m = reinterpret_cast<MSG*>(lp);
        if (m->message == WM_NULL && m->wParam == kPumpMarker && m->hwnd == nullptr && g_vhookCount == 0) {
            HookVtable<0>(SDK::UUserWidget::GetDefaultObj());
            HookVtable<1>(SDK::AFS_PlayerCharacter::GetDefaultObj());
            HookVtable<2>(SDK::APlayerController::GetDefaultObj());
            g_run = false;   // hooks are in; stop kicking the message loop
        }
    }
    return CallNextHookEx(g_hook, code, wp, lp);
}

DWORD FindGameThread() {
    DWORD tid = 0;
    EnumWindows([](HWND h, LPARAM out) -> BOOL {
        DWORD pid = 0;
        DWORD t = GetWindowThreadProcessId(h, &pid);
        wchar_t cls[64] = {};
        GetClassNameW(h, cls, 64);
        if (pid == GetCurrentProcessId() && wcscmp(cls, L"UnrealWindow") == 0) {
            *reinterpret_cast<DWORD*>(out) = t;
            return FALSE;
        }
        return TRUE;
    }, reinterpret_cast<LPARAM>(&tid));
    return tid;
}

void Main() {
    for (int i = 0; i < 600 && !g_gameThread; ++i) { g_gameThread = FindGameThread(); if (!g_gameThread) Sleep(100); }
    if (!g_gameThread) return;
    g_hook = SetWindowsHookExW(WH_GETMESSAGE, GetMsgProc, g_self, g_gameThread);
    if (!g_hook) return;
    cb::Options opt;
    opt.game = "queens-domain";
    opt.build = "qdbridge-0.1";
    opt.tcpPort = ArgInt(L"-qdbridge-port=", 27990);
    if (!g_server.Start(opt, g_adapter)) return;
    while (g_run) {
        PostThreadMessageW(g_gameThread, WM_NULL, kPumpMarker, 0);
        Sleep(16);
    }
}

}  // namespace

BOOL APIENTRY DllMain(HMODULE mod, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_self = mod;
        DisableThreadLibraryCalls(mod);
        CreateThread(nullptr, 0, [](LPVOID) -> DWORD { Main(); return 0; }, nullptr, 0, nullptr);
    }
    return TRUE;
}
