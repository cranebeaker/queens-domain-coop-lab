"""Co-op scenarios for Knights' Domain.

  py -3 -I runner/scenarios.py [name ...]      run scenarios against an already booted pair (rig.py pair)
  py -3 -I runner/scenarios.py --suite lane1   boot fresh pairs and run all of Lane 1 (about 12 minutes)

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
    taker.exec(f"tpto {target} 120")
    time.sleep(1)
    taker.exec(f"interact {target}")
    after_t = before_t
    for _ in range(10):   # the pickup animation runs before the save entry is written
        time.sleep(2)
        after_t = obj_state(taker, target)
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


SCENARIOS = {"movement": movement, "loot_and_world": loot_and_world, "client_loot": client_loot,
             "host_world_objects": host_world_objects, "client_world_objects": client_world_objects,
             "host_gate": host_gate, "client_gate": client_gate,
             "checkpoint": checkpoint, "client_checkpoint": client_checkpoint, "story_flags": story_flags,
             "rejoin": rejoin, "client_progress_persists": client_progress_persists}

# Lane 1 runs as two groups, each on a freshly booted pair, so one side's actions never leak into the other's checks.
SUITES = {
    "lane1": [
        # the gate goes first: after the long-range teleports of the object checks its sublevel stops animating
        ["host_gate", "movement", "loot_and_world", "host_world_objects", "checkpoint", "story_flags", "rejoin"],
        ["client_gate", "client_loot", "client_world_objects", "client_checkpoint", "client_progress_persists"],
    ],
}


def run(names, results):
    a, b = rig.connect("A"), rig.connect("B")
    for n in names:
        try:
            ok, info = SCENARIOS[n](a, b)
        except Exception as e:  # report and keep going
            ok, info = False, {"error": repr(e)}
        verdict = "SKIP" if ok is None else "PASS" if ok else "FAIL"
        results[n] = {"result": verdict, **info}
        print(f"{n:24s} {verdict}  {json.dumps(info)[:400]}", flush=True)
        if n == "rejoin" or n.endswith("_persists"):   # these replace a copy: reconnect
            a, b = rig.connect("A"), rig.connect("B")


def main(argv):
    results = {}
    if argv and argv[0] == "--suite":
        for group in SUITES[argv[1]]:
            rig.qdlab.stop()
            time.sleep(3)
            rig.boot("A", "host")
            rig.boot("B", "join")
            time.sleep(10)
            run(group, results)
        rig.qdlab.stop()
    else:
        run(argv or list(SCENARIOS), results)
    out = os.path.join(rig.LAB, "results", time.strftime("%Y%m%d-%H%M%S") + ".json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)
    print("SUMMARY", json.dumps({n: r["result"] for n, r in results.items()}))
    print("saved", out)


if __name__ == "__main__":
    main(sys.argv[1:])
