"""Watch the Palworld REST API and emit structured events.

This is the integration seam for anything smarter (see README, Phase 2).
It deliberately has no dependencies beyond the standard library so you can
drop it on the server and run it with nothing installed.

    ADMIN_PASSWORD=... python3 watch.py

The API is HTTP Basic auth as user "admin" over plain HTTP, so this must run
on the server itself or through an SSH tunnel. Do not point it at a public
port - that would be handing over the server.
"""

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("PAL_API", "http://127.0.0.1:8212/v1/api")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "10"))


def call(path, payload=None):
    """GET, or POST when payload is given. Returns parsed JSON or None."""
    url = f"{BASE}/{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    token = base64.b64encode(f"admin:{ADMIN_PASSWORD}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")

    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode().strip()
    return json.loads(body) if body else None


def announce(message):
    """Broadcast a message in-game. Visible to every connected player."""
    call("announce", {"message": message})


def emit(event, **fields):
    """One JSON object per line - pipe it into whatever you like."""
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **fields}
    print(json.dumps(record), flush=True)


def main():
    if not ADMIN_PASSWORD:
        sys.exit("ADMIN_PASSWORD is not set. It must match .env on the server.")

    try:
        info = call("info")
    except urllib.error.HTTPError as e:
        sys.exit(f"API rejected the request ({e.code}). Check ADMIN_PASSWORD.")
    except urllib.error.URLError as e:
        sys.exit(f"Cannot reach {BASE} ({e.reason}). Is the container up, and "
                 "are you on the server or tunnelled in?")
    emit("connected", server=info)

    known = {}
    while True:
        try:
            players = {p["playerId"]: p for p in (call("players") or {}).get("players", [])}
        except (urllib.error.URLError, json.JSONDecodeError) as e:
            # A restart or save pause shows up here. Keep the loop alive.
            emit("api_error", detail=str(e))
            time.sleep(POLL_SECONDS)
            continue

        for pid, p in players.items():
            if pid not in known:
                emit("join", name=p.get("name"), level=p.get("level"), player_id=pid)
        for pid, p in known.items():
            if pid not in players:
                emit("leave", name=p.get("name"), player_id=pid)

        known = players
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
