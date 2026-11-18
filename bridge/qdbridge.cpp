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
#include "SDK/FS_SaveSlot_classes.hpp"
#include "SDK/EnemyBase_classes.hpp"
#include "SDK/ItemPickup_Base_classes.hpp"
#include "SDK/ItemPickup_Base_parameters.hpp"
#include "SDK/SavableActor_classes.hpp"
#include "SDK/SavableActorState_structs.hpp"
#include "SDK/Checkpoint_classes.hpp"
#include "SDK/BreakableBarricade_classes.hpp"

#include <algorithm>
#include <cstring>

#include <coopbridge/server.h>

#include <Windows.h>

#include <atomic>
#include <cmath>
#include <cstdio>
#include <fstream>
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

Value PlayerState() {
    Value v = Value::object();
    SDK::AFS_PlayerChar_C* p = Player();
    v.set("present", p != nullptr);
    if (!p) return v;
    v.set("class", ObjName(p->Class));
    v.set("pos", Vec(p->K2_GetActorLocation()));
    v.set("health", p->Health);
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
std::vector<SDK::AActor*> WorldActors(SDK::UClass* cls) {
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
        if (p.X == 0 && p.Y == 0 && p.Z == 0) continue;
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
    for (SDK::AActor* a : WorldActors(SDK::AActor::StaticClass())) {
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
        if (cmd == "call") {
            std::string fn; in >> fn;
            auto* p = Player();
            if (!p || !CallNoParams(p, fn)) throw cb::Error(cb::errc::kInvalidParams, "cannot call " + fn);
            return {true, "called " + fn};
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
