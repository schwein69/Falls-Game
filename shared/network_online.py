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
    2. Il server risponde "WAIT" finche' la coda non e' piena, poi manda "GAME:<host>:<porta>"
       — dove <porta> ora e' quella del PROXY di quella partita (vedi server/proxy.py), non
       piu' direttamente quella del server di gioco.
    3. Ci connettiamo al proxy via TCP (connessione tenuta aperta per tutta la partita) per
       sapere chi e' il primary ATTUALE, e ci uniamo a lui in UDP come sempre. Se piu' avanti
       il primary cambia (crash + failover), il proxy ce lo dice sulla stessa connessione, e ci
       riagganciamo da soli, senza bisogno di nessuna azione da parte del giocatore.
    """
    try:
        local_udp_port = nm.prepare_local_socket()
        my_label = os.getpid() * 1_000_000 + random.randint(0, 999_999)

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
                    print(f"[Network] Match trovato! Mi connetto al proxy {host}:{port_str}")
                    _connect_via_proxy(nm, host, int(port_str))
                    nm.matchmaking_status = "matched"
                    return

    except Exception as e:
        print(f"[Network] Matchmaking fallito: {e}")
        nm.matchmaking_status = "error"
        nm.matchmaking_error = str(e)


def _connect_via_proxy(nm, proxy_host, proxy_port, connect_timeout=10):
    """Ci connettiamo al proxy della partita via TCP e restiamo in ascolto per tutta la sua
    durata: e' cosi' che sappiamo a chi mandare il traffico UDP di gioco ORA, e come veniamo
    avvisati se cambia (il primary crasha, subentra il backup)"""
    deadline = time.time() + connect_timeout
    proxy_sock = None
    info = None
    last_error = None
    while time.time() < deadline:
        try:
            proxy_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            proxy_sock.settimeout(2)
            proxy_sock.connect((proxy_host, proxy_port))

            buf = b""
            while b"\n" not in buf:
                chunk = proxy_sock.recv(4096)
                if not chunk:
                    raise ConnectionError("Il proxy ha chiuso la connessione.")
                buf += chunk
            line, _ = buf.split(b"\n", 1)
            info = json.loads(line.decode())
            break
        except Exception as e:
            last_error = e
            try:
                proxy_sock.close()
            except Exception:
                pass
            proxy_sock = None
            time.sleep(0.3)

    if proxy_sock is None or info is None:
        raise ConnectionError(f"Impossibile connettersi al proxy dopo {connect_timeout}s: {last_error}")

    nm.proxy_sock = proxy_sock  # teniamo un riferimento: serve anche per chiuderla in stop()

    buf = b""

    def _read_line():
        nonlocal buf
        while b"\n" not in buf:
            chunk = proxy_sock.recv(4096)
            if not chunk:
                raise ConnectionError("Il proxy ha chiuso la connessione.")
            buf += chunk
        line, buf = buf.split(b"\n", 1)
        return json.loads(line.decode())

    # L'indirizzo del primary ATTUALE l'abbiamo gia' letto qui sopra, dentro il ciclo di
    # ritentativo — _read_line() da qui in poi serve solo per i FUTURI avvisi di failover.
    primary_host, primary_port = info["primary_host"], info["primary_port"]
    print(f"[Network] Il proxy indica il primary su {primary_host}:{primary_port}. Mi unisco.")
    proxy_sock.settimeout(None)
    nm.join_network(primary_host, primary_port)

    def _listen_for_failover():
        while True:
            try:
                info = _read_line()
            except Exception as e:
                print(f"[Network] Connessione col proxy interrotta: {e}")
                return
            if "new_primary_host" in info:
                new_host, new_port = info["new_primary_host"], info["new_primary_port"]
                my_id = nm.player_id
                print(f"[Network] Il primary e' cambiato (failover)! Mi riaggancio a "
                      f"{new_host}:{new_port} mantenendo lo stesso ID ({my_id}) e la STESSA "
                      f"porta locale — nessuna azione richiesta.")
                nm.last_host_seen = time.time()
                nm.join_network(new_host, new_port, rejoin_id=my_id)

    threading.Thread(target=_listen_for_failover, daemon=True).start()


# Lato server (usato da server/game_instance.py)
def start_dedicated_server(port, players_info):
    """Crea la NetworkManager per il server dedicato di una partita online: non e' MAI un
    giocatore, solo il punto di smistamento tra i client di quella partita.

    players_info: lista di [player_id, ip, udp_port] gia' decisa dal matchmaking server —
    diventa la lista di indirizzi pre-autorizzati a unirsi (NetworkManager.expected_players),
    cosi' ogni client riceve esattamente l'ID che il matchmaking gli ha gia' assegnato."""
    nm = NetworkManager()
    nm.expected_players = {(ip, udp_port): pid for (pid, ip, udp_port) in players_info}
    nm.setup_host(port=port)
    nm.start_heartbeat()
    return nm