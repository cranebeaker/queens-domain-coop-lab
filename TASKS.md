# Tasks

Round 1 measured Knights' Domain; round 2 makes the whole game shared. The code lives in our fork of the mod,
[cranebeaker/KnightsDomain](https://github.com/cranebeaker/KnightsDomain), branch `world-sync` (offered to the author
as a pull request when it is done). This repository keeps the test rig, the scenarios and the tools.

## Round 2: the whole game in co-op

### What is already in `world-sync` (passing in the rig, two players, Ruins)

| System | Code | Scenarios |
|---|---|---|
| World objects, pickups, gates, lifts, breakables | `sync/WorldSync.cpp` | `loot_and_world`, `client_loot`, `host_world_objects`, `client_world_objects`, `host_gate`, `client_gate` |
| Story counters, checkpoints, teleporters, rejoin | `sync/WorldSync.cpp` | `checkpoint`, `client_checkpoint`, `story_flags`, `rejoin`, `client_progress_persists` |
| Enemy projectiles and area attacks | `sync/HazardSync.cpp` | `ranged_attacks` (pass), `area_attacks` (being fixed) |
| Loot rolled once, picked up once | `sync/HazardSync.cpp`, `sync/WorldSync.cpp` | `drops_and_xp` |
| Player sync survives the crash after a death | `sync/PlayerSync.cpp` | `death`, `lag` |

Every world change in the game goes through the save's `SavableActorStates` and `NamedSaveData`, which WorldSync
mirrors, so those rows hold for every area, not only the Ruins.

### How to find work: the Blueprint index

`py -3 -I runner/setup.py decompile` decompiles every Blueprint of your copy to text in `tools/ue/bp` and writes
`tools/ue/bp_index.md`: per Blueprint, the calls that assume one player (`player0`), write the save, pause, play
cutscenes, stream levels, spawn actors or roll random numbers. Read the Blueprint's text before changing anything
about it: it is the game's real logic. Never commit anything under `tools/ue`.

### Lane A (owner): systems

| # | Task | Start from |
|---|---|---|
| A1 | `area_attacks` passes: the boss's fire hurts the other player too | `Enemies/Dragon/*`, `Enemies/Fisher_00/.../Fisher_SZ_FirePuddle` |
| A2 | Cutscenes play for everyone: who starts one, what the others see, nobody stuck | `Cutscenes/CutsceneManagerBase`, `Cutscenes/RitualCutscene`, `Cutscenes/Ending1..4`, `RuinsLevel/Intro/Sequences` |
| A3 | Trigger volumes that set story state or quests fire once for the party | `Blueprints/ActivationTrigger`, `NamedSaveDataSetterVolume`, `ChallengeCompleteVolume`, `QuestForceUpdateVolume`, `ActivatableNamedSaveDataSetter` |
| A4 | NPC dialogue and turn-ins agree on both machines | `NPCs/*` (Chef eggs, Fish Wife, Librarian, Knight NPCs), `Blueprints/DialogueManager` |
| A5 | Pause and rest: no player freezes the other | `LD45/Blueprints/Base_LD45_GM`, `Player/FS_PlayerChar`, checkpoint rest |
| A6 | Pull request to the author once A1 to A5 and Lane B pass | |

### Lane B (friend): bosses and the later areas

For each boss and miniboss: how it wakes, whether it locks an arena, its phases, its special attacks (spawned actors
go through HazardSync already), its death and its reward (`*_DummyNPC.PickupCurrentReward`). Make it right for both
players, then add a scenario that teleports both players to it and fights it with `kd god 1` / `kd ehit`.

| # | Area | Bosses and minibosses (folders under `Enemies/`) |
|---|---|---|
| B1 | Library, Mausoleum | `Necronomicon`, `Boner_00/.../ShrineBoss`, `Boner_ExecutionerMiniboss`, `LostLibrarian` |
| B2 | Aqua Ruins, Stone Garden | `WhaleKing` (water vortex, splash ring, poison circle), `FishManer_00` boss, `FishWifeEvil`, `Golem` |
| B3 | Secret zones | `Dragon/Dragon_ROS`, `JosamiKnight_00/DesBoss`, `Cocker_00`, `Ribber_00` boss, `RoyalDuo`, `BlackRobotDuo`, `BloodPoolDuo` |
| B4 | Other World, Castle | `JosamiLord` (and Blood Sisters), `Queen`, `DemonBosses` (fight controller, spider, hellbat), `MechaKnight`, `Sipeha` |
| B5 | Arena locks and boss doors in the level | `RuinsLevel/Library/BossDoor`, `RuinsLevel/AquaRuins`, `AquaBossExitDoor`, `RuinsLevel/ForbiddenDoors` |

### Rules

- Code goes to `cranebeaker/KnightsDomain` branch `world-sync` as pull requests (one task per PR, the scenario's
  output from two runs pasted in). Rig and scenarios go to this repository as pull requests.
- Never commit game files, decompiled Blueprints, the SDK dump, saves, logs, or paths with your username.
- Test windows go on your second monitor (edit `MON_X` / `MON_Y` in `runner/qdlab.py` for your screens).
- After a change, run your own scenarios; the full suite (`--suite all`, then `lag`) runs before a merge.

## Round 1 (done): what Knights' Domain 1.0.6 shared on its own

Lane 1 (world, loot, saves) and Lane 2 (combat, death, network) are recorded in the README. In short: players and
enemies were shared; world objects, pickups, checkpoints, story state, projectiles, area attacks and loot were not.
