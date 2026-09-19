# The setup prompt

You don't have to read the rest of this repo. Copy everything in the box below,
paste it into Claude or ChatGPT, and answer its questions. It will walk you from
"I have nothing" to "my friends are playing on my server."

Works best with a model that can browse or run commands, but plain chat is fine —
it will just hand you commands to paste into your own terminal instead.

---

```text
You are helping me stand up a dedicated Palworld server that my friends on PC and
Xbox can both join. I am not a sysadmin. Explain each step in plain language, give
me one command at a time, and wait for me to tell you the result before moving on.

Use this repo as the source of truth for the server config:
https://github.com/timlinnet/palworld-server-kit

THE CONSTRAINT THAT DECIDES EVERYTHING:
Xbox consoles cannot type in an IP address. Console players can only join servers
that show up in the in-game Community Servers list. So the server MUST run with
COMMUNITY=true, MUST have a server password set, and UDP port 8211 MUST be
reachable from the public internet. A private IP-only server is invisible to Xbox
players. There is no workaround. Do not suggest one.

WHAT I NEED YOU TO DO, IN ORDER:

1. Help me pick and rent a VPS. Requirements: x86 (not ARM), at least 16 GB RAM,
   4+ cores, SSD, Ubuntu 22.04 or 24.04, unmetered or generous bandwidth, and a
   provider that lets me open arbitrary UDP ports. Give me 2-3 concrete options
   with current monthly prices, including at least one budget European host
   (Contabo, Hetzner) and one mainstream one. Tell me which you'd pick and why.
   Ask me where my friends live before you answer — latency follows the players,
   not me.

2. Walk me through creating the server, setting up SSH key login, and disabling
   password login for root. Do not let me skip this part.

3. Install Docker and Docker Compose on the box.

4. Clone the repo above into ~/palworld, copy .env.example to .env, and help me
   fill it in. Generate a strong ADMIN_PASSWORD for me and make it different from
   SERVER_PASSWORD. Tell me to save both somewhere safe before we continue.

5. Open the firewall: UDP 8211 and UDP 27015 inbound, public. TCP 8212 must stay
   closed to the internet — it's the REST API and it's HTTP Basic auth over a
   plain socket, so anyone who reaches it with the admin password owns the server.
   Check the provider's own firewall or security group too, not just ufw.

6. Bring it up with `docker compose up -d`, then tail the logs. First boot pulls
   about 15 GB of game files, so tell me to expect several minutes of apparent
   nothing. Tell me what "ready" looks like in the log output.

7. Verify from outside: confirm UDP 8211 is actually reachable, and confirm the
   server name appears in the in-game Community Servers list. If it doesn't
   appear, diagnose in this order: provider firewall, host firewall, COMMUNITY
   setting, SERVER_PASSWORD missing.

8. Set up automatic backups. The compose file already enables them every 6 hours
   with 14-day retention — confirm they're actually landing on disk, and show me
   how to restore one.

9. Optional, ask me first: the repo has a Discord bridge (bridge/) that posts
   join/leave/level-up events to a channel, and a local dashboard (dashboard/)
   that reaches the REST API over an SSH tunnel. Set up whichever I want.

RULES:
- Never tell me to expose port 8212 to the internet.
- Never put a password in a command I'd paste into a shared terminal or a file
  that gets committed. .env is gitignored — keep it that way.
- If I ask you to skip the SSH hardening or the firewall step, push back once.
- Tell me the running monthly cost at the end, and how to shut it all down and
  stop paying when we're done with the world.
```

---

## After it's running

Your friends join from **Join Multiplayer Game → Community Servers**, search for
your exact `SERVER_NAME`, and enter `SERVER_PASSWORD`. If it doesn't show up, the
server isn't reachable on UDP 8211 from outside — check the provider's security
group first, then the host firewall. That's the failure ~90% of the time.

## When you're done with the world

```bash
docker compose down
```

Then destroy the VPS at your provider so you stop paying for it. Copy the
`palworld/` directory off the box first if you want to keep the save.
