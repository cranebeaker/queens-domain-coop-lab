"""Co-op scenarios for Knights' Domain.

  py -3 -I runner/scenarios.py [name ...]      run scenarios against an already booted pair (rig.py pair)
  py -3 -I runner/scenarios.py --suite lane1   boot fresh pairs and run all of Lane 1 (about 12 minutes)
  py -3 -I runner/scenarios.py --suite all --rerun results/<time>.json   only what did not pass last time
  py -3 -I runner/scenarios.py --suite all --only ros_dragon,queen        just these (each with its group's pair)
  py -3 -I runner/scenarios.py --help                                     every option and suite

Each scenario prints PASS / FAIL / SKIP with the numbers it measured. A scenario passes when the co-op behaviour is
right, so FAIL means the gap is real. SKIP means the setup did not happen (the measurement would prove nothing).
"""
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rig  # noqa: E402


def dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def remote_players(g):
    """Puppets of the other players (Knights' Domain: SkeletalMeshActors with the knight mesh; feet position)."""
    return g.state(sections=["puppets"])["puppets"]


def local_pos(g):
    return g.state(sections=["player"])["player"]["pos"]


def save(g):
    return g.state(sections=["save"])["save"]


def changed(x, y):
    return {k: (x.get(k), y.get(k)) for k in set(x) | set(y) if x.get(k) != y.get(k)}


def find_obj(g, name, near=None):
    """The loaded saved-world object called `name`: names repeat across sublevels, so take the one nearest to `near`
    (a position) or else nearest to the player (the bridge's own rule for names). Returns (entry, save)."""
    st = g.state(sections=["savables_all", "save"])
    same = [s for s in st["savables_all"] if s["name"] == name]
    if not same:
        return None, st["save"]
    key = (lambda s: dist(s["pos"], near)) if near else (lambda s: s["dist"])
    return min(same, key=key), st["save"]


def obj_state(g, name, near=None):
    """An object's own live state plus the save values of its GUIDs, as seen by one copy."""
    obj, sv = find_obj(g, name, near)
    if obj is None:
        return None
    saved = sv.get("savableActorStates", {})
    return {"live": obj["live"], "saved": [saved.get(gd) for gd in obj["guids"]]}


# ------------------------------------------------------------------ players

def movement(a, b):
    """B moves 8 m; A's puppet of B must end within 1 m of B."""
    p = local_pos(b)
    b.exec(f"tp {p[0] + 800} {p[1]} {p[2] + 50} 0")
    time.sleep(3)
    target = local_pos(b)
    puppets = remote_players(a)
    if not puppets:
        return False, {"why": "A has no puppet of B"}
    # puppet positions are feet, the local player is the capsule centre (~90 cm up): compare horizontally
    err = min(dist(q["pos"][:2], target[:2]) for q in puppets)
    return err <= 100, {"b_pos": target, "puppet_error_cm": round(err)}


# ------------------------------------------------------------------ loot

def pickup_by(taker, other, who, target):
    """`taker` picks up `target`: the other player's copy should be taken too and both saves should agree."""
    before_t, before_o = obj_state(taker, target), obj_state(other, target)
    if before_t is None:
        return None, {"why": f"{target} not loaded on {who}"}
    inv_before = save(taker).get("inventory", {})
    after_t = before_t
    # after a long teleport (the gate check) the pickup's sublevel can take a moment to settle: the first interact
    # then does nothing, so the player tries again, as someone standing at the item would
    for attempt in range(3):
        taker.exec(f"tpto {target} 120")
        time.sleep(1 + 2 * attempt)
        taker.exec(f"interact {target}")
        for _ in range(5):   # the pickup animation runs before the save entry is written
            time.sleep(2)
            after_t = obj_state(taker, target)
            if after_t != before_t:
                break
        if after_t != before_t:
            break
    time.sleep(2)
    after_o = obj_state(other, target)
    res = {"taker": who, "pickup": target, "taker_state": [before_t, after_t], "other_state": [before_o, after_o],
           "taker_inventory_change": changed(inv_before, save(taker).get("inventory", {}))}
    if after_t == before_t:
        return None, {"why": f"the pickup did not change state on {who}", **res}
    return after_o == after_t, res


def loot_and_world(a, b):
    return pickup_by(a, b, "A", "JarHammer_Pickup_2")


def client_loot(a, b):
    return pickup_by(b, a, "B", "Herb_Item_BP_2")


# ------------------------------------------------------------------ world objects

# How to use each kind of world object. Alternatives are tried in order until the object's state changes.
USE = {
    "interact": [["tpto {o} 150", "interact {o}"], ["tpside {o} 150", "interact {o}"], ["tpside {o} -150", "interact {o}"]],
    "key": [["tpto {o} 150", "callon {o} InsertKey"]],
    "activate": [["tpto {o} 200", "activate {o}"]],
    "break": [["tpto {o} 200", "callon {o} Break"]],
    "barricade": [["tpto {o} 200", "breakhp {o}"]],
}


def use_objects(actor, other, items, settle=6):
    """`actor` uses each (object, how); report what changed on `actor` and whether `other` followed."""
    rows, all_follow, any_changed = [], True, False
    for n, how in items:
        try:
            actor.exec(f"tpto {n} 300")   # walk up first, so the name means the object in front of the player
        except Exception:
            rows.append({"object": n, "skipped": "not loaded on the acting copy"})
            continue
        time.sleep(2)
        entry, _ = find_obj(actor, n)
        pos = entry["pos"] if entry else None
        before_actor, before_other = obj_state(actor, n, pos), obj_state(other, n, pos)
        if before_other is None:   # that part of the level is not streamed in on the other copy: go and look
            try:
                other.exec(f"tpto {n} 400")
                time.sleep(3)
            except Exception:
                pass
            before_other = obj_state(other, n, pos)
        if before_actor is None:
            rows.append({"object": n, "skipped": "not loaded on the acting copy"})
            continue
        after_actor, used = before_actor, None
        for alt in USE[how]:
            try:
                for c in alt:
                    actor.exec(c.format(o=n))
                    time.sleep(1)
            except Exception as e:
                used = f"failed: {e}"
                continue
            time.sleep(settle)
            after_actor = obj_state(actor, n, pos)
            used = " / ".join(alt).format(o=n)
            if after_actor != before_actor:
                break
        after_other = obj_state(other, n, pos)
        did = after_actor != before_actor
        follows = after_other == after_actor
        if after_other is None and before_other is not None and entry is not None:
            # it was there before and is gone now: a broken barricade or barrel removes itself. Then the other
            # copy's save decides (the GUIDs are the acting copy's, they are the same object on both)
            sv = save(other).get("savableActorStates", {})
            after_other = {"live": "removed", "saved": [sv.get(gd) for gd in entry["guids"]]}
            follows = after_other["saved"] == after_actor["saved"]
        any_changed |= did
        if did:
            all_follow &= follows
        rows.append({"object": n, "pos": pos, "how": used, "changed_on_actor": did, "other_matches": follows,
                     "actor": [before_actor, after_actor], "other": [before_other, after_other]})
    if not any_changed:
        return None, {"why": "no object changed state when used", "rows": rows}
    return all_follow, {"rows": rows}


def host_world_objects(a, b):
    """Host uses one of every kind of saved world object; the client's copies should follow."""
    return use_objects(a, b, [("Chest10_2", "interact"), ("SecretWall7_2", "interact"),
                              ("M_DoorSmall_00_BP2", "interact"), ("EggDoor_BP6", "key"),
                              ("IntroDungerLift_BP_2", "activate"), ("Barrel27_2", "break"),
                              ("BreakableBarricade2_3", "barricade")])


def client_world_objects(a, b):
    """Client uses other objects of the same kinds; the host's copies should follow."""
    return use_objects(b, a, [("Chest7_2", "interact"), ("SecretWall11_2", "interact"),
                              ("M_DoorSmall_00_BP", "interact"), ("EggDoor_BP5", "key"),
                              ("IntroDungerLift_BP2_2", "activate"), ("Barrel37_2", "break")])


def passthrough(g, gate_name):
    r = None
    for _ in range(3):
        g.exec(f"clearpath {gate_name} 2000")
        r = json.loads(g.exec(f"passthrough {gate_name} 250 160")["output"])
        if r["forward"]["passed"] == r["backward"]["passed"]:
            return r["forward"]["passed"], r
        time.sleep(2)   # one direction blocked by something passing by: measure again
    return r["forward"]["passed"] and r["backward"]["passed"], r


def gate(puller, other, puller_name):
    """`puller` pulls AquaLever_23; can each player physically walk through AquaGate_01_BP10 afterwards?"""
    g, lever = "AquaGate_01_BP10", "AquaLever_23"
    closed_p, closed_o = passthrough(puller, g)[0], passthrough(other, g)[0]
    if closed_p or closed_o:
        return None, {"why": "the gate did not block before the lever was pulled", "puller": closed_p, "other": closed_o}
    puller.exec(f"tpto {lever} 150")
    time.sleep(1)
    puller.exec(f"interact {lever}")
    # the gate animates open over several seconds after the lever: re-measure until it lets the puller through
    open_p, rp, waited = False, None, 0
    for waited in range(5, 31, 5):
        time.sleep(5)
        puller.exec(f"tpto {lever} 150")   # stand by the lever (where a player would be) between measurements
        open_p, rp = passthrough(puller, g)
        if open_p:
            break
    open_o, ro = passthrough(other, g)
    res = {"puller": puller_name, "seconds_until_open": waited, "puller_can_pass": open_p, "other_can_pass": open_o,
           "lever_puller": obj_state(puller, lever), "lever_other": obj_state(other, lever),
           "puller_sweep": rp, "other_sweep": ro}
    if not open_p:
        return None, {"why": "the gate did not open for the player who pulled the lever", **res}
    return open_o, res


def host_gate(a, b):
    return gate(a, b, "A")


def client_gate(a, b):
    return gate(b, a, "B")


# ------------------------------------------------------------------ checkpoints, story, saves

CKPT_KEYS = ("lastCheckpoint", "savePoint", "savePointsVisited", "unlockedTeleporters")


def rest(g):
    g.exec("tpto Checkpoint17 200")
    time.sleep(1)
    g.exec("interact Checkpoint17")
    time.sleep(8)


def checkpoint_by(rester, other, who):
    """`rester` rests at a checkpoint: checkpoint, visited list and teleporters should match on the other player."""
    before_r = {k: save(rester).get(k) for k in CKPT_KEYS}
    before_o = {k: save(other).get(k) for k in CKPT_KEYS}
    rest(rester)
    sr, so = save(rester), save(other)
    after_r, after_o = {k: sr.get(k) for k in CKPT_KEYS}, {k: so.get(k) for k in CKPT_KEYS}
    res = {"rester": who, "rester_save": [before_r, after_r], "other_save": [before_o, after_o]}
    if after_r == before_r:
        return None, {"why": "resting did not change the resting player's save", **res}
    return all(after_r[k] == after_o[k] for k in CKPT_KEYS), res


def checkpoint(a, b):
    return checkpoint_by(a, b, "A")


def client_checkpoint(a, b):
    return checkpoint_by(b, a, "B")


def story_flags(a, b):
    """After the host's actions: story flags (namedSaveData), quest managers and the whole saved world should match."""
    sa, sb = a.state(sections=["save", "savables"]), b.state(sections=["save", "savables"])
    qa = {s["name"]: s["live"] for s in sa["savables"] if "Quest" in s["class"]}
    qb = {s["name"]: s["live"] for s in sb["savables"] if "Quest" in s["class"]}
    na, nb = sa["save"].get("namedSaveData", {}), sb["save"].get("namedSaveData", {})
    sav_a, sav_b = sa["save"].get("savableActorStates", {}), sb["save"].get("savableActorStates", {})
    differ = [k for k in set(sav_a) | set(sav_b) if sav_a.get(k) != sav_b.get(k)]
    res = {"namedSaveData_diff": changed(na, nb), "quest_manager_diff": changed(qa, qb),
           "saved_world_entries_host": len(sav_a), "saved_world_entries_client": len(sav_b),
           "saved_world_entries_that_differ": len(differ)}
    return not res["namedSaveData_diff"] and not res["quest_manager_diff"] and not differ, res


def rejoin(a, b):
    """Client leaves and joins again after the host changed the world: the new client should see the host's world.
    Runs after host_world_objects (it reuses the chest the host opened)."""
    target = "Chest10_2"
    host_view = obj_state(a, target)
    rig.qdlab.stop_copy("B")
    time.sleep(3)
    b2 = rig.boot("B", "join")
    time.sleep(10)
    client_view = obj_state(b2, target)
    puppets = remote_players(a)
    res = {"object": target, "host": host_view, "rejoined_client": client_view, "host_sees_puppets": len(puppets)}
    if host_view is None or host_view.get("saved") in (None, [None]):
        return None, {"why": "host never changed the chest (run host_world_objects first)", **res}
    return client_view == host_view and len(puppets) == 1, res


def progress_persists(g, name, pickup):
    """`name` picks something up, rests (the game saves at checkpoints), quits and loads its slot again alone:
    its own items and world changes from the co-op session should still be there."""
    if obj_state(g, pickup) is None:
        return None, {"why": f"{pickup} not loaded on {name}"}
    g.exec(f"tpto {pickup} 120")
    time.sleep(1)
    g.exec(f"interact {pickup}")
    time.sleep(5)
    rest(g)
    try:
        g.exec("callon Checkpoint17 Save")
    except Exception:
        pass
    time.sleep(5)
    before, before_obj = save(g), obj_state(g, pickup)
    rig.qdlab.stop_copy(name)
    time.sleep(3)
    g2 = rig.boot(name, None, load=True)
    time.sleep(5)
    after, after_obj = save(g2), obj_state(g2, pickup)
    keep = ("inventory", "lastCheckpoint", "savePointsVisited")
    res = {"player": name, "pickup": pickup, "pickup_state": [before_obj, after_obj],
           "before_reload": {k: before.get(k) for k in keep}, "after_reload": {k: after.get(k) for k in keep}}
    if before_obj in (None,) or before_obj.get("saved") in (None, [None]):
        return None, {"why": "the pickup was not taken before the reload", **res}
    return all(before.get(k) == after.get(k) for k in keep) and before_obj == after_obj, res


def client_progress_persists(a, b):
    return progress_persists(b, "B", "Herb_Item_BP3_5")


# ------------------------------------------------------------------ Lane 2: combat

def enemies(g):
    return [e for e in g.state(sections=["enemies"])["enemies"] if not e["dead"] and not e["hidden"]]


def kd_log_all(name):
    p = os.path.join(rig.qdlab.mod_saved(name, rig.PROFILES[name]), "KnightsDomain.log")
    if not os.path.isfile(p):
        return []
    with open(p, encoding="utf-8", errors="replace") as f:
        return f.readlines()


def elist_montages(name, cls, from_line):
    """From the mod's `kd elist` lines logged since `from_line`: how many samples showed each montage on `cls`
    enemies, and which owner slots they had."""
    montages, owners = {}, {}
    for line in kd_log_all(name)[from_line:]:
        if "[edbg] E " not in line or cls not in line:
            continue
        fields = dict(f.split("=", 1) for f in line.split() if "=" in f)
        m = fields.get("montage", "?")
        montages[m] = montages.get(m, 0) + 1
        o = fields.get("owner", "?")
        owners[o] = owners.get(o, 0) + 1
    return montages, owners


def watch_summary(w, hit_cm=250):
    """Condense a bridge `watch` record: watched actors by class, and hits split by whether a watched actor was close."""
    by_class = {}
    for r in w["actors"]:
        c = by_class.setdefault(r["class"], {"count": 0, "closest_cm": None})
        c["count"] += 1
        if c["closest_cm"] is None or r["minDist"] < c["closest_cm"]:
            c["closest_cm"] = r["minDist"]
    hits = w["health"]["hits"]
    near = [h for h in hits if h["nearestDist"] is not None and h["nearestDist"] <= hit_cm]
    return {"watched": by_class, "hits": len(hits), "hits_with_watched_actor_close": len(near),
            "damage": round(w["health"]["damage"]), "hit_log": hits[:12]}


def place_in_line(a, b, enemy_class, far_cm, near_cm, approach):
    """A stands `far_cm` from the nearest `enemy_class`, B `near_cm` from it on the same side (between it and A).
    Returns (enemy, geometry) or (None, why)."""
    a.exec(f"tp {approach[0]} {approach[1]} {approach[2]} 0")
    time.sleep(2)
    a.exec(f"kd egoto {enemy_class} {far_cm}")
    time.sleep(2)
    pa = local_pos(a)
    b.exec(f"tp {pa[0]} {pa[1]} {pa[2] + 50} 0")
    time.sleep(2)
    b.exec(f"kd egoto {enemy_class} {near_cm}")
    time.sleep(2)
    pb = local_pos(b)
    pa = local_pos(a)
    targets = sorted((e for e in enemies(a) if enemy_class in e["class"]), key=lambda e: dist(e["pos"], pa))
    if not targets:
        return None, {"why": f"no live {enemy_class} within 60 m of A"}
    en = targets[0]
    # B's position relative to the enemy -> A line (horizontal): fraction along it and sideways offset
    ex, ey = en["pos"][:2]
    vx, vy = pa[0] - ex, pa[1] - ey
    wx, wy = pb[0] - ex, pb[1] - ey
    L = math.hypot(vx, vy) or 1.0
    along = (wx * vx + wy * vy) / (L * L)
    side = abs(wx * vy - wy * vx) / L
    geo = {"enemy": en["name"], "enemy_class": en["class"], "enemy_pos": en["pos"], "a_pos": pa, "b_pos": pb,
           "a_to_enemy_cm": round(dist(pa, en["pos"])), "b_to_enemy_cm": round(dist(pb, en["pos"])),
           "b_along_line": round(along, 2), "b_off_line_cm": round(side)}
    return en, geo


def ranged_attacks(a, b, seconds=30):
    """A ranged enemy targets A (A keeps hitting it, so it stays on A); B stands between them in god mode.
    Shared projectiles: B sees the enemy's projectiles and gets hit by them. Both copies record every projectile-like
    actor within 60 m and every health dent (Knights' Domain issue 2: projectiles only exist for their target)."""
    archer = "Mossy_Archer"
    for g in (a, b):
        g.exec("kd god 1")
    time.sleep(1.5)
    en, geo = place_in_line(a, b, archer, 1000, 500, (8900, -4300, 4300))   # east side: level ground, clear shot
    if en is None:
        return None, geo
    # keep the fight to ranged attacks: kill every other enemy within 30 m of the archer (the host owns them)
    killed = []
    for e in enemies(a):
        if archer not in e["class"] and dist(e["pos"], en["pos"]) < 3000:
            a.exec(f"kd ehit {e['name']} 4000")
            killed.append(e["class"])
    time.sleep(2)
    archers = [e["name"] for e in enemies(a) if archer in e["class"] and dist(e["pos"], en["pos"]) < 3000]
    log_from = {n: len(kd_log_all(n)) for n in ("A", "B")}
    for g in (a, b):
        g.exec("watch start 6000")
    end = time.time() + seconds
    next_hit = 0.0
    while time.time() < end:
        if time.time() >= next_hit:
            for n in archers:   # last hitter within 6 s owns and targets: keep every archer on A
                a.exec(f"kd ehit {n} 1")
            next_hit = time.time() + 3
        for g in (a, b):   # the mod logs each archer's owner and playing montage: what each copy shows
            g.exec(f"kd elist {archer}")
        time.sleep(0.5)
    for g in (a, b):
        g.exec("watch stop")
    wa = a.state(sections=["watch"])["watch"]
    wb = b.state(sections=["watch"])["watch"]
    sa, sb = watch_summary(wa), watch_summary(wb)
    for g in (a, b):
        g.exec("kd god 0")
    time.sleep(1)
    sa["archer_montages"], sa["archer_owners"] = elist_montages("A", archer, log_from["A"])
    sb["archer_montages"], sb["archer_owners"] = elist_montages("B", archer, log_from["B"])
    res = {"geometry": geo, "other_enemies_killed": killed, "archers_on_a": len(archers), "seconds": seconds,
           "a": sa, "b": sb}
    if sa["hits_with_watched_actor_close"] == 0:
        return None, {"why": "A was never hit by a projectile: the enemy did not shoot at A", **res}
    b_sees = sum(c["count"] for c in sb["watched"].values()) > 0
    return b_sees and sb["hits_with_watched_actor_close"] > 0, res


AREA_WORDS = ("Puddle", "Fireball", "Creep", "Explosion", "Shockwave", "Splash", "Vortex", "Whirl", "Wave", "AOE")


def hits_by_class(w, near_cm):
    """Each health dent credited to the class of the closest watched actor (within `near_cm`), else 'none close'."""
    cls_of = {r["name"]: r["class"] for r in w["actors"] if "FX" not in r["class"]}
    out = {}
    for h in w["health"]["hits"]:
        k = cls_of.get(h["nearest"]) if h["nearest"] and h["nearestDist"] <= near_cm else None
        k = k or "none close"
        c = out.setdefault(k, {"hits": 0, "damage": 0.0})
        c["hits"] += 1
        c["damage"] += h["amount"]
    return {k: {"hits": v["hits"], "damage": round(v["damage"])} for k, v in out.items()}


def area_attacks(a, b, seconds=150, near_cm=500):
    """The Ruins dragon boss fights A (A keeps hitting it, so it stays on A); B in god mode is kept 1.5 m from A.
    Shared area attacks: B sees the fire puddles and fireball blasts around A and takes damage from them. Hits are
    credited to the closest watched actor's class, so flame-breath projectiles are counted apart from the area ones."""
    boss = "Dragon_Green"
    for g in (a, b):
        g.exec("kd god 1")
    time.sleep(1.5)
    a.exec("tp -28574 754 -12500 0")   # the boss arena (the dragon sleeps here in trap mode until woken)
    time.sleep(3)
    a.exec(f"kd egoto {boss} 1200")
    time.sleep(2)
    a.exec(f"kd ewake {boss}")
    time.sleep(4)
    dragons = [e for e in enemies(a) if boss in e["class"]]
    if not dragons:
        return None, {"why": f"no {boss} near A after waking it"}
    target = dragons[0]["name"]
    hp_before = dragons[0].get("hp")

    def side(w, name, log_from):
        actors = {}
        for r in w["actors"]:
            if "FX" in r["class"]:   # level effects (waterfall / swamp splashes) match the words but are not attacks
                continue
            kind = "area" if any(x in r["class"] for x in AREA_WORDS) else "projectile"
            c = actors.setdefault(r["class"], {"kind": kind, "count": 0, "closest_cm": None})
            c["count"] += 1
            c["closest_cm"] = r["minDist"] if c["closest_cm"] is None else min(c["closest_cm"], r["minDist"])
        montages, owners = elist_montages(name, boss, log_from[name])
        return {"actors": actors, "hits": len(w["health"]["hits"]), "damage": round(w["health"]["damage"]),
                "hits_by_closest_class": hits_by_class(w, near_cm), "boss_montages": montages, "boss_owners": owners}

    area_hits = lambda s: sum(v["hits"] for k, v in s["hits_by_closest_class"].items()
                              if any(x in k for x in AREA_WORDS))
    # Puddle hits are rare (one in 150 s on A once, while the flame breath landed 63 times): a round where the boss
    # barely used its puddles proves nothing either way, so up to three rounds are fought. B passes with one puddle
    # hit; B taking none while A took several in the same round is a real failure.
    rounds = []
    for rnd in range(3):
        log_from = {n: len(kd_log_all(n)) for n in ("A", "B")}
        for g in (a, b):
            g.exec("kd god 1")
            g.exec("watch start 6000")
        gaps = []
        end = time.time() + seconds
        next_hit = 0.0
        while time.time() < end:
            if time.time() >= next_hit:
                a.exec(f"kd ehit {target} 1")   # last hitter within 6 s owns and targets
                next_hit = time.time() + 3
            pa = local_pos(a)
            # B stands where A stands: the dragon aims at A's feet, so its fire lands where B is too
            b.exec(f"tp {pa[0] + 20} {pa[1]} {pa[2] + 30} 0")
            time.sleep(0.6)
            gaps.append(round(dist(local_pos(b)[:2], local_pos(a)[:2])))
            b.exec(f"kd elist {boss}")
            a.exec(f"kd elist {boss}")
            time.sleep(1.4)
        for g in (a, b):
            g.exec("watch stop")
        wa = a.state(sections=["watch"])["watch"]
        wb = b.state(sections=["watch"])["watch"]
        sa, sb = side(wa, "A", log_from), side(wb, "B", log_from)
        rounds.append({"round": rnd + 1, "a_area_hits": area_hits(sa), "b_area_hits": area_hits(sb),
                       "b_to_a_cm": {"median": sorted(gaps)[len(gaps) // 2] if gaps else None,
                                     "max": max(gaps) if gaps else None}, "a": sa, "b": sb})
        if area_hits(sb) > 0 or area_hits(sa) >= 3:
            break
    for g in (a, b):
        g.exec("kd god 0")
    time.sleep(1)

    last = rounds[-1]
    after = [e for e in enemies(a) if e["name"] == target]
    res = {"boss": target, "boss_hp": [hp_before, after[0].get("hp") if after else None], "seconds": seconds,
           "rounds": [{k: v for k, v in r.items() if k not in ("a", "b")} for r in rounds],
           "b_to_a_cm": last["b_to_a_cm"], "a": last["a"], "b": last["b"]}
    if not any(r["a_area_hits"] or r["b_area_hits"] for r in rounds):
        return None, {"why": "no player was hit with an area-attack actor close: the boss did not use one", **res}
    b_sees_area = any(v["kind"] == "area" for v in last["b"]["actors"].values())
    return b_sees_area and any(r["b_area_hits"] > 0 for r in rounds), res

def match_enemy(g, en, max_cm=600):
    """The same enemy on another copy (object names differ per copy): same class, nearest position."""
    cands = [e for e in g.state(sections=["enemies"])["enemies"] if e["class"] == en["class"]]
    cands.sort(key=lambda e: dist(e["pos"], en["pos"]))
    return cands[0] if cands and dist(cands[0]["pos"], en["pos"]) <= max_cm else None


def rewards(g):
    s = g.state(sections=["save"])["save"]
    return {"xp": s.get("xp"), "kills": dict(s.get("killedEnemyCounts") or {}),
            "itemDrops": list(s.get("itemDrops") or []), "inventory": dict(s.get("inventory") or {})}


def visible_pickups(g):
    return {p["name"]: p for p in g.state(sections=["pickups"])["pickups"] if not p["hidden"]}


def delta(x, y):
    return {k: (x.get(k), y.get(k)) for k in set(x) | set(y) if x.get(k) != y.get(k)}


def drops_and_xp(a, b, spot=(3000, -2000, 500), kills=20):
    """A kills a few small enemies (`kd ehit`, B stands 1.5 m behind A). Shared rewards: each enemy dies on both
    copies (both kill counters go up), both get the same XP, and the loot is rolled once: what drops can be picked up
    by one player, after which it is gone for the other. (The 1.0.3 source: every non-owner "kills its own copy and
    rolls its own rewards".)"""
    for g in (a, b):
        g.exec("kd god 1")
    a.exec(f"tp {spot[0]} {spot[1]} {spot[2]} 0")
    time.sleep(3)
    pa = local_pos(a)
    targets = [e for e in enemies(a) if e.get("hp") and e["hp"] < 200 and dist(e["pos"], pa) < 6000]
    targets.sort(key=lambda e: dist(e["pos"], pa))
    targets = targets[:kills]
    if not targets:
        return None, {"why": "no small enemy within 60 m of A"}

    def b_behind_a(en_pos):
        # B 1.5 m behind A (away from the enemy), so A stays the nearest player and keeps ownership
        p = local_pos(a)
        dx, dy = p[0] - en_pos[0], p[1] - en_pos[1]
        L = math.hypot(dx, dy) or 1.0
        b.exec(f"tp {p[0] + dx / L * 150} {p[1] + dy / L * 150} {p[2] + 30} 0")
        time.sleep(1.5)

    b_behind_a(targets[0]["pos"])
    time.sleep(6)   # B just arrived: let the area's pickups stream in before the baseline
    before = {"A": rewards(a), "B": rewards(b)}
    picks_before = {"A": set(visible_pickups(a)), "B": set(visible_pickups(b))}
    rows = []
    for en in targets:
        a.exec(f"kd egoto {en['name']} 300")
        time.sleep(1.5)
        b_behind_a(en["pos"])
        k0 = {"A": rewards(a)["kills"], "B": rewards(b)["kills"]}
        on_b = match_enemy(b, en, 400)   # B's copy of this enemy, before the hit
        # 4000: kills any small enemy, and stays under the mod's 5000 cap for a hit relayed to another owner
        a.exec(f"kd ehit {en['name']} 4000")
        time.sleep(5)
        after_a = next((e for e in a.state(sections=["enemies"])["enemies"] if e["name"] == en["name"]), None)
        k1 = {"A": rewards(a)["kills"], "B": rewards(b)["kills"]}
        b_after = next((e for e in b.state(sections=["enemies"])["enemies"] if on_b and e["name"] == on_b["name"]),
                       None)
        # the enemy died on a copy when that copy's own kill counter went up (object names differ per copy)
        rows.append({"enemy": en["class"], "dead_on_a": after_a is None or after_a["dead"],
                     "kill_counted_on_a": sum(k1["A"].values()) > sum(k0["A"].values()),
                     "kill_counted_on_b": sum(k1["B"].values()) > sum(k0["B"].values()),
                     "b_copy": None if on_b is None else {
                         "off_cm": round(dist(on_b["pos"], en["pos"])),
                         "after": "gone" if b_after is None else {"dead": b_after["dead"], "hp": b_after.get("hp")}}})
    time.sleep(3)
    after = {"A": rewards(a), "B": rewards(b)}

    pk_a, pk_b = visible_pickups(a), visible_pickups(b)
    kill_at = [en["pos"] for en in targets]
    # drops land at the enemy's feet and are spawned at run time ("<Class>_<number>", and the bridge's drop flag);
    # placed pickups that stream in near a kill are not drops
    near_kill = lambda p: any(dist(p["pos"], k) < 800 for k in kill_at) and p["name"].startswith(p["class"] + "_")
    new = {"A": {n: p for n, p in pk_a.items() if n not in picks_before["A"] and near_kill(p) and p.get("drop", True)},
           "B": {n: p for n, p in pk_b.items() if n not in picks_before["B"] and near_kill(p) and p.get("drop", True)}}
    res = {"spot": spot, "kills": rows,
           "xp": {n: [before[n]["xp"], after[n]["xp"]] for n in ("A", "B")},
           "kill_counts": {n: after[n]["kills"] for n in ("A", "B")},
           "inventory_change_from_kills": {n: delta(before[n]["inventory"], after[n]["inventory"]) for n in ("A", "B")},
           "itemDrops_added": {n: len(after[n]["itemDrops"]) - len(before[n]["itemDrops"]) for n in ("A", "B")},
           "pickups_left_on_ground": {n: sorted(p["class"] for p in new[n].values()) for n in ("A", "B")}}

    # who can pick up a drop left on the ground: each copy picks up one of its own, the other copy is checked
    pick = {}
    for actor, other, an, on in ((b, a, "B", "A"), (a, b, "A", "B")):
        # what is still on the ground now: with shared loot the other player's pickup may already have taken one
        still = visible_pickups(actor)
        mine = sorted((still[n] for n in new[an] if n in still), key=lambda p: p["dist"])
        if not mine:
            pick[an] = "no drop left on the ground on this copy"
            continue
        p = mine[0]
        twins = sorted((q for q in visible_pickups(other).values() if q["class"] == p["class"]),
                       key=lambda q: dist(q["pos"], p["pos"]))
        twin = twins[0] if twins and dist(twins[0]["pos"], p["pos"]) < 800 else None
        inv0, inv0_other = rewards(actor)["inventory"], rewards(other)["inventory"]
        actor.exec(f"tpto {p['name']} 100")
        time.sleep(1)
        try:
            actor.exec(f"interact {p['name']}")
        except Exception as e:
            pick[an] = {"pickup": p["class"], "error": repr(e)}
            continue
        time.sleep(4)
        pick[an] = {"pickup": p["class"], f"{on}_has_one_there": twin is not None,
                    f"still_on_{on}": None if twin is None else twin["name"] in visible_pickups(other),
                    "inventory_here": delta(inv0, rewards(actor)["inventory"]),
                    f"inventory_{on}": delta(inv0_other, rewards(other)["inventory"])}
    res["pickup"] = pick
    for g in (a, b):
        g.exec("kd god 0")
    if not any(r["dead_on_a"] for r in rows):
        return None, {"why": "no enemy died on A", **res}
    xp_ok = after["A"]["xp"] == after["B"]["xp"] and after["A"]["xp"] != before["A"]["xp"]
    kills_ok = after["A"]["kills"] == after["B"]["kills"]
    dead_ok = all(r["kill_counted_on_b"] for r in rows if r["dead_on_a"])
    # one set of drops: both copies show the same items on the ground, and picking one up removes it on the other
    same_drops = res["pickups_left_on_ground"]["A"] == res["pickups_left_on_ground"]["B"] and         res["itemDrops_added"]["A"] == res["itemDrops_added"]["B"]
    tried = [v for v in pick.values() if isinstance(v, dict)]
    picked_once = all(v.get("still_on_" + o) is not True for v in tried for o in ("A", "B"))
    drops_once = same_drops and picked_once
    res["checks"] = {"xp_equal_and_gained": xp_ok, "kill_counts_equal": kills_ok, "kill_counted_on_both": dead_ok,
                     "same_drops_on_both": same_drops, "pickup_removes_it_for_the_other": picked_once}
    if not xp_ok or not kills_ok or not dead_ok:
        return False, res
    if not new["A"] and not new["B"]:
        return None, {"why": "nothing dropped on either copy this time (XP and kills agreed)", **res}
    return drops_once, res


def enemy_by_name(g, name):
    return next((e for e in g.state(sections=["enemies"])["enemies"] if e["name"] == name), None)


def kd_log_since(name, from_line, word):
    return [l.rstrip() for l in kd_log_all(name)[from_line:] if word in l]


def enemy_hp_agree(a, b, enemy_class="Mossy_Fighter", spot=(3000, -2000, 500), dmg=3, settle=1.2):
    """A and B take turns hitting the same enemy (`kd ehit`, 3 damage) until it dies. Shared enemy health: after
    every hit both copies show the same HP, every hit lowers it, and the enemy dies on both at the same hit. The hits
    on the side that does not own the enemy are relayed to the owner, so this covers both directions (and the mod's
    last-hitter ownership moves the enemy between the players during the fight)."""
    for g in (a, b):
        g.exec("kd god 1")
    a.exec(f"tp {spot[0]} {spot[1]} {spot[2]} 0")
    time.sleep(3)
    pa = local_pos(a)
    cands = sorted((e for e in enemies(a) if enemy_class in e["class"] and e.get("hp")),
                   key=lambda e: dist(e["pos"], pa))
    if not cands:
        return None, {"why": f"no live {enemy_class} within 60 m of A"}
    en = cands[0]
    a.exec(f"kd egoto {en['name']} 250")
    time.sleep(1.5)
    pa = local_pos(a)
    b.exec(f"tp {pa[0]} {pa[1]} {pa[2] + 50} 0")
    time.sleep(4)
    en = enemy_by_name(a, en["name"])
    twin = match_enemy(b, en, 400)
    if twin is None:
        return None, {"why": "B has no copy of the enemy within 4 m of where A sees it", "enemy": en}
    log_from = {n: len(kd_log_all(n)) for n in ("A", "B")}
    rows, worst = [], 0.0
    hp_start = [round(en["hp"], 1), round(twin["hp"], 1)]
    for i in range(60):
        hitter, name, g = (("A", en["name"], a) if i % 2 == 0 else ("B", twin["name"], b))
        g.exec(f"kd ehit {name} {dmg}")
        time.sleep(settle)
        ea, eb = enemy_by_name(a, en["name"]), enemy_by_name(b, twin["name"])
        ha = None if ea is None else (0.0 if ea["dead"] else round(ea["hp"], 1))
        hb = None if eb is None else (0.0 if eb["dead"] else round(eb["hp"], 1))
        dead = [ea is None or ea["dead"], eb is None or eb["dead"]]
        if ha is not None and hb is not None:
            worst = max(worst, abs(ha - hb))
        rows.append({"hit": i + 1, "by": hitter, "hp": [ha, hb], "dead": dead})
        if all(dead):
            break
        if any(dead):   # one side says dead: give the other a few seconds to follow
            time.sleep(4)
            eb2, ea2 = enemy_by_name(b, twin["name"]), enemy_by_name(a, en["name"])
            rows.append({"after_4s": True, "dead": [ea2 is None or ea2["dead"], eb2 is None or eb2["dead"]]})
            break
    for g in (a, b):
        g.exec("kd god 0")
    owners = {n: [l.split("] ", 2)[-1] for l in kd_log_since(n, log_from[n], "[edbg] hit")][:4] for n in ("A", "B")}
    rejected = {n: len(kd_log_since(n, log_from[n], "damage request rejected")) for n in ("A", "B")}
    final = rows[-1]["dead"]
    hit_rows = [r for r in rows if "hit" in r]
    prev, lost = hp_start[0], []
    for r in hit_rows:   # a hit that left the enemy alive at the same HP on the owner side did nothing
        h = r["hp"][0]
        if h is not None and not r["dead"][0] and prev is not None and h >= prev:
            lost.append({"hit": r["hit"], "by": r["by"]})
        prev = h
    first_dead = {s: next((r["hit"] for r in rows if "hit" in r and r["dead"][k]), None)
                  for k, s in enumerate(("A", "B"))}
    res = {"enemy": en["class"], "hp_start": hp_start, "damage_per_hit": dmg, "hits": len([r for r in rows if "hit" in r]),
           "worst_hp_gap": round(worst, 1), "hits_that_did_nothing": lost, "died_at_hit": first_dead, "dead_at_end": final,
           "first_hit_log": owners, "rejected_requests": rejected, "trace": rows}
    if not any(final):
        return None, {"why": "the enemy did not die within 60 hits", **res}
    res["checks"] = {"hp_agrees": worst <= 1.0, "dies_on_both_at_same_hit": all(final) and
                     first_dead["A"] == first_dead["B"], "every_hit_counts": not lost}
    return all(res["checks"].values()), res


def safe(fn, default=None):
    try:
        return fn()
    except Exception as e:   # the copy is loading a map, or the bridge is busy
        return default if default is not None else {"error": type(e).__name__}


def puppet_follows(mover, watcher, dx=600):
    """`mover` steps `dx` cm; how far is `watcher`'s puppet of it from where it ended (horizontal cm)."""
    p = local_pos(mover)
    mover.exec(f"tp {p[0] + dx} {p[1]} {p[2] + 50} 0")
    time.sleep(3)
    t = local_pos(mover)
    pups = [q for q in remote_players(watcher) if not q["hidden"]]
    return round(min(dist(q["pos"][:2], t[:2]) for q in pups)) if pups else None


DIE_CONTINUE = "BndEvt__DieUI_ContinueButton_K2Node_ComponentBoundEvent_4_CommonButtonBaseClicked__DelegateSignature"


def death(a, b, wait_s=120, spot=(3000, -2000, 500)):
    """A dies (the player Blueprint's own Kill). Shared death: B sees A's knight die, the session survives A's
    respawn, and afterwards movement syncs both ways again. Also records where A comes back. Death opens the game's
    Game Over screen (`DieUI`); the scenario presses its Continue button like a player would."""
    keys = ("lastCheckpoint", "savePoint")
    b.exec("kd god 1")   # B must not die to whatever is around
    a.exec("kd god 0")
    a.exec("kd party down 0")   # the game's own death (with B next to A, A would go down instead: see party_scenarios)
    a.exec(f"tp {spot[0]} {spot[1]} {spot[2]} 0")   # away from where a new game starts
    time.sleep(3)
    # A kills the small enemies near the spot first: does the host's reload bring them back, and on whose copy?
    near = lambda g: sorted(e["class"] for e in enemies(g) if e.get("hp") and e["hp"] < 200 and
                            dist(e["pos"], spot) < 2500)
    live = {"before_kills": {"A": near(a), "B": None}}
    pa = local_pos(a)
    b.exec(f"tp {pa[0] + 300} {pa[1]} {pa[2] + 50} 0")
    time.sleep(5)
    live["before_kills"]["B"] = near(b)
    for e in [e for e in enemies(a) if e.get("hp") and e["hp"] < 200 and dist(e["pos"], spot) < 2500]:
        a.exec(f"kd ehit {e['name']} 4000")
        time.sleep(0.5)
    time.sleep(4)
    live["after_kills"] = {"A": near(a), "B": near(b)}
    a.exec(f"tp {spot[0]} {spot[1]} {spot[2]} 0")
    time.sleep(2)
    sa = a.state(sections=["save", "player"])
    save_before = {k: sa["save"].get(k) for k in keys}
    died_at = sa["player"]["pos"]
    b.exec(f"tp {died_at[0] + 300} {died_at[1]} {died_at[2] + 50} 0")   # B watches from 3 m away
    time.sleep(3)
    log_from = {n: len(kd_log_all(n)) for n in ("A", "B")}
    a.exec("die")
    t0 = time.time()
    timeline, b_anims, a_dead_seen, back_since, continued = [], {}, False, None, None
    while time.time() - t0 < wait_s:
        t = round(time.time() - t0, 1)
        ia = safe(lambda: a.eval("inworld"), False)
        pl = safe(lambda: a.state(sections=["player"])["player"], {}) if ia else {}
        pups = safe(lambda: remote_players(b), [])
        anims = sorted({q.get("anim", "") for q in pups if not q["hidden"]})
        for an in anims:
            b_anims.setdefault(an, t)
        row = {"t": t, "a_inworld": ia, "a_dead": pl.get("dead"), "a_world": safe(lambda: a.eval("world"), "?"),
               "b_puppets": len([q for q in pups if not q["hidden"]]), "b_puppet_anim": anims,
               "b_inworld": safe(lambda: b.eval("inworld"), False)}
        if not timeline or {k: v for k, v in row.items() if k != "t"} != {k: v for k, v in timeline[-1].items()
                                                                          if k != "t"}:
            timeline.append(row)   # only log changes
        a_dead_seen |= bool(pl.get("dead")) or not ia
        if a_dead_seen and continued is None and "DieUI_C" in str(safe(lambda: a.exec("widgets DieUI_C"), "")):
            if safe(lambda: a.exec(f"uicall DieUI_C {DIE_CONTINUE}"), None) is not None:
                continued = t   # the Game Over screen is up: press Continue
        if a_dead_seen and ia and pl.get("present") and not pl.get("dead"):
            back_since = back_since or time.time()
            if time.time() - back_since > 8:
                break
        else:
            back_since = None
        time.sleep(1)
    back = back_since is not None
    res = {"died_at": died_at, "save_before": save_before, "pressed_continue_at_s": continued,
           "b_puppet_anims_seen": b_anims, "timeline": timeline}
    if not a_dead_seen:
        return None, {"why": "A did not die", **res}
    if not back:
        return False, {"why": f"A was not back in the world after {wait_s} s", **res}
    sa = a.state(sections=["save", "player", "checkpoints"])
    came_back = sa["player"]["pos"]
    cps = sorted(sa["checkpoints"], key=lambda c: dist(c["pos"], came_back)) if sa["checkpoints"] else []
    res["came_back_at"] = came_back
    res["came_back_from_death_spot_cm"] = round(dist(came_back, died_at))
    res["nearest_checkpoint"] = {"name": cps[0]["name"], "cm": round(dist(cps[0]["pos"], came_back))} if cps else None
    res["save_after"] = {k: sa["save"].get(k) for k in keys}
    res["respawn_s"] = round(back_since - t0, 1)
    time.sleep(3)
    # puppets: each copy should show the other player again; if not, wait up to 60 s and note when they came back
    vis = lambda g: len([q for q in safe(lambda: remote_players(g), []) if not q["hidden"]])
    t1, rec = time.time(), {"right_after": {"A_sees_B": vis(a), "B_sees_A": vis(b)}}
    while (vis(a) == 0 or vis(b) == 0) and time.time() - t1 < 60:
        time.sleep(2)
    rec["both_visible_after_s"] = round(time.time() - t1, 1) if vis(a) and vis(b) else None
    res["puppets_after_respawn"] = rec
    b.exec(f"tp {spot[0] + 300} {spot[1]} {spot[2]} 0")   # B is still at the death spot; A goes back there too
    a.exec(f"tp {spot[0]} {spot[1]} {spot[2]} 0")
    time.sleep(5)
    live["after_a_respawned"] = {"A": near(a), "B": near(b)}
    res["small_enemies_within_25m"] = live
    res["after_respawn"] = {"b_puppet_of_a_error_cm": safe(lambda: puppet_follows(a, b)),
                            "a_puppet_of_b_error_cm": safe(lambda: puppet_follows(b, a))}
    words = ("session", "peer", "disconnect", "timeout", "left", "joined", "world changed")
    res["mod_log"] = {n: [l.split("] ", 2)[-1][:140] for l in kd_log_all(n)[log_from[n]:]
                          if any(w in l.lower() for w in words) and "[smoke]" not in l][:12] for n in ("A", "B")}
    # errors the mod caught (a ticker that hits an exception is removed for the rest of the session)
    res["mod_errors"] = {n: [l.split("] ", 1)[-1].strip()[:160] for l in kd_log_all(n)[log_from[n]:] if "[ERR]" in l][:6]
                         for n in ("A", "B")}
    b.exec("kd god 0")
    death_anim = any("Death" in an for an in b_anims)
    ar = res["after_respawn"]
    synced = all(isinstance(v, (int, float)) and v <= 100 for v in ar.values())
    same_world = live["after_a_respawned"]["A"] == live["after_a_respawned"]["B"]
    no_errors = not any(res["mod_errors"].values())
    res["checks"] = {"b_saw_a_die": death_anim, "session_survived_and_syncs": synced,
                     "enemies_agree_after_reload": same_world, "no_mod_errors": no_errors}
    return death_anim and synced and same_world and no_errors, res


def host_leaves(a, b, spot=(3000, -2000, 500), after_s=60):
    """Mid-fight, the host's game is killed (like a crash or Alt+F4). The client keeps playing: it stays in its world,
    the host's knight goes away, the enemies around it keep fighting (they hit the client), and the client's own hits
    still hurt them. Records the client's mod log from the moment the host went away."""
    b.exec("kd god 1")
    a.exec("kd god 1")
    a.exec(f"tp {spot[0]} {spot[1]} {spot[2]} 0")
    time.sleep(3)
    pa = local_pos(a)
    b.exec(f"tp {pa[0] + 200} {pa[1]} {pa[2] + 50} 0")
    time.sleep(4)
    near = lambda g: [e for e in enemies(g) if dist(e["pos"], local_pos(g)) < 2500]
    # start the fight: A hits everything close (so the host owns it) and wakes it up
    for e in near(a):
        a.exec(f"kd ehit {e['name']} 1")
        time.sleep(0.3)
    time.sleep(4)
    owners_before = {}
    lf = len(kd_log_all("B"))
    b.exec("kd elist")
    time.sleep(1)
    for line in kd_log_all("B")[lf:]:
        if "[edbg] E " in line:
            o = dict(f.split("=", 1) for f in line.split() if "=" in f).get("owner", "?")
            owners_before[o] = owners_before.get(o, 0) + 1
    before = {"b_enemies_within_25m": len(near(b)), "b_sees_a": len([q for q in remote_players(b) if not q["hidden"]]),
              "b_enemy_owners": owners_before}
    log_from = len(kd_log_all("B"))
    b.exec("watch start 3000")
    t0 = time.time()
    rig.qdlab.stop_copy("A")   # the host is gone without a goodbye
    gone_s = round(time.time() - t0, 1)
    track, puppet_gone_at = [], None
    pos0 = {e["name"]: e["pos"] for e in near(b)}
    while time.time() - t0 < after_s:
        t = round(time.time() - t0, 1)
        vis = len([q for q in safe(lambda: remote_players(b), []) if not q["hidden"]])
        if vis == 0 and puppet_gone_at is None:
            puppet_gone_at = t
        track.append(t)
        time.sleep(2)
    b.exec("watch stop")
    w = b.state(sections=["watch"])["watch"]
    hits_on_b = [h for h in w["health"]["hits"] if h["ms"] > 3000]   # hits taken after the host was gone
    now = {e["name"]: e for e in near(b)}
    moved = sum(1 for n, p in pos0.items() if n in now and dist(now[n]["pos"], p) > 100)
    # B's own hit on an enemy after the host left
    target = sorted(now.values(), key=lambda e: dist(e["pos"], local_pos(b)))
    hit = None
    if target:
        en = target[0]
        lf = len(kd_log_all("B"))
        b.exec(f"kd ehit {en['name']} 4000")
        time.sleep(4)
        after = next((e for e in b.state(sections=["enemies"])["enemies"] if e["name"] == en["name"]), None)
        hit = {"enemy": en["class"], "hp_before": en.get("hp"), "dead_after": after is None or after["dead"],
               "log": [l.split("] ", 2)[-1].strip()[:120] for l in kd_log_all("B")[lf:] if "[edbg]" in l][:2]}
    words = ("session", "peer", "disconnect", "timeout", "timed out", "host", "left", "owner", "lost")
    blog = [l.split("] ", 1)[-1].strip()[:150] for l in kd_log_all("B")[log_from:]
            if any(x in l.lower() for x in words) and "[smoke]" not in l and "[dev]" not in l]
    owner_lines = [l for l in blog if "owner " in l]
    noticed = None
    for l in kd_log_all("B")[log_from:]:   # when did B notice: the first disconnect line, against the kill time
        if "disconnected" in l or "host lost" in l:
            try:
                hh, mm, ss = l[1:13].split(":")
                lt = time.localtime(t0)
                noticed = round((int(hh) - lt.tm_hour) * 3600 + (int(mm) - lt.tm_min) * 60 + float(ss) - lt.tm_sec
                                - (t0 % 1), 1)
            except ValueError:
                pass
            break
    res = {"before": before, "host_killed_in_s": gone_s, "watched_s": after_s,
           "b_inworld": safe(lambda: b.eval("inworld"), False), "b_world": safe(lambda: b.eval("world"), "?"),
           "b_noticed_host_gone_after_s": noticed, "b_puppet_of_a_gone_after_s": puppet_gone_at,
           "b_enemies_moved_more_than_1m": f"{moved} of {len(pos0)}",
           "b_hits_taken_after_host_left": len(hits_on_b),
           "b_hit_after_host_left": hit,
           "b_log_owner_changes": len(owner_lines), "b_log": [l for l in blog if "owner " not in l][:14] + owner_lines[:4]}
    b.exec("kd god 0")
    checks = {"b_still_in_world": bool(res["b_inworld"]), "host_knight_removed": puppet_gone_at is not None,
              "enemies_keep_fighting": bool(hits_on_b) or moved > 0,
              "b_hits_still_hurt": bool(hit and hit["dead_after"])}
    res["checks"] = checks
    return all(checks.values()), res


LAG_RUNS = ("movement", "ranged_attacks", "area_attacks", "drops_and_xp", "enemy_hp_agree", "death", "host_leaves")
# what each scenario gives on the plain loopback link (two fresh runs each, see README): lag is judged against it
LAN_VERDICT = {"movement": "PASS", "ranged_attacks": "FAIL", "area_attacks": "FAIL", "drops_and_xp": "FAIL",
               "enemy_hp_agree": "PASS or FAIL", "death": "PASS", "host_leaves": "PASS"}


def lag_summary(name, info):
    """The few numbers of each scenario worth comparing with and without lag."""
    g = lambda *ks: {k: info.get(k) for k in ks if k in info}
    if "why" in info or "error" in info:
        return g("why", "error")
    if name == "movement":
        return g("puppet_error_cm")
    if name == "ranged_attacks":
        return {s: {"projectiles_seen": sum(c["count"] for c in info[s]["watched"].values()),
                    "hits_with_projectile_close": info[s]["hits_with_watched_actor_close"]} for s in ("a", "b")}
    if name == "area_attacks":
        return {s: {"area_actors_seen": sum(v["count"] for v in info[s]["actors"].values() if v["kind"] == "area"),
                    "hits": info[s]["hits"]} for s in ("a", "b")}
    if name == "drops_and_xp":
        return {"xp": info["xp"], **info["checks"]}
    if name == "enemy_hp_agree":
        return {**g("hits", "worst_hp_gap", "hits_that_did_nothing", "died_at_hit"), **info["checks"]}
    if name == "death":
        return {**g("respawn_s", "puppets_after_respawn", "after_respawn"), **info["checks"]}
    if name == "host_leaves":
        return {**g("b_noticed_host_gone_after_s", "b_puppet_of_a_gone_after_s"), **info["checks"]}
    return {}


def lag(a, b, names=LAG_RUNS):
    """Scenarios 7 to 12 (and movement) again, each from a fresh pair whose client joins through the Co-op Anything
    hub's chaos relay: 25 +/- 25 ms each way and 2 % loss (50 to 150 ms round trip). Lag must not make anything
    worse: a scenario that passes on the plain link must still pass, and the mod's own ping must show the lag."""
    out, worse = {}, []
    for n in names:
        rig.qdlab.stop()
        rig.stop_relay()
        try:
            a2, b2 = rig.pair(lag=True)
            ok, info = SCENARIOS[n](a2, b2)
        except Exception as e:
            ok, info = False, {"error": repr(e)}
        verdict = "SKIP" if ok is None else "PASS" if ok else "FAIL"
        pings = [l.split("worst ping ")[-1].strip() for l in kd_log_all("B") if "worst ping" in l]
        stats = safe(lambda: rig.chaos_call("stats"), {})
        relay = {d: {k: v for k, v in stats.get(d, {}).items() if k in ("seen", "forwarded", "dropped")}
                 for d in ("c2s", "s2c") if isinstance(stats, dict)}
        out[n] = {"result": verdict, "lan": LAN_VERDICT[n], "b_worst_ping": pings[-1] if pings else None,
                  "relay": relay, **lag_summary(n, info)}
        print(f"  lag/{n:15s} {verdict}  {json.dumps(out[n])}", flush=True)
        if LAN_VERDICT[n] == "PASS" and verdict != "PASS":
            worse.append(n)
    rig.qdlab.stop()
    rig.stop_relay()
    out["worse_than_lan"] = worse
    return not worse, out


# ------------------------------------------------------------------ Lane B: bosses and arenas
# Each boss scenario puts both players at a boss, starts the fight the way the game does (a trigger, the lectern, the
# boss's own aggro), from the host or from the client, and checks both copies: boss awake, arena locked, the boss's
# attacks hit each player while it fights them, its death, the arena opening again, the save flags and each player's
# own reward. Bosses run on the host; "client started" means the client's player set the fight off.

def actors(g, word, radius=0):
    """Loaded actors whose class or name contains `word` (bridge `actors`): name, class, pos, hidden, collision."""
    return json.loads(g.exec(f"actors {word} {radius}")["output"])


def getvar(g, actor, var):
    try:
        return g.exec(f"getvar {actor} {var}")["output"]
    except Exception:
        return None


def trap_mode(g, name):
    """True for the Bridge Lord's dormant decoy, False for a real boss, None if it could not be read. A failed read used
    to count as "not a decoy", so under load the host's kill step once hit the decoy and the real boss lived on."""
    for _ in range(3):
        v = getvar(g, name, "InTrapMode")
        if v in ("true", "false"):
            return v == "true"
        time.sleep(0.5)
    return None


def boss_of(g, cls):
    """This copy's boss of class `cls` (actor names differ per copy): name, dead, hp, plus DeathIsSuspended."""
    found = [e for e in g.state(sections=["enemies"])["enemies"] if cls in e["class"]]
    if not found:
        return None
    for e in found:   # a dormant copy elsewhere (the Bridge Lord's decoy) is not the boss being fought
        e["trap"] = trap_mode(g, e["name"])
    e = sorted(found, key=lambda x: (x["trap"] is True, x["trap"] is None, x["dead"], x["dist"]))[0]
    e["suspended"] = getvar(g, e["name"], "DeathIsSuspended") == "true"
    return e


def door_open(g, name):
    """A gate with a blocker box (Sword Shrine gates, the Jigeki door) is open when the box has no collision; other
    Door_Base doors report Opened; other arena walls (the Necronomicon's) are open when hidden or without collision."""
    for comp in ("ClosedBox", "BlockerBox"):   # Sword Shrine gates; doors that block with a box (the Jigeki door)
        box = getvar(g, name, f"{comp}.collision")
        if box is not None:
            return box == "none"
    v = getvar(g, name, "Opened")
    if v in ("true", "false"):
        return v == "true"
    found = [x for x in actors(g, name) if x["name"] == name]
    if not found:
        return None
    return found[0]["hidden"] or not found[0]["collision"]


def named(g, *words):
    nsd = save(g).get("namedSaveData") or {}
    return {k: v for k, v in nsd.items() if any(w in k for w in words)}


def ensure_boss(a, b, cls, spawner, wait_s=20):
    """The boss exists on both copies; a spawner that has not spawned it yet (quest conditions, distance) is told to
    on the host, which decides spawns for everyone."""
    def here(g):   # hidden too: some bosses stay hidden until their intro (the Whaleking)
        return any(cls in x["class"] and "Spawner" not in x["class"] for x in actors(g, cls))
    if not here(a):
        a.exec(f"callon {spawner} Force Respawn")
    end = time.time() + wait_s
    while time.time() < end:
        if here(a) and here(b):
            return True
        time.sleep(1)
    return False


def fight_half(g, other, cls, seconds, goto_cm=250):
    """`g` fights the boss for `seconds`, or up to twice as long until it has been hit (its hits make it the boss's
    target, the other player stands 20 m back); both players' health dents counted."""
    for x in (g, other):
        x.exec("watch start 3000 Zzz")
    # each machine aims at its own copy by name (names differ per machine; a class can match a decoy too)
    tg, to = ((boss_of(x, cls) or {}).get("name", cls) for x in (g, other))
    start = time.time()
    end, hard_end = start + seconds, start + 2 * seconds
    next_goto = 0.0
    turn, last_drops, quiet_since = 0, 0, time.time()
    while time.time() < hard_end:
        # past `seconds`, keep going only until `g` has been hit (a slow attack cycle can miss a fixed window)
        drops = g.state(sections=["watch"])["watch"]["health"]["drops"]
        if time.time() >= end and drops > 0:
            break
        if drops != last_drops:
            last_drops, quiet_since = drops, time.time()
        elif time.time() - quiet_since > 12:
            # not hit for a while: the boss cannot reach this spot (Grave Guardian: from one side it jumps, misses and
            # walks back to its shrine), so step round to another side of it, as a player would
            # (the goto measures from where the player stands, so the turn applies once and the next ones keep that side)
            turn, quiet_since = 90, time.time()
            next_goto = 0.0
        if time.time() >= next_goto:
            g.exec(f"kd egoto {tg} {goto_cm} {turn}")
            turn = 0
            other.exec(f"kd egoto {to} 2000")   # the other player stands back, so the boss turns to `g`
            next_goto = time.time() + 6
        g.exec(f"kd ehit {tg} 1")
        time.sleep(2.5)
    out = []
    for x in (g, other):
        x.exec("watch stop")
        h = x.state(sections=["watch"])["watch"]["health"]
        out.append({"hits": h["drops"], "damage": round(h["damage"])})
    return out


def fight(a, b, cls, seconds=60, goto_cm=250, host_first=False):
    """Half the time the boss fights B (its attacks reach B through the mirrored hitboxes), half the time A. Each
    stands `goto_cm` from the boss (farther for a boss in a pool that throws the player out)."""
    if host_first:
        ha = fight_half(a, b, cls, seconds / 2, goto_cm)
        hb = fight_half(b, a, cls, seconds / 2, goto_cm)
    else:
        hb = fight_half(b, a, cls, seconds / 2, goto_cm)
        ha = fight_half(a, b, cls, seconds / 2, goto_cm)
    return {"b_fights": {"b": hb[0], "a": hb[1]}, "a_fights": {"a": ha[0], "b": ha[1]}}


def boss_far(g, cls):
    """The name of this copy's boss of class `cls` wherever it is (the enemy list only covers 60 m around the player):
    a copy not in its dormant trap mode first."""
    found = [x["name"] for x in actors(g, cls) if cls in x["class"] and "Spawner" not in x["class"]]
    trap = {n: trap_mode(g, n) for n in found}
    found.sort(key=lambda n: (trap[n] is True, trap[n] is None))
    return found[0] if found else None


def kill(a, cls, tries=60, dmg=4000):
    """The host (the boss's owner) hits it until it is dead or kneels (bosses with a talk-to-loot death)."""
    far = None
    if not boss_of(a, cls):
        far = boss_far(a, cls)
        if far:   # it moved away (the Bridge Lord leaps down into its arena)
            a.exec(f"kd egoto {far} 400")
            time.sleep(2)
    for i in range(tries):
        bs = boss_of(a, cls)
        if not bs:
            # still outside the 60 m enemy list (it fell somewhere the goto found no ground): hit it by name; it only
            # counts as done once its own copy says it is dead
            far = far or boss_far(a, cls)
            if not far or getvar(a, far, "bDead") == "true":
                return i
            a.exec(f"kd ehit {far} {dmg}")
            time.sleep(1.5)
            continue
        if bs["dead"] or bs["suspended"]:
            return i
        a.exec(f"kd ehit {bs['name']} {dmg}")
        time.sleep(1.5)
    return tries


def talk_reward(g, boss_name, presses=25, boss_end=None):
    """The local player talks to its own copy of a defeated boss and clicks through the reward dialogue; then the
    dialogue's own end runs (what leaving the conversation does), which is when the boss really dies."""
    inv0 = dict(save(g).get("inventory") or {})
    held0 = {x["name"] for x in actors(g, "Pickup", 6000)}
    carried = getvar(g, boss_name, "HeldItem")   # an item the boss holds (a Josami knight's key)
    g.exec(f"interact {boss_name}")
    time.sleep(3)
    # loot handed over as an item the player holds up (the Blood Lords' amulets, a knight's key): the player puts it
    # away with a button, which ends the pickup and lets the boss die for good
    new = [x["name"] for x in actors(g, "Pickup", 6000) if x["name"] not in held0]
    if carried and carried != "None":
        new.append(carried)
    for name in new:
        try:
            g.exec(f"callon {name} PutAway")
            time.sleep(2)
        except Exception:
            pass
    for _ in range(presses):
        try:
            g.exec("uicall DialogueScreen ConfirmPressed")
        except Exception:
            break
        time.sleep(1.2)
    for npc in actors(g, "DummyNPC"):
        for fn in ("EndDialogue", "DoneSpeaking"):
            try:
                g.exec(f"callon {npc['name']} {fn}")
                break
            except Exception:
                pass
    if boss_end:   # bosses whose own event ends the talk (the Evil Fishwife's DoneSpeaking)
        try:
            g.exec(f"callon {boss_name} {boss_end}")
        except Exception:
            pass
    time.sleep(4)
    inv = dict(save(g).get("inventory") or {})
    return {k: (inv0.get(k), v) for k, v in inv.items() if inv0.get(k) != v}


def both(fn, a, b):
    return {"a": fn(a), "b": fn(b)}


def placed(g, probe):
    """Some loaded actor named or classed `probe` has a real position (its sublevel is streamed in)."""
    return any(x["pos"] != [0, 0, 0] for x in actors(g, probe) if probe in x["name"] or probe in x["class"])


def stands_at(g, spot):
    """Put the player at `spot`: True if they stand there (the floor is loaded; it may be a little below the spot),
    not fall through it."""
    g.exec("tp {} {} {} 0".format(*spot))
    time.sleep(2.5)
    p1 = local_pos(g)
    time.sleep(1)
    p2 = local_pos(g)
    flat = ((p1[0] - spot[0]) ** 2 + (p1[1] - spot[1]) ** 2) ** 0.5
    return flat < 400 and abs(p2[2] - p1[2]) < 30 and spot[2] - 1200 < p1[2] < spot[2] + 300


def stream_in(g, levels, probe, spot, wait_s=20):
    """A teleport skips the game's level loading (walking through a LevelManagerTrigger, a teleporter's priority load):
    an arena in a sublevel can stay streamed out, its actors at the origin or its floor missing under the player.
    `levels` is the area's level list, which the game's level manager loads (bridge `levellist`), or "loc:<name>", a
    teleporter or rest spot the player teleports to first the game's own way (bridge `tploc`: some areas only get
    their floors that way), or "call:<player function>" for the player's own teleport there. Then the player is put at `spot`. True once `probe` is placed and the player stands there."""
    if levels.startswith("loc:"):
        g.exec(f"tploc {levels[4:]}")
        time.sleep(12)
    elif levels.startswith("call:"):   # the player's own teleport to a place without a rest spot (Sipeha's world)
        g.exec(f"call {levels[5:]}")
        time.sleep(12)
    else:
        g.exec(f"levellist {levels}")
    end = time.time() + wait_s
    while time.time() < end:
        if (not probe or placed(g, probe)) and stands_at(g, spot):
            return True
        time.sleep(1)
    return False


def boss_scenario_start(a, b, spot_a, spot_b, levels=None, probe=None):
    """Both players in god mode at their spots; with `levels`, the area's level list is loaded first on each machine.
    False if a player could not be put there (the area did not load)."""
    for g in (a, b):
        g.exec("kd god 1")
    if levels:
        ok = all([stream_in(g, levels, probe, spot) for g, spot in ((a, spot_a), (b, spot_b))])
        time.sleep(2)
        return ok
    a.exec("tp {} {} {} 0".format(*spot_a))
    b.exec("tp {} {} {} 0".format(*spot_b))
    time.sleep(8)
    return True


def boss_scenario_end(a, b):
    for g in (a, b):
        g.exec("kd god 0")


def grave_guardian(a, b):
    """Grave Guardian (Sword Shrine, Ruins). The CLIENT crosses its trigger: the entrance gate closes and the boss
    wakes on both copies; its swings hit each player it fights; it kneels on both; each player talks to their own copy
    and gets the Stone Duck key; the gates open on both and both saves have ShrineBossKilled."""
    cls, spawner, gates = "Shrineboss_BP", "ShrinebossSpawner", ("SwordShrineGate_BP_2", "SwordShrineGate_BP2")
    boss_scenario_start(a, b, (17900, -7300, 50), (17900, -7500, 50))
    if not ensure_boss(a, b, cls, spawner):
        return None, {"why": "the Grave Guardian is not on both copies"}
    res, checks = {}, {}
    # Arriving may already have set the fight off. The host resets the arena the way the game does when its player
    # leaves the fight (entrance open, exit shut, boss back at its spawner, the start armed again): it reaches the
    # client too.
    a.exec(f"callon {spawner} PlayerTeleportedOutOfBossArena")
    time.sleep(8)
    ensure_boss(a, b, cls, spawner)
    entrance = getvar(a, spawner, "EntranceGate")
    res["entrance"] = entrance
    res["after_reset"] = both(lambda g: door_open(g, entrance), a, b)
    checks["arena_reset_both"] = res["after_reset"] == {"a": True, "b": True}
    # the client walks in: what the shrine's trigger runs for the player who crosses it (only on that machine)
    b.exec(f"activate {spawner}")
    time.sleep(6)
    res["after_start"] = {"entrance_open": both(lambda g: door_open(g, entrance), a, b),
                          "boss": both(lambda g: boss_of(g, cls), a, b)}
    checks["arena_locked_both"] = res["after_start"]["entrance_open"] == {"a": False, "b": False}
    checks["boss_awake_both"] = all(x and not x["trap"] for x in res["after_start"]["boss"].values())
    res["fight"] = fight(a, b, cls)
    checks["attacks_hit_b"] = res["fight"]["b_fights"]["b"]["hits"] > 0
    checks["attacks_hit_a"] = res["fight"]["a_fights"]["a"]["hits"] > 0
    res["kill_hits"] = kill(a, cls)
    time.sleep(4)
    res["after_kill"] = both(lambda g: boss_of(g, cls), a, b)
    checks["defeated_both"] = all(x and (x["dead"] or x["suspended"]) for x in res["after_kill"].values())
    res["reward_a"] = talk_reward(a, res["after_kill"]["a"]["name"]) if res["after_kill"]["a"] else {}
    res["reward_b"] = talk_reward(b, res["after_kill"]["b"]["name"]) if res["after_kill"]["b"] else {}
    checks["reward_a"] = "StoneDuckShopUnlock" in res["reward_a"]
    checks["reward_b"] = "StoneDuckShopUnlock" in res["reward_b"]
    time.sleep(4)
    res["saves"] = both(lambda g: named(g, "ShrineBoss"), a, b)
    res["gates_after"] = {gt: both(lambda g: door_open(g, gt), a, b) for gt in gates}
    checks["killed_flag_both"] = all(s.get("ShrineBossKilled") == 1 for s in res["saves"].values())
    checks["own_reward_counter_each"] = all(s.get("ShrineBossRewardCount") == 1 for s in res["saves"].values())
    checks["arena_open_both"] = all(v == {"a": True, "b": True} for v in res["gates_after"].values())
    boss_scenario_end(a, b)
    res["checks"] = checks
    return all(checks.values()), res


def executioner(a, b):
    """Executioner (Library). It wakes for the CLIENT, who fights it: the boss runs on the host, whose aggro closes the
    front door on both copies; its axe swings and spins hit the client (no attack of it reached a follower before);
    it dies on both, the door opens on both and both saves have ExecutionerKilled."""
    cls, spawner = "ExecutionerMiniboss_BP", "ExecutionerMiniboss_Spawner"
    boss_scenario_start(a, b, (26000, 754, 1300), (26000, 900, 1300))
    if not ensure_boss(a, b, cls, spawner):
        return None, {"why": "the Executioner is not on both copies"}
    res, checks = {}, {}
    door = getvar(a, spawner, "FrontDoor")
    res["door"] = door
    a.exec(f"callon {door} OpenDoor")
    time.sleep(6)
    res["door_opened_by_host"] = both(lambda g: door_open(g, door), a, b)
    checks["door_open_follows"] = res["door_opened_by_host"]["b"] is True
    b.exec(f"kd ewake {cls}")
    time.sleep(3)
    res["fight"] = fight(a, b, cls)
    checks["attacks_hit_b"] = res["fight"]["b_fights"]["b"]["hits"] > 0
    checks["attacks_hit_a"] = res["fight"]["a_fights"]["a"]["hits"] > 0
    res["door_in_fight"] = both(lambda g: door_open(g, door), a, b)
    checks["arena_locked_both"] = res["door_in_fight"] == {"a": False, "b": False}
    res["kill_hits"] = kill(a, cls)
    time.sleep(6)
    res["after_kill"] = both(lambda g: boss_of(g, cls), a, b)
    checks["dead_both"] = all(x is None or x["dead"] for x in res["after_kill"].values())
    res["door_after"] = both(lambda g: door_open(g, door), a, b)
    checks["arena_open_both"] = res["door_after"] == {"a": True, "b": True}
    res["saves"] = both(lambda g: named(g, "Executioner"), a, b)
    checks["killed_flag_both"] = all(s.get("ExecutionerKilled") == 1 for s in res["saves"].values())
    boss_scenario_end(a, b)
    res["checks"] = checks
    return all(checks.values()), res


def necronomicon(a, b):
    """Necronomicon (Library top). The CLIENT reads the lectern: the door appears and the boss starts its intro on both
    copies. Every form change of the host's copy runs on the client's copy too; its attacks hit each player it fights;
    it kneels on both, each player gets its reward from their own copy, both saves have NecronimiconDefeated (the
    game's spelling) and the door agrees afterwards."""
    cls, spawner, door = "Necronomicon_BP", "Necronomicon_Boss_Spawner", "NecroBossDoor_SM"
    boss_scenario_start(a, b, (26396, -300, 15150), (26396, -450, 15150))
    if not ensure_boss(a, b, cls, spawner):
        return None, {"why": "the Necronomicon is not on both copies"}
    res, checks = {}, {}
    log_from = {n: len(kd_log_all(n)) for n in ("A", "B")}
    res["door_before"] = both(lambda g: door_open(g, door), a, b)
    b.exec(f"interact {spawner}")
    time.sleep(12)
    res["after_start"] = {"door_open": both(lambda g: door_open(g, door), a, b),
                          "boss": both(lambda g: boss_of(g, cls), a, b)}
    checks["arena_locked_both"] = res["after_start"]["door_open"] == {"a": False, "b": False}
    checks["boss_awake_both"] = all(x and not x["trap"] for x in res["after_start"]["boss"].values())
    res["fight"] = fight(a, b, cls)
    checks["attacks_hit_b"] = res["fight"]["b_fights"]["b"]["hits"] > 0
    checks["attacks_hit_a"] = res["fight"]["a_fights"]["a"]["hits"] > 0
    # forms: its AI leaves the book form on its own and its other forms end when their own health runs out. Light
    # hits for a while; every form change the host's copy makes must run on the client's copy too.
    end = time.time() + 120
    while time.time() < end:
        bs = boss_of(a, cls)
        if not bs or bs["dead"] or bs["suspended"]:
            break
        a.exec(f"kd ehit {cls} 150")
        time.sleep(3)
    sent = [l.split("[bosssync] sent ")[1].split(" (")[0] for l in kd_log_all("A")[log_from["A"]:] if "[bosssync] sent " in l]
    ran = [l.split("[bosssync] ran ")[1].split(" on ")[0] for l in kd_log_all("B")[log_from["B"]:] if "[bosssync] ran " in l]
    form_fns = ("TransitionTo", "EnterEyeState", "ChangeFromBookForm", "Return From BOok Form", "Exit")
    res["forms_sent_by_host"] = [x for x in sent if x.startswith(form_fns)]
    res["forms_run_on_client"] = [x for x in ran if x.startswith(form_fns)]
    checks["forms_follow"] = bool(res["forms_sent_by_host"]) and         sorted(res["forms_sent_by_host"]) == sorted(res["forms_run_on_client"])
    res["kill_hits"] = kill(a, cls)
    time.sleep(8)
    res["after_kill"] = both(lambda g: boss_of(g, cls), a, b)
    checks["defeated_both"] = all(x and (x["dead"] or x["suspended"]) for x in res["after_kill"].values())
    res["reward_a"] = talk_reward(a, res["after_kill"]["a"]["name"]) if res["after_kill"]["a"] else {}
    res["reward_b"] = talk_reward(b, res["after_kill"]["b"]["name"]) if res["after_kill"]["b"] else {}
    # its reward NPC hands out the next item of its list per conversation: each player gets the same one
    checks["reward_each"] = bool(res["reward_a"]) and set(res["reward_a"]) == set(res["reward_b"])
    time.sleep(4)
    res["saves"] = both(lambda g: named(g, "Necron"), a, b)
    checks["defeated_flag_both"] = all(s.get("NecronimiconDefeated") == 1 for s in res["saves"].values())
    counters = [s.get("NecronomiconRewardCount") for s in res["saves"].values()]
    checks["own_reward_counter_each"] = counters[0] == counters[1] and (counters[0] or 0) >= 1
    # its door stays after the fight (only an arena reset hides it, also in single player): it must agree
    res["door_after"] = both(lambda g: door_open(g, door), a, b)
    checks["door_agrees_after"] = res["door_after"]["a"] == res["door_after"]["b"]
    boss_scenario_end(a, b)
    res["checks"] = checks
    return all(checks.values()), res


def lost_librarian(a, b):
    """Lost Librarian (Library). No arena or reward talk: it dies on both copies and both saves have
    LostLibrarianKilled, which the Librarian NPC's greeting reads."""
    cls, spawner = "LostLibrarian_BP", "LostLibrarian_Spawner_BP"
    boss_scenario_start(a, b, (28657, -3400, 3700), (28657, -3550, 3700))
    if not ensure_boss(a, b, cls, spawner):
        return None, {"why": "the Lost Librarian is not on both copies"}
    res, checks = {}, {}
    res["kill_hits"] = kill(a, cls)
    time.sleep(6)
    res["after_kill"] = both(lambda g: boss_of(g, cls), a, b)
    checks["dead_both"] = all(x is None or x["dead"] for x in res["after_kill"].values())
    res["saves"] = both(lambda g: named(g, "LostLibrarian"), a, b)
    checks["killed_flag_both"] = all(s.get("LostLibrarianKilled") == 1 for s in res["saves"].values())
    boss_scenario_end(a, b)
    res["checks"] = checks
    return all(checks.values()), res


def calls_follow(log_from, words):
    """Calls the host's BossSync sent whose function starts with one of `words`, and the ones the client ran."""
    sent = [ln.split("[bosssync] sent ")[1].split(" (")[0] for ln in kd_log_all("A")[log_from["A"]:]
            if "[bosssync] sent " in ln]
    ran = [ln.split("[bosssync] ran ")[1].split(" on ")[0] for ln in kd_log_all("B")[log_from["B"]:]
           if "[bosssync] ran " in ln]
    return [x for x in sent if x.startswith(words)], [x for x in ran if x.startswith(words)]


def suspended_boss_end(a, b, cls, res, checks, reward_counter, boss_end=None):
    """Bosses that kneel when beaten: each player talks to their own copy and gets the same reward, and each keeps
    their own reward counter."""
    res["kill_hits"] = kill(a, cls)
    time.sleep(6)
    res["after_kill"] = both(lambda g: boss_of(g, cls), a, b)
    checks["defeated_both"] = all(x and (x["dead"] or x["suspended"]) for x in res["after_kill"].values())
    for who, g in (("a", a), ("b", b)):
        x = res["after_kill"][who]
        res[f"reward_{who}"] = talk_reward(g, x["name"], boss_end=boss_end) if x else {}
    checks["reward_each"] = bool(res["reward_a"]) and set(res["reward_a"]) == set(res["reward_b"])
    time.sleep(4)
    counters = [named(g, reward_counter).get(reward_counter) for g in (a, b)]
    res["reward_counters"] = counters
    checks["own_reward_counter_each"] = counters[0] == counters[1] and (counters[0] or 0) >= 1


def whaleking(a, b):
    """Whaleking (Aqua Ruins depths). The CLIENT wakes it: it rises and shows on both copies; its bites, slams and tail
    slaps hit each player it fights (montage hitboxes and its own stomp checks); every water, poison and horn change
    of the host's copy runs on the client's; it lies defeated on both, each player gets the reward from their own copy
    and both saves have WhaleKingIsDead."""
    cls, spawner = "Whaleking_Boss_BP", "WhaleKingSpawner"
    if not boss_scenario_start(a, b, (-2470, -9502, -4200), (-2300, -9502, -4200), "AquaRuins", spawner):
        return None, {"why": "the Whaleking's lake did not load"}
    if not ensure_boss(a, b, cls, spawner):
        return None, {"why": "the Whaleking is not on both copies"}
    res, checks = {}, {}
    log_from = {n: len(kd_log_all(n)) for n in ("A", "B")}
    b.exec(f"kd ewake {cls}")
    time.sleep(15)   # its intro
    res["after_start"] = both(lambda g: boss_of(g, cls), a, b)
    checks["boss_awake_both"] = all(x and not x["trap"] and not x["hidden"] for x in res["after_start"].values())
    res["fight"] = fight(a, b, cls, seconds=90)
    # below half its health: its second phase drains the arena and brings poison
    a.exec(f"kd ehp {cls} 0.45")
    time.sleep(2)
    res["fight_phase2"] = fight(a, b, cls, seconds=60)
    # it keeps one target for long stretches, so a player may only get its turn in the second phase: count the whole
    # fight (each player must still be hit at some point)
    hits = lambda who: res["fight"][f"{who}_fights"][who]["hits"] + res["fight_phase2"][f"{who}_fights"][who]["hits"]
    checks["attacks_hit_b"] = hits("b") > 0
    checks["attacks_hit_a"] = hits("a") > 0
    res["calls_sent"], res["calls_run"] = calls_follow(log_from, ("DrainWater", "RefillWater", "RemovePoison",
                                                                   "KillPoisonCircles", "DoHornBreak"))
    checks["arena_changes_follow"] = sorted(res["calls_sent"]) == sorted(res["calls_run"])
    res["pools"] = both(lambda g: len(actors(g, "WhaleKingPoisonCircle")), a, b)
    suspended_boss_end(a, b, cls, res, checks, "WhaleKingRewardCount")
    res["saves"] = both(lambda g: named(g, "WhaleKingIsDead"), a, b)
    checks["killed_flag_both"] = all(x.get("WhaleKingIsDead") == 1 for x in res["saves"].values())
    boss_scenario_end(a, b)
    res["checks"] = checks
    return all(checks.values()), res


def king_of_stone(a, b):
    """King of Stone (Stone Garden). The CLIENT wakes it and it rises on both copies; its attacks hit each player it
    fights. The host's player then leaves the arena while the client keeps fighting: the boss must not heal to full or
    give up (it did, for everyone). It lies defeated on both, each player gets the reward from their own copy and both
    saves have SZWhaleKingIsDead."""
    cls, spawner = "Whaleking_SZ_BP", "WhaleKing_SZ_Spawner_2"
    if not boss_scenario_start(a, b, (16374, -28000, -13850), (16200, -28000, -13850), "StoneGarden", spawner):
        return None, {"why": "the Stone Garden did not load"}
    if not ensure_boss(a, b, cls, spawner):
        return None, {"why": "the King of Stone is not on both copies"}
    res, checks = {}, {}
    b.exec(f"kd ewake {cls}")
    time.sleep(12)
    res["after_start"] = both(lambda g: boss_of(g, cls), a, b)
    checks["boss_awake_both"] = all(x and not x["trap"] for x in res["after_start"].values())
    res["fight"] = fight(a, b, cls, seconds=60)
    checks["attacks_hit_b"] = res["fight"]["b_fights"]["b"]["hits"] > 0
    checks["attacks_hit_a"] = res["fight"]["a_fights"]["a"]["hits"] > 0
    a.exec(f"kd ehp {cls} 0.6")
    time.sleep(2)
    hp_before = (boss_of(a, cls) or {}).get("hp")
    a.exec("tp 16374 -20000 -13500 0")   # 87 m out of its arena
    end = time.time() + 15
    while time.time() < end:   # the client keeps fighting meanwhile
        b.exec(f"kd ehit {cls} 1")
        time.sleep(2.5)
    res["host_left"] = {"hp_before": hp_before, "hp_after": both(lambda g: (boss_of(g, cls) or {}).get("hp"), a, b)}
    checks["no_reset_while_client_fights"] = hp_before is not None and \
        all(h is not None and h <= hp_before + 1 for h in res["host_left"]["hp_after"].values())
    a.exec("tp 16374 -28000 -13850 0")
    time.sleep(3)
    suspended_boss_end(a, b, cls, res, checks, "WhaleKingSGRewardCount")
    res["saves"] = both(lambda g: named(g, "SZWhaleKingIsDead"), a, b)
    checks["killed_flag_both"] = all(x.get("SZWhaleKingIsDead") == 1 for x in res["saves"].values())
    boss_scenario_end(a, b)
    res["checks"] = checks
    return all(checks.values()), res


def royal_fishmaner(a, b):
    """Royal Fishmaner (Aqua Ruins depths). It wakes for the CLIENT; its swings and its area attack hit each player it
    fights (its area attack's hitbox only reached the machine that runs it before); it dies on both and both saves
    have AquaRuinsFishmanMiniboss."""
    cls, spawner = "Fishmaner_Boss", "FishManerSpawner17"
    boss_scenario_start(a, b, (-6589, -10200, -5000), (-6589, -10350, -5000))
    if not ensure_boss(a, b, cls, spawner):
        return None, {"why": "the Royal Fishmaner is not on both copies"}
    res, checks = {}, {}
    b.exec(f"kd ewake {cls}")
    time.sleep(4)
    res["fight"] = fight(a, b, cls, seconds=70)
    checks["attacks_hit_b"] = res["fight"]["b_fights"]["b"]["hits"] > 0
    checks["attacks_hit_a"] = res["fight"]["a_fights"]["a"]["hits"] > 0
    res["kill_hits"] = kill(a, cls)
    time.sleep(6)
    res["after_kill"] = both(lambda g: boss_of(g, cls), a, b)
    checks["dead_both"] = all(x is None or x["dead"] for x in res["after_kill"].values())
    res["saves"] = both(lambda g: named(g, "AquaRuinsFishmanMiniboss"), a, b)
    checks["killed_flag_both"] = all(x.get("AquaRuinsFishmanMiniboss") == 1 for x in res["saves"].values())
    boss_scenario_end(a, b)
    res["checks"] = checks
    return all(checks.values()), res


def evil_fishwife(a, b):
    """Evil Fishwife (Jigeki temple). The CLIENT steps into her arena: the temple door closes and her guardians rise on
    both copies; her attacks hit each player she fights; every guardian resurrection on the host's copy runs on the
    client's; she lies defeated on both, each player gets the reward from their own copy, both saves have
    EvilFishWifeDefeated and the door opens on both."""
    cls, spawner = "FishWifeEvil", "FishWifeEvil_Spawner"
    if not boss_scenario_start(a, b, (-18881, -16900, -13250), (-18881, -17050, -13250), "AquaRuins", spawner):
        return None, {"why": "the Jigeki temple did not load"}
    if not ensure_boss(a, b, cls, spawner):
        return None, {"why": "the Evil Fishwife is not on both copies"}
    res, checks = {}, {}
    log_from = {n: len(kd_log_all(n)) for n in ("A", "B")}
    door = getvar(a, spawner, "JigekiDoor") or getvar(a, spawner, "Door")
    res["door"] = door
    b.exec(f"activate {spawner}")
    time.sleep(6)
    res["door_in_fight"] = both(lambda g: door_open(g, door), a, b)
    checks["arena_locked_both"] = res["door_in_fight"] == {"a": False, "b": False}
    res["guardians"] = both(lambda g: len([x for x in actors(g, "Guardian_Fishman") if not x["hidden"]]), a, b)
    checks["guardians_both"] = res["guardians"]["a"] > 0 and res["guardians"]["a"] == res["guardians"]["b"]
    res["fight"] = fight(a, b, cls, seconds=60)
    checks["attacks_hit_b"] = res["fight"]["b_fights"]["b"]["hits"] > 0
    checks["attacks_hit_a"] = res["fight"]["a_fights"]["a"]["hits"] > 0
    res["calls_sent"], res["calls_run"] = calls_follow(log_from, ("Resurrect", "FinalKill"))
    checks["guardian_calls_follow"] = sorted(res["calls_sent"]) == sorted(res["calls_run"])
    suspended_boss_end(a, b, cls, res, checks, "FishwifeEvilRewardCount", boss_end="DoneSpeaking")
    res["saves"] = both(lambda g: named(g, "EvilFishWifeDefeated"), a, b)
    checks["defeated_flag_both"] = all(x.get("EvilFishWifeDefeated") == 1 for x in res["saves"].values())
    res["door_after"] = both(lambda g: door_open(g, door), a, b)
    checks["arena_open_both"] = res["door_after"] == {"a": True, "b": True}
    boss_scenario_end(a, b)
    res["checks"] = checks
    return all(checks.values()), res


def white_tree_golem(a, b):
    """White Tree Golem (golden room under the Ruins). The CLIENT uses the big nut: the golem appears and wakes on both
    copies; its attacks hit each player it fights; it dies on both, both saves have WhiteTreeGolemDead and the big nut
    it leaves is there for both."""
    cls, nut, spawner = "Golem_WhiteTree_BP", "BigNut_SM_2", "Golem_WhiteTree_Spawner_2"
    if not boss_scenario_start(a, b, (12218, -700, -1450), (12218, -850, -1450), "GreenRuins", nut):
        return None, {"why": "the golden room did not load"}
    # the nut wakes the golem its spawner placed when the room was shown (the game's level manager, which a teleport
    # skips): spawn it the way that does
    if not ensure_boss(a, b, cls, spawner):
        return None, {"why": "the White Tree Golem is not on both copies"}
    res, checks = {}, {}
    b.exec(f"interact {nut}")
    time.sleep(12)
    res["after_start"] = both(lambda g: [x["name"] for x in actors(g, cls)], a, b)
    checks["golem_on_both"] = all(res["after_start"].values())
    if not checks["golem_on_both"]:
        return False, {**res, "checks": checks}
    res["fight"] = fight(a, b, cls, seconds=60)
    checks["attacks_hit_b"] = res["fight"]["b_fights"]["b"]["hits"] > 0
    checks["attacks_hit_a"] = res["fight"]["a_fights"]["a"]["hits"] > 0
    res["kill_hits"] = kill(a, cls)
    time.sleep(8)
    res["after_kill"] = both(lambda g: boss_of(g, cls), a, b)
    checks["dead_both"] = all(x is None or x["dead"] for x in res["after_kill"].values())
    res["saves"] = both(lambda g: named(g, "WhiteTreeGolemDead"), a, b)
    checks["killed_flag_both"] = all(x.get("WhiteTreeGolemDead") == 1 for x in res["saves"].values())
    res["nut_pickup"] = both(lambda g: [p["class"] for p in visible_pickups(g).values() if "Nut" in p["class"]], a, b)
    checks["nut_for_both"] = all(res["nut_pickup"].values())
    boss_scenario_end(a, b)
    res["checks"] = checks
    return all(checks.values()), res


def boss_encounter(a, b, cls, spawner, spot, start, levels, door_vars=(), doors=(), partners=(), flags=(),
                   talk_counter=None, boss_end=None, fight_s=60, spawn_first=True, open_after=True, extra=None,
                   partner_spawners=(), goto_cm=250, fight_cls=None):
    """The shared shape of a boss fight check. Both players stand at `spot`; `start` = (who, bridge command) sets the
    fight off on that player's machine; then on both copies: the boss (and its `partners`) exist and are awake, the
    arena doors (spawner variables `door_vars`, or actor names `doors`) are shut, the boss's attacks hit each player it
    fights, the boss and partners die (or kneel, then each player talks to their own copy for the reward counted in
    `talk_counter`), the save `flags` are set and the doors are open again (`open_after`). `extra(a, b, res, checks)`
    adds boss-specific checks before the kill."""
    res, checks = {}, {}
    if not boss_scenario_start(a, b, spot, (spot[0] + 150, spot[1], spot[2]), levels, spawner):
        return None, {"why": f"{levels} did not load at the arena"}
    # a boss whose spawner placed it when the area was shown (the game's level manager, which a teleport skips) is
    # spawned the way that does; the fight's start then wakes it (and its partners)
    if spawn_first and not ensure_boss(a, b, cls, spawner):
        return None, {"why": f"{cls} is not on both copies"}
    for c, sp in zip(partners, partner_spawners):
        if not ensure_boss(a, b, c, sp):
            return None, {"why": f"{c} is not on both copies"}
    door_names = list(doors)
    for v in door_vars:
        d = getvar(a, spawner, v)
        if d and d != "None":
            door_names.append(d)
    res["doors"] = door_names
    who, cmd = start
    for i, c in enumerate(cmd.split(" ; ")):   # several steps: each a few seconds after the last
        if i:
            time.sleep(5)
        (b if who == "b" else a).exec(c)
    time.sleep(10)
    for c in (cls, *partners):
        res[f"present_{c}"] = both(lambda g: len([x for x in actors(g, c) if c in x["class"] and "Spawner" not in x["class"]
                                                   and x["pos"] != [0, 0, 0]]), a, b)
        checks[f"{c}_on_both"] = res[f"present_{c}"]["a"] > 0 and res[f"present_{c}"]["b"] > 0
    if not all(checks.values()):
        boss_scenario_end(a, b)
        res["checks"] = checks
        return False, res
    res["doors_in_fight"] = {d: both(lambda g: door_open(g, d), a, b) for d in door_names}
    checks["arena_locked_both"] = all(v == {"a": False, "b": False} for v in res["doors_in_fight"].values())
    res["fight"] = fight(a, b, fight_cls or cls, seconds=fight_s, goto_cm=goto_cm)   # the one that attacks
    checks["attacks_hit_b"] = res["fight"]["b_fights"]["b"]["hits"] > 0
    checks["attacks_hit_a"] = res["fight"]["a_fights"]["a"]["hits"] > 0
    if extra:
        extra(a, b, res, checks)
    for c in partners:
        res[f"kill_{c}"] = kill(a, c)
    if talk_counter:
        suspended_boss_end(a, b, cls, res, checks, talk_counter, boss_end=boss_end)
    else:
        res["kill_hits"] = kill(a, cls)
        time.sleep(8)
        for g in (a, b):   # each player walks over to their own copy (it may have fallen or leapt away)
            x = boss_of(g, cls)
            if (not x or x["trap"]) and boss_far(g, cls):
                g.exec(f"kd egoto {boss_far(g, cls)} 300")
        time.sleep(3)
        res["after_kill"] = both(lambda g: boss_of(g, cls), a, b)
        checks["dead_both"] = all(x is None or x["dead"] or x["suspended"] for x in res["after_kill"].values())
    time.sleep(4)
    res["saves"] = both(lambda g: named(g, *flags), a, b) if flags else {}
    for f in flags:
        checks[f"{f}_both"] = all((x.get(f) or 0) >= 1 for x in res["saves"].values())
    if open_after and door_names:
        res["doors_after"] = {d: both(lambda g: door_open(g, d), a, b) for d in door_names}
        checks["arena_open_both"] = all(v == {"a": True, "b": True} for v in res["doors_after"].values())
    boss_scenario_end(a, b)
    res["checks"] = checks
    return all(checks.values()), res


def ros_dragon(a, b):
    """Ruins of Sorrow dragon (Dekanima). The CLIENT sets its spawner off and it runs on the host's copy too; the
    dragon wakes on both copies; its attacks hit each player; it lies downed on both, each player loots their own copy
    (the reward talk) and both saves have ROSDragonDead. (Its door shuts from the arena trigger after the egg's hatch
    sequence, which the rig does not play, so the door is not checked here.)"""
    log_from = {n: len(kd_log_all(n)) for n in ("A", "B")}

    def extra(a, b, res, checks):
        sent, ran = calls_between(log_from, "B", "A", ("Activate",))
        res["activate"] = {"sent_by_client": sent, "ran_on_host": ran}
        # the client's start must run on the host; later Activates fire on both copies by themselves (the players walk
        # through the spawner's trigger at the same moment) and the host rightly skips the client's echo of those
        checks["start_follows"] = bool(sent) and len(ran) >= 1

    return boss_encounter(a, b, "Dragon_Ros", "ROSDragon_Spawner", (3549, 9691, -6080),
                          ("b", "activate ROSDragon_Spawner"), "ROS", flags=("ROSDragonDead",),
                          talk_counter="ROSDragonRewardCount", boss_end="RewardDialogueEnded", extra=extra)


def calls_between(log_from, sender, receiver, words):
    """BossSync calls whose function starts with one of `words` that `sender` ("A"/"B") sent and `receiver` ran."""
    sent = [ln.split("[bosssync] sent ")[1].split(" (")[0] for ln in kd_log_all(sender)[log_from[sender]:]
            if "[bosssync] sent " in ln]
    ran = [ln.split("[bosssync] ran ")[1].split(" on ")[0] for ln in kd_log_all(receiver)[log_from[receiver]:]
           if "[bosssync] ran " in ln]
    return [x for x in sent if x.startswith(words)], [x for x in ran if x.startswith(words)]


def josami_des(a, b):
    """Josami Des (Depths). The CLIENT starts the blood pool fight (the pool's BeginFight: the dome shuts, the knights
    rise) and it starts on the host's copy too; the knights' attacks hit each player; when the pool and its knights
    are down and the host's player has talked to the knight that kneels, its FinishFight (the dome dropping) runs on
    both and both saves have BloodAngelJosamiDefeated.
    (The dome's blocker is an invisible mesh whose switches do not show its state, so the dome is checked through
    the fight's own calls.)"""
    spawner = "Josami_Des_BloodPoolSpawner_2"
    log_from = {n: len(kd_log_all(n)) for n in ("A", "B")}
    ok, res = boss_encounter(a, b, "JosamiBloodPool", spawner, (-19155, -10812, -16600),
                             # the pool's event trigger runs BeginFight when a player steps in: the dome shuts
                             ("b", f"activate {spawner} ; callon {spawner} BeginFight"), "SZ_Depths", partners=(),
                             spawn_first=False, open_after=False,
                             fight_cls="JosamiKnight")   # the pool itself never attacks: its knights do
    if ok is None:
        return ok, res
    checks = res["checks"]
    sent, ran = calls_between(log_from, "B", "A", ("BeginFight",))
    res["begin_fight"] = {"sent_by_client": sent, "ran_on_host": ran}
    checks["fight_start_follows"] = bool(sent) and len(ran) >= 1
    time.sleep(6)
    # the fight ends when the knights are down too (the pool no longer raises them): the host's player kills them
    for _ in range(40):
        alive = [x["name"] for x in a.state(sections=["enemies"])["enemies"]
                 if "JosamiKnight" in x["class"] and not x["dead"]]
        if not alive:
            break
        a.exec(f"kd ehit {alive[0]} 9000")
        time.sleep(1.5)
    time.sleep(8)
    # the last knight kneels; talking to it ends the fight (its FinishFight): the host's player talks to the knights
    res["talked_to"] = []
    for k in [x["name"] for x in a.state(sections=["enemies"])["enemies"] if "JosamiKnight" in x["class"]][:6]:
        res["talked_to"].append(k)
        talk_reward(a, k)
        if calls_between(log_from, "A", "B", ("FinishFight",))[0]:
            break
    time.sleep(6)
    sent, ran = calls_between(log_from, "A", "B", ("FinishFight",))
    res["finish_fight"] = {"sent_by_host": sent, "ran_on_client": ran}
    checks["fight_end_follows"] = bool(sent) and len(ran) >= 1
    # its end saves the spawner's own flag (BloodAngelJosamiDefeated) and records the kills
    flag = getvar(a, spawner, "NamedSaveDataToCheck") or "BloodAngelJosamiDefeated"
    res["saves"] = both(lambda g: named(g, flag), a, b)
    checks["defeated_flag_both"] = all((x.get(flag) or 0) >= 1 for x in res["saves"].values())
    res["checks"] = checks
    return all(checks.values()), res


def royal_duo(a, b):
    """Royal Duo (Castle). The CLIENT starts the fight: the arena door shuts and BOTH the Fated Wheeler and the Lordly
    Spiker are there on both copies (the Spiker only spawned for the player who started it); their attacks hit each
    player; both die on both copies, both saves have RoyalDuoDefeated and the door opens on both."""
    return boss_encounter(a, b, "Fated_Wheeler_Miniboss", "Fated_Wheeler_MinibossSpawner_2", (-67332, -20063, -36700),
                          ("b", "callon Fated_Wheeler_MinibossSpawner_2 StartFight"), "loc:Castle_Teleporter", door_vars=("ArenaDoor",),
                          partners=("Lordly_Spiker_Miniboss",), partner_spawners=("Lordly_Spiker_Miniboss_Spawner_2",),
                          flags=("RoyalDuoDefeated",))


def black_robot_duo(a, b):
    """Black Robot Duo (Castle). The CLIENT starts the fight: the door shuts and both robots are there on both copies;
    the large robot's flamethrower and the drill robot's attacks hit each player; both die on both copies, both saves
    have BlackRobotsDefeated and the door opens on both."""
    return boss_encounter(a, b, "LargeRobot_Miniboss", "LargeRobot_Miniboss_Spawner_2", (-60606, -24300, -38480),
                          ("b", "callon LargeRobot_Miniboss_Spawner_2 StartFight"), "loc:Castle_Teleporter",
                          door_vars=("Door", "ArenaDoor", "FrontDoor"), partners=("DrillRobot_Miniboss",),
                          partner_spawners=("DrillRobot_Miniboss_Spawner_2",), flags=("BlackRobotsDefeated",))


def blood_pool_duo(a, b):
    """Blood Pool Duo (Castle). The CLIENT starts the fight: both doors shut and the Stalker and the Snail Bull rise
    from the pool on both copies; their attacks hit each player; the pool dies on both, both saves have
    BloodDuoDefeated and the doors open on both."""
    return boss_encounter(a, b, "BloodPool_FakeEnemy", "BloodPool_EnemySpawner_2", (-52089, -20628, -36800),
                          ("b", "callon BloodPool_EnemySpawner_2 StartFight"), "loc:Castle_Teleporter", door_vars=("FrontDoor", "RearDoor"),
                          partners=("Stalker_Miniboss", "SnailBull_Miniboss"),
                          partner_spawners=("Miniboss_Stalker_Spawner_2", "SnailBull_Miniboss_Spawner_2"),
                          flags=("BloodDuoDefeated",), fight_cls="Stalker_Miniboss")


def cocker_boss(a, b):
    """Cocker miniboss (Aqua Ruins depths). It wakes for the CLIENT; its area attack hits each player it fights (it only
    reached the machine that runs it before); it dies on both and both saves have AquaRuinsCockerMiniboss."""
    return boss_encounter(a, b, "Cocker_Boss", "EnemySpawner2", (-9921, -14900, -5100),
                          ("b", "kd ewake Cocker_Boss"), "AquaRuins", flags=("AquaRuinsCockerMiniboss",))


def ribber_boss(a, b):
    """Ribber miniboss (Aqua Ruins depths). It wakes for the CLIENT; its area attack hits each player it fights; it dies
    on both and both saves have AquaRuinsRibberMiniboss."""
    return boss_encounter(a, b, "Ribber_Boss", "EnemySpawner", (-5671, -11250, -2650),
                          ("b", "kd ewake Ribber_Boss"), "AquaRuins", flags=("AquaRuinsRibberMiniboss",))


def loot_kneeling(a, b, classes):
    """Bosses that kneel and hand their loot to whoever talks to them (Blood Lords, Mecha Knights, Bridge Lord): each
    player talks to their own copies; what each got."""
    out = {}
    for who, g in (("a", a), ("b", b)):
        got = {}
        for c in classes:
            for x in g.state(sections=["enemies"])["enemies"]:
                if c in x["class"] and getvar(g, x["name"], "DeathIsSuspended") == "true":
                    got.update(talk_reward(g, x["name"]))
        out[who] = got
    return out


def demon_duo(a, b):
    """Demon duo (Mausoleum dark maze). The CLIENT sets the spider's spawner off: the front door shuts and both the
    Demon Spider and the Hellbat are there on both copies; their attacks hit each player; both die on both copies,
    both saves have DemonBossesKilled and the door opens again on both."""
    return boss_encounter(a, b, "DemonSpider", "DemonSpider_Spawner_0", (-16948, 18700, -8250),
                          ("b", "activate DemonSpider_Spawner_0"), "Mausoleum", door_vars=("FrontDoor",), partners=("Hellbat",),
                          flags=("DemonBossesKilled",))


def bridge_lord(a, b):
    """Bridge Lord (castle bridge). The CLIENT sets it off: both bridge doors shut and their blockers rise on both
    copies; its gunfire and blade hit each player; it kneels on both, each player talks to their own copy, both saves
    have BridgeLordDead and the doors open on both."""
    def extra(a, b, res, checks):
        res["loot"] = None
    ok, res = boss_encounter(a, b, "JosamiLord_BP", "JosamiLord_Spawner_2", (-51820, -17253, -32050),
                             ("b", "activate JosamiLord_Spawner_2"), "loc:Castle_Teleporter", door_vars=("FrontDoor", "RearDoor", "Front", "Rear"),
                             open_after=False, extra=extra)
    if ok is None:
        return ok, res
    checks = res["checks"]
    # its reward is a death talk on each player's own copy (no item); the talk's end saves BridgeLordDead
    res["loot"] = loot_kneeling(a, b, ("JosamiLord_BP",))
    time.sleep(4)
    res["saves"] = both(lambda g: named(g, "BridgeLordDead"), a, b)
    checks["BridgeLordDead_both"] = all((x.get("BridgeLordDead") or 0) >= 1 for x in res["saves"].values())
    res["doors_after"] = {d: both(lambda g: door_open(g, d), a, b) for d in res["doors"]}
    checks["arena_open_both"] = all(v == {"a": True, "b": True} for v in res["doors_after"].values())
    res["checks"] = checks
    return all(checks.values()), res


def blood_lords(a, b):
    """Blood Lords (Prison). The CLIENT starts the fight: the door shuts and both sisters are there on both copies; their
    attacks hit each player; both kneel on both copies, each player takes the amulets from their own copies, both saves
    have RedLordsDead and the door opens on both."""
    ok, res = boss_encounter(a, b, "JosamiBloodLord_Sword", "JosamiBloodLord_Sword_Spawner_2", (27600, 1500, -6950),
                             ("b", "callon JosamiBloodLord_Sword_Spawner_2 StartFight"), "Prison_SZ", door_vars=("Door",),
                             partners=("JosamiBloodLord_Gun",), spawn_first=False, open_after=False)
    if ok is None or not all(v for k, v in res["checks"].items() if k.endswith("_on_both")):
        return ok, res
    checks = res["checks"]
    res["loot"] = loot_kneeling(a, b, ("JosamiBloodLord_Sword", "JosamiBloodLord_Gun"))
    checks["loot_each"] = bool(res["loot"]["a"]) and set(res["loot"]["a"]) == set(res["loot"]["b"])
    time.sleep(4)
    res["saves"] = both(lambda g: named(g, "RedLordsDead"), a, b)
    checks["RedLordsDead_both"] = all((x.get("RedLordsDead") or 0) >= 1 for x in res["saves"].values())
    res["doors_after"] = {d: both(lambda g: door_open(g, d), a, b) for d in res["doors"]}
    checks["arena_open_both"] = all(v == {"a": True, "b": True} for v in res["doors_after"].values())
    res["checks"] = checks
    return all(checks.values()), res


def mecha_knights(a, b):
    """Mossy Mecha Knight (shop isle) and Mausoleum lance knight. Each wakes for the CLIENT; its attacks hit each player
    it fights; it kneels on both copies and each player gets its key from their own copy."""
    res, checks = {}, {}
    for cls, spawner, spot, levels in (
            ("MechaKnight_DualAxe_Mossy", "MechaKnight_DualAxe_ShopSpawner", (3808, 40207, 2510), "loc:GR_ShopIsland_Save"),
            ("MechaKnight_MausLance", "MechaKnight_00_Spawner", (5318, 14413, 7910), "Mausoleum")):
        ok, r = boss_encounter(a, b, cls, spawner, spot, ("b", f"kd ewake {cls}"), levels, open_after=False)
        if ok is None:
            res[cls] = r
            checks[f"{cls}_setup"] = False
            continue
        r["loot"] = loot_kneeling(a, b, (cls,))
        r["checks"]["loot_each"] = bool(r["loot"]["a"]) and set(r["loot"]["a"]) == set(r["loot"]["b"])
        res[cls] = r
        for k, v in r["checks"].items():
            checks[f"{cls}:{k}"] = v
    res["checks"] = checks
    return all(checks.values()), res


def queen(a, b):
    """The Queen (castle throne room). She wakes for the CLIENT: her door shuts on both copies (it shut on her aggro,
    which only fires on the host); her attacks hit each player; when she enters a later phase on the host the
    client's copy enters it too, and the throne breaks on both. Her death plays an ending and changes level (cutscenes
    are Lane A), so the scenario stops before it."""
    cls, spawner = "Queen_BP", "Queen_Spawner_BP_2"
    if not boss_scenario_start(a, b, (-51797, -53400, -34450), (-51650, -53400, -34450), "AutumnIslandCastle",
                               spawner):
        return None, {"why": "the throne room did not load"}
    if not ensure_boss(a, b, cls, spawner):
        return None, {"why": "the Queen is not on both copies"}
    res, checks = {}, {}
    log_from = {n: len(kd_log_all(n)) for n in ("A", "B")}
    door = None
    for v in ("Door", "FrontDoor"):
        d = getvar(a, spawner, v)
        if d and d != "None":
            door = d
            break
    res["door"] = door
    b.exec(f"kd ewake {cls}")
    time.sleep(8)
    res["fight"] = fight(a, b, cls, seconds=120, host_first=True)   # her attack cycle is slow
    checks["attacks_hit_b"] = res["fight"]["b_fights"]["b"]["hits"] > 0
    checks["attacks_hit_a"] = res["fight"]["a_fights"]["a"]["hits"] > 0
    if door:
        res["door_in_fight"] = both(lambda g: door_open(g, door), a, b)
        checks["arena_locked_both"] = res["door_in_fight"] == {"a": False, "b": False}
    # her phases are the native CurrentSubState (the client's copy follows the owner's); her death plays the ending,
    # so she is brought to a later phase, not killed
    def phase(g):
        x = boss_of(g, cls)
        v = getvar(g, x["name"], "CurrentSubState") if x else None
        return int(v) if v not in (None, "") and v.lstrip("-").isdigit() else None
    res["phase_before"] = both(phase, a, b)
    a.exec(f"kd ehp {cls} 0.3")
    for _ in range(6):   # a few hits for her to notice the lower health
        a.exec(f"kd ehit {cls} 1")
        time.sleep(2.5)
    time.sleep(6)
    res["phase_after"] = both(phase, a, b)
    p0, p1 = res["phase_before"]["a"], res["phase_after"]
    checks["later_phase_both"] = p0 is not None and p1["a"] is not None and p1["a"] > p0 and p1["a"] == p1["b"]
    res["calls_sent"], res["calls_run"] = calls_follow(log_from, ("DoStateTransition", "Break"))
    # the host's Blueprint may run a call twice in a row; the client runs each one once
    checks["throne_follows"] = set(res["calls_sent"]) == set(res["calls_run"])
    boss_scenario_end(a, b)
    res["checks"] = checks
    return all(checks.values()), res


def sipeha(a, b):
    """Sipeha (its own world). It wakes for the CLIENT: its arena door shuts on both copies; its attacks hit each player
    it fights; it kneels on both. (What follows is an ending: Lane A.)"""
    return boss_encounter(a, b, "Sipeha_BP", "Sipeha_Spawner", (27088, -33900, 20600), ("b", "activate Sipeha_Spawner"),
                          "call:TeleportToSipehaArena",
                          door_vars=("Door",), open_after=False)


def boss_reset_guard(a, b):
    """The host's player leaves a boss fight the client is still in (the game's PlayerTeleported, which a rest, a death
    or a teleporter runs, and its heartbeat Reset). Neither may reset the boss: same health, entrance still shut, on
    both copies. Then, with the client gone too, the host's reset does run, and the entrance opens on BOTH copies."""
    cls, spawner = "Shrineboss_BP", "ShrinebossSpawner"
    boss_scenario_start(a, b, (17900, -7300, 50), (17900, -7500, 50))
    if not ensure_boss(a, b, cls, spawner):
        return None, {"why": "the Grave Guardian is not on both copies"}
    res, checks = {}, {}
    a.exec(f"callon {spawner} PlayerTeleportedOutOfBossArena")
    time.sleep(8)
    ensure_boss(a, b, cls, spawner)
    entrance = getvar(a, spawner, "EntranceGate")
    b.exec(f"activate {spawner}")
    time.sleep(5)
    b.exec(f"kd egoto {cls} 250")
    b.exec(f"kd ehit {cls} 50")
    time.sleep(3)
    hp0 = both(lambda g: (boss_of(g, cls) or {}).get("hp"), a, b)
    a.exec(f"callon {spawner} PlayerTeleported")
    time.sleep(2)
    a.exec(f"callon {spawner} Reset")
    time.sleep(6)
    res["hp_before"], res["hp_after"] = hp0, both(lambda g: (boss_of(g, cls) or {}).get("hp"), a, b)
    res["entrance_after_host_left"] = both(lambda g: door_open(g, entrance), a, b)
    checks["boss_kept_both"] = all(res["hp_after"][k] is not None and hp0[k] is not None and res["hp_after"][k] <= hp0[k] + 1
                                   for k in ("a", "b"))
    checks["arena_stays_locked_both"] = res["entrance_after_host_left"] == {"a": False, "b": False}
    b.exec("tp 11000 -7000 300 0")   # the client leaves too: now the host's reset is real
    time.sleep(6)
    a.exec(f"callon {spawner} PlayerTeleportedOutOfBossArena")
    time.sleep(8)
    res["entrance_after_reset"] = both(lambda g: door_open(g, entrance), a, b)
    checks["reset_opens_both"] = res["entrance_after_reset"] == {"a": True, "b": True}
    boss_scenario_end(a, b)
    res["checks"] = checks
    return all(checks.values()), res


def arena_pull_in(a, b):
    """The client sets a boss fight off while the host's player stands 10 m back, outside the gate: the host's player is
    brought next to the client before the entrance shuts, so nobody is locked out of the fight; the entrance is shut on
    both."""
    cls, spawner = "Shrineboss_BP", "ShrinebossSpawner"
    boss_scenario_start(a, b, (17900, -7300, 50), (17900, -7500, 50))
    if not ensure_boss(a, b, cls, spawner):
        return None, {"why": "the Grave Guardian is not on both copies"}
    res, checks = {}, {}
    a.exec(f"callon {spawner} PlayerTeleportedOutOfBossArena")
    time.sleep(8)
    entrance = getvar(a, spawner, "EntranceGate")
    a.exec("tp 17000 -7000 -50 0")   # just outside the gate, 10 m from the client
    time.sleep(3)
    pb = local_pos(b)
    res["host_to_client_before_cm"] = round(dist(local_pos(a), pb))
    b.exec(f"activate {spawner}")
    time.sleep(6)
    res["host_to_client_after_cm"] = round(dist(local_pos(a), local_pos(b)))
    res["entrance"] = both(lambda g: door_open(g, entrance), a, b)
    checks["host_brought_in"] = res["host_to_client_before_cm"] > 800 and res["host_to_client_after_cm"] < 400
    checks["arena_locked_both"] = res["entrance"] == {"a": False, "b": False}
    boss_scenario_end(a, b)
    res["checks"] = checks
    return all(checks.values()), res


SCENARIOS = {"movement": movement, "loot_and_world": loot_and_world, "client_loot": client_loot,
             "host_world_objects": host_world_objects, "client_world_objects": client_world_objects,
             "host_gate": host_gate, "client_gate": client_gate,
             "checkpoint": checkpoint, "client_checkpoint": client_checkpoint, "story_flags": story_flags,
             "rejoin": rejoin, "client_progress_persists": client_progress_persists,
             "ranged_attacks": ranged_attacks, "area_attacks": area_attacks, "drops_and_xp": drops_and_xp,
             "enemy_hp_agree": enemy_hp_agree, "death": death, "host_leaves": host_leaves,
             "grave_guardian": grave_guardian, "executioner": executioner, "necronomicon": necronomicon,
             "lost_librarian": lost_librarian,
             "whaleking": whaleking, "king_of_stone": king_of_stone, "royal_fishmaner": royal_fishmaner,
             "evil_fishwife": evil_fishwife, "white_tree_golem": white_tree_golem,
             "ros_dragon": ros_dragon, "josami_des": josami_des, "royal_duo": royal_duo, "black_robot_duo": black_robot_duo, "blood_pool_duo": blood_pool_duo, "cocker_boss": cocker_boss, "ribber_boss": ribber_boss,
             "demon_duo": demon_duo, "bridge_lord": bridge_lord, "blood_lords": blood_lords, "mecha_knights": mecha_knights, "queen": queen, "sipeha": sipeha,
             "boss_reset_guard": boss_reset_guard, "arena_pull_in": arena_pull_in,
             "lag": lag}

# Each group runs on a freshly booted pair, so one group's actions never leak into the next one's checks.

# ------------------------------------------------------------------ Lane A: events shared by the party

def find(g, match, radius=0):
    return json.loads(g.exec(f"find {match} {radius}")["output"])


def props(g, actor, *names):
    return json.loads(g.exec(f"prop {actor} {' '.join(names)}")["output"])


TRIGGER_PROPS = ("HasAlreadybeenTriggered", "Activations")


def triggers_shared(a, b, tries=12):
    """A walks into activation triggers near the start (stands in their volume): each one that fires on A should fire
    on B's copy too (same one-shot flag and activation count), although B's player is not in it."""
    rows = []
    for t in find(a, "ActivationTrigger", 15000)[:tries]:
        n = t["name"]
        # B nearby (so its copy is streamed in and the name means the same trigger) but outside the volume
        b.exec(f"tp {t['pos'][0] + 600} {t['pos'][1]} {t['pos'][2] + 100} 0")
        time.sleep(3)
        try:
            b.exec(f"tpto {n} 600")
        except Exception:
            continue
        time.sleep(2)
        try:
            before_a, before_b = props(a, n, *TRIGGER_PROPS), props(b, n, *TRIGGER_PROPS)
        except Exception:
            continue
        if before_a.get("HasAlreadybeenTriggered") or before_b.get("HasAlreadybeenTriggered"):
            continue
        a.exec(f"tp {t['pos'][0]} {t['pos'][1]} {t['pos'][2] + 40} 0")   # into the volume, like walking in
        how = "walked in"
        time.sleep(4)
        after_a = props(a, n, *TRIGGER_PROPS)
        if after_a == before_a:
            a.exec(f"callon {n} DoTrigger")   # volume needs something else (grounding, an item): fire it directly
            how = "DoTrigger"
            time.sleep(4)
            after_a = props(a, n, *TRIGGER_PROPS)
        try:
            after_b = props(b, n, *TRIGGER_PROPS)
        except Exception as e:
            after_b = {"error": str(e)}
        if after_a == before_a:
            continue
        rows.append({"trigger": n, "how": how, "a": [before_a, after_a], "b": [before_b, after_b],
                     "b_follows": after_b == after_a})
        if len(rows) >= 3:
            break
    if not rows:
        return None, {"why": "no activation trigger near the start changed when A used it"}
    return all(r["b_follows"] for r in rows), {"rows": rows}


QUEEN_CUTSCENES = (77518, -52166, -34400)   # 998_QueenCutscenes: Sipeha transition and endings 1 and 2


def cutscene_shared(a, b):
    """A starts a level-placed cutscene (the Sipeha transition in the Queen's arena, not an ending): B's player should
    be in it too."""
    for g in (a, b):
        g.exec(f"tp {QUEEN_CUTSCENES[0]} {QUEEN_CUTSCENES[1]} {QUEEN_CUTSCENES[2]} 0")
        g.exec("streamlevel 998_QueenCutscenes")
    mgrs = []
    for _ in range(10):   # the cutscene sublevel streams in once a player is there
        time.sleep(3)
        mgrs = [m for m in find(a, "CutsceneManagerBase") if "Ending" not in m["class"]]
        if mgrs and [m for m in find(b, "CutsceneManagerBase") if "Ending" not in m["class"]]:
            break
    if not mgrs:
        return None, {"why": "no cutscene manager streamed in at the Queen's arena"}
    pick = next((m for m in mgrs if "Sipeha" in m["class"]), mgrs[0])
    n = pick["name"]
    before = {"A": a.state(sections=["player"])["player"].get("cutscene"),
              "B": b.state(sections=["player"])["player"].get("cutscene")}
    a.exec(f"callon {n} BeginCutscene")
    time.sleep(5)
    during = {"A": a.state(sections=["player"])["player"].get("cutscene"),
              "B": b.state(sections=["player"])["player"].get("cutscene")}
    for g in (a, b):   # leave nobody stuck in it
        try:
            g.exec(f"callon {n} SkipCutscene")
        except Exception:
            pass
    time.sleep(5)
    res = {"manager": n, "class": pick["class"], "before": before, "during": during}
    if not during["A"]:
        return None, {"why": "the cutscene did not start on A", **res}
    return bool(during["B"]), res


def pot_loot(a, b, spot=(3000, -2000, 500), tries=25):
    """A breaks pots until one drops loot: the pot breaks on B too, B sees the same drop (one of it), and B cannot
    roll a second one."""
    a.exec(f"tp {spot[0]} {spot[1]} {spot[2]} 0")
    time.sleep(3)
    pots = find(a, "Pot_Base", 8000)
    rows = []
    for pt in pots[:tries]:
        n = pt["name"]
        a.exec(f"tpto {n} 200")
        time.sleep(1)
        b.exec(f"tpto {n} 300")
        time.sleep(2)
        near = lambda g: sorted(p["class"] for p in g.state(sections=["pickups"])["pickups"]
                                if not p["hidden"] and dist(p["pos"], pt["pos"]) < 400)
        before_a, before_b = near(a), near(b)
        try:
            a.exec(f"callon {n} Break")
        except Exception:
            continue
        time.sleep(5)
        after_a = near(a)
        new_a = [c for c in after_a if c not in before_a or after_a.count(c) > before_a.count(c)]
        new_b = []
        for _ in range(6 if new_a else 1):   # B's copy can land a moment later: poll before calling it missing
            after_b = near(b)
            new_b = [c for c in after_b if c not in before_b or after_b.count(c) > before_b.count(c)]
            if sorted(new_b) == sorted(new_a):
                break
            time.sleep(1)
        row = {"pot": n, "a_new": new_a, "b_new": new_b, "b_pot_hidden": not any(
            q["name"] == n for q in find(b, "Pot_Base", 2000) if dist(q["pos"], pt["pos"]) < 50)}
        if new_a and sorted(new_b) != sorted(new_a):   # where did B's copy go?
            row["b_same_class_cm"] = sorted(int(dist(p["pos"], pt["pos"])) for p in b.state(sections=["pickups"])["pickups"]
                                            if p["class"] in new_a and not p["hidden"])[:5]
        rows.append(row)
        if new_a:
            break
    if not rows or not rows[-1]["a_new"]:
        return None, {"why": "no pot dropped anything on A", "rows": rows}
    last = rows[-1]
    return sorted(last["a_new"]) == sorted(last["b_new"]), {"rows": rows}

SCENARIOS.update({"triggers_shared": triggers_shared, "cutscene_shared": cutscene_shared, "pot_loot": pot_loot})

SUITES = {
    "lane1": [
        # the gate goes first: after the long-range teleports of the object checks its sublevel stops animating
        ["host_gate", "movement", "loot_and_world", "host_world_objects", "checkpoint", "story_flags", "rejoin"],
        ["client_gate", "client_loot", "client_world_objects", "client_checkpoint", "client_progress_persists"],
    ],
    # every Lane 2 scenario changes the fight or ends the session: one fresh pair each
    "lane2": [["ranged_attacks"], ["area_attacks"], ["drops_and_xp"], ["enemy_hp_agree"], ["death"],
              ["host_leaves"]],
    # round 2, Lane A: events the party shares (the cutscene last: it can move players around)
    "laneA": [["triggers_shared", "pot_loot"], ["cutscene_shared"]],
}
# Lane B: each boss changes the world for good (a defeated boss stays defeated): one fresh pair per area group
SUITES["b1"] = [["grave_guardian"], ["executioner", "lost_librarian"], ["necronomicon"]]
SUITES["b2"] = [["whaleking"], ["king_of_stone"], ["royal_fishmaner"], ["evil_fishwife"], ["white_tree_golem"]]
SUITES["b3"] = [["ros_dragon"], ["josami_des"], ["royal_duo"], ["black_robot_duo"], ["blood_pool_duo"], ["cocker_boss", "ribber_boss"]]
SUITES["b4"] = [["demon_duo"], ["bridge_lord"], ["blood_lords"], ["mecha_knights"], ["queen"], ["sipeha"]]
SUITES["b5"] = [["boss_reset_guard"], ["arena_pull_in"]]
SUITES["laneb"] = SUITES["b1"] + SUITES["b2"] + SUITES["b3"] + SUITES["b4"] + SUITES["b5"]
import party_scenarios  # noqa: E402
SCENARIOS.update(party_scenarios.SCENARIOS)
SUITES["party"] = party_scenarios.SUITE
SUITES["all"] = SUITES["lane1"] + SUITES["lane2"] + SUITES["laneA"] + SUITES["laneb"] + SUITES["party"]


RESULTS_PATH = None   # set by main: the run's results file, rewritten after every scenario


def save_results(results):
    if RESULTS_PATH:
        with open(RESULTS_PATH, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=1)


def run(names, results):
    if names == ["lag"]:   # lag boots its own pairs (7 of them)
        t = time.time()
        ok, info = lag(None, None)
        results["lag"] = {"result": "PASS" if ok else "FAIL", "seconds": round(time.time() - t), **info}
        return
    a, b = rig.connect("A"), rig.connect("B")
    for n in names:
        t = time.time()
        try:
            ok, info = SCENARIOS[n](a, b)
        except Exception as e:  # report and keep going
            ok, info = False, {"error": repr(e)}
        verdict = "SKIP" if ok is None else "PASS" if ok else "FAIL"
        results[n] = {"result": verdict, "seconds": round(time.time() - t), **info}
        save_results(results)   # a crash or a power cut keeps what ran so far
        print(f"{n:24s} {verdict}  {results[n]['seconds']:4d}s  {json.dumps(info)[:400]}", flush=True)
        if n == "rejoin" or n.endswith("_persists"):   # these replace a copy: reconnect
            a, b = rig.connect("A"), rig.connect("B")


def pick_groups(groups, only=None, rerun=None):
    """The suite's groups cut down to `only` (names) or to what did not pass in an earlier results file (`rerun`).
    A group keeps its order, so a scenario still runs after the ones it depends on in its group (the gate first)."""
    keep = set(only or [])
    if rerun:
        old = json.load(open(rerun, encoding="utf-8"))
        keep |= {n for n, r in old.items() if r.get("result") != "PASS"}
    if not only and not rerun:
        return groups
    return [[n for n in g if n in keep] for g in groups if any(n in keep for n in g)]


def pair_alive():
    """The pair from an earlier run is still up and connected (for --reuse)."""
    try:
        return rig.connected(rig.connect("A", wait_s=2), rig.connect("B", wait_s=2))
    except Exception:
        return False


USAGE = """usage: scenarios.py [name ...] | --suite <name> [--only a,b,...] [--rerun results.json] [--reuse] [--keep]
  --only    run just these scenarios of the suite (each with its group's fresh pair)
  --rerun   run what did not PASS in an earlier results file (results/<time>.json)
  --reuse   the first group runs on the pair that is already up (when it is connected) instead of a fresh one
  --keep    leave the last pair running at the end (for --reuse next time)
suites: """


def main(argv):
    global RESULTS_PATH
    results = {}
    if argv and argv[0] in ("-h", "--help"):
        sys.exit(USAGE + ", ".join(SUITES))

    def opt(flag):
        return argv[argv.index(flag) + 1] if flag in argv else None
    RESULTS_PATH = os.path.join(rig.LAB, "results", time.strftime("%Y%m%d-%H%M%S") + ".json")
    os.makedirs(os.path.dirname(RESULTS_PATH), exist_ok=True)
    print("results go to", RESULTS_PATH, flush=True)
    t_all = time.time()
    boot_s = 0
    if argv and argv[0] == "--suite":
        only = opt("--only").split(",") if opt("--only") else None
        groups = pick_groups(SUITES[argv[1]], only, opt("--rerun"))
        reuse = "--reuse" in argv and pair_alive()
        for gi, group in enumerate(groups):
            if group == ["lag"]:
                run(group, results)
                continue
            if not (gi == 0 and reuse):
                t = time.time()
                err = None
                for attempt in range(3):   # a copy can hang while loading (the PC is short of memory): boot again
                    rig.qdlab.stop()
                    rig.stop_relay()
                    try:
                        rig.fast_pair()
                        err = None
                        break
                    except Exception as e:
                        err = e
                        rig.log(f"pair boot failed ({e!r}), attempt {attempt + 1} of 3")
                boot_s += time.time() - t
                if err:
                    for n in group:
                        results[n] = {"result": "FAIL", "error": f"the pair did not boot: {err!r}"}
                        print(f"{n:24s} FAIL  the pair did not boot", flush=True)
                    save_results(results)
                    continue
            run(group, results)
        if "--keep" not in argv:
            rig.qdlab.stop()
    else:
        run(argv or [n for n in SCENARIOS if n != "lag"], results)
    save_results(results)
    total = time.time() - t_all
    print("SUMMARY", json.dumps({n: r["result"] for n, r in results.items()}))
    print(f"TIME {total / 60:.1f} min, of which booting pairs {boot_s / 60:.1f} min")
    print("saved", RESULTS_PATH)


if __name__ == "__main__":
    main(sys.argv[1:])
