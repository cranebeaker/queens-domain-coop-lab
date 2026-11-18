"""Index the game's decompiled Blueprints by what matters for co-op.

  py -3 -I tools/bpindex.py <decompiled dir> <out.md>

<decompiled dir> is the output of `kismet-analyzer gen-cfg-tree` (one .txt per asset, see README "Reading the game's
Blueprints"). The index lists, per Blueprint, the calls that make it single-player or touch shared state:

  player0   asks for "the player" (GetPlayerCharacter / Pawn / Controller / CameraManager with index 0): with two
            players this means "the local player" on each machine, so triggers, arenas and boss targeting can disagree
  save      writes the save (savable actor states, named data, drops, inventory, kills, checkpoints)
  pause     pauses the game or the world (menus, rest, dialogue)
  cutscene  plays a level sequence, changes the view target or blocks input
  stream    loads or unloads levels
  spawn     spawns actors at run time
  random    rolls random numbers (results differ per machine unless the roll is shared)

The output stays local: it is built from your own copy of the game and is not part of this repository.
"""
import os
import re
import sys
from collections import defaultdict

CATEGORIES = {
    "player0": [r"GameplayStatics->GetPlayerCharacter\b", r"GameplayStatics->GetPlayerPawn\b",
                r"GameplayStatics->GetPlayerController\b", r"GameplayStatics->GetPlayerCameraManager\b"],
    "save": [r"Save_Data_Index_to_World_Save", r"SaveNamedData", r"SaveDropData", r"DeleteDropData",
             r"AddItemToInventory", r"TryConsumingItemFromInventory", r"RegisterEnemyKill", r"SaveGameToDisk",
             r"GrantGuideToPlayer", r"FULLY_Delete_Item_From_Inventory"],
    "pause": [r"SetGamePaused", r"SetGlobalTimeDilation", r"SetCustomTimeDilation"],
    "cutscene": [r"LevelSequencePlayer", r"CreateLevelSequencePlayer", r"SetViewTargetWithBlend", r"DisableInput",
                 r"CutsceneManager", r"SetCinematicMode"],
    "stream": [r"LoadStreamLevel", r"UnloadStreamLevel", r"OpenLevel", r"LevelStreaming"],
    "spawn": [r"BeginDeferredActorSpawnFromClass", r"SpawnActor", r"TrySpawningItem", r"SpawnDropActor"],
    "random": [r"KismetMathLibrary->Random", r"RandomFloat", r"RandomInteger", r"RandomBool", r"RollItemDrop"],
}
COMPILED = {k: [re.compile(p) for p in v] for k, v in CATEGORIES.items()}
FUNC = re.compile(r"^FunctionExport (\S+)")


def scan(path):
    """{category: {function: count}} for one decompiled asset."""
    out = defaultdict(lambda: defaultdict(int))
    fn = "?"
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = FUNC.match(line)
            if m:
                fn = m.group(1)
                continue
            for cat, pats in COMPILED.items():
                if any(p.search(line) for p in pats):
                    out[cat][fn] += 1
    return out


def main(src, dst):
    rows = []
    for root, _, files in os.walk(src):
        for name in files:
            if not name.endswith(".txt"):
                continue
            path = os.path.join(root, name)
            found = scan(path)
            if found:
                rows.append((os.path.relpath(path, src).replace("\\", "/")[:-4], found))
    rows.sort()
    totals = defaultdict(int)
    for _, found in rows:
        for cat in found:
            totals[cat] += 1
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write("# Blueprint co-op index\n\n")
        f.write("Blueprints per category: " + ", ".join(f"{k} {totals[k]}" for k in CATEGORIES) + "\n\n")
        for cat in CATEGORIES:
            f.write(f"## {cat}\n\n")
            for asset, found in rows:
                if cat in found:
                    fns = ", ".join(f"{fn} x{n}" if n > 1 else fn for fn, n in sorted(found[cat].items()))
                    f.write(f"- `{asset}`: {fns}\n")
            f.write("\n")
    print(f"{len(rows)} Blueprints indexed -> {dst}")
    for k in CATEGORIES:
        print(f"  {k:9s} {totals[k]}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
