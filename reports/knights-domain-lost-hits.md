# DRAFT: a hit relayed to the other player is sometimes lost when ownership flips back

Draft for the Knights' Domain author. Agree on it in a PR before anyone posts it.

---

Hi! We measured enemy health sync with a test rig (two copies of the game on one PC, host and client over the dev
UDP transport, a bridge that reads each enemy's HP on both machines). Health agrees very well; we found one case
where a hit vanishes. In case exact numbers help:

**Version:** Knights' Domain built from the public `source` branch (1.0.3) in the dev flavour, Queen's Domain Steam
build 26663759, Windows 10 (the mod logs `unknown game build` on it). We know 1.0.6 changed attack logic; we will rerun
this when the 1.0.6 source is up.

**Setup:** Ruins, one `Mossy_Fighter_BP_C` (67.2 HP with the 2-player scaling). Host and client stand next to it
and take turns hitting it with `ehit <enemy> 3`, one hit every ~1.2 s, until it dies. After every hit we read its HP
on both machines.

**What we saw** (two fresh runs):

| | Run 1 | Run 2 |
|---|---|---|
| Hits until it died | 23 | 27 |
| HP difference between host and client, worst | 0.0 | 0.0 |
| Died on both at the same hit | yes | yes |
| Hits that did nothing | none | host's hits 3, 5, 7, 9 |

With the last-hitter rule the owner changes on every hit: the client's hit makes the host hand the enemy to P2,
the host's next hit is sent to P2 (`hit ... sent to its owner P2`), and the host hands the enemy back to P1. In run 2
the client logged `owner ...: P2 -> P1` about 3 ms after the host sent each lost hit, and neither side logged a
rejected request or `relayed hit ... sent back to the host`. The enemy's HP stayed the same on both machines, so the
hit was not applied anywhere (or was applied on P2 and then overwritten by P1's older HP when P1 took it back).

**What we expected:** every hit lands once, whoever owns the enemy at that moment.

**Idea, if useful:** apply the hit on whichever side becomes the owner, or keep the owner for the length of one
round trip after it sends a hit away, so the hit and the hand-over cannot cross.

Happy to rerun anything on a newer build.
