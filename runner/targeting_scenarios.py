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


def enemy_split(a, b, radius=1500, watch_s=40):
    """Both players stand side by side among a group of small enemies and do not attack. The group splits between
    them: each owns (is attacked by) part of it, the two shares differ by at most two (an enemy already fighting
    stays with its player), and both players take hits.
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
    res = {"samples": samples, "hits_taken": hits}
    # the split settles within a few seconds and then holds: every later look with two or more enemies near is shared
    # (a look with one enemy near, the others dead or wandered off, says nothing about sharing)
    judged = [s for s in samples if s["t"] >= 10 and s["P1"] + s["P2"] >= 2]
    if len(judged) < 3:
        return None, {"why": "fewer than two awake enemies near the players most of the time", **res}
    balanced = all(s["P1"] >= 1 and s["P2"] >= 1 and abs(s["P1"] - s["P2"]) <= 2 for s in judged)
    res["checks"] = {"split_holds": balanced, "both_take_hits": hits["A"] > 0 and hits["B"] > 0}
    return all(res["checks"].values()), res


def client_pulls_aggro(a, b, watch_s=25):
    """A stands next to a small enemy and does nothing; B hits it from 3 m (twice A's distance). The enemy turns on B (B owns it and B
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
    b.exec(f"tp {ep[0] + dx / L * 300} {ep[1] + dy / L * 300} {ep[2] + 60} 0")   # 3 m past the enemy, away from A
    time.sleep(1.5)
    on_b = sc.match_enemy(b, en, 600)
    b.exec("watch start 3000")
    t0 = time.time()
    owner_seen = []
    while time.time() - t0 < watch_s:
        if on_b:
            b.exec(f"kd ehit {on_b['name']} 1")
            b.exec(f"kd elist {on_b['name']}")   # B's side of it, for the log
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


def boss_threat(a, b, fight_s=60):
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
    next_go = time.time() + 10
    while time.time() - t0 < fight_s:
        a.exec(f"kd ehit {cls} 1")
        if time.time() - t0 > 8:
            b.exec(f"kd ehit {cls} 60")
        if time.time() >= next_go:   # it strides about the bridge: both stay within its reach, as fighting players do
            for g in (a, b):
                g.exec(f"kd egoto {cls} 600")
            next_go = time.time() + 10
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


def _max_hp(g, name, enemy_name):
    """Max HP of one enemy on `g` (`kd elist <actor name>`: hp=cur/max)."""
    sc = _sc()
    mark = len(sc.kd_log_all(name))
    g.exec(f"kd elist {enemy_name}")
    time.sleep(1.0)
    for line in sc.kd_log_all(name)[mark:]:
        if "[edbg] E " in line and "hp=" in line:
            try:
                return float(line.split("hp=", 1)[1].split()[0].split("/")[1])
            except Exception:
                return None
    return None


def _hits_on(a, b, secs):
    """Health lost per hit by each player while both stand among the group (god mode keeps them alive)."""
    for g in (a, b):
        g.exec("watch start 3000")
    time.sleep(secs)
    out = {}
    for n, g in (("A", a), ("B", b)):
        g.exec("watch stop")
        out[n] = sorted(round(h["amount"], 1) for h in g.state(sections=["watch"])["watch"]["health"]["hits"]
                        if h.get("amount") and h["amount"] > 0)
    return out


def difficulty_hard(a, b, secs=25):
    """The host switches the co-op difficulty from Normal to Hard (`kd difficulty`, as its menu does). Both copies get
    the host's values (HpScale damage x1.25 for two players), an ordinary enemy's max HP goes from x1.12 to x2.00 on
    both, and the damage each player takes per hit grows by 25 % on both. The host's choice is put back at the end."""
    sc = _sc()
    for g in (a, b):
        g.exec("kd god 1")
    a.exec("kd difficulty normal")
    a.exec("tp {} {} {} 0".format(*GROUP_SPOT))
    time.sleep(3)
    pa = sc.local_pos(a)
    b.exec(f"tp {pa[0] + 150} {pa[1]} {pa[2] + 30} 0")
    time.sleep(8)
    res = {}
    try:
        cands = [e for e in sc.enemies(a) if e.get("hp") and e["hp"] < 200 and sc.dist(e["pos"], pa) < 5000]
        if not cands:
            return None, {"why": "no small enemy near A"}
        en = sorted(cands, key=lambda e: sc.dist(e["pos"], pa))[0]
        on_b = sc.match_enemy(b, en, 800)
        before = {"A": _max_hp(a, "A", en["name"]), "B": _max_hp(b, "B", on_b["name"]) if on_b else None}
        normal_hits = _hits_on(a, b, secs)
        mark = {n: len(sc.kd_log_all(n)) for n in ("A", "B")}
        a.exec("kd difficulty hard")
        time.sleep(4)
        logs = {"A": [l.split("] ", 2)[-1] for l in sc.kd_log_all("A")[mark["A"]:] if "HpScale ->" in l],
                "B": [l.split("] ", 2)[-1] for l in sc.kd_log_all("B")[mark["B"]:] if "host HP scale" in l]}
        after = {"A": _max_hp(a, "A", en["name"]), "B": _max_hp(b, "B", on_b["name"]) if on_b else None}
        hard_hits = _hits_on(a, b, secs)
    finally:
        a.exec("kd difficulty normal")
        for g in (a, b):
            g.exec("kd god 0")
    ratio_hp = {n: round(after[n] / before[n], 3) if before[n] and after[n] else None for n in ("A", "B")}
    # the weakest hit of the group's attacks is the same attack before and after: its damage x1.25
    ratio_dmg = {n: round(hard_hits[n][0] / normal_hits[n][0], 3) if normal_hits[n] and hard_hits[n] else None
                 for n in ("A", "B")}
    res = {"enemy": en["class"], "max_hp_before": before, "max_hp_after": after, "hp_ratio": ratio_hp,
           "hits_normal": {n: v[:8] for n, v in normal_hits.items()},
           "hits_hard": {n: v[:8] for n, v in hard_hits.items()}, "damage_ratio": ratio_dmg, "logs": logs}
    want_hp = 2.0 / 1.12
    res["checks"] = {
        "host_sent_hard": any("(hard)" in l and "damage x1.25" in l for l in logs["A"]),
        "client_got_it": any("normal x2.00" in l and "damage x1.25" in l for l in logs["B"]),
        "enemy_hp_doubled_both": all(r is not None and abs(r - want_hp) < 0.05 * want_hp for r in ratio_hp.values()),
        "damage_up_25_both": all(r is not None and 1.15 <= r <= 1.35 for r in ratio_dmg.values()),
    }
    return all(res["checks"].values()), res


SCENARIOS = {"enemy_split": enemy_split, "client_pulls_aggro": client_pulls_aggro, "boss_threat": boss_threat,
             "difficulty_hard": difficulty_hard}
# each one changes who fights whom: one fresh pair each
SUITE = [["enemy_split"], ["client_pulls_aggro"], ["boss_threat"], ["difficulty_hard"]]
