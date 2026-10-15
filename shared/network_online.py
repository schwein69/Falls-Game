import socket
import json
import random
import threading
import time
import os

from network import NetworkManager
from config import MATCHMAKING_HOSTNAME, MATCHMAKING_TCP_PORT


# Lato client
def start_online_session():
    """Crea una NetworkManager e avvia in background l'handshake col matchmaking server."""
    nm = NetworkManager()
    nm.online.status = "connecting"
    threading.Thread(target=_matchmaking_handshake, args=(nm,), daemon=True).start()
    return nm


def _matchmaking_handshake(nm):
    """Handshake TCP col matchmaking server."""
    try:
        local_udp_port = nm.prepare_local_socket()
        my_label = os.getpid() * 1_000_000 + random.randint(0, 999_999)

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
            tcp.settimeout(15)
            tcp.connect((MATCHMAKING_HOSTNAME, MATCHMAKING_TCP_PORT))
            tcp.sendall((json.dumps({"id": my_label, "udp_port": local_udp_port}) + "\n").encode())
            nm.online.status = "queued"

            buf = bytearray()
            while True:
                msg = _read_line(tcp, buf).decode().strip()
                if msg == "ID_COLLISION":
                    raise ConnectionError("Id gia' in coda con un altro giocatore: riprova.")
                if msg.startswith("GAME:"):
                    break

        _, host, port_str = msg.split(":")
        print(f"[Network] Match trovato! Mi connetto al proxy {host}:{port_str}")
        _connect_via_proxy(nm, host, int(port_str))
        nm.online.status = "matched"

    except Exception as e:
        print(f"[Network] Matchmaking fallito: {e}")
        nm.online.status = "error"
        nm.online.error = str(e)


def start_online_rejoin(player_id):
    nm = NetworkManager()
    nm.online.status = "connecting"
    threading.Thread(target=_rejoin_handshake, args=(nm, player_id), daemon=True).start()
    return nm


def _rejoin_handshake(nm, player_id):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
            tcp.settimeout(10)
            tcp.connect((MATCHMAKING_HOSTNAME, MATCHMAKING_TCP_PORT))
            tcp.sendall((json.dumps({"rejoin_id": player_id}) + "\n").encode())
            answer = _read_line(tcp, bytearray()).decode().strip()
        if answer == "NOT_FOUND":
            raise ConnectionError("La partita non esiste piu'.")
        if not answer.startswith("GAME:"):   
            raise ConnectionError("Il matchmaking non supporta il rientro: aggiorna matchmaking_server.py e riavvialo.")
        _, host, port_str = answer.split(":")
        _connect_via_proxy(nm, host, int(port_str), rejoin_id=player_id)
        nm.online.status = "matched"
    except Exception as e:
        print(f"[Network] Rientro fallito: {e}")
        nm.online.status = "error"
        nm.online.error = str(e)


def _read_line(sock, buf):
    while b"\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("Il proxy ha chiuso la connessione.")
        buf += chunk
    line, _, rest = bytes(buf).partition(b"\n")
    buf[:] = rest
    return json.loads(line)


def _connect_via_proxy(nm, proxy_host, proxy_port, connect_timeout=10, rejoin_id=None):
    deadline = time.time() + connect_timeout
    while True:                  
        sock, buf = socket.socket(socket.AF_INET, socket.SOCK_STREAM), bytearray()
        try:
            sock.settimeout(2)
            sock.connect((proxy_host, proxy_port))
            info = _read_line(sock, buf)
            break
        except Exception as e:
            sock.close()
            if time.time() >= deadline:
                raise ConnectionError(f"Impossibile connettersi al proxy dopo {connect_timeout}s: {e}")
            time.sleep(0.3)

    nm.online.proxy_sock = sock    # serve anche per chiuderlo in stop()
    sock.settimeout(None)
    print(f"[Network] Il proxy indica il primary su {proxy_host}:{info['primary_port']}. Mi unisco.")
    nm.join_network(proxy_host, info["primary_port"], rejoin_id=rejoin_id)  
    def _listen_for_failover():
        while True:
            try:
                info = _read_line(sock, buf)
            except Exception as e:
                print(f"[Network] Connessione col proxy interrotta: {e}")
                return
            if "new_primary_host" in info:
                print(f"[Network] Il primary e' cambiato (failover): mi riaggancio con lo stesso ID ({nm.player_id}).")
                nm.last_host_seen = time.time()
                nm.join_network(proxy_host, info["new_primary_port"], rejoin_id=nm.player_id)

    threading.Thread(target=_listen_for_failover, daemon=True).start()


# Lato server (usato da server/game_instance.py)
def start_dedicated_server(port, players_info):
    nm = NetworkManager()
    nm.room.expected = {(ip, udp_port): pid for (pid, ip, udp_port) in players_info}
    nm.setup_host(port=port)
    nm.start_heartbeat()
    return nm