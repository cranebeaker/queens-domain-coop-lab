# Queen's Domain co-op lab

Local test rig for Knights' Domain (the co-op mod by Mangolotis). Two copies of the game run side by side on the
second monitor, A hosts and B joins over UDP loopback, and Python scenarios drive both and compare their state.

This repo holds only our own code: the bridge, the runner and the scenarios. Each person makes their own game copies,
SDK dump and Knights' Domain build on their own PC with `runner/setup.py`. Knights' Domain's licence allows building it
for your own use only, so its code is never changed here and its builds are never committed or shared. What we share
is test results and bug reports. Who does what: [TASKS.md](TASKS.md).

## Setup (once per PC)

Needs Windows, the game on Steam (Steam running), Visual Studio 2022 with C++, CMake 3.25+, git and Python 3.10+.
About 2.5 GB free disk.

```
py -3 -I runner/setup.py all
```

That makes copies A and B of your install, builds Dumper-7 and dumps the game's SDK into `sdk/`, clones
Knights' Domain's `source` branch next to this repo and builds its dev flavour into both copies, and builds the bridge.
Steps can be run one by one (`copies`, `dumper`, `sdk`, `kd`, `bridge`); see `py -3 -I runner/setup.py`. If your
game is not in the default Steam folder, pass its folder: `runner/setup.py all "D:\SteamLibrary\steamapps\common\QueensDomain"`.

Test windows open on a second monitor placed to the right of a 1920-wide main screen. Change `MON_X` / `MON_Y` in
`runner/qdlab.py` for your screens.

## Layout

| Path | What |
|---|---|
| `copies/A`, `copies/B` | Test copies of the game (B hard-links the big pak). Each has its own `Saved` (`-NotInstalled`) and its own `_localappdata`, so the mod's log and config never touch your real `%LOCALAPPDATA%\QueensDomain`. |
| `sdk/CppSDK` | Game C++ SDK from Dumper-7 (needed to build the mod and the bridge). |
| `bridge/` | `qdbridge.dll`: our test bridge, injected into each copy. Speaks the hub's bridge protocol on `tcp://127.0.0.1:27990` (A) / `27991` (B). |
| `runner/qdlab.py` | Launch, place, inject, stop. Only ever touches processes started from `copies/`. |
| `runner/rig.py` | Boot one copy or the pair into a new game. |
| `runner/scenarios.py` | The co-op checks. Results go to `results/<time>.json`. |
| `runner/setup.py` | One-time setup (copies, Dumper-7, SDK, Knights' Domain dev build, bridge). |
| `third_party/coopbridge` | Bridge server header and Python client from the [Co-op Anything](https://github.com/cranebeaker/coop-anything) hub (MIT). |
| `tools/Dumper-7` | Not in git: cloned by setup at a pinned commit and patched to build with `/std:c++latest` and to write into `sdk/`. |

The mod's dev build lives in `..\knights-domain\build-dev\Release` (its `source` branch, 1.0.3), installed into both
copies as `xinput1_3.dll` plus `KnightsDomain\KnightsDomain.dll`. The released mod is 1.0.6; its newer source is not
public, so results here are for 1.0.3 until it is.

## Run it

```
py -3 -I runner/rig.py pair          # boots A (host) and B (client), ~2.5 min
py -3 -I runner/scenarios.py         # runs every scenario against the running pair
py -3 -I runner/rig.py stop          # kills the lab copies (never the real game)
```

Poke a copy by hand:

```
py -3 -I runner/rig.py state A player save puppets pickups enemies
py -3 -I runner/rig.py exec A "tp -800 -130 120 0"
py -3 -I runner/rig.py exec A "kd enemy"            # any Knights' Domain dev command (god, ehit, ewake, elist ...)
```

## Bridge commands

`state` sections: `player` (local pos, health), `save` (inventory, savableActorStates, namedSaveData, itemDrops,
unlockedTeleporters, killedEnemyCounts, xp, level, lastCheckpoint), `enemies` (within 60 m: class, pos, hp, dead,
aiDisabled), `players` (player characters in the world), `puppets` (the mod's knight bodies of remote players),
`pickups` (item pickups within 60 m), `savables` (saved world objects within 60 m: chests, levers, gates, secret
walls, pickups, quest state; with their save GUIDs and live state), `checkpoints` (loaded checkpoints). Save also has
`savePoint` and `savePointsVisited`.

`exec`: `newgame <slot>`, `loadslot <slot>`, `skipintro`, `tp x y z [yaw]`, `tpto <actor> [cm]`,
`interact <actor>` (any actor with an Interact event: pickup, chest, lever, checkpoint), `call <BP function with no params>` on the player, `kd <line>` (the mod's dev command file).

`eval`: `world`, `title`, `loading`, `inworld`, `health`.

## Results so far (2026-11-17, Steam build 26663759, Knights' Domain 1.0.3 dev build)

Each row ran twice from a fresh boot with the same result. "Gap" means the scenario shows something that is not shared.

| Scenario | Result | Measured |
|---|---|---|
| movement | PASS | B moved 8 m, A's model of B ended 0 cm away (horizontal) |
| loot_and_world | FAIL (gap) | A picked up `JarHammer_Pickup_2`: A inventory +1 and A's save marked it taken; on B the pickup is still there and B's save has no entry for it |
| client_loot | FAIL (gap) | B picked up `Herb_Item_BP_2` (B +1 HealingHerb, B's save marks it taken); A's copy of the pickup is unchanged |
| host_world_objects | FAIL (gap) | A opened `Chest10_2` and pulled `AquaLever_23`: both changed state on A only (live state and save 1 on A, unset on B). `SecretWall7_2` did not react to a plain interact |
| client_world_objects | FAIL (gap) | B opened `SecretWall11_2`: changed on B only. `Chest3_2` did not open (probably locked) |
| checkpoint | FAIL (gap) | A rested at `Checkpoint17`: A's save got `lastCheckpoint` and `savePointsVisited` = `GR_Camp_Save`; B's save unchanged |
| rejoin | FAIL (gap) | B left and joined again after A opened `Chest10_2`: the rejoined B loads its own new world with the chest closed. Old teammate model removed and a new one spawned correctly |

In short, version 1.0.3 shares players and enemies, but no world object, pickup, checkpoint or save state, in either
direction. Every player plays in their own world. The `WorldFlags` and `InteractRequest` messages in its protocol are
defined but never sent.

## Gotchas

- Dumper-7 (commit 2c1eae3) generates Blueprint-class function wrappers with the class name where the function
  name belongs (`GetStaticName(L"TitleScreenUI_C", ...)`). Calling one is a fatal `Failed to find function`. The
  bridge calls BP functions by name with the SDK parameter structs instead. Engine-class wrappers are fine.
- If `CMAKE_TOOLCHAIN_FILE` is set in your environment (an Android NDK install sets it), CMake configures fail; `setup.py` drops it for its own builds.
- The bridge pumps inside `ProcessEvent` of a few native vtables (UUserWidget, AFS_PlayerCharacter,
  APlayerController), the same context the title-screen buttons run in.

## Next scenarios

See [TASKS.md](TASKS.md). Lane 1 (world, loot, saves) is measured above; Lane 2 (combat, death, network) is in progress.
