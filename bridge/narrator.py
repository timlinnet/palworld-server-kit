"""Tell a story in-game, one beat at a time.

Broadcasts beats from story.json via the Palworld announce API. Advances when
the target player levels up, or on a timer - whichever comes first - so the
story keeps moving whether they're grinding or exploring.

Beats flagged burst:true fire immediately after the beat before them, spaced
only by burst_delay_seconds. That's the punchline: the setup is paced, the
payoff is a volley.

Progress is saved, so a story survives restarts and resumes next time the
target logs in rather than starting over.

    python3 narrator.py              # normal service mode
    python3 narrator.py --preview    # play the whole thing now, to judge it
    python3 narrator.py --reset      # rewind to the first beat

Config (bridge.env):
    PAL_API           default http://127.0.0.1:8212/v1/api
    ADMIN_PASSWORD    required
    TARGET_PLAYER     in-game name that drives the story; blank = idle
    STORY_FILE        default /home/ubuntu/palworld/story.json
    STORY_STATE       default /home/ubuntu/palworld/story_state.json
    STORY_INTERVAL    seconds between beats on the timer, default 300
    POLL_SECONDS      how often to check who's online, default 20
"""

import base64
import json
import os
import sys
import time
import urllib.request

PAL_API = os.environ.get("PAL_API", "http://127.0.0.1:8212/v1/api")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
TARGET_PLAYER = os.environ.get("TARGET_PLAYER", "").strip()
STORY_FILE = os.environ.get("STORY_FILE", "/home/ubuntu/palworld/story.json")
STORY_STATE = os.environ.get("STORY_STATE", "/home/ubuntu/palworld/story_state.json")
STORY_INTERVAL = float(os.environ.get("STORY_INTERVAL", "300"))
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "20"))


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def pal(path, payload=None):
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


def load_story():
    with open(STORY_FILE) as fh:
        story = json.load(fh)
    return story.get("beats", []), float(story.get("burst_delay_seconds", 7))


def load_state():
    try:
        with open(STORY_STATE) as fh:
            return json.load(fh)
    except Exception:
        return {"index": 0, "last_level": None}


def save_state(state):
    with open(STORY_STATE, "w") as fh:
        json.dump(state, fh)


def say(text):
    try:
        pal("announce", {"message": text})
        log(f"beat: {text}")
        return True
    except Exception as e:
        log(f"announce failed: {e}")
        return False


def emit_from(index, beats, burst_delay):
    """Say beats[index], then any consecutive burst beats after it.
    Returns the index to resume from."""
    if index >= len(beats):
        return index
    say(beats[index]["text"])
    index += 1
    while index < len(beats) and beats[index].get("burst"):
        time.sleep(burst_delay)
        say(beats[index]["text"])
        index += 1
    return index


def find_target(players):
    for p in players:
        if (p.get("name") or "").lower() == TARGET_PLAYER.lower():
            return p
    return None


def main():
    if not ADMIN_PASSWORD:
        sys.exit("ADMIN_PASSWORD is not set")

    beats, burst_delay = load_story()

    if "--reset" in sys.argv:
        save_state({"index": 0, "last_level": None})
        print("story rewound to the first beat")
        return

    if "--preview" in sys.argv:
        # Play everything now, ignoring triggers. Everyone online sees it.
        print(f"previewing {len(beats)} beats")
        i = 0
        while i < len(beats):
            nxt = emit_from(i, beats, burst_delay)
            i = nxt
            if i < len(beats):
                time.sleep(3)
        return

    if not TARGET_PLAYER:
        log("TARGET_PLAYER is not set - idling. Set it in bridge.env and restart.")

    state = load_state()
    last_beat_at = 0.0
    log(f"narrator ready: beat {state['index']}/{len(beats)}, "
        f"target={TARGET_PLAYER or '(unset)'}, interval={STORY_INTERVAL}s")

    while True:
        if not TARGET_PLAYER or state["index"] >= len(beats):
            time.sleep(POLL_SECONDS)
            continue

        try:
            players = (pal("players") or {}).get("players", [])
        except Exception as e:
            log(f"api error: {e}")
            time.sleep(POLL_SECONDS)
            continue

        target = find_target(players)
        if not target:
            # Not online - hold position, don't burn beats on an empty room.
            time.sleep(POLL_SECONDS)
            continue

        level = int(target.get("level") or 0)
        levelled = state["last_level"] is not None and level > state["last_level"]
        state["last_level"] = level

        due = (time.time() - last_beat_at) >= STORY_INTERVAL
        if levelled or due or last_beat_at == 0.0:
            state["index"] = emit_from(state["index"], beats, burst_delay)
            last_beat_at = time.time()
            if state["index"] >= len(beats):
                log("story complete")

        save_state(state)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
