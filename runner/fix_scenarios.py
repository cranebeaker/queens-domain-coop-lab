"""1.6.5 play-test fixes: health bars after a teammate's killing blow, an enemy turning to the player hitting it,
enemies leaving a downed player, and lifts riding for everyone.

Owners come from the host's `kd elist` lines; the bars from `kd bars` (every enemy health bar widget: its enemy, the
HP it shows, whether it faded out); lifts from the bridge's `prop` reader on both copies.
"""
import json
import time

import party_scenarios as ps
import targeting_scenarios as ts

GROUP_SPOT = ts.GROUP_SPOT


def _sc():
    import scenarios   # the main module (circular at import time)
    return scenarios


def owner_of(a, name):
    """The owner ("P1"/"P2") and HP of one enemy on the host, by its host actor name."""
    sc = _sc()
    mark = len(sc.kd_log_all("A"))
    a.exec(f"kd elist {name}")
    time.sleep(0.5)
    for line in sc.kd_log_all("A")[mark:]:
        if "[edbg] E " in line:
            f = dict(x.split("=", 1) for x in line.split() if "=" in x)
            return f.get("owner"), f.get("hp"), f.get("dead")
    return None, None, None


def bars(g, name):
    """Every enemy health bar widget on `g`: {enemy, dead, enemyHp, barHp, fadedOut, opacity}."""
    sc = _sc()
    mark = len(sc.kd_log_all(name))
    g.exec("kd bars")
    time.sleep(0.6)
    rows = []
    for line in sc.kd_log_all(name)[mark:]:
        if "[edbg] bar " in line:
            rows.append(dict(x.split("=", 1) for x in line.split() if "=" in x))
    return rows


def place_b_beside(a, b, ep, pa):
    """B 3 m from the enemy at `ep`, on the ground A stands on (as in client_pulls_aggro). True when placed."""
    sc = _sc()
    dx, dy = ep[0] - pa[0], ep[1] - pa[1]
    L = (dx * dx + dy * dy) ** 0.5 or 1.0
    ux, uy = dx / L, dy / L
    for cx, cy in ((ux, uy), (-uy, ux), (uy, -ux), (0.7 * ux - 0.7 * uy, 0.7 * uy + 0.7 * ux),
                   (0.7 * ux + 0.7 * uy, 0.7 * uy - 0.7 * ux)):
        b.exec(f"tp {ep[0] + cx * 300} {ep[1] + cy * 300} {ep[2] + 60} 0")
        time.sleep(1.5)
        pb = sc.local_pos(b)
        if pb and abs(pb[2] - pa[2]) < 25:
            return True
    return False


def small_enemies_near(a, pa, n):
    sc = _sc()
    c = [e for e in sc.enemies(a) if e.get("hp") and 0 < e["hp"] < 200 and sc.dist(e["pos"], pa) < 5000]
    c.sort(key=lambda e: sc.dist(e["pos"], pa))
    return c[:n]


def remote_kill_bars(a, b, tries=6):
    """A swings at a small enemy so A's game shows its health bar, then B lands the killing blow. Both kinds: the enemy
    run by A (B's hit goes to A's copy) and run by B (A's copy only hears that it died). Four seconds later the enemy is
    dead with no health left on both copies, and no bar of it is still showing on either.
    (Play test 1.6.4: "the health persists sometimes when my friend gets the killing blow".)"""
    sc = _sc()
    for g in (a, b):
        g.exec("kd god 1")
    a.exec("tp {} {} {} 0".format(*GROUP_SPOT))
    time.sleep(3)
    rows = []
    for en in small_enemies_near(a, sc.local_pos(a), tries):
        kind = "owned_by_b" if len(rows) % 2 == 0 else "owned_by_a"
        a.exec(f"kd egoto {en['name']} 140")
        time.sleep(1.2)
        pa = sc.local_pos(a)
        cur = next((e for e in sc.enemies(a) if e["name"] == en["name"]), None)
        if not cur or cur.get("dead") or not place_b_beside(a, b, cur["pos"], pa):
            continue
        on_b = sc.match_enemy(b, cur, 600)
        if not on_b:
            continue
        if kind == "owned_by_b":   # B hits it until it runs on B
            for _ in range(8):
                b.exec(f"kd ehit {on_b['name']} 1")
                time.sleep(0.8)
                if owner_of(a, en["name"])[0] == "P2":
                    break
        else:
            a.exec(f"kd ehit {en['name']} 1")
            time.sleep(1.0)
        owner = owner_of(a, en["name"])[0]
        # A's real swings: the game's own hit shows A's bar for this enemy
        a.exec(f"kd egoto {en['name']} 140")
        shown = False
        for _ in range(4):
            a.exec("kd swing 1")
            time.sleep(0.9)
            if any(r.get("enemy") == en["name"] and r.get("fadedOut") == "0" for r in bars(a, "A")):
                shown = True
                break
        b.exec(f"kd ehit {on_b['name']} 4000")
        time.sleep(4.0)
        # a bar still showing (not faded, it had a target) whose enemy is dead or gone is the bug
        def stale(rows):
            return [r for r in rows if r.get("fadedOut") == "0" and not r.get("barHp", "0/0").endswith("/0") and
                    (r.get("enemy") == "-" or r.get("dead") == "1")]
        ba, bb = stale(bars(a, "A")), stale(bars(b, "B"))
        o, hp_a, dead_a = owner_of(a, en["name"])
        row = {"enemy": en["name"], "kind": kind, "owner_before_kill": owner, "a_bar_shown": shown,
               "a_stale_bars": ba, "b_stale_bars": bb, "a_hp_after": hp_a, "a_dead_after": dead_a}
        # the enemy is gone from A's list (removed after its death) or listed dead with no health left
        row["ok"] = (not ba and not bb and
                     (hp_a is None or (dead_a == "1" and float(hp_a.split("/")[0]) <= 0)))
        rows.append(row)
        if len(rows) >= 4:
            break
    for g in (a, b):
        g.exec("kd god 0")
    told = [l.split("] ", 2)[-1] for n in ("A", "B") for l in sc.kd_log_all(n) if "bar told it died" in l]
    res = {"rows": rows, "bar_fixes_logged": told[:6]}
    tested = [r for r in rows if r["a_bar_shown"]]
    if len(tested) < 2 or not any(r["kind"] == "owned_by_b" and r["owner_before_kill"] == "P2" for r in tested):
        return None, {"why": "A's bar did not show for enough enemies, or none ran on B", **res}
    # only enemies A really hit count (a still-dormant one takes no damage from anyone and proves nothing)
    res["checks"] = {"dead_no_health_no_bar": all(r["ok"] for r in tested)}
    return all(res["checks"].values()), res


def aggro_to_hitter(a, b, rounds=3):
    """A hits a small enemy (it runs on A and fights A), then stops; B keeps hitting it from 3 m. The enemy turns to B
    within about 3 s of A's last hit (rule: an owner who has not hit it for 3 s no longer keeps it from the hitter).
    (Before: 6 s.)"""
    sc = _sc()
    for g in (a, b):
        g.exec("kd god 1")
    a.exec("tp {} {} {} 0".format(*GROUP_SPOT))
    time.sleep(3)
    rows = []
    for en in small_enemies_near(a, sc.local_pos(a), 8):
        a.exec(f"kd egoto {en['name']} 150")
        time.sleep(1.2)
        pa = sc.local_pos(a)
        cur = next((e for e in sc.enemies(a) if e["name"] == en["name"]), None)
        if not cur or cur.get("dead") or not place_b_beside(a, b, cur["pos"], pa):
            continue
        on_b = sc.match_enemy(b, cur, 600)
        if not on_b:
            continue
        for _ in range(3):
            a.exec(f"kd ehit {en['name']} 1")
            time.sleep(1.0)
        if owner_of(a, en["name"])[0] != "P1":
            continue
        last_a = time.time()
        turned = None
        while time.time() - last_a < 12:
            b.exec(f"kd ehit {on_b['name']} 1")
            if owner_of(a, en["name"])[0] == "P2":
                turned = round(time.time() - last_a, 1)
                break
            time.sleep(0.4)
        rows.append({"enemy": en["name"], "turned_after_s": turned})
        # back to A for the next round: A hits it alone
        if len(rows) >= rounds:
            break
    for g in (a, b):
        g.exec("kd god 0")
    res = {"rows": rows}
    if len(rows) < 2:
        return None, {"why": "not enough enemies to test", **res}
    res["checks"] = {"turns_within_5s": all(r["turned_after_s"] is not None and r["turned_after_s"] <= 5.0 for r in rows)}
    return all(res["checks"].values()), res


def _downed_case(a, b, victim, helper, vname, hname, vslot, hslot):
    """`victim` fights a small enemy and goes down next to `helper`: the enemy leaves the downed player and goes for
    the helper (owner changes to the helper on the host, the helper takes its hits)."""
    sc = _sc()
    victim.exec("kd god 0")
    helper.exec("kd god 1")
    victim.exec("tp {} {} {} 0".format(*GROUP_SPOT))
    helper.exec("tp {} {} {} 0".format(GROUP_SPOT[0] + 150, GROUP_SPOT[1], GROUP_SPOT[2]))
    time.sleep(3)
    pv = sc.local_pos(victim)
    ens = small_enemies_near(victim, pv, 6)
    for en in ens:
        name_on_a = en["name"] if victim is a else (sc.match_enemy(a, en, 600) or {}).get("name")
        if not name_on_a:
            continue
        victim.exec(f"kd egoto {en['name']} 150")
        time.sleep(1.2)
        pv = sc.local_pos(victim)
        cur = next((e for e in sc.enemies(victim) if e["name"] == en["name"]), None)
        if not cur or cur.get("dead"):
            continue
        ok = place_b_beside(victim, helper, cur["pos"], pv) if helper is b else _place_a(a, b, cur, pv)
        if not ok:
            continue
        for _ in range(3):
            victim.exec(f"kd ehit {en['name']} 1")
            time.sleep(1.0)
        if owner_of(a, name_on_a)[0] != vslot:
            continue
        helper.exec("watch start 3000")
        victim.exec("die")
        down = ps.wait_for(lambda: ps.pstate(victim, vname).get("localDowned"), 10)
        if down is None:
            helper.exec("watch stop")
            return {"enemy": en["name"], "why": f"{vname} did not go down"}
        t0 = time.time()
        moved = None
        while time.time() - t0 < 12:
            if owner_of(a, name_on_a)[0] == hslot:
                moved = round(time.time() - t0, 1)
                break
            time.sleep(0.4)
        views = []
        for _ in range(4):
            time.sleep(2)
            ph = sc.local_pos(helper)
            on_h = sc.match_enemy(helper, cur, 1500) or {}
            views.append({"owner": owner_of(a, name_on_a)[0], "dist_to_helper": round(sc.dist(on_h["pos"], ph)) if on_h.get("pos") else None,
                          **{k: on_h.get(k) for k in ("move", "speed", "aiDisabled", "dead", "controller")}})
        helper.exec("watch stop")
        hits = len(helper.state(sections=["watch"])["watch"]["health"]["hits"])
        return {"enemy": en["name"], "owner_to_helper_s": moved, "helper_hits": hits, "helper_view": views}
    return {"why": "no enemy to test"}


def _place_a(a, b, cur, pb):
    """A (the helper) 3 m from the enemy B fights, on B's ground."""
    return place_b_beside(b, a, cur["pos"], pb)


def _downed(a, b, host_case):
    time.sleep(15)   # past the join's enemy respawns and item catch-up, which run in the first seconds of a pair
    for g in (a, b):
        g.exec("kd party down 1")
        g.exec("kd party hold 0")
    try:
        c = (_downed_case(a, b, a, b, "A", "B", "P1", "P2") if host_case
             else _downed_case(a, b, b, a, "B", "A", "P2", "P1"))
    finally:
        for g in (a, b):
            g.exec("kd god 0")
    res = {"case": c}
    if "why" in c:
        return None, res
    res["checks"] = {"enemy_leaves_downed_within_3s": c["owner_to_helper_s"] is not None and c["owner_to_helper_s"] <= 3.0,
                     "helper_takes_hits": c["helper_hits"] > 0}
    return all(res["checks"].values()), res


def downed_host_retarget(a, b):
    """The host goes down while an enemy fights it: the enemy goes for the client at once.
    (Play test 1.6.4: "if I'm downed the enemy that was on me just sits there".)"""
    return _downed(a, b, True)


def downed_client_retarget(a, b):
    """The same with the client down and the host standing."""
    return _downed(a, b, False)


LIFT, LEVERS = "IntroDungerLift_BP_2", ("AQR_ElevatorLever9_2", "AQR_ElevatorLever10_5", "AQR_ElevatorLever11_8")


def _lift(g):
    sc = _sc()
    try:
        return sc.props(g, LIFT, "Running", "ActivelyMoving", "AtEnd", "CurrentPathIndex")
    except Exception as e:
        return {"error": str(e)}


def lift_ride(a, b, watch_s=40):
    """A pulls the lever of the first lift; B stands next to it. B's copy of the lift rides too (it is seen moving,
    and reaches the end within a few seconds of A's), instead of jumping to the end at once.
    (Play test 1.6.4: "for the player that didn't pull the lever it just teleports them to the end".)"""
    sc = _sc()
    for g in (a, b):
        g.exec("kd god 1")
    before = {"A": _lift(a), "B": _lift(b)}
    res = {"before": before}
    pulled = None
    for lever in LEVERS:
        a.exec(f"tpto {lever} 150")
        b.exec(f"tpto {lever} 250")
        time.sleep(3)
        mark = {"A": len(sc.kd_log_all("A")), "B": len(sc.kd_log_all("B"))}
        a.exec(f"interact {lever}")
        t0 = time.time()
        samples = []
        while time.time() - t0 < watch_s:
            la, lb = _lift(a), _lift(b)
            samples.append((round(time.time() - t0, 1), la.get("ActivelyMoving"), la.get("AtEnd"),
                            lb.get("ActivelyMoving"), lb.get("AtEnd")))
            if len(samples) > 6 and not la.get("ActivelyMoving") and not lb.get("ActivelyMoving") and \
                    any(s[1] for s in samples):
                break
            time.sleep(0.5)
        if any(s[1] for s in samples):
            pulled = lever
            res["samples"] = samples
            res["logs"] = {n: [l.split("] ", 2)[-1] for l in sc.kd_log_all(n)[mark[n]:]
                               if "TryPullLever" in l or "lift " in l][:8] for n in ("A", "B")}
            break
    for g in (a, b):
        g.exec("kd god 0")
    res["lever"] = pulled
    if not pulled:
        return None, {"why": "no lever moved the lift on A", **res}
    s = res["samples"]
    a_end = next((t for t, am, ae, bm, be in s if ae != s[0][2] and not am), None)
    b_end = next((t for t, am, ae, bm, be in s if be != s[0][4] and not bm), None)
    res["a_end_s"], res["b_end_s"] = a_end, b_end
    res["checks"] = {
        "b_lift_rides": any(bm for _, _, _, bm, _ in s),
        "b_ends_with_a": a_end is not None and b_end is not None and abs(a_end - b_end) <= 4.0,
    }
    return all(res["checks"].values()), res


SCENARIOS = {"remote_kill_bars": remote_kill_bars, "aggro_to_hitter": aggro_to_hitter,
             "downed_host_retarget": downed_host_retarget, "downed_client_retarget": downed_client_retarget,
             "lift_ride": lift_ride}
SUITE = [["remote_kill_bars"], ["aggro_to_hitter"], ["downed_host_retarget"], ["downed_client_retarget"], ["lift_ride"]]
