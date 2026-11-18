# DRAFT: enemy projectiles and area attacks exist only on the machine that runs the enemy

Draft for a comment on Knights' Domain issue 2 ("Enemy and Boss Special Attacks not Replicating over Network",
part ②), since that issue already covers it. Agree on it in a PR before anyone posts it.

---

Hi! We measured part ② of this issue with a small test rig: two copies of the game on one PC (host and client over
the dev UDP transport), plus a bridge that records every projectile near each player and every hit they take. In
case exact numbers help:

**Version:** Knights' Domain built from the public `source` branch (1.0.3) in the dev flavour, Queen's Domain Steam
build 26663759, Windows 10. We know 1.0.6 moved attack logic to the host, so this may already look different there;
we will rerun it when the 1.0.6 source is up.

**Setup:** Ruins, the two `Mossy_Archer_BP_C` on the ledge near (8400, -3900, 4000). The host stands 10 m from them
and keeps hitting them (so the host owns them and they target the host). The client stands exactly on the line
between the archers and the host, halfway (5 m from the archer, 0 cm off the line). Both have `god` on, so every hit
shows as a health dent instead of killing anyone. 30 seconds, done twice from a fresh start:

| | Host (targeted) | Client (in the line of fire) |
|---|---|---|
| Archers play `Boner_Bow_Attack` | yes (57 / 56 samples) | yes (56 / 55 samples) |
| Archer owner | P1 | P1 |
| Arrows (`Mossy_Arrow_Projectile_C`) seen within 60 m | 12 / 11 | 0 / 0 |
| Hits taken (17 damage each, arrow within 40 to 60 cm) | 11 / 11 | 0 / 0 |

So the client sees the archers draw and shoot, but no arrow ever exists on the client's machine. On the host's
machine the arrows fly straight through the spot where the client is standing and hit the host.

**What we expected:** the client sees the arrows, and an arrow passing through the client hits the client.

**Area attacks, same result** (a second test, also two fresh runs): the Ruins dragon boss (`Dragon_Green_BP_C`)
fights the host for 75 s while the client (god mode) is kept about 1.5 m from the host. Both machines play the same
boss montages (`Dragon_Atk_FireBreath_Montage`, `Dragon_Air_Atk_Fireballs_Montage`, ...), but:

| | Host (targeted) | Client (1.5 m away) |
|---|---|---|
| `Dragon_FirePuddle_C` seen | 59 / 16 | 0 / 0 |
| `Dragon_Fireball_C` seen | 0 / 9 | 0 / 0 |
| `Dragon_FlamebreathProjectile_C` seen | 48 / 16 | 0 / 0 |
| Hits with a puddle / fireball / flame closest | 16 + 0 + 91 / 22 + 17 + 32 | 0 / 0 |

So puddles and fireball blasts, like projectiles, only exist (and only hurt) on the machine that runs the boss.

**What we noticed in the 1.0.3 source:** followers replay `AttackStart` as the generic attack montage, which the
comment above `kCustomAttacks` in EnemySync.cpp says "shows the swing but deals no damage". The arrows, puddles and
fireballs are spawned by the attack Blueprints, which only run on the owner. One idea, if useful: a small `ProjectileSpawned` event from the
owner (class, transform, velocity) that followers spawn locally as a visual, plus the owner checking projectile
overlaps against the other players' positions, so a projectile can hit anyone in its path.

Happy to rerun anything on a newer build.
