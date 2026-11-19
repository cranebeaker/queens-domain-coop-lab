"""Where things are: every placed actor of the game's maps with its class and world position.

  py -3 -I tools/maplocs.py [map name filter]

Reads the maps that `runner/setup.py decompile` unpacked into tools/ue/unpacked, converts each with kismet-analyzer's
`to-json` and writes tools/ue/actor_locs.tsv (map, actor, class, x, y, z). Scenarios use it to find a boss's spawner, an
arena door or a checkpoint without walking there. Positions are the actor's root component; components attached to
another actor's component get their parents' offsets added (rotation ignored, which is right for the spawners, bosses
and doors placed in the Ruins maps). Like everything under tools/ue it stays local.
"""
import csv
import json
import os
import subprocess
import sys
import tempfile

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UE = os.path.join(LAB, "tools", "ue")
MAPS = os.path.join(UE, "unpacked", "QueensDomain", "Content", "RuinsLevel", "Maps")
KA = os.path.join(UE, "ka-bin", "kismet-analyzer.exe")
OUT = os.path.join(UE, "actor_locs.tsv")


def prop(data, name):
    for p in data or []:
        if p.get("Name") == name:
            return p
    return None


def vector(p):
    """A Vector struct property -> (x, y, z)."""
    if not p:
        return None
    for v in p.get("Value") or []:
        val = v.get("Value")
        if isinstance(val, dict) and "X" in val:
            return float(val["X"]), float(val["Y"]), float(val["Z"])
    return None


def actors(path):
    env = dict(os.environ)
    env.setdefault("DOTNET_ROLL_FORWARD", "LatestMajor")   # kismet-analyzer targets .NET 9
    fd, tmp = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            subprocess.run([KA, "to-json", path], stdout=f, check=True, env=env)
        with open(tmp, encoding="utf-8-sig") as f:
            d = json.load(f)
    finally:
        os.remove(tmp)
    imps, ex = d["Imports"], d["Exports"]

    def name(i):
        if i < 0:
            return imps[-i - 1]["ObjectName"]
        if i > 0:
            return ex[i - 1]["ObjectName"]
        return None

    def comp_loc(i, depth=0):
        if i <= 0 or i > len(ex) or depth > 8:
            return None
        data = ex[i - 1].get("Data")
        loc = vector(prop(data, "RelativeLocation")) or (0.0, 0.0, 0.0)
        parent = prop(data, "AttachParent")
        if parent and isinstance(parent.get("Value"), int) and parent["Value"] > 0:
            up = comp_loc(parent["Value"], depth + 1)
            if up:
                loc = (loc[0] + up[0], loc[1] + up[1], loc[2] + up[2])
        return loc

    for e in ex:
        root = prop(e.get("Data"), "RootComponent")
        if not root or not isinstance(root.get("Value"), int) or root["Value"] <= 0:
            continue
        loc = comp_loc(root["Value"])
        if loc:
            yield e["ObjectName"], name(e.get("ClassIndex", 0)) or "?", loc


def main(argv):
    want = argv[0] if argv else ""
    rows = []
    for fn in sorted(os.listdir(MAPS)):
        if not fn.endswith(".umap") or want not in fn:
            continue
        n = 0
        for actor, cls, (x, y, z) in actors(os.path.join(MAPS, fn)):
            rows.append((fn[:-5], actor, cls, round(x), round(y), round(z)))
            n += 1
        print(f"{fn}: {n} actors", flush=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(("map", "actor", "class", "x", "y", "z"))
        w.writerows(rows)
    print(f"{len(rows)} actors -> {OUT}")


if __name__ == "__main__":
    main(sys.argv[1:])
