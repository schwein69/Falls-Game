import redis
import ast

r = redis.Redis()

def save_match_state(match_id, state):
    r.set(f"match:{match_id}:state", str(state))

def load_match_state(match_id):
    data = r.get(f"match:{match_id}:state")
    return ast.literal_eval(data.decode()) if data else None

def set_player_status(match_id, player_ip, status):
    r.set(f"match:{match_id}:player:{player_ip}", status)

def get_player_status(match_id, player_ip):
    val = r.get(f"match:{match_id}:player:{player_ip}")
    return val.decode() if val else None

def link_player_to_match(player_ip, match_id):
    r.set(f"player:{player_ip}:match_id", match_id)

def get_player_match(player_ip):
    val = r.get(f"player:{player_ip}:match_id")
    return val.decode() if val else None

def expire_player_keys(match_id, player_ip, seconds):
    r.expire(f"match:{match_id}:state", seconds)
    r.expire(f"match:{match_id}:player:{player_ip}", seconds)
    r.expire(f"player:{player_ip}:match_id", seconds)

def cleanup_match(match_id, player_ips):
    for ip in player_ips:
        r.delete(f"match:{match_id}:player:{ip}")
        r.delete(f"player:{ip}:match_id")
    r.delete(f"match:{match_id}:state")
