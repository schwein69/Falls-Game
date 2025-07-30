import threading
import time
from redis_state import *

def logic_server(match_id, players):
    print(f"[{match_id}] Starting logic server")
    player_sockets = {p[1][0]: p[0] for p in players}
    player_ips = list(player_sockets.keys())

    state = {
        "status": "active",
        "players": player_ips,
        "score": {ip: 0 for ip in player_ips}
    }
    save_match_state(match_id, state)

    for ip in player_ips:
        set_player_status(match_id, ip, "active")
        link_player_to_match(ip, match_id)
        try:
            player_sockets[ip].sendall(f"[START] Match {match_id} started\n".encode())
        except:
            set_player_status(match_id, ip, "disconnected")

    try:
        while True:
            for ip in player_ips:
                status = get_player_status(match_id, ip)
                if status != "active":
                    expire_player_keys(match_id, ip, 10)

            time.sleep(1)

            # Receive and respond to player inputs
            for ip, conn in player_sockets.items():
                try:
                    conn.settimeout(0.1)
                    msg = conn.recv(1024).decode().strip()
                    if msg:
                        print(f"[{match_id}] {ip} says: {msg}")
                        conn.sendall(f"[ECHO] {msg}\n".encode())
                except Exception:
                    continue

            if all(get_player_status(match_id, ip) == "disconnected" for ip in player_ips):
                print(f"[{match_id}] All players disconnected")
                break

    finally:
        for conn in player_sockets.values():
            try: conn.close()
            except: pass
        cleanup_match(match_id, player_ips)
        print(f"[{match_id}] Logic server shut down")
