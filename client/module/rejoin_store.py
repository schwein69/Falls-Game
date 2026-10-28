import glob
import json
import os
import time

from config import REJOIN_TIMER

# One file per player in a ".rejoin" folder next to this module: .rejoin/player_<id>.json
_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".rejoin")
MAX_AGE = REJOIN_TIMER + 5   
MIN_STALE = 2.0              # younger than this = the file is still being rewritten, so that game is still running here


def _path(player_id):
    return os.path.join(_DIR, f"player_{player_id}.json")


def save(player_id, name, mode):
    """Write our identity and the time. mode is "p2p" or "online" (it tells the rejoin where to look for the match)."""
    try:
        os.makedirs(_DIR, exist_ok=True)
        with open(_path(player_id) + ".tmp", "w") as f:
            json.dump({"player_id": player_id, "name": name, "mode": mode, "timestamp": time.time()}, f)
        os.replace(_path(player_id) + ".tmp", _path(player_id))    
    except Exception as e:
        print(f"[Rejoin] Impossibile salvare: {e}")             


def load():
    """Return the data of the most recent interrupted match, or None. Called once at startup."""
    now, best = time.time(), None
    for path in glob.glob(os.path.join(_DIR, "player_*.json")):  
        try:
            with open(path) as f:
                data = json.load(f)
            age = now - data["timestamp"]
            if age > MAX_AGE:
                os.remove(path)                                  # too old: the host has already removed us
            elif age >= MIN_STALE and (best is None or data["timestamp"] > best["timestamp"]):
                best = data                                      # valid; keep the newest
        except Exception:
            continue                                             # unreadable or damaged file: skip it
    return best


def clear(player_id):
    """Delete the file: the match ended, we were eliminated, or we left on purpose (nothing to rejoin)."""
    try:
        if player_id is not None and os.path.exists(_path(player_id)):
            os.remove(_path(player_id))
    except Exception as e:
        print(f"[Rejoin] Impossibile cancellare: {e}")