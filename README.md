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
py -3 -I runner/scenarios.py --suite lane1   # boots fresh pairs itself and runs all of Lane 1, ~12 min
py -3 -I runner/rig.py pair                  # or: boot A (host) and B (client) by hand, ~2.5 min
py -3 -I runner/scenarios.py host_gate       # ... and run single scenarios against the running pair
py -3 -I runner/rig.py stop                  # kills the lab copies (never the real game)
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
walls, pickups, quest state; with their save GUIDs and live state), `savables_all` (the same for everything loaded),
`checkpoints` (loaded checkpoints). Save also has `savePoint` and `savePointsVisited`.

Actor names repeat across sublevels; every command that takes a name uses the loaded actor of that name nearest to
the player.

`exec`: `newgame <slot>`, `loadslot <slot>`, `skipintro`, `tp x y z [yaw]`, `tpto <actor> [cm]`,
`tpside <actor> <cm> [up]` (stand on a chosen side along its facing), `interact <actor>` (any actor with an Interact
event: pickup, chest, door, lever, secret wall, checkpoint), `activate <actor>` / `deactivate <actor>` (lifts, doors,
switches), `callon <actor> <function>` (any function with no parameters, e.g. `InsertKey`, `Break`), `breakhp <barricade>`
(HP to 0, then the game's own `TookHit`), `fns <actor>` (list its Blueprint functions), `clearpath <actor> [cm]`
(turn off collision of nearby enemies, test setup only), `passthrough <actor> [cm] [up]` (can the player walk through
it, both ways, with collision), `sweep x y z`, `call <function>` on the player, `kd <line>` (the mod's dev command file).

`eval`: `world`, `title`, `loading`, `inworld`, `health`.

## Results: Lane 1 (2026-11-18, Steam build 26663759, Knights' Domain 1.0.3 dev build)

`--suite lane1`, two complete runs from empty saves, identical results: 2 pass, 10 fail, 0 skip. A scenario passes when
the co-op behaviour is right, so FAIL means the thing is not shared.

| Scenario | Result | Measured |
|---|---|---|
| movement | PASS | B moved 8 m, A's model of B ended 0 cm away (horizontal) |
| loot_and_world | FAIL | A picked up `JarHammer_Pickup_2`: A inventory +1, A's save marks it taken; on B it is still there, no save entry |
| client_loot | FAIL | B picked up `Herb_Item_BP_2`: B +1 HealingHerb, B's save marks it taken; A's copy unchanged |
| host_world_objects | FAIL | A used 7 objects, all changed on A only (table below) |
| client_world_objects | FAIL | B used 6 other objects of the same kinds, all changed on B only |
| host_gate | FAIL | A pulled `AquaLever_23`: 5 s later A walks through `AquaGate_01_BP10` both ways; B is still blocked by the gate both ways |
| client_gate | FAIL | B pulled the same lever on a fresh pair: B walks through after 5 s, A is still blocked |
| checkpoint | FAIL | A rested at `Checkpoint17`: A's save got `lastCheckpoint` and `savePointsVisited` = `GR_Camp_Save`; B's unchanged |
| client_checkpoint | FAIL | B rested there: the same, the other way round |
| story_flags | FAIL | after A's actions, 8 story counters are set on A and missing on B (`TotalChestsOpened`, `TotalSecretDoorsOpened`, `BrokeBarricade`, `TotalBrokenPermaBreakables`, `CheckpointUnlockCount`, `TotalAchievementWeapons`, `WindGemsAcquired`, `FullEnergyCellsCount`); A's save has 14 world entries, B's 6, 8 differ |
| rejoin | FAIL | B left and joined again after A opened `Chest10_2`: the rejoined B has its own new world with the chest closed. The old teammate model was removed and a new one spawned correctly (A sees exactly 1) |
| client_progress_persists | PASS | B picked up `Herb_Item_BP3_5`, rested, quit and loaded its slot alone: HealingHerb 2, checkpoint `GR_Camp_Save` and the taken pickup all kept |

World objects, each used by one player (state = the object's own save data, live and in the save):

| Kind | Used by A | Used by B | How | On the user | On the other player |
|---|---|---|---|---|---|
| Chest | `Chest10_2` | `Chest7_2` | interact | 1 | unset |
| Secret wall | `SecretWall7_2` | `SecretWall11_2` | interact from its front | 1 | unset |
| Door | `M_DoorSmall_00_BP2` | `M_DoorSmall_00_BP` | interact | 1 | unset |
| Keyed door | `EggDoor_BP6` | `EggDoor_BP5` | insert key | 1 | unset |
| Lift | `IntroDungerLift_BP_2` | `IntroDungerLift_BP2_2` | activate | 1, 1 | unset |
| Barrel (breakable) | `Barrel27_2` | `Barrel37_2` | break | 1 | unset |
| Barricade | `BreakableBarricade2_3` | | final hit | 1 | unset |
| Lever and gate | `AquaLever_23` | `AquaLever_23` (fresh pair) | interact | lever 1, gate opens | lever unset, gate shut (walk test) |

In short, version 1.0.3 shares players and enemies, but no world object, pickup, checkpoint, story flag or save state,
in either direction, and a rejoining player gets a fresh world. Each player's own progress does save and reload
correctly. The `WorldFlags` and `InteractRequest` messages in its protocol are defined but never sent.

Not covered: `Chest3_2` stays shut with interact, `InsertKey` and `Open` (it needs its real key item); the keyed doors
cover the key mechanism. `ActivationTrigger_C` volumes save nothing when entered (they are not one-time triggers).
Quest state is covered by `story_flags`; no quest manager changed in these runs.

## Gotchas

- Dumper-7 (commit 2c1eae3) generates Blueprint-class function wrappers with the class name where the function
  name belongs (`GetStaticName(L"TitleScreenUI_C", ...)`). Calling one is a fatal `Failed to find function`. The
  bridge calls BP functions by name with the SDK parameter structs instead. Engine-class wrappers are fine.
- If `CMAKE_TOOLCHAIN_FILE` is set in your environment (an Android NDK install sets it), CMake configures fail; `setup.py` drops it for its own builds.
- The bridge pumps inside `ProcessEvent` of a few native vtables (UUserWidget, AFS_PlayerCharacter,
  APlayerController), the same context the title-screen buttons run in.
- A new game in a used save slot keeps parts of the old world, so `rig.py` deletes the copy's own `SaveGames` before
  every new-game boot (never for a load). Without that, results leak from one run into the next.
- Doors and gates finish opening a few seconds after the lever, and only while their part of the level is streamed
  in. The gate checks run first in each group, before the long-range teleports of the object checks.

## Next scenarios

See [TASKS.md](TASKS.md). Lane 1 (world, loot, saves) is measured above; Lane 2 (combat, death, network) is in progress.
