"""Two-copy rig for Knights' Domain tests. Run with: py -3 -I runner/rig.py <command>

  boot <A|B> [--host | --join] [--slot N]   launch a copy with the dev mod and the bridge, start a new game
  pair [--lag]                               boot A as host and B as client (UDP loopback) and print both states;
                                             --lag puts the hub's chaos relay between them (see LAG below)
  state <A|B> [section ...]                  print the bridge state of a running copy
  exec <A|B> <line>                          run a bridge command (newgame, tp, call, kd <mod dev command>, ...)
  stop                                       kill every lab copy

Copy A uses bridge port 27990 and mod profile 1 (host). Copy B uses 27991 and profile 2 (client).

LAG: the Co-op Anything hub's tools/chaos relay (clone the hub next to this repo, or set QD_HUB to it) listens in
front of the host's UDP port and B joins through it. Each direction gets 25 +/- 25 ms and 2 % loss, so a round trip
takes 50 to 150 ms. The relay's info and packet log go to results/chaos/ (ignored by git).
"""
import subprocess
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

HUB = os.environ.get("QD_HUB") or os.path.join(os.path.dirname(LAB), "coop-anything")
CHAOS = os.path.join(HUB, "tools", "chaos", "chaos.py")
CHAOS_DIR = os.path.join(LAB, "results", "chaos")
CHAOS_INFO = os.path.join(CHAOS_DIR, "info.json")
LAG = {"latency_ms": 25, "jitter_ms": 25, "loss": 0.02}   # per direction: 50 to 150 ms round trip, 2 % loss
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


def mod_args(name, role, join_port=None):
    args = ["-qdcoop-transport=udp", f"-qdcoop-profile={PROFILES[name]}", f"-qdbridge-port={PORTS[name]}"]
    if role == "host":
        args += ["-qdcoop-autohost", f"-qdcoop-udp-port={KD_UDP_PORT}"]
    elif role == "join":
        args += [f"-qdcoop-autojoin=127.0.0.1:{join_port or KD_UDP_PORT}"]
    return args


def boot(name, role=None, slot=0, load=False, join_port=None):
    if not load:
        # a new game in a used slot keeps parts of the old world: start every test from an empty save folder
        # (the copy's own Saved, never the owner's)
        saves = os.path.join(qdlab.COPIES, name, "QueensDomain", "Saved", "SaveGames")
        if os.path.isdir(saves):
            shutil.rmtree(saves)
    pid = qdlab.launch(name, mod_args(name, role, join_port))
    log(f"{name}: launched pid {pid}")
    time.sleep(8)
    dll = os.path.join(qdlab.COPIES, name, "qdbridge.dll")   # inject a copy so the build output never stays locked
    shutil.copyfile(BRIDGE_DLL, dll)
    qdlab.inject(pid, dll)
    g = connect(name)
    wait_until(lambda: g.eval("title") is True, 120, what=f"{name} title screen")
    time.sleep(4)   # the title screen must settle before a new game (see games/queens-domain NOTES in the hub)
    g.exec(f"{'loadslot' if load else 'newgame'} {slot}")
    log(f"{name}: {'loaded' if load else 'new game in'} slot {slot}")
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


def chaos_call(method, params="{}"):
    info = json.load(open(CHAOS_INFO, encoding="utf-8"))
    out = subprocess.run([sys.executable, "-I", CHAOS, "call", "--rpc-port", str(info["rpc"]), method, params],
                         capture_output=True, text=True, timeout=20)
    if out.returncode:
        raise RuntimeError(f"chaos {method}: {out.stderr.strip()[-300:]}")
    return json.loads(out.stdout)


def start_relay():
    """Start the chaos relay in front of the host's UDP port with the LAG settings; returns the port B joins."""
    if not os.path.isfile(CHAOS):
        sys.exit(f"no chaos relay at {CHAOS}: clone https://github.com/cranebeaker/coop-anything next to this repo "
                 "or set QD_HUB")
    stop_relay()
    os.makedirs(CHAOS_DIR, exist_ok=True)
    subprocess.Popen([sys.executable, "-I", CHAOS, "relay", "--target", f"127.0.0.1:{KD_UDP_PORT}",
                      "--profile", "lan", "--info-file", CHAOS_INFO, "--log", os.path.join(CHAOS_DIR, "run.jsonl")],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    wait_until(lambda: os.path.isfile(CHAOS_INFO), 20, step=0.5, what="chaos relay")
    chaos_call("set_params", json.dumps({"params": LAG}))
    port = json.load(open(CHAOS_INFO, encoding="utf-8"))["listen"]
    log(f"chaos relay on udp {port} -> {KD_UDP_PORT}: {LAG}")
    return port


def stop_relay():
    if os.path.isfile(CHAOS_INFO):
        try:
            chaos_call("shutdown")
        except Exception:
            pass
        os.remove(CHAOS_INFO)
        time.sleep(1)


def pair(lag=False):
    a = boot("A", "host")
    b = boot("B", "join", join_port=start_relay() if lag else None)
    time.sleep(10)
    return a, b


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
        stop_relay()
    elif cmd == "boot":
        name = argv[1]
        role = "host" if "--host" in argv else "join" if "--join" in argv else None
        slot = int(argv[argv.index("--slot") + 1]) if "--slot" in argv else 0
        g = boot(name, role, slot)
        print(json.dumps(g.state(sections=["player"]), indent=1))
    elif cmd == "pair":
        a, b = pair(lag="--lag" in argv)
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
