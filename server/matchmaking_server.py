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
_redis_warned = False

STATUS_KEY = "matchmaking:status"  

pending_clients = []  # list of (player_id, client_ip, udp_port, tcp_conn)
active_games = {}     # port -> {players: [...], start_ts: ...}
active_processes = {} # port -> subprocess.Popen del supervisor di quella partita — serve SOLO per
                       # sapere quando la partita e' davvero finita, vedi cleanup_finished_games()
PORT_BLOCK = 3                                           # porte per partita: supervisor, server di gioco, server di ripartenza
MAX_ACTIVE_GAMES = globals().get("MAX_ACTIVE_GAMES", 50)  
port_lock = threading.RLock()   #
CLEANUP_INTERVAL = 2.0  # secondi tra un controllo delle partite concluse e il successivo


def write_status():
    global _redis_warned
    if r is None:
        return
    try:
        data = {
            "pending": [{"id": p[0], "ip": p[1], "udp_port": p[2]} for p in pending_clients],
            "active_games": {str(port): info for port, info in active_games.items()},
            "timestamp": time.time(),
        }
        r.set(STATUS_KEY, json.dumps(data))
    except Exception as e:
        if not _redis_warned:                                # una volta sola: Redis e' facoltativo
            _redis_warned = True
            print(f"{LOG_PREFIX} Redis non raggiungibile ({e}): niente stato per la dashboard.")


def _wait_for_supervisor_ready(tcp_port, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.connect(("127.0.0.1", tcp_port))
            return True
        except Exception:
            time.sleep(0.1)
    print(f"{LOG_PREFIX} Attenzione: il supervisor sulla porta {tcp_port} non risulta pronto dopo "
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


def find_game_of(player_id):
    """Il rientro: in quale partita in corso sta questo giocatore? Risponde come il matchmaking
    normale ("GAME:host:porta-del-supervisor"), oppure "NOT_FOUND" se la partita e' finita."""
    with port_lock:
        for supervisor_port, game in active_games.items():
            if player_id in game["players"]:
                return f"GAME:{MATCHMAKING_HOSTNAME}:{supervisor_port}"
    return "NOT_FOUND"


def _close(conn):
    try:
        conn.close()
    except Exception:
        pass


def _send(conn, data):
    """Manda dei byte; False se il client e' gia' sparito."""
    try:
        conn.sendall(data)
        return True
    except Exception:
        return False


def _allocate_game_port():
    """Il primo blocco di porte libero (si riusano quelli delle partite finite), o None se sono tutti occupati."""
    for i in range(MAX_ACTIVE_GAMES):
        base = GAME_INSTANCE_BASE_PORT + i * PORT_BLOCK
        if base not in active_processes:
            return base
    return None


def _start_game(group, supervisor_tcp_port):
    """Gruppo completo e porta gia' riservata (sotto lock): avvia il supervisor della partita SENZA lock,
    cosi' l'attesa non blocca code e cancellazioni, e poi manda a ognuno GAME:host:porta."""
    udp_base_port = supervisor_tcp_port + 1
    players_info = [(pid, ip, udp) for (pid, ip, udp, _) in group]
    print(f"{LOG_PREFIX} Avvio il supervisor (porta TCP {supervisor_tcp_port}, server di gioco "
          f"sulla {udp_base_port}) per i giocatori: {[p[0] for p in players_info]}")
    supervisor_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "supervisor.py")
    try:
        process = subprocess.Popen(
            [sys.executable, supervisor_path, str(supervisor_tcp_port), json.dumps(players_info), str(udp_base_port)])
    except Exception as e:
        print(f"{LOG_PREFIX} Supervisor non partito: {e}")
        with port_lock:
            active_processes.pop(supervisor_tcp_port, None)       # libero la porta riservata
        for _, _, _, c in group:
            _send(c, b"SERVER_FULL")
            _close(c)
        return
    _wait_for_supervisor_ready(supervisor_tcp_port)
    with port_lock:                                               # il lock serve solo per aggiornare le liste
        active_processes[supervisor_tcp_port] = process
        active_games[supervisor_tcp_port] = {"players": [p[0] for p in players_info], "start_ts": time.time()}
        write_status()
    for pid, _, _, c in group:
        try:
            c.sendall(f"GAME:{MATCHMAKING_HOSTNAME}:{supervisor_tcp_port}".encode())
            c.close()
        except Exception as e:
            print(f"{LOG_PREFIX} Failed to notify client {pid}: {e}")


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


def handle_client(conn, addr):
    entry = None
    try:
        info = json.loads(conn.recv(2048).decode())
        if "rejoin_id" in info:                                   # rientro in partita
            _send(conn, find_game_of(info["rejoin_id"]).encode())
            _close(conn)
            return
        player_id, udp_port = info.get("id"), info.get("udp_port")
        print(f"{LOG_PREFIX} New matchmaking connection: {player_id}@{addr[0]}:{udp_port}")
        entry = (player_id, addr[0], udp_port, conn)

        with port_lock:
            if any(p[0] == player_id for p in pending_clients):
                print(f"{LOG_PREFIX} Player {player_id} rifiutato: id gia' in coda (collisione).")
                _send(conn, b"ID_COLLISION")
                _close(conn)
                return
            pending_clients.append(entry)
            write_status()
            group = None
            if len(pending_clients) >= MAX_PLAYERS:               # gruppo completo: parte la partita
                group = pending_clients[:MAX_PLAYERS]
                del pending_clients[:MAX_PLAYERS]
                game_port = _allocate_game_port()
                if game_port is not None:
                    active_processes[game_port] = None            # riservata: nessun altro gruppo la prende
                write_status()
            elif not _send(conn, b"WAIT"):                        # sotto lock: WAIT parte sempre prima di un eventuale GAME
                _remove_from_queue(entry, "connessione persa mentre mandavo WAIT")
                return

        if group is not None:                                     # fuori dal lock: l'avvio puo' richiedere secondi
            if game_port is None:
                print(f"{LOG_PREFIX} Troppe partite in corso ({MAX_ACTIVE_GAMES}): gruppo rimandato a casa.")
                for _, _, _, c in group:
                    _send(c, b"SERVER_FULL")
                    _close(c)
            else:
                _start_game(group, game_port)
            return                                                # questa connessione ha gia' fatto il suo dovere

        _wait_in_queue(conn, entry)
    except Exception as e:
        print(f"{LOG_PREFIX} Error handling matchmaking client: {e}")
        if entry is not None:
            _remove_from_queue(entry, "errore nella connessione")
        _close(conn)
    finally:
        write_status()


def cleanup_finished_games():
    while True:
        time.sleep(CLEANUP_INTERVAL)
        with port_lock:
            finished_ports = [port for port, proc in active_processes.items()
                               if proc is not None and proc.poll() is not None]   # None = porta riservata, sta partendo
            if not finished_ports:
                continue
            for port in finished_ports:
                print(f"{LOG_PREFIX} Partita sulla porta {port} conclusa (il supervisor si e' "
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