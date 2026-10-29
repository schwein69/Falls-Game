import socket
import json
import random
import threading
import os
import time

from network import NetworkManager
from network_discovery import DiscoveryListener
from config import MATCHMAKING_HOSTNAME, MATCHMAKING_TCP_PORT, MATCHMAKING_DISCOVERY_PORT

DISCOVERY_WAIT = 2.0     


# Starts a NetworkManager and runs the matchmaking handshake in a background thread.
def start_online_session():
    """Crea una NetworkManager e avvia in background l'handshake col matchmaking server."""
    nm = NetworkManager()
    nm.online.status = "connecting"
    threading.Thread(target=_matchmaking_handshake, args=(nm,), daemon=True).start()
    return nm


# Find the matchmaking server: MATCHMAKING_HOSTNAME if set, otherwise its UDP announcement on the LAN.
def _find_server():
    if MATCHMAKING_HOSTNAME:
        return MATCHMAKING_HOSTNAME, MATCHMAKING_TCP_PORT
    listener = DiscoveryListener(port=MATCHMAKING_DISCOVERY_PORT)
    try:
        listener.start()
        end = time.time() + DISCOVERY_WAIT
        while time.time() < end and not listener.bind_failed:
            hosts = listener.get_hosts()
            if hosts:
                ip, _, port = hosts[0]
                print(f"[Network] Matchmaking server trovato sulla LAN: {ip}:{port}")
                return ip, port
            time.sleep(0.1)
    finally:
        listener.stop()
    raise ConnectionError("Matchmaking server non trovato sulla rete: controlla che sia acceso "
                          "oppure imposta la variabile MATCHMAKING_HOSTNAME con il suo IP.")


# Runs in a thread: find the server, join the queue (TCP) and wait. WAIT = keep waitin;
# GAME:... = match ready, then we join the game server on the same machine as the matchmaking.
def _matchmaking_handshake(nm):
    """Handshake TCP col matchmaking server."""
    try:
        server_host, server_port = _find_server()
        local_udp_port = nm.prepare_local_socket()
        my_label = os.getpid() * 1_000_000 + random.randint(0, 999_999)

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
            tcp.settimeout(15)
            tcp.connect((server_host, server_port))
            tcp.sendall(json.dumps({"id": my_label, "udp_port": local_udp_port}).encode())
            tcp.settimeout(None)             # in coda si puo' aspettare anche minuti: il limite di 15 s vale solo per connettersi
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

        _, _, port_str = msg.split(":")
        host = server_host                  
        print(f"[Network] Match trovato! Mi unisco al server di gioco {host}:{port_str}")
        nm.join_network(host, int(port_str))
        nm.online.status = "matched"

    except Exception as e:
        print(f"[Network] Matchmaking fallito: {e}")
        nm.online.status = "error"
        nm.online.error = str(e)


# Rejoin after an interruption (online): same idea as start_online_session, but it asks where our match is.
def start_online_rejoin(player_id):
    nm = NetworkManager()
    nm.online.status = "connecting"
    threading.Thread(target=_rejoin_handshake, args=(nm, player_id), daemon=True).start()
    return nm


# Runs in a thread: ask the matchmaking for the game of player_id (NOT_FOUND if it is over) and rejoin it with the same id.
def _rejoin_handshake(nm, player_id):
    try:
        server_host, server_port = _find_server()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
            tcp.settimeout(10)
            tcp.connect((server_host, server_port))
            tcp.sendall(json.dumps({"rejoin_id": player_id}).encode())
            answer = tcp.recv(1024).decode().strip()
        if answer == "NOT_FOUND":
            raise ConnectionError("La partita non esiste piu'.")
        if not answer.startswith("GAME:"):   
            raise ConnectionError("Il matchmaking non supporta il rientro: aggiorna matchmaking_server.py e riavvialo.")
        _, _, port_str = answer.split(":")
        nm.join_network(server_host, int(port_str), rejoin_id=player_id)
        nm.online.status = "matched"
    except Exception as e:
        print(f"[Network] Rientro fallito: {e}")
        nm.online.status = "error"
        nm.online.error = str(e)


# SERVER (game_instance): the network manager of one match. It accepts only the players the matchmaking
# chose (room.expected), listens on the given port (TCP + UDP) and starts the heartbeat.
def start_dedicated_server(port, players_info):
    nm = NetworkManager()
    nm.room.expected = {(ip, udp_port): pid for (pid, ip, udp_port) in players_info}
    nm.setup_host(port=port)
    nm.start_heartbeat()
    return nm