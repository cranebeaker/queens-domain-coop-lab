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

**What we did and saw** (each case ran twice from a fresh start, same result):

| Action | On the player who did it | On the other player |
|---|---|---|
| Host picks up `JarHammer_Pickup_2` | item in inventory, pickup marked taken in the save | pickup still there, no save entry |
| Client picks up `Herb_Item_BP_2` | item in inventory, marked taken | pickup still there on the host |
| Host opens `Chest10_2` | chest state 1 (live and in save) | chest still closed |
| Host pulls `AquaLever_23` | lever state 1 | lever unchanged |
| Client opens `SecretWall11_2` | wall state 1 | wall unchanged on the host |
| Host rests at `Checkpoint17` | `LastTouchedCheckpoint` and `SavePointsVisited` = `GR_Camp_Save` | client save unchanged |
| Client leaves and rejoins after the host opened `Chest10_2` | | rejoined client sees the chest closed |

"State" is the object's `SavableActor_C.MySaveData` and the matching entry in the save's `SavableActorStates`.

**What we expected:** a pickup taken, a chest opened or a lever pulled by anyone would change for everyone, and a
joining player would see the host's world.

**What we noticed in the 1.0.3 source:** the protocol defines `WorldFlags`, `InteractRequest` and `InteractResult`
(Protocol.h), but nothing sends or handles them yet, so we guess world sync is planned. If it helps, the
`SavableActorStates` map (GUID to int) plus calling `RestoreFromSave` on the actor looks like a natural thing to sync:
every chest, door, lever, secret wall, barricade and pickup goes through it.

Happy to rerun anything on a newer build.
