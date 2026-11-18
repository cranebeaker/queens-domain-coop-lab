"""Co-op scenarios for Knights' Domain, run against a booted pair (rig.py pair: A host, B client).

  py -3 -I runner/scenarios.py [name ...]      default: all, in order

Each scenario prints PASS / FAIL with the numbers it measured. FAIL on a gap scenario means the gap is real.
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


def loot_and_world(a, b):
    """A picks up the nearest pickup. Shared loot: B's copy of that pickup should be gone too, and the world state
    (save maps) should match on both."""
    picks = sorted(a.state(sections=["pickups"])["pickups"], key=lambda p: p["dist"])
    picks = [p for p in picks if not p["hidden"]]
    if not picks:
        return None, {"why": "no visible pickup within 60 m of A"}
    target = picks[0]["name"]
    before_a = a.state(sections=["save"])["save"]
    before_b = b.state(sections=["save"])["save"]
    b_names_before = {p["name"] for p in b.state(sections=["pickups"])["pickups"] if not p["hidden"]}
    a.exec(f"tpto {target} 120")
    time.sleep(1)
    a.exec(f"interact {target}")
    time.sleep(6)
    after_a = a.state(sections=["save"])["save"]
    after_b = b.state(sections=["save"])["save"]
    a_names = {p["name"] for p in a.state(sections=["pickups"])["pickups"] if not p["hidden"]}
    b_names = {p["name"] for p in b.state(sections=["pickups"])["pickups"] if not p["hidden"]}

    def changed(x, y):
        return {k: (x.get(k), y.get(k)) for k in set(x) | set(y) if x.get(k) != y.get(k)}

    res = {
        "pickup": target,
        "on_b_before": target in b_names_before,
        "gone_on_a": target not in a_names,
        "gone_on_b": target not in b_names,
        "a_inventory_change": changed(before_a.get("inventory", {}), after_a.get("inventory", {})),
        "b_inventory_change": changed(before_b.get("inventory", {}), after_b.get("inventory", {})),
        "a_savable_change": changed(before_a.get("savableActorStates", {}), after_a.get("savableActorStates", {})),
        "b_savable_change": changed(before_b.get("savableActorStates", {}), after_b.get("savableActorStates", {})),
        "savable_a_vs_b": changed(after_a.get("savableActorStates", {}), after_b.get("savableActorStates", {})),
    }
    ok = res["gone_on_a"] and res["gone_on_b"] and not res["savable_a_vs_b"]
    return ok, res


def obj_state(g, name):
    """An object's own live state plus the save values of its GUIDs, as seen by one copy."""
    st = g.state(sections=["savables", "save"])
    obj = next((s for s in st["savables"] if s["name"] == name), None)
    if obj is None:
        return None
    saved = st["save"].get("savableActorStates", {})
    return {"live": obj["live"], "saved": [saved.get(gd) for gd in obj["guids"]]}


def use_objects(actor, other, names, settle=6):
    """`actor` walks up to each object and uses it; report what changed on `actor` and whether `other` followed."""
    rows, all_follow, any_changed = [], True, False
    for n in names:
        before_actor, before_other = obj_state(actor, n), obj_state(other, n)
        if before_actor is None:
            rows.append({"object": n, "skipped": "not loaded on the acting copy"})
            continue
        actor.exec(f"tpto {n} 150")
        time.sleep(1)
        try:
            actor.exec(f"interact {n}")
        except Exception as e:
            rows.append({"object": n, "skipped": f"interact failed: {e}"})
            continue
        time.sleep(settle)
        after_actor, after_other = obj_state(actor, n), obj_state(other, n)
        changed = after_actor != before_actor
        follows = after_other == after_actor
        any_changed |= changed
        if changed:
            all_follow &= follows
        rows.append({"object": n, "changed_on_actor": changed, "other_matches": follows,
                     "actor": [before_actor, after_actor], "other": [before_other, after_other]})
    if not any_changed:
        return None, {"why": "no object changed state when used", "rows": rows}
    return all_follow, {"rows": rows}


def host_world_objects(a, b):
    """Host opens a chest, pulls a lever, opens a secret wall: the client's copies should follow."""
    return use_objects(a, b, ["Chest10_2", "AquaLever_23", "SecretWall7_2"])


def client_world_objects(a, b):
    """Client opens a different chest and secret wall: the host's copies should follow."""
    return use_objects(b, a, ["Chest3_2", "SecretWall11_2"])


def client_loot(a, b):
    """Client picks up an item: it should vanish for the host and both saves should agree."""
    target = "Herb_Item_BP_2"
    before_a, before_b = obj_state(a, target), obj_state(b, target)
    if before_b is None:
        return None, {"why": f"{target} not loaded on B"}
    inv_before = b.state(sections=["save"])["save"].get("inventory", {})
    b.exec(f"tpto {target} 120")
    time.sleep(1)
    b.exec(f"interact {target}")
    time.sleep(6)
    after_a, after_b = obj_state(a, target), obj_state(b, target)
    inv_after = b.state(sections=["save"])["save"].get("inventory", {})
    res = {"pickup": target, "b": [before_b, after_b], "a": [before_a, after_a],
           "b_inventory_change": {k: (inv_before.get(k), inv_after.get(k)) for k in set(inv_before) | set(inv_after)
                                  if inv_before.get(k) != inv_after.get(k)}}
    if after_b == before_b:
        return None, {"why": "the pickup did not change state on B", **res}
    return after_a == after_b, res


def checkpoint(a, b):
    """Host rests at a checkpoint: checkpoint, visited list and teleporters should match on the client."""
    keys = ("lastCheckpoint", "savePoint", "savePointsVisited", "unlockedTeleporters")
    before_a = {k: a.state(sections=["save"])["save"].get(k) for k in keys}
    before_b = {k: b.state(sections=["save"])["save"].get(k) for k in keys}
    a.exec("tpto Checkpoint17 200")
    time.sleep(1)
    a.exec("interact Checkpoint17")
    time.sleep(8)
    sa, sb = a.state(sections=["save"])["save"], b.state(sections=["save"])["save"]
    after_a = {k: sa.get(k) for k in keys}
    after_b = {k: sb.get(k) for k in keys}
    res = {"a": [before_a, after_a], "b": [before_b, after_b]}
    if after_a == before_a:
        return None, {"why": "resting did not change the host's save", **res}
    return all(after_a[k] == after_b[k] for k in keys), res


def rejoin(a, b):
    """Client leaves and joins again after the host changed the world: the new client should see the host's world.
    Run after host_world_objects (it reuses the chest the host opened)."""
    target = "Chest10_2"
    host_view = obj_state(a, target)
    rig.qdlab.stop_copy("B")
    time.sleep(3)
    b2 = rig.boot("B", "join")
    time.sleep(10)
    client_view = obj_state(b2, target)
    puppets = remote_players(a)
    res = {"object": target, "host": host_view, "rejoined_client": client_view, "host_sees_puppets": len(puppets)}
    if host_view is None or not host_view.get("saved") or host_view["saved"] == [None]:
        return None, {"why": "host never changed the chest (run host_world_objects first)", **res}
    return client_view == host_view and len(puppets) >= 1, res


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
            a.exec(f"kd ehit {e['name']} 99999")
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


def area_attacks(a, b, seconds=75, near_cm=500):
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
    log_from = {n: len(kd_log_all(n)) for n in ("A", "B")}
    for g in (a, b):
        g.exec("watch start 6000")
    gaps = []
    end = time.time() + seconds
    next_hit = 0.0
    while time.time() < end:
        if time.time() >= next_hit:
            a.exec(f"kd ehit {target} 1")   # last hitter within 6 s owns and targets
            next_hit = time.time() + 3
        pa = local_pos(a)
        b.exec(f"tp {pa[0] + 150} {pa[1]} {pa[2] + 30} 0")
        time.sleep(0.6)
        gaps.append(round(dist(local_pos(b)[:2], local_pos(a)[:2])))
        b.exec(f"kd elist {boss}")
        a.exec(f"kd elist {boss}")
        time.sleep(1.4)
    for g in (a, b):
        g.exec("watch stop")
    wa = a.state(sections=["watch"])["watch"]
    wb = b.state(sections=["watch"])["watch"]
    for g in (a, b):
        g.exec("kd god 0")
    time.sleep(1)

    def side(w, name):
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

    sa, sb = side(wa, "A"), side(wb, "B")
    after = [e for e in enemies(a) if e["name"] == target]
    res = {"boss": target, "boss_hp": [hp_before, after[0].get("hp") if after else None], "seconds": seconds,
           "b_to_a_cm": {"median": sorted(gaps)[len(gaps) // 2] if gaps else None, "max": max(gaps) if gaps else None},
           "a": sa, "b": sb}
    area_hits = lambda s: sum(v["hits"] for k, v in s["hits_by_closest_class"].items()
                              if any(x in k for x in AREA_WORDS))
    if area_hits(sa) == 0:
        return None, {"why": "A was never hit with an area-attack actor close: the boss did not use one on A", **res}
    b_sees_area = any(v["kind"] == "area" for v in sb["actors"].values())
    return b_sees_area and area_hits(sb) > 0, res


SCENARIOS = {"movement": movement, "loot_and_world": loot_and_world, "client_loot": client_loot,
             "host_world_objects": host_world_objects, "client_world_objects": client_world_objects,
             "checkpoint": checkpoint, "rejoin": rejoin, "ranged_attacks": ranged_attacks,
             "area_attacks": area_attacks}


def main(names):
    names = names or list(SCENARIOS)
    a, b = rig.connect("A"), rig.connect("B")
    results = {}
    for n in names:
        try:
            ok, info = SCENARIOS[n](a, b)
        except Exception as e:  # report and keep going
            ok, info = False, {"error": repr(e)}
        verdict = "SKIP" if ok is None else "PASS" if ok else "FAIL"
        results[n] = {"result": verdict, **info}
        print(f"{n:16s} {verdict}  {json.dumps(info)}", flush=True)
    out = os.path.join(rig.LAB, "results", time.strftime("%Y%m%d-%H%M%S") + ".json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)
    print("saved", out)


if __name__ == "__main__":
    main(sys.argv[1:])
