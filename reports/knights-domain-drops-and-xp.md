# DRAFT: loot is rolled on each machine, and the client sometimes misses a kill

Draft for the Knights' Domain author. Agree on it in a PR before anyone posts it. Part 1 may be intended (the
1.0.3 source says every non-owner "kills its own copy and rolls its own rewards"), so it is written as a question.
Part 2 looks like a bug, but see the caveat about the game build.

---

Hi! Two things we measured with a test rig (two copies of the game on one PC, host and client over the dev UDP
transport, a bridge that reads each copy's save state and pickups). In case exact numbers help:

**Version:** Knights' Domain built from the public `source` branch (1.0.3) in the dev flavour, Queen's Domain Steam
build 26663759, Windows 10. On that build the mod logs `unknown game build: Blueprint hooks are incomplete, enemy
sync will miss events`, so part 2 may come from that. We will rerun it on 1.0.6 when the source is up.

**Setup:** Ruins, near (3000, -2000). The host kills small enemies one at a time with `ehit <enemy> 4000` (Ratty
Buggers, Mossy Fighters, a Dragonfly, Lookers; the host owns them), the client stands 1.5 m behind the host. We read
`xp`, `killedEnemyCounts`, `itemDrops` and the pickups within 8 m of each kill on both machines, then each player
picks up one of the drops on their own screen.

**1. Each machine rolls its own loot (question: intended?)**

| | Host | Client |
|---|---|---|
| XP after 10 kills (run 2) | 432 | 432 |
| `itemDrops` added | 4 | 2 |
| Drops on the ground | Coin, Herb, Herb, Poison Cure | Coin |
| Picks up one | Poison Cure: +1 in host's inventory | Coin: +1 in client's inventory |
| That drop on the other machine | does not exist | does not exist |

So both players get their own random loot from the same kill (and the saves end up with different `itemDrops`).
That can be fine for co-op ("instanced loot"); we only want to know whether it is the plan, because players will
see different items on the ground at the same spot.

**2. The client sometimes misses a host kill**

In 3 of 12 runs (70 kills in all), one kill did not reach the client: the enemy died on the host, the host's
`killedEnemyCounts` and XP went up, the client's did not (run 1: host 432 XP, client 422 XP, one Looker missing in the
client's kill counts). The other 67 kills counted on both. We did not catch which step dropped it: the client's log
has nothing at the default level.

**What we expected:** every kill counts for both players, so XP stays equal.

Happy to rerun anything on a newer build.
