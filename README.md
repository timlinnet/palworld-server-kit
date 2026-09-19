# Palworld server kit

A crossplay Palworld dedicated server your PC **and Xbox** friends can actually
join, plus a REST API seam for automation. Runs anywhere Docker runs.

This ran a real 10+ player world for months. It's published because the hard
parts — the Xbox constraint, the ports, the API that will hand over your server
if you expose it — took a while to work out, and nobody should have to work them
out twice.

## Just want it running?

**[Copy the setup prompt](SETUP-PROMPT.md)** and paste it into Claude or ChatGPT.
It'll pick you a host, harden the box, open the right ports, and get your friends
connected, one step at a time. You don't need to read anything else here.

If you'd rather drive it yourself, the rest of this file is the manual.

---

## The one constraint that shapes everything

**Xbox consoles cannot type in an IP address.** Console players can only join
servers that appear in the in-game **community server list**. So:

- `COMMUNITY=true` is mandatory, not optional.
- A community server *must* have `SERVER_PASSWORD` set.
- UDP **8211** must be reachable from the public internet.

A private, IP-only server is simply invisible to Xbox players. There is no
workaround.

## Why not Fly.io

Evaluated and rejected. Fly requires UDP services to bind the special
`fly-global-services` address — `0.0.0.0` will not work, because Linux picks the
wrong source address for replies. PalServer is a closed-source UE 5.1 binary;
`-MULTIHOME=<ip>` would *probably* satisfy it, but an unverified "probably"
between your friends and the server is a bad trade. Add the required dedicated
IPv4, ~1300-byte MTU, and no port rewriting, and it's a lot of variables for a
platform built for ephemeral scale-to-zero HTTP. This is a 24/7 stateful RAM-hog
on UDP.

Any x86 VPS with 16 GB RAM and Docker is the right primitive.

## Requirements

Official spec: 4+ cores, 16 GB RAM (32 GB recommended), SSD. 16 GB is comfortable
for the 10–16 player range.

## Deploy

```bash
git clone <this repo> && cd palworld-server-kit
cp .env.example .env
$EDITOR .env          # set SERVER_NAME, SERVER_PASSWORD, ADMIN_PASSWORD
docker compose up -d
docker compose logs -f
```

First boot downloads ~15 GB of server files, so give it several minutes before
expecting anything. The server is ready when the logs settle.

Open **only** these on the host firewall:

| Port  | Proto | Purpose  | Public?                  |
|-------|-------|----------|--------------------------|
| 8211  | UDP   | Game     | **Yes**                  |
| 27015 | UDP   | Query    | Yes                      |
| 8212  | TCP   | REST API | **No — loopback only**   |

The compose file binds 8212 to `127.0.0.1` deliberately. The REST API is HTTP
Basic auth over a plain socket: anyone who can reach that port with the admin
password owns the server. Reach it over an SSH tunnel instead:

```bash
ssh -N -L 8212:127.0.0.1:8212 user@your-server
```

Check your provider's security group as well as the host firewall. Both have to
agree, and the provider's is the one people forget.

## How your friends join

1. Xbox: **Join Multiplayer Game → Community Servers**, search `SERVER_NAME`.
2. Enter `SERVER_PASSWORD`.

If it doesn't appear, the server is not reachable on UDP 8211 from outside —
check the provider's security group and the host firewall, in that order.

## REST API

Auth is HTTP Basic, username `admin`, password = `ADMIN_PASSWORD`.

```bash
curl -u "admin:$ADMIN_PASSWORD" http://127.0.0.1:8212/v1/api/info
curl -u "admin:$ADMIN_PASSWORD" http://127.0.0.1:8212/v1/api/players
curl -u "admin:$ADMIN_PASSWORD" http://127.0.0.1:8212/v1/api/metrics
```

Available: server info, player list, settings, announce, kick, ban, unban, save,
shutdown, force stop, world actor snapshot, metrics.

RCON is deprecated upstream and is disabled here. Use the REST API.

## Discord bridge

`bridge/palworld_discord_bridge.py` polls the REST API and posts to a Discord
webhook. It runs **on the VPS**, not a desktop, so it keeps reporting when your
PC is off.

Posts on player **join**, **leave**, and **level-up**. Level-ups are also
announced in-game. If the API stops responding for 4 consecutive polls it posts
one "server not responding" warning, then one "back up" when it recovers — not a
message per failed poll.

Deployed as a systemd unit (`palworld-bridge.service`, enabled at boot):

```bash
sudo systemctl status palworld-bridge
sudo journalctl -u palworld-bridge -f
```

Config lives in `~/palworld/bridge.env` on the server, mode `600` — it holds the
admin password and the webhook URL. Anyone with that webhook URL can post to the
channel as the bot, so it stays off this repo and out of git.

On start it **baselines** the current player list rather than announcing everyone
as a fresh join. Same after an outage.

`bridge/status_board.py` keeps one self-updating status message in a channel
instead of a stream of them. `bridge/narrator.py` broadcasts a scripted story
beat on level-ups — `bridge/story.json` is an example; rewrite it for your world.

## Dashboard

`dashboard/` is a small local web UI for the REST API: players online, uptime,
announce, kick, restart, and a patch-day update check.

```powershell
.\start-dashboard.ps1          # server on this PC
.\start-dashboard-remote.ps1   # server on a VPS — opens an SSH tunnel first
```

It binds to `127.0.0.1` only and keeps the admin password in the Python process
rather than the browser. Don't open `dashboard/index.html` as a `file://` page —
its API calls will have nothing to talk to.

For the remote launcher, set these first (or edit the top of the script):

```powershell
$env:PAL_VPS_HOST  = "user@your.server.ip"
$env:PAL_GAME_ADDR = "your.server.ip:8211"   # optional, derived from the above
$env:PAL_PYTHON    = "C:\path\to\python.exe" # optional, defaults to `python` on PATH
```

It expects your VPS private key at `~/.ssh/palworld_vps`.

## Automation

`agent/watch.py` polls the API and emits one JSON event per line (`join`,
`leave`, `api_error`). Standard library only — no install step.

```bash
ADMIN_PASSWORD=... python3 agent/watch.py
```

The seam for anything smarter is `announce()` in `agent/watch.py`. The world
actor snapshot and metrics endpoints are the interesting inputs: an agent can see
world state and player positions, not just a chat log.

## Mods

Server-side modding in Palworld is limited; the ecosystem is overwhelmingly
client-side and built around the **Steam** build. The Microsoft Store / Game Pass
build installs under `C:\Program Files\WindowsApps`, which is ACL-locked and the
least moddable target there is.

If mods matter, buy Palworld on Steam. It doesn't strand anyone: crossplay across
Steam/Xbox/PS5 on a single dedicated server has been supported since v0.5.0, so
Steam-for-you and Xbox-for-them on this server works fine.

## Shutting it down

```bash
docker compose down
```

Copy the `palworld/` directory off the box if you want to keep the save, then
destroy the VPS so you stop paying for it.

## License

MIT. Do whatever you like with it.
