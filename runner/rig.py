"""Two-copy rig for Knights' Domain tests. Run with: py -3 -I runner/rig.py <command>

  boot <A|B> [--host | --join] [--slot N]   launch a copy with the dev mod and the bridge, start a new game
  pair                                       boot A as host and B as client (UDP loopback) and print both states
  state <A|B> [section ...]                  print the bridge state of a running copy
  exec <A|B> <line>                          run a bridge command (newgame, tp, call, kd <mod dev command>, ...)
  stop                                       kill every lab copy

Copy A uses bridge port 27990 and mod profile 1 (host). Copy B uses 27991 and profile 2 (client).
"""
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LAB = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(LAB, "third_party", "coopbridge"))

import qdlab  # noqa: E402
from bridge_client import Bridge  # noqa: E402

BRIDGE_DLL = os.path.join(LAB, "bridge", "build", "Release", "qdbridge.dll")
PORTS = {"A": 27990, "B": 27991, "C": 27992, "D": 27993}
PROFILES = {"A": 1, "B": 2, "C": 3, "D": 4}
KD_UDP_PORT = 27777


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def connect(name, wait_s=60):
    return Bridge.connect(f"tcp://127.0.0.1:{PORTS[name]}", wait_s=wait_s)


def wait_until(fn, timeout, step=1.0, what="condition"):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = fn()
            if last:
                return last
        except Exception as e:  # game loading or bridge busy: keep polling
            last = e
        time.sleep(step)
    raise TimeoutError(f"timed out waiting for {what} (last: {last})")


def mod_args(name, role):
    args = ["-qdcoop-transport=udp", f"-qdcoop-profile={PROFILES[name]}", f"-qdbridge-port={PORTS[name]}"]
    if role == "host":
        args += ["-qdcoop-autohost", f"-qdcoop-udp-port={KD_UDP_PORT}"]
    elif role == "join":
        args += [f"-qdcoop-autojoin=127.0.0.1:{KD_UDP_PORT}"]
    return args


def boot(name, role=None, slot=0):
    pid = qdlab.launch(name, mod_args(name, role))
    log(f"{name}: launched pid {pid}")
    time.sleep(8)
    dll = os.path.join(qdlab.COPIES, name, "qdbridge.dll")   # inject a copy so the build output never stays locked
    shutil.copyfile(BRIDGE_DLL, dll)
    qdlab.inject(pid, dll)
    g = connect(name)
    wait_until(lambda: g.eval("title") is True, 120, what=f"{name} title screen")
    time.sleep(4)   # the title screen must settle before a new game (see games/queens-domain NOTES in the hub)
    g.exec(f"newgame {slot}")
    log(f"{name}: new game in slot {slot}")
    g.close()
    g = connect(name, wait_s=120)
    wait_until(lambda: g.eval("inworld") is True, 240, step=2, what=f"{name} in world")
    for _ in range(20):   # the beach intro can start a few seconds after the world appears
        try:
            g.exec("skipintro")
        except Exception:
            pass
        time.sleep(1)
    log(f"{name}: in world {g.eval('world')}")
    return g


def kd_log(name, lines=30):
    p = os.path.join(qdlab.mod_saved(name, PROFILES[name]), "KnightsDomain.log")
    if not os.path.isfile(p):
        return []
    with open(p, encoding="utf-8", errors="replace") as f:
        return f.readlines()[-lines:]


def main(argv):
    if not argv:
        sys.exit(__doc__)
    cmd = argv[0]
    if cmd == "stop":
        qdlab.stop()
    elif cmd == "boot":
        name = argv[1]
        role = "host" if "--host" in argv else "join" if "--join" in argv else None
        slot = int(argv[argv.index("--slot") + 1]) if "--slot" in argv else 0
        g = boot(name, role, slot)
        print(json.dumps(g.state(sections=["player"]), indent=1))
    elif cmd == "pair":
        a = boot("A", "host")
        b = boot("B", "join")
        time.sleep(10)
        for n, g in (("A", a), ("B", b)):
            print(n, json.dumps(g.state(sections=["player"])))
            print("".join(kd_log(n, 12)))
    elif cmd == "state":
        with connect(argv[1]) as g:
            print(json.dumps(g.state(sections=argv[2:]), indent=1))
    elif cmd == "exec":
        with connect(argv[1]) as g:
            print(g.exec(" ".join(argv[2:])))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
