import socket
import threading
import subprocess
import json
import time
import sys
import os
import redis

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
from config import *

REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))
r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

STATUS_KEY = "matchmaking:status"  

pending_clients = []  # list of (player_id, client_ip, udp_port, tcp_conn)
active_games = {}     # port -> {players: [...], start_ts: ...}
active_processes = {} # port -> subprocess.Popen del proxy di quella partita — serve SOLO per
                       # sapere quando la partita e' davvero finita, vedi cleanup_finished_games()
next_game_port = GAME_INSTANCE_BASE_PORT
port_lock = threading.Lock()
CLEANUP_INTERVAL = 2.0  # secondi tra un controllo delle partite concluse e il successivo


def write_status():
    try:
        data = {
            "pending": [{"id": p[0], "ip": p[1], "udp_port": p[2]} for p in pending_clients],
            "active_games": {str(port): info for port, info in active_games.items()},
            "timestamp": time.time(),
        }
        r.set(STATUS_KEY, json.dumps(data))
    except Exception as e:
        print(f"{LOG_PREFIX} Failed writing status to Redis: {e}")


def _wait_for_proxy_ready(tcp_port, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.connect(("127.0.0.1", tcp_port))
            return True
        except Exception:
            time.sleep(0.1)
    print(f"{LOG_PREFIX} Attenzione: il proxy sulla porta {tcp_port} non risulta pronto dopo "
          f"{timeout}s, procedo comunque.")
    return False


def _remove_from_queue(entry, reason):
    """Toglie un giocatore dalla coda in modo sicuro (idempotente: se e' gia' stato tolto,
    es. perche' nel frattempo e' scattato un match, non fa nulla di male a riprovare)."""
    with port_lock:
        if entry in pending_clients:
            pending_clients.remove(entry)
            print(f"{LOG_PREFIX} Player {entry[0]} rimosso dalla coda ({reason}).")
            write_status()


def handle_client(conn, addr):
    global next_game_port
    entry = None
    try:
        data = conn.recv(2048).decode()
        info = json.loads(data)
        player_id = info.get("id")
        udp_port = info.get("udp_port")
        client_ip = addr[0]
        print(f"{LOG_PREFIX} New matchmaking connection: {player_id}@{client_ip}:{udp_port}")

        entry = (player_id, client_ip, udp_port, conn)
        with port_lock:
            pending_clients.append(entry)
            write_status()
            if len(pending_clients) >= MAX_PLAYERS:
                group = pending_clients[:MAX_PLAYERS]
                del pending_clients[:MAX_PLAYERS]

                proxy_tcp_port = next_game_port
                udp_base_port = next_game_port + 1
                next_game_port += 10
                players_info = [(pid, ip, udp) for (pid, ip, udp, _) in group]
                players_json = json.dumps(players_info)

                print(f"{LOG_PREFIX} Avvio il proxy (porta TCP {proxy_tcp_port}, blocco UDP da "
                      f"{udp_base_port}) per i giocatori: {[p[0] for p in players_info]}")
                proxy_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxy.py")
                proxy_process = subprocess.Popen([sys.executable, proxy_path,
                                   str(proxy_tcp_port), players_json, str(udp_base_port)])
                active_processes[proxy_tcp_port] = proxy_process

                _wait_for_proxy_ready(proxy_tcp_port)

                active_games[proxy_tcp_port] = {
                    "players": [p[0] for p in players_info],
                    "start_ts": time.time(),
                }
                write_status()

                for (pid, ip, udp, c) in group:
                    try:
                        msg = f"GAME:{MATCHMAKING_HOSTNAME}:{proxy_tcp_port}"
                        c.sendall(msg.encode())
                        c.close()
                    except Exception as e:
                        print(f"{LOG_PREFIX} Failed to notify client {pid}: {e}")
                return  # consumato da un match: questa connessione ha gia' fatto il suo dovere
            else:
                try:
                    conn.sendall(b"WAIT")
                except Exception:
                    _remove_from_queue(entry, "connessione persa mentre mandavo WAIT")
                    return

        while True:
            try:
                data = conn.recv(64)
            except Exception:
                data = b""
            if not data:
                _remove_from_queue(entry, "connessione chiusa dal client")
                try:
                    conn.close()
                except Exception:
                    pass
                return
            if data.strip() == b"CANCEL":
                _remove_from_queue(entry, "annullato esplicitamente dal client")
                try:
                    conn.close()
                except Exception:
                    pass
                return

    except Exception as e:
        print(f"{LOG_PREFIX} Error handling matchmaking client: {e}")
        if entry is not None:
            _remove_from_queue(entry, "errore nella connessione")
        try:
            conn.close()
        except:
            pass
    finally:
        write_status()


def cleanup_finished_games():
    while True:
        time.sleep(CLEANUP_INTERVAL)
        with port_lock:
            finished_ports = [port for port, proc in active_processes.items()
                               if proc.poll() is not None]
            if not finished_ports:
                continue
            for port in finished_ports:
                print(f"{LOG_PREFIX} Partita sulla porta {port} conclusa (il proxy si e' "
                      f"chiuso). Rimossa dalla lista.")
                active_games.pop(port, None)
                active_processes.pop(port, None)
            write_status()


def start_matchmaking():
    write_status()
    threading.Thread(target=cleanup_finished_games, daemon=True).start()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((SERVER_BIND, MATCHMAKING_TCP_PORT))
        s.listen()
        print(f"{LOG_PREFIX} Matchmaking server listening on {SERVER_BIND}:{MATCHMAKING_TCP_PORT}")
        print(f"{LOG_PREFIX} Stato condiviso su Redis ({REDIS_HOST}:{REDIS_PORT}, chiave "
              f"'{STATUS_KEY}') — avvia la dashboard con: streamlit run streamlit_dashboard.py")
        while True:
            conn, addr = s.accept()
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()


if __name__ == "__main__":
    start_matchmaking()