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


def drops_and_xp(a, b, spot=(3000, -2000, 500), kills=10):
    """A kills a few small enemies (`kd ehit`, B stands 1.5 m behind A). Shared rewards: each enemy dies on both
    copies (both kill counters go up), both get the same XP, and the loot is rolled once: what drops can be picked up
    by one player, after which it is gone for the other. (The 1.0.3 source: every non-owner "kills its own copy and
    rolls its own rewards".)"""
    for g in (a, b):
        g.exec("kd god 1")
    a.exec(f"tp {spot[0]} {spot[1]} {spot[2]} 0")
    time.sleep(3)
    pa = local_pos(a)
    targets = [e for e in enemies(a) if e.get("hp") and e["hp"] < 200 and dist(e["pos"], pa) < 4000]
    targets.sort(key=lambda e: dist(e["pos"], pa))
    targets = targets[:kills]
    if not targets:
        return None, {"why": "no small enemy within 40 m of A"}

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
    near_kill = lambda p: any(dist(p["pos"], k) < 800 for k in kill_at)   # drops land at the enemy's feet
    new = {"A": {n: p for n, p in pk_a.items() if n not in picks_before["A"] and near_kill(p)},
           "B": {n: p for n, p in pk_b.items() if n not in picks_before["B"] and near_kill(p)}}
    res = {"spot": spot, "kills": rows,
           "xp": {n: [before[n]["xp"], after[n]["xp"]] for n in ("A", "B")},
           "kill_counts": {n: after[n]["kills"] for n in ("A", "B")},
           "inventory_change_from_kills": {n: delta(before[n]["inventory"], after[n]["inventory"]) for n in ("A", "B")},
           "itemDrops_added": {n: len(after[n]["itemDrops"]) - len(before[n]["itemDrops"]) for n in ("A", "B")},
           "pickups_left_on_ground": {n: sorted(p["class"] for p in new[n].values()) for n in ("A", "B")}}

    # who can pick up a drop left on the ground: each copy picks up one of its own, the other copy is checked
    pick = {}
    for actor, other, an, on in ((b, a, "B", "A"), (a, b, "A", "B")):
        mine = sorted(new[an].values(), key=lambda p: p["dist"])
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
    b.exec("kd god 0")
    death_anim = any("Death" in an for an in b_anims)
    ar = res["after_respawn"]
    synced = all(isinstance(v, (int, float)) and v <= 100 for v in ar.values())
    same_world = live["after_a_respawned"]["A"] == live["after_a_respawned"]["B"]
    res["checks"] = {"b_saw_a_die": death_anim, "session_survived_and_syncs": synced,
                     "enemies_agree_after_reload": same_world}
    return death_anim and synced and same_world, res


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


SCENARIOS = {"movement": movement, "loot_and_world": loot_and_world, "client_loot": client_loot,
             "host_world_objects": host_world_objects, "client_world_objects": client_world_objects,
             "checkpoint": checkpoint, "rejoin": rejoin, "ranged_attacks": ranged_attacks,
             "area_attacks": area_attacks, "drops_and_xp": drops_and_xp,
             "enemy_hp_agree": enemy_hp_agree, "death": death, "host_leaves": host_leaves}


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
