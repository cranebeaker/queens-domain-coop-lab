"""Party scenarios: down instead of dead and revive, team wipe, party rest, party save, trading.

The mod's dev console drives what a player would do with keys and the F5 menu ("kd party ..."): `hold 1` acts as if
Interact were held, `trade ...` presses the trade page's buttons. Each check reads the mod's own state line
("party state {...}" in KnightsDomain.log) and the game's state through the bridge.
"""
import json
import time

import rig

HERB = "/Game/Items/Consumables/BerserkFruit/BerserkFruit.BerserkFruit"
PUNCH = "/Game/Items/Consumables/B33Punch/B33Punch.B33Punch"
REST_BTN = "BndEvt__SaveMenu_RestButton_K2Node_ComponentBoundEvent_0_CommonButtonBaseClicked__DelegateSignature"
SAVE_BTN = "BndEvt__SavedPopup_SaveButton_K2Node_ComponentBoundEvent_3_CommonButtonBaseClicked__DelegateSignature"
DIE_CONTINUE = "BndEvt__DieUI_ContinueButton_K2Node_ComponentBoundEvent_4_CommonButtonBaseClicked__DelegateSignature"
CHECKPOINT = "Checkpoint17"
FAR_SPOT = (3000, -2000, 500)


def _sc():
    import scenarios   # the main module (circular at import time)
    return scenarios


def safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def pstate(g, name):
    """The mod's party state on `name` (asks for it, then reads the newest state line of the log)."""
    g.exec("kd party state")
    time.sleep(0.6)
    for line in reversed(_sc().kd_log_all(name)):
        if "party state {" in line:
            return json.loads(line.split("party state ", 1)[1].strip())
    return {}


def player(g):
    return safe(lambda: g.state(sections=["player"])["player"], {})


def wait_for(fn, timeout, step=0.5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = safe(fn)
        if v:
            return round(time.time() - t0, 1)
        time.sleep(step)
    return None


def side_by_side(a, b, dx=150):
    pa = _sc().local_pos(a)
    b.exec(f"tp {pa[0] + dx} {pa[1]} {pa[2] + 30} 0")
    time.sleep(3)


def clear_near(a, radius=3000):
    """Kill the enemies within `radius` of A: a revive stops when the reviver takes a hit (god mode only keeps the
    health from running out), and roaming enemies reach this spot in some runs."""
    sc = _sc()
    pa = sc.local_pos(a)
    for _ in range(3):
        near = [e for e in sc.enemies(a) if sc.dist(e["pos"], pa) < radius]
        if not near:
            return 0
        for e in near:
            safe(lambda: a.exec(f"kd ehit {e['name']} 4000"))
        time.sleep(2)
    return len([e for e in sc.enemies(a) if sc.dist(e["pos"], pa) < radius])


def revive(a, b):
    """A runs out of health next to B: A goes down instead of dying (B sees A lying, A takes no damage and cannot
    act), B holds Interact next to A and A stands up at 35 % health. Then the team wipe: A goes down again and B dies
    for real (nobody up to revive B); with nobody left to revive A, A dies too, and both come back at their save."""
    sc = _sc()
    for g in (a, b):
        g.exec("kd party down 1")
        g.exec("kd party hold 0")
        g.exec("kd god 0")
    b.exec("kd god 1")
    a.exec(f"tp {FAR_SPOT[0]} {FAR_SPOT[1]} {FAR_SPOT[2]} 0")
    time.sleep(3)
    side_by_side(a, b)
    res = {"enemies_left_near": clear_near(a)}
    a.exec("die")
    down_s = wait_for(lambda: pstate(a, "A").get("localDowned"), 10)
    res["a_down_after_s"] = down_s
    if down_s is None:
        res["a_state"] = pstate(a, "A")
        res["a_player"] = player(a)
        return (False if not player(a).get("dead") else None), {"why": "A did not go down", **res}
    time.sleep(2)
    sa, sb = pstate(a, "A"), pstate(b, "B")
    res["a_dead_flag_while_down"] = sa.get("dead")
    res["b_sees_a_down"] = sb.get("downed", [False])[0]
    pups = safe(lambda: sc.remote_players(b), [])
    res["b_puppet_anims"] = sorted({q.get("anim", "") for q in pups if not q.get("hidden")})
    res["b_prompt_slot"] = sb.get("reviveCandidate")
    # B revives: hold Interact for the revive time
    b.exec("kd party hold 1")
    up_s = wait_for(lambda: not pstate(a, "A").get("localDowned"), 12)
    b.exec("kd party hold 0")
    time.sleep(1.5)
    sa, sb = pstate(a, "A"), pstate(b, "B")
    res["revived_after_s"] = up_s
    res["a_health_after_revive"] = sa.get("health")
    res["a_dead_after_revive"] = sa.get("dead")
    res["b_sees_a_up"] = not sb.get("downed", [True])[0]
    ok_revive = (sa.get("localDowned") is False and not sa.get("dead") and (sa.get("health") or 0) > 0 and
                 res["b_sees_a_down"] and res["b_sees_a_up"] and res["a_dead_flag_while_down"] is False and
                 up_s is not None and up_s >= 2.0)

    # team wipe
    a.exec("die")
    res["a_down_again_s"] = wait_for(lambda: pstate(a, "A").get("localDowned"), 10)
    b.exec("kd god 0")
    b.exec("die")
    t0 = time.time()
    a_dead_s = b_dead_s = None
    a_cont = b_cont = False
    a_back = b_back = None
    while time.time() - t0 < 150:
        t = round(time.time() - t0, 1)
        for g, name in ((a, "A"), (b, "B")):
            ia = safe(lambda: g.eval("inworld"), False)
            pl = player(g) if ia else {}
            dead = (not ia) or bool(pl.get("dead"))
            if name == "A" and dead and a_dead_s is None:
                a_dead_s = t
            if name == "B" and dead and b_dead_s is None:
                b_dead_s = t
            if dead and "DieUI_C" in str(safe(lambda: g.exec("widgets DieUI_C"), "")):
                if safe(lambda: g.exec(f"uicall DieUI_C {DIE_CONTINUE}"), None) is not None:
                    if name == "A":
                        a_cont = True
                    else:
                        b_cont = True
            back = ia and pl.get("present") and not pl.get("dead") and (a_dead_s if name == "A" else b_dead_s) is not None
            if name == "A" and back and a_back is None:
                a_back = t
            if name == "B" and back and b_back is None:
                b_back = t
        if a_back is not None and b_back is not None:
            break
        time.sleep(1)
    res["wipe"] = {"a_dead_s": a_dead_s, "b_dead_s": b_dead_s, "a_continue": a_cont, "b_continue": b_cont,
                   "a_back_s": a_back, "b_back_s": b_back}
    time.sleep(5)
    res["after_wipe"] = {"A": pstate(a, "A"), "B": pstate(b, "B")}
    ok_wipe = (a_dead_s is not None and b_dead_s is not None and a_back is not None and b_back is not None and
               not res["after_wipe"]["A"].get("localDowned") and not res["after_wipe"]["B"].get("localDowned"))
    res["revive_ok"], res["wipe_ok"] = ok_revive, ok_wipe
    return ok_revive and ok_wipe, res


def _menu_open(g):
    out = str(safe(lambda: g.exec("widgets SaveMenu_C")["output"], ""))
    return "inviewport=0" not in out


def _party_checkpoint(a, b, button, label):
    sc = _sc()
    for g in (a, b):
        g.exec("kd god 1")
    b.exec(f"tp {FAR_SPOT[0]} {FAR_SPOT[1]} {FAR_SPOT[2]} 0")
    time.sleep(4)
    log_from = len(sc.kd_log_all("B"))
    a.exec(f"tpto {CHECKPOINT} 200")
    time.sleep(2)
    a.exec(f"interact {CHECKPOINT}")
    time.sleep(3)
    cp = [c for c in a.state(sections=["checkpoints"])["checkpoints"] if c["name"] == CHECKPOINT]
    cp_pos = cp[0]["pos"] if cp else sc.local_pos(a)
    b_start = sc.local_pos(b)
    res = {"checkpoint": CHECKPOINT, "b_start_cm_from_checkpoint": round(sc.dist(b_start, cp_pos))}
    a.exec(f"uicall SaveMenu_C {button}")
    # B is taken to the checkpoint and goes through its menu
    t0 = time.time()
    done_line = None
    while time.time() - t0 < 70:
        lines = sc.kd_log_all("B")[log_from:]
        hit = [l for l in lines if f"party {label} done" in l]
        miss = [l for l in lines if "not followed" in l]
        if hit or miss:
            done_line = (hit or miss)[0].split("] ", 2)[-1].strip()
            break
        time.sleep(1)
    res["b_finished_after_s"] = round(time.time() - t0, 1)
    res["b_log"] = [l.split("] ", 2)[-1].strip()[:160] for l in sc.kd_log_all("B")[log_from:] if "[party]" in l][:12]
    time.sleep(3)
    b_end = sc.local_pos(b)
    res["b_end_cm_from_checkpoint"] = round(sc.dist(b_end, cp_pos))
    res["b_menu_left_open"] = _menu_open(b)
    res["b_state"] = pstate(b, "B")
    # A leaves its own menu the way a player would
    safe(lambda: a.exec("uicall SaveMenu_C Close"))
    # B has its game back: no menu, not resting, no UI cursor
    pb = player(b)
    res["b_player"] = {k: pb.get(k) for k in ("resting", "cursor", "dead")}
    ok = (done_line is not None and f"party {label} done" in done_line and res["b_end_cm_from_checkpoint"] < 800 and
          not res["b_menu_left_open"] and res["b_state"].get("driving") is False and res["b_state"].get("rest") == 0
          and not pb.get("resting") and not pb.get("cursor"))
    res["done_line"] = done_line
    return ok, res


def party_rest(a, b):
    """A rests at a checkpoint (the menu's Rest): B, far away, is taken there, rests through the same menu, and gets
    its controls back."""
    return _party_checkpoint(a, b, REST_BTN, "rest")


def party_save(a, b):
    """A saves at a checkpoint (the menu's Save): B is taken there and saves through the same menu."""
    return _party_checkpoint(a, b, SAVE_BTN, "save")


def _count(st, path):
    return int(st.get("inv", {}).get(path, 0))


def trade(a, b):
    """A and B trade consumables: A offers 3 fruit, B offers 1 punch. B changes its offer after A confirmed, which
    takes A's confirmation back (nothing changes hands); after both confirm the final offers, the items move once."""
    for g in (a, b):
        g.exec("kd god 1")
        g.exec("kd party trade cancel")
    a.exec(f"kd give {HERB} 5")
    b.exec(f"kd give {PUNCH} 2")
    time.sleep(1)
    side_by_side(a, b, 200)
    sa0, sb0 = pstate(a, "A"), pstate(b, "B")
    before = {"A": {"herb": _count(sa0, HERB), "punch": _count(sa0, PUNCH)},
              "B": {"herb": _count(sb0, HERB), "punch": _count(sb0, PUNCH)}}
    res = {"before": before}
    if before["A"]["herb"] < 3 or before["B"]["punch"] < 1:
        return None, {"why": "the test items were not given", **res}
    a.exec("kd party trade req 1")
    time.sleep(1.5)
    res["b_incoming"] = pstate(b, "B").get("trade")
    b.exec("kd party trade accept")
    time.sleep(1.5)
    res["open"] = {"A": pstate(a, "A").get("trade"), "B": pstate(b, "B").get("trade")}
    a.exec(f"kd party trade offer {HERB} 3")
    b.exec(f"kd party trade offer {PUNCH} 2")
    time.sleep(1.5)
    a.exec("kd party trade confirm 1")
    time.sleep(1)
    b.exec(f"kd party trade offer {PUNCH} 1")   # B changes its offer: A's confirmation is taken back
    time.sleep(1.5)
    mid_a = pstate(a, "A")
    res["a_confirm_after_b_changed"] = mid_a.get("myConfirm")
    b.exec("kd party trade confirm 1")
    time.sleep(2)
    res["moved_too_early"] = _count(pstate(b, "B"), HERB) != before["B"]["herb"]
    a.exec("kd party trade confirm 1")
    time.sleep(4)
    sa1, sb1 = pstate(a, "A"), pstate(b, "B")
    after = {"A": {"herb": _count(sa1, HERB), "punch": _count(sa1, PUNCH)},
             "B": {"herb": _count(sb1, HERB), "punch": _count(sb1, PUNCH)}}
    res["after"] = after
    res["trade_closed"] = {"A": sa1.get("trade"), "B": sb1.get("trade")}
    ok = (res["b_incoming"] == 2 and res["open"] == {"A": 3, "B": 3} and res["a_confirm_after_b_changed"] is False and
          not res["moved_too_early"] and
          after["A"]["herb"] == before["A"]["herb"] - 3 and after["A"]["punch"] == before["A"]["punch"] + 1 and
          after["B"]["herb"] == before["B"]["herb"] + 3 and after["B"]["punch"] == before["B"]["punch"] - 1 and
          res["trade_closed"] == {"A": 0, "B": 0})
    return ok, res


SCENARIOS = {"revive": revive, "party_rest": party_rest, "party_save": party_save, "trade": trade}
# revive ends in a team wipe (both reload): it goes last in its pair
SUITE = [["trade", "party_rest", "party_save"], ["revive"]]
