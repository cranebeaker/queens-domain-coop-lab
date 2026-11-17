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


SCENARIOS = {"movement": movement, "loot_and_world": loot_and_world}


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
