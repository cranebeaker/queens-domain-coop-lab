# DRAFT: world state is not shared between players

Draft for a Knights' Domain GitHub issue. Agree on it in a PR before anyone posts it.

---

**Title:** Pickups, chests, levers and checkpoints are not shared between players

Hi! First, thanks for Knights' Domain, the combat sync is really impressive. We built a small test rig that runs two
copies of the game (host and client) and reads each copy's save state, and found that world state stays local to the
player who changed it. The README says the host's world and story progress are shared, so we think this may be a bug
or a missing piece.

**Version:** Knights' Domain built from the public `source` branch (1.0.3) in the dev flavour (UDP loopback), Queen's
Domain Steam build 26663759, Windows 11. We could not test 1.0.6 the same way because two copies on one PC need the
dev UDP transport. If 1.0.6 changed this, please say so and we will retest when its source is up.

**What we did and saw** (two complete runs from empty saves, identical results):

| Action | On the player who did it | On the other player |
|---|---|---|
| Host picks up `JarHammer_Pickup_2` | item in inventory, pickup marked taken in the save | pickup still there, no save entry |
| Client picks up `Herb_Item_BP_2` | item in inventory, marked taken | pickup still there on the host |
| Host opens a chest, a secret wall, a door and a keyed door (key inserted), starts a lift, breaks a barrel and a barricade | each object's state 1 (live and in the save) | all unchanged |
| Client does the same with other objects of those kinds | state 1 | all unchanged on the host |
| Host pulls `AquaLever_23` | 5 s later the host can walk through `AquaGate_01_BP10` | the client is still blocked by the gate (collision sweep, both ways) |
| Client pulls it (fresh session) | the client walks through | the host is blocked |
| Host rests at `Checkpoint17` | `LastTouchedCheckpoint` and `SavePointsVisited` = `GR_Camp_Save` | client save unchanged (and the other way round) |
| After the host's actions | story counters such as `TotalChestsOpened`, `BrokeBarricade`, `CheckpointUnlockCount` set | missing |
| Client leaves and rejoins after the host opened `Chest10_2` | | rejoined client sees the chest closed |

What does work: movement (the teammate model sits exactly on the player), and each player's own items and checkpoint
survive a save and reload after the session.

"State" is the object's `SavableActor_C.MySaveData` and the matching entry in the save's `SavableActorStates`.

**What we expected:** a pickup taken, a chest opened or a lever pulled by anyone would change for everyone, and a
joining player would see the host's world.

**What we noticed in the 1.0.3 source:** the protocol defines `WorldFlags`, `InteractRequest` and `InteractResult`
(Protocol.h), but nothing sends or handles them yet, so we guess world sync is planned. If it helps, the
`SavableActorStates` map (GUID to int) plus calling `RestoreFromSave` on the actor looks like a natural thing to sync:
every chest, door, lever, secret wall, barricade and pickup goes through it.

Happy to rerun anything on a newer build.
