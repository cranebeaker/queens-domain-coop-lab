# Tasks

Goal: measure exactly what Knights' Domain shares between players and what it doesn't, with scenarios that run on
real game copies, so we can send its author precise bug reports (and later decide whether to build world sync).

Each task is one scenario in `runner/scenarios.py` (plus any bridge commands it needs in `bridge/qdbridge.cpp`).
A scenario passes when the co-op behaviour is right, so a gap shows up as FAIL with the numbers that prove it.
Run every scenario at least twice before calling a result.

## Lane 1 (owner): world, loot, saves

| # | Scenario | What to measure |
|---|---|---|
| 1 | `loot_and_world` (done, gap) | host picks up an item: nothing reaches the client |
| 2 | `client_loot` (done, gap) | client picks up an item: nothing reaches the host |
| 3 | `host_world_objects` / `client_world_objects` (done, gap) | chest, lever, secret wall used on one side change only on that side |
| 4 | `checkpoint` (done, gap) | host rests at a checkpoint: only the host's save records it |
| 5 | teleporters (folded into 4) | checkpoints are the teleport destinations; `savePointsVisited` is not shared |
| 6 | `rejoin` (done, gap) | a rejoining client gets its own fresh world, not the host's |

Lane 1 follow-ups: secret walls that need a hit instead of an interact, a door that needs a key, and whether the
client can walk through a gate the host opened (needs a movement check, not just the save state).

## Lane 2 (friend): combat, death, network

| # | Scenario | What to measure |
|---|---|---|
| 7 | `ranged_attacks` (done, gap) | archers shooting at A: B sees the bow animation but no arrow, and is never hit standing in the line of fire (their open issue 2) |
| 8 | `area_attacks` (done, gap) | Ruins dragon boss: fire puddles, fireballs and flame breath exist and hit only on the targeted player. Water vortex and whale splash are in later levels, not measured yet |
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
