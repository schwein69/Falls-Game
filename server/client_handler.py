from redis_state import get_player_match, load_match_state, set_player_status

def try_resume(conn, ip):
    match_id = get_player_match(ip)
    if not match_id:
        return False

    state = load_match_state(match_id)
    if not state:
        return False

    set_player_status(match_id, ip, "active")
    conn.sendall(f"[RESUME] Reconnected to match {match_id}\n".encode())
    return True
