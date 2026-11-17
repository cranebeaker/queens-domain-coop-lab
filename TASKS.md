# Tasks

Goal: measure exactly what Knights' Domain shares between players and what it doesn't, with scenarios that run on
real game copies, so we can send its author precise bug reports (and later decide whether to build world sync).

Each task is one scenario in `runner/scenarios.py` (plus any bridge commands it needs in `bridge/qdbridge.cpp`).
A scenario passes when the co-op behaviour is right, so a gap shows up as FAIL with the numbers that prove it.
Run every scenario at least twice before calling a result.

## Lane 1 (owner): world, loot, saves

| # | Scenario | What to measure |
|---|---|---|
| 1 | `loot_and_world` (done) | host picks up an item: does it vanish for the client, and do both saves agree? Result: FAIL, nothing reaches the client |
| 2 | `client_loot` | the same with the client picking up |
| 3 | `doors_and_levers` | host opens a door / pulls a lever / breaks a wall: diff `savableActorStates` and `namedSaveData`, and check the client can walk through |
| 4 | `checkpoints` | host rests at a checkpoint: `lastCheckpoint`, enemy respawn on both, what the client's save says |
| 5 | `teleporters` | host unlocks a teleporter: `unlockedTeleporters` on both |
| 6 | `rejoin` | client leaves and rejoins: what it keeps, what the host's world looks like to it |

## Lane 2 (friend): combat, death, network

| # | Scenario | What to measure |
|---|---|---|
| 7 | `ranged_attacks` | a ranged enemy targets A, B stands in the line of fire with `kd god`: is B hit, does B see the projectile (their open issue 2) |
| 8 | `area_attacks` | the same for area attacks (water vortex, boss splash) |
| 9 | `drops_and_xp` | A kills an enemy (`kd ehit`): `itemDrops`, `xp`, `killedEnemyCounts` on both, and who can pick up the drop |
| 10 | `enemy_hp_agree` | hit an enemy from both sides: HP and death agree on both copies |
| 11 | `death` | A dies: what B sees, does the session survive A's reload, where does A come back |
| 12 | `host_leaves` | stop A mid-fight: B's log and state, does B keep playing |
| 13 | `lag` | rerun 7 to 12 through the Co-op Anything hub's `tools/chaos` relay (50 to 150 ms, 2% loss) |

## Rules

- Never change Knights' Domain's code, and never commit or share a build of it. Its licence allows building it for
  your own use only. Our code lives in `bridge/` and `runner/`.
- Never commit game files, the SDK dump, saves, logs with your username or paths with your username. `.gitignore`
  covers the usual places; check `git status` before every commit.
- Test windows go on your second monitor (edit `MON_X` / `MON_Y` in `runner/qdlab.py` for your screens).
- One scenario per pull request, with the scenario's output pasted in the PR (two runs).
- Draft bug reports for Knights' Domain in your PR first, so both of us agree on them before anyone posts.
