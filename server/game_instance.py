import sys
import os
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))

from network_online import start_dedicated_server
from game_authority import GameAuthority
from config import MAX_PLAYERS, LOG_PREFIX

JOIN_TIMEOUT_SECONDS = 30
IDLE_SHUTDOWN_SECONDS = 20
STATUS_POLL_INTERVAL = 0.1
SNAPSHOT_INTERVAL = 0.05 


def make_rpc_handler(nm, authority):

    def handle(payload):
        method = payload.get("method")
        args = payload.get("args", [])

        if method == "update_pos":
            pid, x, y, z = args
            authority.handle_update_pos(pid, x, y, z)

        elif method == "block_step":
            pid, block_id = args
            result = authority.handle_block_step(pid, block_id)
            if result:
                nm.broadcast_rpc("block_destroyed", result["block_id"], result["pid"])

        elif method == "player_left":
            pid = args[0]
            winner_pid = authority.remove_player(pid)
            if winner_pid is not None:
                nm.broadcast_rpc("game_won", winner_pid)

        elif method == "player_died":
            pid = args[0]
            winner_pid = authority.handle_player_died(pid)
            if winner_pid is not None:
                nm.broadcast_rpc("game_won", winner_pid)

    return handle


def main():
    if len(sys.argv) < 3:
        print(f"{LOG_PREFIX} Uso: game_instance.py <porta> '<players_json>'")
        sys.exit(1)

    port = int(sys.argv[1])
    players_info = json.loads(sys.argv[2])  # lista di [pid, ip, udp_port]

    authority = GameAuthority()
    nm = start_dedicated_server(port, players_info)

    nm.on_client_joined = lambda pid, addr: nm.send_to(
        addr, "sync_state", authority.already_destroyed_blocks(), authority.snapshot_payload())

    expected_count = len(players_info)
    print(f"{LOG_PREFIX}[GameInstance:{port}] Avviata. Seed pavimento: {authority.floor_seed}. "
          f"Attendo {expected_count} giocatori: {players_info}")

    rpc_handler = make_rpc_handler(nm, authority)

    start_wait = time.time()
    while len(nm.clients) < expected_count:
        nm.process_queue(rpc_handler)
        if time.time() - start_wait > JOIN_TIMEOUT_SECONDS:
            print(f"{LOG_PREFIX}[GameInstance:{port}] Timeout: solo {len(nm.clients)}/{expected_count} "
                  f"giocatori connessi. Avvio comunque con chi c'e'.")
            break
        time.sleep(STATUS_POLL_INTERVAL)

    if len(nm.clients) == 0:
        print(f"{LOG_PREFIX}[GameInstance:{port}] Nessun giocatore si e' connesso. Chiudo.")
        nm.stop()
        return

    print(f"{LOG_PREFIX}[GameInstance:{port}] {len(nm.clients)} giocatori connessi. Via!")
    nm.broadcast_rpc("start_game", authority.floor_seed)

    nm.start_broadcast_tick(authority.snapshot_payload, interval=SNAPSHOT_INTERVAL)

    last_seen_player_time = time.time()
    try:
        while True:
            nm.process_queue(rpc_handler)
            time.sleep(STATUS_POLL_INTERVAL)
            if len(nm.clients) > 0:
                last_seen_player_time = time.time()
            elif time.time() - last_seen_player_time > IDLE_SHUTDOWN_SECONDS:
                print(f"{LOG_PREFIX}[GameInstance:{port}] Tutti i giocatori se ne sono andati. Chiudo.")
                break
    except KeyboardInterrupt:
        pass
    finally:
        nm.stop()


if __name__ == "__main__":
    main()