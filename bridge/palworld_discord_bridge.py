"""Bridge the Palworld server to Discord.

Polls the REST API and posts to a Discord webhook when players join, leave,
or level up. Level-ups are also announced in-game.

Runs on the VPS alongside the server, so it keeps working when your PC is
off. Standard library only - nothing to install.

Config comes from the environment (see bridge.env):
    PAL_API           default http://127.0.0.1:8212/v1/api
    ADMIN_PASSWORD    required - REST API password
    DISCORD_WEBHOOK   required - webhook URL for #palworld
    POLL_SECONDS      default 15
    ANNOUNCE_IN_GAME  default 1 - also announce level-ups to players in-game
"""

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

PAL_API = os.environ.get("PAL_API", "http://127.0.0.1:8212/v1/api")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "15"))
ANNOUNCE_IN_GAME = os.environ.get("ANNOUNCE_IN_GAME", "1") == "1"
# Announce only every Nth level. Every single level is noise once people get
# going, and early levels come fast.
LEVEL_STEP = max(1, int(os.environ.get("LEVEL_STEP", "5")))

# Consecutive API failures before we assume the server is down and say so once.
DOWN_AFTER = 4


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def pal(path, payload=None):
    """Call the Palworld REST API. Raises on failure."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{PAL_API}/{path}", data=data,
                                 method="POST" if data else "GET")
    token = base64.b64encode(f"admin:{ADMIN_PASSWORD}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode().strip()
    return json.loads(body) if body else {}


def discord(content):
    """Post to the webhook. Never raises - Discord being down must not kill
    the bridge, and a failed post is not worth losing player state over."""
    payload = json.dumps({
        "content": content,
        # Belt and braces: the bot should never be able to ping the channel.
        "allowed_mentions": {"parse": []},
    }).encode()
    req = urllib.request.Request(DISCORD_WEBHOOK, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    # Discord's edge blocks the default "Python-urllib/x.y" agent with a 403.
    # curl works, urllib doesn't - took a while to spot. Any sane UA passes.
    req.add_header("User-Agent",
                   "PalworldDiscordBridge/1.0 (+https://palworldgame.com)")
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        log(f"discord post failed: {e}")


def announce(message):
    if not ANNOUNCE_IN_GAME:
        return
    try:
        pal("announce", {"message": message})
    except Exception as e:
        log(f"in-game announce failed: {e}")


def main():
    missing = [n for n, v in (("ADMIN_PASSWORD", ADMIN_PASSWORD),
                              ("DISCORD_WEBHOOK", DISCORD_WEBHOOK)) if not v]
    if missing:
        sys.exit(f"Missing required env: {', '.join(missing)}")

    # playerId -> {"name": str, "level": int}
    known = {}
    baselined = False
    failures = 0
    reported_down = False

    log(f"bridge starting, polling {PAL_API} every {POLL_SECONDS}s")

    while True:
        try:
            players = (pal("players") or {}).get("players", [])
            failures = 0
            if reported_down:
                discord(":white_check_mark: Server is back up.")
                reported_down = False
                # State is stale after an outage - rebaseline rather than
                # spamming a join line for everyone still connected.
                known = {}
                baselined = False
        except Exception as e:
            failures += 1
            log(f"api error ({failures}): {e}")
            if failures >= DOWN_AFTER and not reported_down:
                discord(":warning: Palworld server is not responding.")
                reported_down = True
            time.sleep(POLL_SECONDS)
            continue

        current = {
            p["playerId"]: {"name": p.get("name") or "(unknown)",
                            "level": int(p.get("level") or 0)}
            for p in players if p.get("playerId")
        }

        # First successful poll after start just records who's already on -
        # otherwise a restart would announce everyone joining again.
        if not baselined:
            known = current
            baselined = True
            log(f"baselined with {len(known)} player(s) online")
            time.sleep(POLL_SECONDS)
            continue

        for pid, p in current.items():
            was = known.get(pid)
            if was is None:
                discord(f":green_circle: **{p['name']}** joined "
                        f"(level {p['level']}) - {len(current)} online")
                log(f"join: {p['name']} lvl {p['level']}")
            elif p["level"] > was["level"]:
                log(f"levelup: {p['name']} {was['level']} -> {p['level']}")
                # Floor-division compare, not "level % 5 == 0", so a jump from
                # 4 to 6 still counts as crossing the 5 milestone.
                if p["level"] // LEVEL_STEP > was["level"] // LEVEL_STEP:
                    discord(f":arrow_up: **{p['name']}** reached "
                            f"**level {p['level']}**")
                    announce(f"{p['name']} reached level {p['level']}!")

        for pid, p in known.items():
            if pid not in current:
                discord(f":red_circle: **{p['name']}** left - "
                        f"{len(current)} online")
                log(f"leave: {p['name']}")

        known = current
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
