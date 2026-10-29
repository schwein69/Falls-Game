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

r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
_redis_warned = False                

STATUS_KEY = "matchmaking:status"     

pending_clients = []  # list of (player_id, client_ip, udp_port, tcp_conn): the queue
active_games = {}     # port -> {players: [...], start_ts: ...}
active_processes = {} # port -> Popen of the game, _AdoptedGame, or None
MAX_ACTIVE_GAMES = globals().get("MAX_ACTIVE_GAMES", 10)   
port_lock = threading.RLock()        
CLEANUP_INTERVAL = 2.0


def _running_games():
    games = {}
    for key in r.scan_iter(GAME_KEY_PREFIX + "*"):
        raw = r.get(key)
        if raw is not None:                                        
            info = json.loads(raw)
            games[info["port"]] = info
    return games


# Stand-in for a game started by a previous matchmaking.
class _AdoptedGame:
    def __init__(self, port):
        self.port = port

    def poll(self):
        try:
            alive = r.exists(GAME_KEY_PREFIX + str(self.port))
        except redis.RedisError:
            alive = True                                         
        return None if alive else 0


# At startup: take over the games still running.
def adopt_running_games():
    with port_lock:
        for port, info in _running_games().items():
            if port not in active_processes:
                active_processes[port] = _AdoptedGame(port)
                active_games[port] = {"players": info["players"], "start_ts": info["start_ts"]}
                print(f"{LOG_PREFIX} Partita gia' in corso sulla porta {port}: la riprendo ({info['players']}).")


# Publish queue and running games on Redis for the dashboard.
def write_status():
    global _redis_warned
    try:
        data = {
            "pending": [{"id": p[0], "ip": p[1], "udp_port": p[2]} for p in pending_clients],
            "active_games": {str(port): info for port, info in active_games.items()},
            "timestamp": time.time(),
        }
        r.set(STATUS_KEY, json.dumps(data))
    except Exception as e:
        if not _redis_warned:
            _redis_warned = True
            print(f"{LOG_PREFIX} Redis non raggiungibile ({e}): la dashboard non si aggiorna.")


# Wait until the new game writes its Redis key.
def _wait_until_running(port, process, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline and process.poll() is None:
        try:
            if r.exists(GAME_KEY_PREFIX + str(port)):
                return True
        except redis.RedisError:
            pass
        time.sleep(0.1)
    return process.poll() is None


# Take a player out of the queue (disconnected, cancelled, error) and update the dashboard.
def _remove_from_queue(entry, reason):
    with port_lock:
        if entry in pending_clients:
            pending_clients.remove(entry)
            print(f"{LOG_PREFIX} Player {entry[0]} rimosso dalla coda ({reason}).")
            write_status()


# For a rejoin: the address of the game this player is in, or NOT_FOUND.
def find_game_of(player_id):
    with port_lock:
        for game_port, game in active_games.items():
            if player_id in game["players"]:
                return f"GAME:{MATCHMAKING_HOSTNAME}:{game_port}"
    return "NOT_FOUND"


# Close a connection.
def _close(conn):
    try:
        conn.close()
    except Exception:
        pass


# Send data.
def _send(conn, data):
    try:
        conn.sendall(data)
        return True
    except Exception:
        return False


# First free game port (GAME_INSTANCE_BASE_PORT + n), or None if MAX_ACTIVE_GAMES games are already running.
def _allocate_game_port():
    for i in range(MAX_ACTIVE_GAMES):
        base = GAME_INSTANCE_BASE_PORT + i
        if base not in active_processes:
            return base
    return None


# Start a game_instance process for the group and tell the players where to connect.
def _start_game(group, game_port):
    players_info = [(pid, ip, udp) for (pid, ip, udp, _) in group]
    print(f"{LOG_PREFIX} Avvio il server di gioco sulla porta {game_port} per i giocatori: {[p[0] for p in players_info]}")
    game_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "game_instance.py")
    detach = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
              if os.name == "nt" else {"start_new_session": True})
    process = None
    try:
        os.makedirs(RUN_DIR, exist_ok=True)
        log_path = os.path.join(RUN_DIR, f"game_{game_port}.log")
        with open(log_path, "ab") as log:                                  
            process = subprocess.Popen(
                [sys.executable, game_path, "--udp-port", str(game_port), "--players", json.dumps(players_info)],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                env=dict(os.environ, PYTHONUNBUFFERED="1"), **detach)
        print(f"{LOG_PREFIX} Log della partita: {os.path.abspath(log_path)}")
        started = _wait_until_running(game_port, process)
    except Exception as e:
        print(f"{LOG_PREFIX} Server di gioco non partito: {e}")
        started = False
    if not started:
        with port_lock:
            active_processes.pop(game_port, None)                # free the reserved port
        for _, _, _, c in group:
            _send(c, b"SERVER_FULL")
            _close(c)
        return
    with port_lock:                                             
        active_processes[game_port] = process
        active_games[game_port] = {"players": [p[0] for p in players_info], "start_ts": time.time()}
        write_status()
    for pid, _, _, c in group:                                   # from now on the clients talk to the game, not to us
        try:
            c.sendall(f"GAME:{MATCHMAKING_HOSTNAME}:{game_port}".encode())
            c.close()
        except Exception as e:
            print(f"{LOG_PREFIX} Failed to notify client {pid}: {e}")


# The player is in the queue
def _wait_in_queue(conn, entry):
    """Il giocatore e' in coda: si resta in ascolto finche' non si disconnette o manda CANCEL."""
    while True:
        try:
            data = conn.recv(64)
        except Exception:
            data = b""
        if not data or data.strip() == b"CANCEL":
            _remove_from_queue(entry, "annullato esplicitamente dal client" if data else "connessione chiusa dal client")
            _close(conn)
            return


# One thread per client connection. A message with rejoin_id asks where its game is; otherwise the player
# joins the queue. The client that completes a group of MAX_PLAYERS starts the game for everybody.
def handle_client(conn, addr):
    entry = None
    try:
        info = json.loads(conn.recv(2048).decode())
        if "rejoin_id" in info:                                  
            _send(conn, find_game_of(info["rejoin_id"]).encode())
            _close(conn)
            return
        player_id, udp_port = info.get("id"), info.get("udp_port")
        print(f"{LOG_PREFIX} New matchmaking connection: {player_id}@{addr[0]}:{udp_port}")
        entry = (player_id, addr[0], udp_port, conn)

        with port_lock:
            if any(p[0] == player_id for p in pending_clients):
                print(f"{LOG_PREFIX} Player {player_id} rifiutato: id gia' in coda.")
                _send(conn, b"ID_COLLISION")
                _close(conn)
                return
            pending_clients.append(entry)
            write_status()
            group = None
            if len(pending_clients) >= MAX_PLAYERS:               
                group = pending_clients[:MAX_PLAYERS]
                del pending_clients[:MAX_PLAYERS]
                game_port = _allocate_game_port()
                if game_port is not None:
                    active_processes[game_port] = None            
                write_status()
            elif not _send(conn, b"WAIT"):                        
                _remove_from_queue(entry, "connessione persa mentre mandavo WAIT")
                return

        if group is not None:                                   
            if game_port is None:
                print(f"{LOG_PREFIX} Troppe partite in corso ({MAX_ACTIVE_GAMES}): gruppo rimandato a casa.")
                for _, _, _, c in group:
                    _send(c, b"SERVER_FULL")
                    _close(c)
            else:
                _start_game(group, game_port)
            return                                               

        _wait_in_queue(conn, entry)
    except Exception as e:
        print(f"{LOG_PREFIX} Error handling matchmaking client: {e}")
        if entry is not None:
            _remove_from_queue(entry, "errore nella connessione")
        _close(conn)
    finally:
        write_status()


# Background thread: every CLEANUP_INTERVAL seconds forget the games whose process has finished.
def cleanup_finished_games():
    while True:
        time.sleep(CLEANUP_INTERVAL)
        with port_lock:
            finished_ports = [port for port, proc in active_processes.items()
                               if proc is not None and proc.poll() is not None] 
            if not finished_ports:
                continue
            for port in finished_ports:
                print(f"{LOG_PREFIX} Partita sulla porta {port} conclusa.")
                active_games.pop(port, None)
                active_processes.pop(port, None)
            write_status()


# Entry point: check Redis, adopt the games still running, start the cleanup thread and accept clients forever.
def start_matchmaking():
    try:
        r.ping()
    except redis.RedisError as e:
        sys.exit(f"{LOG_PREFIX} Redis non raggiungibile su {REDIS_HOST}:{REDIS_PORT} ({e}). "
                 f"Avvialo prima del matchmaking: serve per ritrovare le partite in corso.")
    os.makedirs(RUN_DIR, exist_ok=True)
    adopt_running_games()
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
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()   # one thread per client


if __name__ == "__main__":
    start_matchmaking()