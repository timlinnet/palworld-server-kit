"""Maintain a single self-updating status message in Discord.

Instead of posting a new message every cycle, this creates ONE message and
edits it in place forever. The channel stays clean; the message is always
current. Pin it once and it's a permanent live dashboard.

The message id is stored in state.json so restarts keep editing the same
message rather than orphaning it and starting a new one.

Runs on the VPS beside the server. Standard library only.

Config (see bridge.env - shared with the bridge):
    PAL_API           default http://127.0.0.1:8212/v1/api
    ADMIN_PASSWORD    required
    DISCORD_WEBHOOK   required
    GAME_ADDRESS      shown as the PC join address
    SERVER_PASSWORD   shown as the join password
    BOARD_SECONDS     default 30
    STATE_FILE        default /home/ubuntu/palworld/board_state.json
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
GAME_ADDRESS = os.environ.get("GAME_ADDRESS", "")
SERVER_PASSWORD = os.environ.get("SERVER_PASSWORD", "")
BOARD_SECONDS = float(os.environ.get("BOARD_SECONDS", "30"))
STATE_FILE = os.environ.get("STATE_FILE", "/home/ubuntu/palworld/board_state.json")

# Discord rejects the default urllib agent with a 403 at its edge.
USER_AGENT = "PalworldStatusBoard/1.0 (+https://palworldgame.com)"

GREEN = 0x2ECC71
RED = 0xE74C3C


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def pal(path):
    req = urllib.request.Request(f"{PAL_API}/{path}")
    token = base64.b64encode(f"admin:{ADMIN_PASSWORD}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode().strip()
    return json.loads(body) if body else {}


def discord(method, url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", USER_AGENT)
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode().strip()
    return json.loads(body) if body else {}


def load_state():
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as fh:
        json.dump(state, fh)


def fmt_uptime(seconds):
    seconds = int(seconds or 0)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def build_embed():
    """Return the embed dict describing current server state."""
    try:
        info = pal("info")
        metrics = pal("metrics")
        players = (pal("players") or {}).get("players", [])
    except Exception as e:
        return {
            "title": "Palworld server",
            "description": ":red_circle: **Server is not responding.**",
            "color": RED,
            "footer": {"text": f"Checked {time.strftime('%H:%M')} - {e}"[:2048]},
        }

    players.sort(key=lambda p: (-int(p.get("level") or 0), p.get("name") or ""))

    if players:
        rows = [f"{'PLAYER':<18}{'LVL':>4}{'PING':>7}", "-" * 29]
        for p in players:
            name = (p.get("name") or "?")[:17]
            lvl = int(p.get("level") or 0)
            ping = f"{round(p.get('ping') or 0)}ms"
            rows.append(f"{name:<18}{lvl:>4}{ping:>7}")
        roster = "```\n" + "\n".join(rows) + "\n```"
    else:
        roster = "_Nobody online right now._"

    join = f"**PC:** `{GAME_ADDRESS}`\n**Xbox / PS5:** Community Servers -> search **{info.get('servername','')}**"
    if SERVER_PASSWORD:
        join += f"\n**Password:** `{SERVER_PASSWORD}`"

    return {
        "title": f":green_circle: {info.get('servername', 'Palworld server')}",
        "description": roster + "\n" + join,
        "color": GREEN,
        "fields": [
            {"name": "Online", "value": f"{metrics.get('currentplayernum', 0)} / "
                                        f"{metrics.get('maxplayernum', '?')}", "inline": True},
            {"name": "Server FPS", "value": str(metrics.get("serverfps", "?")), "inline": True},
            {"name": "In-game day", "value": str(metrics.get("days", "?")), "inline": True},
            {"name": "Uptime", "value": fmt_uptime(metrics.get("uptime")), "inline": True},
            {"name": "Base camps", "value": str(metrics.get("basecampnum", 0)), "inline": True},
            {"name": "Version", "value": info.get("version", "?"), "inline": True},
        ],
        "footer": {"text": f"Live - updates every {int(BOARD_SECONDS)}s - "
                           f"last {time.strftime('%H:%M:%S')}"},
    }


def main():
    missing = [n for n, v in (("ADMIN_PASSWORD", ADMIN_PASSWORD),
                              ("DISCORD_WEBHOOK", DISCORD_WEBHOOK)) if not v]
    if missing:
        sys.exit(f"Missing required env: {', '.join(missing)}")

    state = load_state()
    message_id = state.get("message_id")
    log(f"status board starting (existing message: {message_id})")

    while True:
        embed = build_embed()
        payload = {"embeds": [embed], "allowed_mentions": {"parse": []}}

        try:
            if message_id:
                # Edit in place. If the message was deleted by hand, Discord
                # 404s - drop the id and create a fresh one next loop.
                discord("PATCH",
                        f"{DISCORD_WEBHOOK}/messages/{message_id}", payload)
            else:
                created = discord("POST", f"{DISCORD_WEBHOOK}?wait=true", payload)
                message_id = created.get("id")
                save_state({"message_id": message_id})
                log(f"created status message {message_id}")
        except urllib.error.HTTPError as e:
            if e.code == 404 and message_id:
                log("status message gone - will recreate")
                message_id = None
                save_state({})
            else:
                log(f"discord error: {e}")
        except Exception as e:
            log(f"update failed: {e}")

        time.sleep(BOARD_SECONDS)


if __name__ == "__main__":
    main()
