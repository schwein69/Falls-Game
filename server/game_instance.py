import sys
import os
import json
import time
import socket
import threading
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))

from network_online import start_dedicated_server
from game_authority import GameAuthority
from config import LOG_PREFIX

JOIN_TIMEOUT_SECONDS = 30
IDLE_SHUTDOWN_SECONDS = 3
STATUS_POLL_INTERVAL = 0.1
SNAPSHOT_INTERVAL = 0.05       # 20 Hz: cadenza con cui ridistribuiamo le posizioni ai client
REPLICATION_INTERVAL = 0.2     # 5 Hz: cadenza con cui il primary replica lo stato al backup


# Gestione RPC dei client
def make_rpc_handler(nm, authority, ready_state):
    def handle(payload):
        method = payload.get("method")
        args = payload.get("args", [])

        if method == "update_pos":
            pid, x, y, z = args
            authority.handle_update_pos(pid, x, y, z)

        elif method == "player_ready":
            if ready_state["countdown_started"]:
                return
            pid = args[0]
            ready_state["ready_pids"].add(pid)
            print(f"{LOG_PREFIX} Player {pid} pronto "
                  f"({len(ready_state['ready_pids'])}/{ready_state['expected_count']})")
            if len(ready_state["ready_pids"]) >= ready_state["expected_count"]:
                ready_state["countdown_started"] = True
                nm.match_in_progress = True
                nm.broadcast_rpc("start_countdown")

        elif method == "block_step":
            pid, block_id = args
            result = authority.handle_block_step(pid, block_id)
            if result:
                nm.broadcast_rpc("block_destroyed", result["block_id"], result["pid"])

        elif method == "player_left":
            pid = args[0]
            game_over, winner_pid = authority.remove_player(pid)
            if game_over:
                nm.broadcast_rpc("game_won", winner_pid)

        elif method == "player_died":
            pid = args[0]
            game_over, winner_pid = authority.handle_player_died(pid)
            nm.broadcast_rpc("player_removed", pid)
            if game_over:
                nm.broadcast_rpc("game_won", winner_pid)

    return handle


def run_as_active_server(port, players_info, authority, log_tag):
    nm = start_dedicated_server(port, players_info)
    expected_count = len(players_info)

    def on_client_joined(pid, addr):
        nm.send_to(addr, "sync_state", authority.already_destroyed_blocks(), authority.snapshot_payload())
    nm.on_client_joined = on_client_joined

    print(f"{LOG_PREFIX}{log_tag} Avviato. Seed pavimento: {authority.floor_seed}. "
          f"Attendo {expected_count} giocatori: {players_info}")

    ready_state = {"ready_pids": set(), "countdown_started": False, "expected_count": expected_count}
    rpc_handler = make_rpc_handler(nm, authority, ready_state)

    start_wait = time.time()
    while len(nm.clients) < expected_count:
        nm.process_queue(rpc_handler)
        if time.time() - start_wait > JOIN_TIMEOUT_SECONDS:
            print(f"{LOG_PREFIX}{log_tag} Timeout: solo {len(nm.clients)}/{expected_count} "
                  f"giocatori connessi. Avvio comunque con chi c'e'.")
            break
        time.sleep(STATUS_POLL_INTERVAL)

    if len(nm.clients) == 0:
        print(f"{LOG_PREFIX}{log_tag} Nessun giocatore si e' connesso. Chiudo.")
        nm.stop()
        return

    ready_state["expected_count"] = len(nm.clients)
    print(f"{LOG_PREFIX}{log_tag} {len(nm.clients)} giocatori connessi. Via!")
    nm.broadcast_rpc("start_game", authority.floor_seed)
    nm.start_broadcast_tick(authority.snapshot_payload, interval=SNAPSHOT_INTERVAL)

  
    if (not ready_state["countdown_started"]
            and len(ready_state["ready_pids"]) >= ready_state["expected_count"]):
        ready_state["countdown_started"] = True
        nm.match_in_progress = True
        nm.broadcast_rpc("start_countdown")

   
    READY_SAFETY_NET_SECONDS = 10
    countdown_deadline = time.time() + READY_SAFETY_NET_SECONDS

    last_seen_player_time = time.time()
    try:
        while True:
            nm.process_queue(rpc_handler)
            if not ready_state["countdown_started"] and time.time() > countdown_deadline:
                print(f"{LOG_PREFIX}{log_tag} Non tutti pronti dopo {READY_SAFETY_NET_SECONDS}s, "
                      f"avvio comunque (rete di sicurezza).")
                ready_state["countdown_started"] = True
                nm.match_in_progress = True
                nm.broadcast_rpc("start_countdown")
            time.sleep(STATUS_POLL_INTERVAL)
            if len(nm.clients) > 0:
                last_seen_player_time = time.time()
            elif time.time() - last_seen_player_time > IDLE_SHUTDOWN_SECONDS:
                print(f"{LOG_PREFIX}{log_tag} Tutti i giocatori se ne sono andati. Chiudo.")
                break
    except KeyboardInterrupt:
        pass
    finally:
        nm.stop()


# Ruolo PRIMARY
def replicate_to_backup(get_state_fn, backup_replica_port, interval=REPLICATION_INTERVAL):
    """SOLO primary: prova a connettersi al backup (riprovando finche' non e' su) e gli manda
    una copia dello stato (GameAuthority.to_dict()) a intervalli regolari, una riga JSON alla
    volta. E' un semplice canale "push": il primary parla, il backup ascolta e basta."""
    def _loop():
        sock = None
        while True:
            if sock is None:
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.connect(("127.0.0.1", backup_replica_port))
                    print(f"{LOG_PREFIX}[Primary] Connesso al backup per la replica dello stato.")
                except Exception:
                    sock = None
                    time.sleep(1.0)
                    continue
            try:
                line = json.dumps(get_state_fn()) + "\n"
                sock.sendall(line.encode())
            except Exception as e:
                print(f"{LOG_PREFIX}[Primary] Connessione al backup persa ({e}), riprovo...")
                try:
                    sock.close()
                except Exception:
                    pass
                sock = None
                time.sleep(1.0)
                continue
            time.sleep(interval)

    threading.Thread(target=_loop, daemon=True).start()


def run_primary(port, control_port, replica_port, players_info):
    authority = GameAuthority()
    replicate_to_backup(authority.to_dict, replica_port)
    run_as_active_server(port, players_info, authority, "[Primary]")


# Ruolo BACKUP
def run_backup(port, control_port, replica_port, players_info):
    latest_state = {"value": None}  # dict-wrapper: modificabile dai thread senza "nonlocal"

    def _replica_receiver():
        """Ascolta la connessione TCP dal primary e tiene aggiornato l'ultimo stato ricevuto.
        Non fa NULLA con quello stato finche' non arriva l'ordine di promozione — un backup
        passivo, appunto: osserva, non agisce."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("", replica_port))
        srv.listen(1)
        while True:
            conn, _ = srv.accept()
            print(f"{LOG_PREFIX}[Backup] Primary connesso, ricevo lo stato replicato.")
            buf = b""
            try:
                while True:
                    chunk = conn.recv(65536)
                    if not chunk:
                        print(f"{LOG_PREFIX}[Backup] Il primary si e' disconnesso dal canale "
                              f"di replica (chiusura pulita). Torno in ascolto.")
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if line.strip():
                            try:
                                latest_state["value"] = json.loads(line.decode())
                            except Exception:
                                pass
            except (ConnectionResetError, OSError) as e:
                print(f"{LOG_PREFIX}[Backup] Connessione di replica interrotta ({e}). "
                      f"Torno in ascolto di una nuova connessione dal primary.")
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    threading.Thread(target=_replica_receiver, daemon=True).start()

    # Canale di controllo: il proxy si connette qui e manda "PROMOTE" quando il primary muore.
    control_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    control_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    control_srv.bind(("", control_port))
    control_srv.listen(1)
    print(f"{LOG_PREFIX}[Backup] In attesa di eventuale promozione sulla porta di controllo {control_port}...")

    conn, _ = control_srv.accept()
    cmd = conn.recv(1024).decode().strip()
    if cmd != "PROMOTE":
        print(f"{LOG_PREFIX}[Backup] Comando di controllo sconosciuto: {cmd!r}. Chiudo.")
        return

    print(f"{LOG_PREFIX}[Backup] PROMOSSO a primary. Riprendo dall'ultimo stato replicato.")
    if latest_state["value"] is not None:
        authority = GameAuthority.from_dict(latest_state["value"])
    else:
        print(f"{LOG_PREFIX}[Backup] Nessuno stato replicato disponibile, riparto da zero.")
        authority = GameAuthority()

    run_as_active_server(port, players_info, authority, "[Backup->Primary]")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=["primary", "backup"], required=True)
    parser.add_argument("--udp-port", type=int, required=True)
    parser.add_argument("--control-port", type=int, required=True)
    parser.add_argument("--replica-port", type=int, required=True)
    parser.add_argument("--players", type=str, required=True, help="JSON: lista di [pid, ip, udp_port]")
    args = parser.parse_args()

    players_info = json.loads(args.players)

    if args.role == "primary":
        run_primary(args.udp_port, args.control_port, args.replica_port, players_info)
    else:
        run_backup(args.udp_port, args.control_port, args.replica_port, players_info)


if __name__ == "__main__":
    main()