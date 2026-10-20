import socket
import json
import random
import threading
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
            tcp.sendall(json.dumps({"id": my_label, "udp_port": local_udp_port}).encode())
            tcp.settimeout(None)            
            nm.online.status = "queued"

            while True:                      
                data = tcp.recv(1024)
                if not data:
                    raise ConnectionError("Il matchmaking server ha chiuso la connessione.")
                msg = data.decode().strip()
                if msg == "SERVER_FULL":
                    raise ConnectionError("Il server e' pieno: riprova tra poco.")
                if msg == "ID_COLLISION":
                    raise ConnectionError("Id gia' in coda con un altro giocatore: riprova.")
                if msg.startswith("GAME:"):
                    break

        _, host, port_str = msg.split(":")
        print(f"[Network] Match trovato! Mi unisco al server di gioco {host}:{port_str}")
        nm.join_network(host, int(port_str))
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
            tcp.sendall(json.dumps({"rejoin_id": player_id}).encode())
            answer = tcp.recv(1024).decode().strip()
        if answer == "NOT_FOUND":
            raise ConnectionError("La partita non esiste piu'.")
        if not answer.startswith("GAME:"):   
            raise ConnectionError("Il matchmaking non supporta il rientro: aggiorna matchmaking_server.py e riavvialo.")
        _, host, port_str = answer.split(":")
        nm.join_network(host, int(port_str), rejoin_id=player_id)
        nm.online.status = "matched"
    except Exception as e:
        print(f"[Network] Rientro fallito: {e}")
        nm.online.status = "error"
        nm.online.error = str(e)


# Lato server (usato da server/game_instance.py)
def start_dedicated_server(port, players_info):
    nm = NetworkManager()
    nm.room.expected = {(ip, udp_port): pid for (pid, ip, udp_port) in players_info}
    nm.setup_host(port=port)
    nm.start_heartbeat()
    return nm