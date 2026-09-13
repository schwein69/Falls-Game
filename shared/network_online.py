"""
Lato client-server (Online): tutto cio' che riguarda SOLO la modalita' con matchmaking e server
dedicato. Non sa nulla di P2P locale o discovery LAN: quello vive in network_p2p.py.

Contiene le DUE meta' della stessa architettura:
  - start_online_session()   -> lato CLIENT: handshake col matchmaking, poi connessione al
                                 server dedicato assegnato.
  - start_dedicated_server() -> lato SERVER: usato da server/game_instance.py per mettersi in
                                 ascolto sulla porta decisa dal matchmaking, pre-autorizzando
                                 solo i giocatori di quella partita.

In questa modalita' l'host UDP non e' MAI un giocatore (a differenza del P2P): e' sempre un
processo separato. I client non si parlano mai tra loro, solo con questo processo — vedi
network.py, dove join_network manda pacchetti solo verso (host_ip, host_port), mai verso altri
client.
"""

import socket
import json
import random
import threading

from network import NetworkManager
from config import MATCHMAKING_HOSTNAME, MATCHMAKING_TCP_PORT


# ------------------------------------------------------------------
# Lato client
# ------------------------------------------------------------------
def start_online_session():
    """Crea una NetworkManager e avvia in background l'handshake col matchmaking server. Lo
    stato dell'handshake si legge da network_manager.matchmaking_status /
    .matchmaking_error mentre procede (idle -> connecting -> queued -> matched/error)."""
    nm = NetworkManager()
    nm.matchmaking_status = "connecting"
    threading.Thread(target=_matchmaking_handshake, args=(nm,), daemon=True).start()
    return nm


def _matchmaking_handshake(nm):
    """Handshake TCP col matchmaking server (vedi matchmaking_server.py).

    1. Ci connettiamo via TCP e mandiamo {"id": <label>, "udp_port": <porta_udp_locale>}.
    2. Il server risponde "WAIT" finche' la coda non e' piena, poi manda "GAME:<host>:<porta>".
    3. Ci connettiamo via UDP al server dedicato, esattamente come faremmo con un host P2P
       (stesso protocollo JOIN_REQUEST/ASSIGN_ID, condiviso in network.py).
    """
    try:
        local_udp_port = nm.prepare_local_socket()
        my_label = random.randint(1, 999999)

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
            tcp.settimeout(15)
            tcp.connect((MATCHMAKING_HOSTNAME, MATCHMAKING_TCP_PORT))
            tcp.sendall(json.dumps({"id": my_label, "udp_port": local_udp_port}).encode())

            nm.matchmaking_status = "queued"
            while True:
                data = tcp.recv(1024)
                if not data:
                    raise ConnectionError("Il matchmaking server ha chiuso la connessione.")
                msg = data.decode().strip()

                if msg == "WAIT":
                    continue

                if msg.startswith("GAME:"):
                    _, host, port_str = msg.split(":")
                    print(f"[Network] Match trovato! Connessione a {host}:{port_str}")
                    nm.matchmaking_status = "matched"
                    nm.join_network(host, int(port_str))
                    return

    except Exception as e:
        print(f"[Network] Matchmaking fallito: {e}")
        nm.matchmaking_status = "error"
        nm.matchmaking_error = str(e)


# ------------------------------------------------------------------
# Lato server (usato da server/game_instance.py)
# ------------------------------------------------------------------
def start_dedicated_server(port, players_info):
    """Crea la NetworkManager per il server dedicato di una partita online: non e' MAI un
    giocatore, solo il punto di smistamento tra i client di quella partita.

    players_info: lista di [player_id, ip, udp_port] gia' decisa dal matchmaking server —
    diventa la lista di indirizzi pre-autorizzati a unirsi (NetworkManager.expected_players),
    cosi' ogni client riceve esattamente l'ID che il matchmaking gli ha gia' assegnato."""
    nm = NetworkManager()
    nm.expected_players = {(ip, udp_port): pid for (pid, ip, udp_port) in players_info}
    nm.setup_host(port=port)
    return nm
