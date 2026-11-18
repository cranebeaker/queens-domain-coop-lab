# DRAFT: host's player sync stops for good after the host dies (access violation in the playersync ticker)

Draft for the Knights' Domain author. Agree on it in a PR before anyone posts it. This one looks like the most
serious of our findings: after it, the two players no longer see each other until they restart the session.

---

Hi! With a test rig (two copies of the game on one PC, host and client over the dev UDP transport) we had the host
die and press Continue on the Game Over screen. Usually that works well: the client sees the death animation, the
host reloads at its save point in about 9 s, and both players see each other again within a fraction of a second.
But sometimes the host's player sync crashes during that reload:

```
[08:55:30.853] [ERR] [gamethread] playersync: structured exception 0xC0000005 caught
[08:55:30.853] [ERR] [gamethread] ticker 'playersync' removed after a structured exception
[08:55:30.853] [INF] [enemysync] world changed -> Ruins_Level_00; enemy map rebuilt from scratch
```

and on the client half a second later: `[puppet] slot 0: destroyed`.

**Version:** Knights' Domain 1.0.6 built from the public `source` branch in the dev flavour (UDP loopback, two copies of
the game on one PC), Queen's Domain Steam build 26663759, Windows 11. Also measured on 1.0.3 before the 1.0.6 source
was up; it happens on both.

**Setup:** Ruins, near (3000, -2000). The host (no checkpoint rested at yet) dies through the player Blueprint's
`Kill`, then Continue on the Game Over screen; the client watches from 3 m away.

**What happens after it:** the session stays connected and enemies still sync, but neither machine shows the other
player any more (we waited 60 s), and moving either player does nothing on the other screen. Since the ticker is
removed for good, it stays like that for the rest of the session.

**How often:** 1.0.3: 2 of 6 runs (once on a clean loopback link, once with 25 +/- 25 ms lag and 2 % loss). 1.0.6:
1 of 8 runs, on a clean loopback link (worst ping 7 ms):

```
[12:31:00.686] [INF] [net] session: game thread was stalled for 2.0 s; keepalive sent 2 heartbeat(s) meanwhile
[12:31:00.688] [ERR] [gamethread] playersync: structured exception 0xC0000005 caught
[12:31:00.688] [ERR] [gamethread] ticker 'playersync' removed after a structured exception
[12:31:00.688] [INF] [rules] co-op rules OFF: original pause behaviour
[12:31:00.688] [INF] [enemysync] world changed -> Ruins_Level_00; enemy map rebuilt from scratch
```

**What we expected:** both players see each other again after the respawn, as in the other 4 runs.

**Guess (not checked):** the playersync tick runs while the old world's player or puppet is being destroyed, and
reads a pointer that is gone. Re-adding the ticker on the next world change (or checking the local character and the
puppets with `IsValid` at the start of each tick) would at least let it recover.

Happy to rerun anything on a newer build.
