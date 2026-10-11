import glob
import json
import os
import time

from config import REJOIN_TIMER

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".rejoin")
MAX_AGE = REJOIN_TIMER + 5   
MIN_STALE = 2.0              


def _path(player_id):
    return os.path.join(_DIR, f"player_{player_id}.json")


def save(player_id, name, mode):
    try:
        os.makedirs(_DIR, exist_ok=True)
        with open(_path(player_id) + ".tmp", "w") as f:
            json.dump({"player_id": player_id, "name": name, "mode": mode, "timestamp": time.time()}, f)
        os.replace(_path(player_id) + ".tmp", _path(player_id))    
    except Exception as e:
        print(f"[Rejoin] Impossibile salvare: {e}")


def load():
    now, best = time.time(), None
    for path in glob.glob(os.path.join(_DIR, "player_*.json")):
        try:
            with open(path) as f:
                data = json.load(f)
            age = now - data["timestamp"]
            if age > MAX_AGE:
                os.remove(path)
            elif age >= MIN_STALE and (best is None or data["timestamp"] > best["timestamp"]):
                best = data
        except Exception:
            continue
    return best


def clear(player_id):
    try:
        if player_id is not None and os.path.exists(_path(player_id)):
            os.remove(_path(player_id))
    except Exception as e:
        print(f"[Rejoin] Impossibile cancellare: {e}")