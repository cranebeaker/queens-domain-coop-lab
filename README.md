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
`runner/qdlab.py` for your screens. The copies never take the mouse: each copy's `Input.ini` turns mouse capture and
lock off, a new window starts without activating and the foreground goes back to the window you were using, so you
can keep working while a suite runs.

Reading the game's Blueprints (`py -3 -I runner/setup.py decompile`, see TASKS.md) also needs Rust (cargo, from
rustup) and the .NET 9 SDK; a newer .NET runtime is fine when the 9 runtime is missing. `py -3 -I tools/maplocs.py`
then writes `tools/ue/actor_locs.tsv`: every placed actor of the Ruins maps with its class and world position (boss
spawners, arena doors, triggers), which the boss scenarios use for their spots.

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
| `tools/ue` | Not in git: the decompiled Blueprints, their index and `actor_locs.tsv` (all built from your own copy). |
| `tools/maplocs.py`, `tools/bpindex.py` | Build `tools/ue/actor_locs.tsv` and `tools/ue/bp_index.md`. |

The mod's dev build lives in `..\knights-domain\build-dev\Release` (our fork's `world-sync` branch), installed into
both copies as `xinput1_3.dll` plus `KnightsDomain\KnightsDomain.dll`. `setup.py kd` pulls `world-sync`, makes the two
SDK header edits 1.0.6 needs and reinstalls; with another branch checked out there (a fix in progress) it builds that
branch as it is.

## Run it

```
py -3 -I runner/scenarios.py --suite all     # boots fresh pairs itself and runs both lanes, ~35 min
py -3 -I runner/scenarios.py --suite lane1   # or one lane (lane1 ~12 min, lane2 ~20 min)
py -3 -I runner/scenarios.py --suite laneb   # Lane B bosses; or one area: b1 (~15 min) ... b5
py -3 -I runner/scenarios.py lag             # Lane 2 again through a lag relay, ~18 min
py -3 -I runner/rig.py pair                  # or: boot A (host) and B (client) by hand, ~2.5 min
py -3 -I runner/scenarios.py host_gate       # ... and run single scenarios against the running pair
py -3 -I runner/rig.py stop                  # kills the lab copies (never the real game)
```

Bad network: `rig.py pair --lag` puts the [Co-op Anything](https://github.com/cranebeaker/coop-anything) hub's
`tools/chaos` relay between B and A's UDP port, with 25 +/- 25 ms each way and 2 % loss (50 to 150 ms round trip). Clone
the hub next to this repo (`..\coop-anything`) or set `QD_HUB`. `scenarios.py lag` reruns scenarios 7 to 12 (and
movement) that way, each from a fresh pair; it is not part of the default run. `rig.py stop` also stops the relay.

Poke a copy by hand:

```
py -3 -I runner/rig.py state A player save puppets pickups enemies
py -3 -I runner/rig.py exec A "tp -800 -130 120 0"
py -3 -I runner/rig.py exec A "kd enemy"            # any Knights' Domain dev command (god, ehit, ewake, elist ...)
```

## Bridge commands

`state` sections: `player` (local pos, health, dead), `save` (inventory, savableActorStates, namedSaveData, itemDrops,
unlockedTeleporters, killedEnemyCounts, xp, level, lastCheckpoint), `enemies` (within 60 m: class, pos, hp, dead,
aiDisabled), `players` (player characters in the world), `puppets` (the mod's knight bodies of remote players, with the animation each one plays: idle, walk, attack, `Knight_Undead_Death`),
`pickups` (item pickups within 60 m), `savables` (saved world objects within 60 m: chests, levers, gates, secret
walls, pickups, quest state; with their save GUIDs and live state), `savables_all` (the same for everything loaded),
`checkpoints` (loaded checkpoints), `watch` (what `exec watch` recorded, see below). Save also has
`savePoint` and `savePointsVisited`.

Actor names repeat across sublevels; every command that takes a name uses the loaded actor of that name nearest to
the player.

`exec`: `newgame <slot>`, `loadslot <slot>`, `skipintro`, `tp x y z [yaw]`, `tpto <actor> [cm]`,
`tpside <actor> <cm> [up]` (stand on a chosen side along its facing), `interact <actor>` (any actor with an Interact
event: pickup, chest, door, lever, secret wall, checkpoint), `activate <actor>` / `deactivate <actor>` (lifts, doors,
switches), `callon <actor> <function>` (any function with no parameters, e.g. `InsertKey`, `Break`), `breakhp <barricade>`
(HP to 0, then the game's own `TookHit`), `fns <actor>` (list its Blueprint functions), `clearpath <actor> [cm]`
(turn off collision of nearby enemies, test setup only), `passthrough <actor> [cm] [up]` (can the player walk through
it, both ways, with collision), `sweep x y z`, `call <function>` on the player, `kd <line>` (the mod's dev command file), `die` (the local player dies through the player Blueprint's own `Kill`), `widgets [class match]` (live UI widget classes; with a match, also that widget's own functions), `uicall <widget class match> <function>` (call a function on a live widget, e.g. the Game Over screen's Continue button), `kd <line>` (the mod's dev command file),

`actors <word> [radius cm]`: loaded actors whose class (or a parent class) or name contains the word, as JSON with
position, `hidden` and `collision` (the actor's own switch); actors whose root sits at the origin (the Necronomicon's
door) are included. `getvar <actor> <variable>`: a Blueprint variable of an actor (bool, int, float, byte, name,
object name; the name without its GUID suffix), or `<Component>.collision` for a component variable's collision
(`none`, `query`, `physics`, `both`: a gate's blocker box).

`watch start [radius cm] [word ...]` / `watch stop`: on the game thread every 50 ms, record each actor within the radius whose
class or a parent class name contains one of the words (default: Projectile, Arrow, Splash, Vortex, Whirl, Wave, AOE,
Spit, Puddle, Fireball, Creep, Explosion, Shockwave) with its class, instigator and closest approach, and every drop in the local player's health with the closest such actor at that moment. With the
mod's `kd god` on, health sits near 1,000,000 and is only refilled below 100,000, so every hit leaves a dent.

`eval`: `world`, `title`, `loading`, `inworld`, `health`.

## Lane B: bosses and arenas (world-sync fork)

Each boss scenario puts both players at a boss, starts the fight the way the game does (its trigger, its lectern, its
own aggro, a talk), from the host or from the client, and checks both copies: the boss awake, the arena shut, the
boss's attacks hitting each player while it fights them, its death (or kneeling), the arena afterwards, the save flags
and each player's own reward. The fixes are in the mod's `sync/BossSync.cpp` (boss and arena Blueprint calls mirrored
to the other machines, boss hitboxes on the followers' copies), with guards in `EnemySync.cpp` and per-player reward
counters in `WorldSync.cpp`.

<!-- lane-b-status -->
Two full runs of `runner/scenarios.py --suite laneb` on the Lane B5 mod build (`results/20261119-175602.json`, `results/20261119-194135.json`):

| Area | Scenario | Run 1 | Run 2 |
|---|---|---|---|
| B1 | grave_guardian | PASS | PASS |
| B1 | executioner | PASS | PASS |
| B1 | lost_librarian | PASS | PASS |
| B1 | necronomicon | PASS | PASS |
| B2 | whaleking | PASS | PASS |
| B2 | king_of_stone | PASS | PASS |
| B2 | royal_fishmaner | PASS | PASS |
| B2 | evil_fishwife | PASS | PASS |
| B2 | white_tree_golem | PASS | PASS |
| B3 | ros_dragon | PASS | PASS |
| B3 | josami_des | PASS | PASS |
| B3 | royal_duo | PASS | PASS |
| B3 | black_robot_duo | PASS | PASS |
| B3 | blood_pool_duo | PASS | PASS |
| B3 | cocker_boss | PASS | PASS |
| B3 | ribber_boss | PASS | PASS |
| B4 | demon_duo | PASS | PASS |
| B4 | bridge_lord | PASS | PASS |
| B4 | blood_lords | PASS | PASS |
| B4 | mecha_knights | PASS | PASS |
| B4 | queen | PASS | PASS |
| B4 | sipeha | PASS | PASS |
| B5 | boss_reset_guard | PASS | PASS |
| B5 | arena_pull_in | PASS | PASS |

## Status on Knights' Domain 1.0.6 (2026-11-18, Steam build 26663759)

Everything below was measured on 1.0.3 first and then again on 1.0.6 (dev build from the `source` branch):
`--suite all` twice from empty saves, `lag` twice, and `death` six more times. Both 1.0.6 runs of the suite were
identical: 5 pass, 13 fail, 0 skip. A scenario passes when the co-op behaviour is right.

| Area | 1.0.3 | 1.0.6 |
|---|---|---|
| Players see each other, movement | works | works |
| World objects, pickups, gates, checkpoints, story flags, rejoin (Lane 1, 10 checks) | not shared | not shared, same numbers |
| A player's own progress after save and reload | kept | kept |
| Enemy arrows and boss fire attacks for the player who is not the target | missing | missing (host 12 to 13 arrows and 32 to 93 fire actors, client 0) |
| Loot from kills | each machine rolls its own | same |
| XP and kill counts | equal, but the client missed a kill in 3 of 12 runs | equal in all 4 runs (2 plain, 2 with lag) |
| Enemy HP, every hit counts | HP agrees, some host hits lost when ownership flipped | fixed: 23 hits, none lost, in all 4 runs |
| Death and respawn | works, but the host's player sync crashed in 2 of 6 runs | works, crashed in 1 of 8 runs (plain link, 7 ms ping) |
| Host leaves | client notices in about 11 s and keeps playing | same (10.9 s) |
| 50 to 150 ms lag with 2 % loss | same gaps, more missed kills and lost hits | nothing gets worse than on the plain link (2 passes) |

Draft reports for the author are in `reports/`: world state not shared, projectiles and area attacks, loot per
machine, and the player sync crash after death. The lost-hits report was dropped: 1.0.6 fixed it.

The tables below give the measured details. Lane 1 numbers are the same on both versions; Lane 2 rows quote the 1.0.3
runs, with the 1.0.6 outcome in the table above.

## Details: Lane 1 and Lane 2

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
| ranged_attacks | FAIL (gap) | Two `Mossy_Archer_BP_C` shoot at A (10 m away) for 30 s, B in god mode stands on the line halfway (0 cm off it). Both copies play `Boner_Bow_Attack` on the archers (owner P1), but the arrows (`Mossy_Arrow_Projectile_C`) exist only on A: A saw 12 / 11 arrows and took 11 / 11 hits; B saw 0 arrows and took 0 hits |
| area_attacks | FAIL (gap) | The Ruins dragon boss (`Dragon_Green_BP_C`) fights A for 75 s, B in god mode kept a median 1.5 m from A. Both copies play the same boss montages (fire breath, air fireballs), but fire puddles (`Dragon_FirePuddle_C`), fireballs (`Dragon_Fireball_C`) and flame-breath projectiles exist only on A: A saw 59 + 0 + 48 / 16 + 9 + 16 of them and took 16 + 0 + 91 / 22 + 17 + 32 hits from them; B saw none and took 0 hits |
| drops_and_xp | FAIL (gap) | A kills 10 small enemies near (3000, -2000) with `kd ehit` (4000 damage), B 1.5 m behind A. XP and `killedEnemyCounts` agree on both copies in most runs (432 / 432), but B missed one of A's kills in 3 of 12 runs (70 kills in all; run 1 here: 432 vs 422 XP). Loot is rolled on each copy: what drops differs (run 2: A got a coin, two herbs and a poison cure, B one coin), each player picks up their own copy, and nothing of it exists on the other copy |
| enemy_hp_agree | FAIL (gap, intermittent) | A and B take turns hitting a `Mossy_Fighter_BP_C` (67.2 HP) for 3 until it dies. HP agrees on both copies after every hit (worst gap 0.0) and it dies on both at the same hit. But some of A's hits do nothing: run 1 took 23 hits with none lost, run 2 took 27, and A's hits 3, 5, 7 and 9 changed nothing on either copy. Ownership moves to the last hitter, so it flips between the players on every hit. Each lost hit was relayed to B about 3 ms before ownership flipped back to A |
| death | PASS | A dies (`die`) 51 m from where a new game starts, with no checkpoint rested at. B sees A's knight play `Knight_Undead_Death` 1.1 s later. A gets the Game Over screen; after Continue, A's world reloads (B's doesn't) and A is back in 9.6 s at the new-game start. The session survives: each copy shows the other player again within 0.2 s, and movement syncs both ways (0 cm error). Enemies A killed before dying stay dead on both copies. One earlier trial run (1 of 4) had both puppets missing after the respawn, and movement didn't sync; it didn't happen again in the next three |
| host_leaves | PASS | Mid-fight near (3000, -2000) (the host owns 99 enemies), A's game is killed. B removes A's knight after 4.0 s and notices after 10.7 / 10.8 s (`disconnected (host lost)`). It shows "The host left" and "Co-op failed: host lost", ends the session cleanly, takes over the ~59 enemies it was following and scales their HP back for 1 player. B stays in its world: the enemies keep fighting (42 / 44 hits on B in the next minute, 4 of 5 / 4 of 6 moved), and B's own hit still kills |
| lag | PASS / FAIL (pass 2: `death` broke) | Scenarios 7 to 12 and movement again, B joining through the hub's chaos relay (25 +/- 25 ms each way, 2 % loss; the relay dropped 2 to 2.5 % each way, B's mod logged a worst ping of 220 to 1000 ms). Two passes: movement 0 / 0 cm; `ranged_attacks` and `area_attacks` the same gaps (B sees 0 projectiles / area actors); `host_leaves` PASS / PASS (B notices after 10.7 s). Worse with lag: `drops_and_xp` B missed 4 kills in pass 1 (318 vs 432 XP) and 1 in pass 2; `enemy_hp_agree` FAIL / PASS (pass 1: HP 3.0 apart once, 4 host hits lost). `death` PASS / FAIL: in pass 2 the host's mod caught an access violation in its `playersync` ticker during the reload after death and removed the ticker, so neither copy showed the other player for the rest of the session. That is most likely also the unconfirmed `death` failure on the plain link |

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

In short, the mod shares players and enemies, but no world object, pickup, checkpoint, story flag or save state,
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
- `rig.py stop` waits for the copies to exit. A copy relaunched while the old process still holds the mod's log
  cannot rotate it and runs with no log at all (the mod still works).
- `kd ehit <enemy> <damage>` on an enemy another player owns is relayed to that owner, which rejects anything over
  5000 damage ("damage request rejected" in its log, nothing in yours). Use 4000 to kill a small enemy.
- Knights' Domain 1.0.3 logs `unknown game build: Blueprint hooks are incomplete, enemy sync will miss events` on
  the current Steam build (26663759). Keep that in mind for intermittent enemy results.
- The bridge pumps inside `ProcessEvent` of a few native vtables (UUserWidget, AFS_PlayerCharacter,
  APlayerController), the same context the title-screen buttons run in.
- A new game in a used save slot keeps parts of the old world, so `rig.py` deletes the copy's own `SaveGames` before
  every new-game boot (never for a load). Without that, results leak from one run into the next.
- Doors and gates finish opening a few seconds after the lever, and only while their part of the level is streamed
  in. The gate checks run first in each group, before the long-range teleports of the object checks.

## What is left

Both lanes in [TASKS.md](TASKS.md) are done. Not measured: later levels (water vortex, whale boss), more than two
players, and real Steam sessions between two accounts (the rig uses the dev UDP transport).
