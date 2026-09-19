"""Local management dashboard for a Palworld dedicated server.

Serves a small web UI and proxies it to the Palworld REST API. The admin
password lives in this process and is never sent to the browser, and both
this server and the Palworld API bind to loopback only.

    set ADMIN_PASSWORD=...        (PowerShell: $env:ADMIN_PASSWORD="...")
    python dashboard/server.py

Then open http://127.0.0.1:8080

Standard library only - nothing to install.
"""

import base64
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAL_API = os.environ.get("PAL_API", "http://127.0.0.1:8212/v1/api")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
DASH_PORT = int(os.environ.get("DASH_PORT", "8080"))
INDEX = Path(__file__).parent / "index.html"

# Shown on the page so inviting people is copy-paste. Safe to display: this
# server is listed publicly in the community browser anyway, and the page is
# bound to loopback. ADMIN_PASSWORD is deliberately NOT exposed here - it can
# kick, ban and shut down the server.
GAME_ADDRESS = os.environ.get("GAME_ADDRESS", "")
SERVER_PASSWORD = os.environ.get("SERVER_PASSWORD", "")

# Host-level health comes over SSH - the Palworld API only knows about the
# game, not the box it runs on. Cached because shelling out to ssh on every
# 5s dashboard poll would be wasteful and slow.
VPS_HOST = os.environ.get("VPS_HOST", "")
SSH_KEY = os.environ.get("SSH_KEY", "")
HEALTH_TTL = 20
_health_cache = {"at": 0.0, "data": None}

# Steam's record of what is installed vs what is current. The dedicated server
# is appid 2394010; its appmanifest lives in the bind mount, so the host can
# read the installed buildid without entering the container.
STEAM_APPID = "2394010"
STEAM_MANIFEST = os.environ.get(
    "STEAM_MANIFEST",
    f"/home/ubuntu/palworld/data/steamapps/appmanifest_{STEAM_APPID}.acf")

# --tail before --since matters: the container log is ~26 MB, and scanning it
# whole costs ~1.3s per poll versus ~0.2s when tail bounds it first.
HEALTH_CMD = (
    "read L1 L5 L15 _ < /proc/loadavg; "
    "echo \"load=$L1,$L5,$L15\"; "
    "echo \"cores=$(nproc)\"; "
    "free -m | awk '/^Mem:/{print \"mem=\"$3\",\"$2}'; "
    "df -m / | awk 'NR==2{print \"disk=\"$3\",\"$2}'; "
    "echo \"container=$(sudo docker inspect -f '{{.State.Health.Status}}' palworld-server 2>/dev/null)\"; "
    "echo \"bridge=$(systemctl is-active palworld-bridge)\"; "
    "echo \"board=$(systemctl is-active palworld-statusboard)\"; "
    f"echo \"buildid=$(grep -m1 buildid {STEAM_MANIFEST} 2>/dev/null | tr -dc '0-9')\"; "
    "echo \"steamprog=$(sudo docker logs --tail 200 --since 120s palworld-server 2>&1 "
    "| grep -oE 'progress: [0-9.]+' | tail -1 | tr -dc '0-9.')\""
)


def host_health():
    """Fetch host metrics over SSH, cached. Returns dict or {'error': ...}."""
    now = time.time()
    if _health_cache["data"] is not None and now - _health_cache["at"] < HEALTH_TTL:
        return _health_cache["data"]
    if not (VPS_HOST and SSH_KEY):
        return {"error": "VPS_HOST/SSH_KEY not configured"}

    try:
        out = subprocess.run(
            ["ssh", "-i", SSH_KEY, "-o", "BatchMode=yes",
             "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=no",
             VPS_HOST, HEALTH_CMD],
            capture_output=True, text=True, timeout=20,
        )
        if out.returncode != 0:
            raise RuntimeError((out.stderr or "ssh failed").strip()[:200])

        data = {}
        for line in out.stdout.splitlines():
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            data[key.strip()] = val.strip()

        parsed = {
            "load": [float(x) for x in data.get("load", "0,0,0").split(",")],
            "cores": int(data.get("cores") or 0),
            "mem_used_mb": int(data.get("mem", "0,0").split(",")[0] or 0),
            "mem_total_mb": int(data.get("mem", "0,0").split(",")[1] or 0),
            "disk_used_mb": int(data.get("disk", "0,0").split(",")[0] or 0),
            "disk_total_mb": int(data.get("disk", "0,0").split(",")[1] or 0),
            "container": data.get("container") or "unknown",
            "bridge": data.get("bridge") or "unknown",
            "board": data.get("board") or "unknown",
            "buildid": data.get("buildid") or "",
            "steamprog": data.get("steamprog") or "",
        }
    except Exception as e:
        parsed = {"error": str(e)[:200]}

    _health_cache.update(at=now, data=parsed)
    return parsed


# --- is an update actually needed? ----------------------------------------
# Steam's public buildid for the dedicated server, which is exactly what
# steamcmd compares against on boot. If it matches what is installed, pressing
# update would re-download nothing and cost ten minutes of downtime.
STEAM_INFO_URL = f"https://api.steamcmd.net/v1/info/{STEAM_APPID}"
LATEST_TTL = 600
LATEST_FAIL_TTL = 60  # retry sooner when the lookup failed
_latest_cache = {"at": 0.0, "buildid": ""}


def latest_buildid():
    """Latest public build on Steam, or '' if it can't be determined."""
    now = time.time()
    ttl = LATEST_TTL if _latest_cache["buildid"] else LATEST_FAIL_TTL
    if _latest_cache["at"] and now - _latest_cache["at"] < ttl:
        return _latest_cache["buildid"]
    try:
        req = urllib.request.Request(STEAM_INFO_URL,
                                     headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
        branch = data["data"][STEAM_APPID]["depots"]["branches"]["public"]
        build = str(branch["buildid"])
    except Exception:
        # Unknown is not "up to date" - callers must leave the button enabled
        # rather than lock the operator out of the one control that fixes patch day.
        build = ""
    _latest_cache.update(at=now, buildid=build)
    return build


def update_status():
    """Update-job state plus whether pressing update would do anything.

    Two independent reasons the button should be locked:

      busy    - the container is downloading or booting. Restarting now kills
                an in-flight steamcmd download and starts the ~5 GB fetch over.
      current - the installed build already matches Steam's latest, so there is
                nothing to fetch and a press is pure downtime.

    Anything we cannot determine resolves to "enabled". A wrongly-enabled
    button costs a needless restart; a wrongly-disabled one blocks the only
    fix for the server vanishing from the community browser.
    """
    out = dict(_update)
    health = host_health()
    installed = health.get("buildid", "")
    container = health.get("container", "")
    latest = latest_buildid()

    busy = out["state"] == "running" or container == "starting"
    current = bool(installed and latest and installed == latest)

    if busy:
        reason = "busy"
    elif current:
        reason = "current"
    elif installed and latest:
        reason = "outdated"
    else:
        reason = "unknown"

    out.update(busy=busy, current=current, reason=reason, container=container,
               installed_build=installed, latest_build=latest,
               progress=health.get("steamprog", ""))
    return out


# --- update & restart -----------------------------------------------------
# The container runs with UPDATE_ON_BOOT=true, so a plain docker restart makes
# steamcmd fetch the latest server build before the game boots. The world is a
# bind mount under ~/palworld/data and is never touched; we save it first
# anyway. This exists because AUTO_UPDATE is off: after every Palworld patch
# the server stays on the old version until someone restarts it, and clients
# on the new version can no longer see it in the community browser.
RESTART_CMD = "sudo docker restart palworld-server"
UPDATE_WARN_SECONDS = 60
UPDATE_BOOT_TIMEOUT = 12 * 60

_update = {"state": "idle", "step": "", "error": "",
           "old_version": "", "new_version": ""}
_update_lock = threading.Lock()


def _run_update():
    try:
        _, info = pal("GET", "info")
        _update["old_version"] = info.get("version", "")

        status, players = pal("GET", "players")
        online = players.get("players", []) if status == 200 else []
        if online:
            _update["step"] = f"warning {len(online)} player(s) in-game"
            pal("POST", "announce", {"message":
                f"Server updating in {UPDATE_WARN_SECONDS} seconds - you will "
                "be disconnected. Update your own game to rejoin!"})
            time.sleep(UPDATE_WARN_SECONDS)

        _update["step"] = "saving world"
        pal("POST", "save", {})
        time.sleep(10)  # let the save flush before the container gets SIGTERM

        _update["step"] = "restarting server - steamcmd is fetching the patch"
        out = subprocess.run(
            ["ssh", "-i", SSH_KEY, "-o", "BatchMode=yes",
             "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=no",
             VPS_HOST, RESTART_CMD],
            capture_output=True, text=True, timeout=120,
        )
        if out.returncode != 0:
            raise RuntimeError((out.stderr or "ssh restart failed").strip()[:200])

        # The container is now 'starting'; drop the cached health so the UI
        # sees that on its next poll instead of up to 20s of stale 'healthy'.
        _health_cache["at"] = 0.0

        _update["step"] = "waiting for the server to come back up"
        deadline = time.time() + UPDATE_BOOT_TIMEOUT
        while time.time() < deadline:
            time.sleep(15)
            status, info = pal("GET", "info")
            if status == 200:
                # Force a fresh health read so the new buildid and the healthy
                # container land in the UI immediately, not 20s later.
                _health_cache["at"] = 0.0
                _update.update(state="done", step="",
                               new_version=info.get("version", ""))
                return
        raise RuntimeError("Server did not come back within "
                           f"{UPDATE_BOOT_TIMEOUT // 60} minutes - check it "
                           "over SSH before retrying.")
    except Exception as e:
        _update.update(state="failed", step="", error=str(e)[:300])


def start_update():
    """Kick off the update thread. Returns (status, payload) for the browser."""
    if not (VPS_HOST and SSH_KEY):
        return 400, {"error": "VPS_HOST/SSH_KEY not configured"}
    # Checked outside the lock: it can hit SSH on a cold cache, and holding the
    # lock across that would stall the status endpoint. Deliberately does NOT
    # refuse when merely up to date - the UI greys that case out but lets you
    # override it, because a wedged-but-current server still needs a restart.
    if host_health().get("container") == "starting":
        return 409, {"error": "The server is already downloading or booting an "
                              "update. Wait for it to finish - restarting now "
                              "would start the ~5 GB download over."}
    with _update_lock:
        if _update["state"] == "running":
            return 409, {"error": "An update is already running."}
        _update.update(state="running", step="starting", error="",
                       old_version="", new_version="")
    threading.Thread(target=_run_update, daemon=True).start()
    return 200, {"ok": True}


# Only these may be called from the browser. Anything not listed is refused,
# so a stray tab cannot reach /shutdown or /stop.
ALLOWED = {
    "GET": {"info", "players", "metrics", "settings"},
    "POST": {"announce", "kick", "ban", "unban", "save"},
}


def pal(method, path, payload=None):
    """Call the Palworld REST API. Returns (status, parsed_json_or_none)."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{PAL_API}/{path}", data=data, method=method)
    token = base64.b64encode(f"admin:{ADMIN_PASSWORD}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode().strip()
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": f"API returned {e.code}. Check ADMIN_PASSWORD."}
    except urllib.error.URLError as e:
        return 502, {"error": f"Cannot reach the server API ({e.reason}). "
                              "Is PalServer running with RESTAPIEnabled=True?"}
    except json.JSONDecodeError:
        return 502, {"error": "API returned a response that was not JSON."}
    except OSError as e:
        # e.g. RemoteDisconnected while the server is booting: the SSH tunnel
        # accepts the connection locally, then drops it when the far end
        # refuses. Report it like any other unreachable-server condition so
        # callers (the update wait loop especially) just retry.
        return 502, {"error": f"Connection dropped ({e}) - server may be starting."}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, payload, content_type="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/health":
            return self._send(200, host_health())
        if self.path == "/api/update":
            return self._send(200, update_status())
        if self.path == "/api/invite":
            # Served locally, never proxied - the game server has no such endpoint.
            return self._send(200, {"address": GAME_ADDRESS,
                                    "password": SERVER_PASSWORD})
        if self.path.startswith("/api/"):
            name = self.path[len("/api/"):]
            if name not in ALLOWED["GET"]:
                return self._send(404, {"error": f"Unknown endpoint '{name}'."})
            return self._send(*pal("GET", name))
        self._send(404, {"error": "Not found"})

    def do_POST(self):
        if not self.path.startswith("/api/"):
            return self._send(404, {"error": "Not found"})
        if self.path == "/api/update":
            return self._send(*start_update())
        name = self.path[len("/api/"):]
        if name not in ALLOWED["POST"]:
            return self._send(404, {"error": f"Unknown endpoint '{name}'."})

        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length)) if length else {}
        except json.JSONDecodeError:
            return self._send(400, {"error": "Malformed request body."})
        self._send(*pal("POST", name, payload))

    def log_message(self, *args):
        pass  # quiet - the dashboard polls constantly


def main():
    if not ADMIN_PASSWORD:
        sys.exit("ADMIN_PASSWORD is not set. It must match AdminPassword in "
                 "PalWorldSettings.ini.")

    status, info = pal("GET", "info")
    if status != 200:
        print(f"Warning: {info.get('error')}", file=sys.stderr)
        print("Starting anyway - the dashboard will connect once the server is up.\n")
    else:
        print(f"Connected to: {info.get('servername', 'Palworld server')}")

    print(f"Dashboard: http://127.0.0.1:{DASH_PORT}  (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", DASH_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
