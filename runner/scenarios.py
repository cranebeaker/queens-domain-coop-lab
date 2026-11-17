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


SCENARIOS = {"movement": movement, "loot_and_world": loot_and_world, "client_loot": client_loot,
             "host_world_objects": host_world_objects, "client_world_objects": client_world_objects,
             "checkpoint": checkpoint, "rejoin": rejoin}


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
