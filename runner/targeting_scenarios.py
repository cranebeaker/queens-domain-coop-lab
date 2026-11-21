"""Targeting scenarios: enemies split their attention between the players, and nobody can hide behind a teammate.

An ordinary enemy fights the player whose machine runs it (its owner), so "who does it attack" is "who owns it". The
mod's `kd elist` lines on the host give every enemy's owner; the bridge's `watch` counts the hits each player takes.
Bosses run on the host and pick a target by the damage each player dealt ("target -> P2" in the host's log).
"""
import time

import rig

GROUP_SPOT = (3000, -2000, 500)   # Ruins: a field of small enemies (the drops_and_xp spot)


def _sc():
    import scenarios   # the main module (circular at import time)
    return scenarios


def safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def owners_near(a, center, radius):
    """Owner slot ("P1", "P2") of every awake, living ordinary enemy within `radius` of `center`, from the host."""
    sc = _sc()
    mark = len(sc.kd_log_all("A"))
    a.exec("kd elist _C")
    time.sleep(1.2)
    rows = []
    for line in sc.kd_log_all("A")[mark:]:
        if "[edbg] E " not in line:
            continue
        f = dict(x.split("=", 1) for x in line.split() if "=" in x)
        if f.get("dead") == "1" or f.get("boss") == "1" or f.get("trap") == "1":
            continue
        try:
            pos = [float(v) for v in f["pos"].strip("()").split(",")]
        except Exception:
            continue
        if sc.dist(pos, center) <= radius:
            rows.append(f.get("owner"))
    return rows


def enemy_split(a, b, radius=2500, watch_s=40):
    """Both players stand side by side among a group of small enemies and do not attack. The group splits between
    them: each owns (is attacked by) part of it, the two shares differ by at most one, and both players take hits.
    (Before: every enemy started on the host and "clearly closer" never fired for a player 1.5 m away, so the host
    took the whole group and the client none of it.)"""
    sc = _sc()
    for g in (a, b):
        g.exec("kd god 1")
    a.exec("tp {} {} {} 0".format(*GROUP_SPOT))
    time.sleep(3)
    pa = sc.local_pos(a)
    b.exec(f"tp {pa[0] + 150} {pa[1]} {pa[2] + 30} 0")
    time.sleep(2)
    for g in (a, b):
        g.exec("watch start 3000")
    samples = []
    t0 = time.time()
    while time.time() - t0 < watch_s:
        pa = sc.local_pos(a)
        rows = owners_near(a, pa, radius)
        samples.append({"t": round(time.time() - t0), "P1": rows.count("P1"), "P2": rows.count("P2")})
        time.sleep(4)
    for g in (a, b):
        g.exec("watch stop")
    hits = {n: len(g.state(sections=["watch"])["watch"]["health"]["hits"]) for n, g in (("A", a), ("B", b))}
    for g in (a, b):
        g.exec("kd god 0")
    last = samples[-1]
    res = {"samples": samples, "hits_taken": hits}
    if last["P1"] + last["P2"] < 2:
        return None, {"why": "fewer than two awake enemies near the players", **res}
    # the split settles within a few seconds and then holds: the last three samples are all balanced
    tail = samples[-3:]
    balanced = all(s["P1"] >= 1 and s["P2"] >= 1 and abs(s["P1"] - s["P2"]) <= 1 for s in tail)
    res["checks"] = {"split_holds": balanced, "both_take_hits": hits["A"] > 0 and hits["B"] > 0}
    return all(res["checks"].values()), res


def client_pulls_aggro(a, b, watch_s=25):
    """A stands next to a small enemy and does nothing; B hits it from 8 m. The enemy turns on B (B owns it and B
    takes its hits): a player who attacks cannot stay safe behind one who does not."""
    sc = _sc()
    for g in (a, b):
        g.exec("kd god 1")
    a.exec("tp {} {} {} 0".format(*GROUP_SPOT))
    time.sleep(3)
    pa = sc.local_pos(a)
    cands = [e for e in sc.enemies(a) if e.get("hp") and e["hp"] < 200 and sc.dist(e["pos"], pa) < 5000]
    if not cands:
        return None, {"why": "no small enemy near A"}
    cands.sort(key=lambda e: sc.dist(e["pos"], pa))
    en = cands[0]
    a.exec(f"kd egoto {en['name']} 150")
    time.sleep(1.5)
    ep = en["pos"]
    pa = sc.local_pos(a)
    dx, dy = ep[0] - pa[0], ep[1] - pa[1]
    L = (dx * dx + dy * dy) ** 0.5 or 1.0
    b.exec(f"tp {ep[0] + dx / L * 800} {ep[1] + dy / L * 800} {ep[2] + 60} 0")   # 8 m past the enemy, away from A
    time.sleep(1.5)
    on_b = sc.match_enemy(b, en, 600)
    b.exec("watch start 3000")
    t0 = time.time()
    owner_seen = []
    while time.time() - t0 < watch_s:
        if on_b:
            b.exec(f"kd ehit {on_b['name']} 1")
        a.exec(f"kd elist {en['name']}")
        time.sleep(0.8)
        for line in reversed(sc.kd_log_all("A")[-20:]):
            if "[edbg] E " in line:
                f = dict(x.split("=", 1) for x in line.split() if "=" in x)
                owner_seen.append(f.get("owner"))
                break
        time.sleep(1.2)
    b.exec("watch stop")
    hits_b = len(b.state(sections=["watch"])["watch"]["health"]["hits"])
    for g in (a, b):
        g.exec("kd god 0")
    res = {"enemy": en["class"], "b_copy_found": on_b is not None, "owners": owner_seen, "b_hits_taken": hits_b}
    if not on_b:
        return None, {"why": "B has no copy of the enemy", **res}
    turned = "P2" in owner_seen and owner_seen[-1] == "P2"
    res["checks"] = {"enemy_turns_to_b": turned, "b_takes_hits": hits_b > 0}
    return all(res["checks"].values()), res


def boss_threat(a, b, fight_s=40):
    """The Bridge Lord fights A first (A woke it and pokes it); then B deals far more damage. The boss turns to B
    ("target -> P2" on the host) and B takes its hits: the player doing the damage draws the boss."""
    sc = _sc()
    sp, cls = "JosamiLord_Spawner_2", "JosamiLord_BP"
    spot = (-52500, -19000, -38450)
    if not sc.boss_scenario_start(a, b, spot, (spot[0] + 150, spot[1], spot[2]), "loc:Castle_Teleporter", sp):
        return None, {"why": "the bridge did not load"}
    a.exec(f"activate {sp}")
    time.sleep(12)
    mark = len(sc.kd_log_all("A"))
    for g in (a, b):
        g.exec(f"kd egoto {cls} 600")
    time.sleep(2)
    b.exec("watch start 4000")
    t0 = time.time()
    while time.time() - t0 < fight_s:
        a.exec(f"kd ehit {cls} 1")
        if time.time() - t0 > 8:
            b.exec(f"kd ehit {cls} 60")
        time.sleep(2)
    b.exec("watch stop")
    hits_b = len(b.state(sections=["watch"])["watch"]["health"]["hits"])
    targets = [l.split("target -> ", 1)[1].split()[0] for l in sc.kd_log_all("A")[mark:]
               if "JosamiLord_BP" in l and "target -> " in l]
    for g in (a, b):
        g.exec("kd god 0")
    res = {"targets": targets, "b_hits_taken": hits_b,
           "checks": {"boss_turns_to_b": bool(targets) and targets[-1] == "P2", "b_takes_hits": hits_b > 0}}
    return all(res["checks"].values()), res


SCENARIOS = {"enemy_split": enemy_split, "client_pulls_aggro": client_pulls_aggro, "boss_threat": boss_threat}
# each one changes who fights whom: one fresh pair each
SUITE = [["enemy_split"], ["client_pulls_aggro"], ["boss_threat"]]
