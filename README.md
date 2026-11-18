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

`state` sections: `player` (local pos, health, dead), `save` (inventory, savableActorStates, namedSaveData, itemDrops,
unlockedTeleporters, killedEnemyCounts, xp, level, lastCheckpoint), `enemies` (within 60 m: class, pos, hp, dead,
aiDisabled), `players` (player characters in the world), `puppets` (the mod's knight bodies of remote players, with the animation each one plays: idle, walk, attack, `Knight_Undead_Death`),
`pickups` (item pickups within 60 m), `savables` (saved world objects within 60 m: chests, levers, gates, secret
walls, pickups, quest state; with their save GUIDs and live state), `checkpoints` (loaded checkpoints). Save also has
`savePoint` and `savePointsVisited`. `watch` (what `exec watch` recorded, see below).

`exec`: `newgame <slot>`, `loadslot <slot>`, `skipintro`, `tp x y z [yaw]`, `tpto <actor> [cm]`,
`interact <actor>` (any actor with an Interact event: pickup, chest, lever, checkpoint), `call <BP function with no params>` on the player, `die` (the local player dies through the player Blueprint's own `Kill`), `widgets [class match]` (live UI widget classes; with a match, also that widget's own functions), `uicall <widget class match> <function>` (call a function on a live widget, e.g. the Game Over screen's Continue button), `kd <line>` (the mod's dev command file),
`watch start [radius cm] [word ...]` / `watch stop`: on the game thread every 50 ms, record each actor within the radius whose
class or a parent class name contains one of the words (default: Projectile, Arrow, Splash, Vortex, Whirl, Wave, AOE,
Spit, Puddle, Fireball, Creep, Explosion, Shockwave) with its class, instigator and closest approach, and every drop in the local player's health with the closest such actor at that moment. With the
mod's `kd god` on, health sits near 1,000,000 and is only refilled below 100,000, so every hit leaves a dent.

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
| ranged_attacks | FAIL (gap) | Two `Mossy_Archer_BP_C` shoot at A (10 m away) for 30 s, B in god mode stands on the line halfway (0 cm off it). Both copies play `Boner_Bow_Attack` on the archers (owner P1), but the arrows (`Mossy_Arrow_Projectile_C`) exist only on A: A saw 12 / 11 arrows and took 11 / 11 hits; B saw 0 arrows and took 0 hits |
| area_attacks | FAIL (gap) | The Ruins dragon boss (`Dragon_Green_BP_C`) fights A for 75 s, B in god mode kept a median 1.5 m from A. Both copies play the same boss montages (fire breath, air fireballs), but fire puddles (`Dragon_FirePuddle_C`), fireballs (`Dragon_Fireball_C`) and flame-breath projectiles exist only on A: A saw 59 + 0 + 48 / 16 + 9 + 16 of them and took 16 + 0 + 91 / 22 + 17 + 32 hits from them; B saw none and took 0 hits |
| drops_and_xp | FAIL (gap) | A kills 10 small enemies near (3000, -2000) with `kd ehit` (4000 damage), B 1.5 m behind A. XP and `killedEnemyCounts` agree on both copies in most runs (432 / 432), but B missed one of A's kills in 3 of 12 runs (70 kills in all; run 1 here: 432 vs 422 XP). Loot is rolled on each copy: what drops differs (run 2: A got a coin, two herbs and a poison cure, B one coin), each player picks up their own copy, and nothing of it exists on the other copy |
| enemy_hp_agree | FAIL (gap, intermittent) | A and B take turns hitting a `Mossy_Fighter_BP_C` (67.2 HP) for 3 until it dies. HP agrees on both copies after every hit (worst gap 0.0) and it dies on both at the same hit. But some of A's hits do nothing: run 1 took 23 hits with none lost, run 2 took 27, and A's hits 3, 5, 7 and 9 changed nothing on either copy. Ownership moves to the last hitter, so it flips between the players on every hit. Each lost hit was relayed to B about 3 ms before ownership flipped back to A |
| death | PASS | A dies (`die`) 51 m from where a new game starts, with no checkpoint rested at. B sees A's knight play `Knight_Undead_Death` 1.1 s later. A gets the Game Over screen; after Continue, A's world reloads (B's doesn't) and A is back in 9.6 s at the new-game start. The session survives: each copy shows the other player again within 0.2 s, and movement syncs both ways (0 cm error). Enemies A killed before dying stay dead on both copies. One earlier trial run (1 of 4) had both puppets missing after the respawn, and movement didn't sync; it didn't happen again in the next three |
| host_leaves | PASS | Mid-fight near (3000, -2000) (the host owns 99 enemies), A's game is killed. B removes A's knight after 4.0 s and notices after 10.7 / 10.8 s (`disconnected (host lost)`). It shows "The host left" and "Co-op failed: host lost", ends the session cleanly, takes over the ~59 enemies it was following and scales their HP back for 1 player. B stays in its world: the enemies keep fighting (42 / 44 hits on B in the next minute, 4 of 5 / 4 of 6 moved), and B's own hit still kills |

In short, version 1.0.3 shares players and enemies, but no world object, pickup, checkpoint or save state, in either
direction. Every player plays in their own world. The `WorldFlags` and `InteractRequest` messages in its protocol are
defined but never sent.

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

## Next scenarios

See [TASKS.md](TASKS.md). Lane 1 (world, loot, saves) is measured above; Lane 2 (combat, death, network) is in progress: `ranged_attacks`, `area_attacks`, `drops_and_xp`, `enemy_hp_agree`, `death` and `host_leaves` are measured above.
