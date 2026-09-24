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
import time

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
                    print(f"[Network] Match trovato! Mi connetto al proxy {host}:{port_str}")
                    # BUG FIX: prima "matched" veniva impostato QUI, prima ancora di sapere se
                    # la connessione al proxy sarebbe davvero riuscita. screens.py smette di
                    # controllare lo stato non appena lo vede diventare "matched" (passa alla
                    # lobby e non guarda piu'). Se poi _connect_via_proxy falliva (es. il proxy
                    # non aveva ancora finito di avviarsi), l'errore risultante spariva nel
                    # nulla: nessuno stava piu' controllando "matchmaking_error", il client
                    # restava bloccato in silenzio in lobby senza mai entrare in partita.
                    # Ora "matched" si imposta SOLO se la connessione e' davvero riuscita.
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
    avvisati se cambia (il primary crasha, subentra il backup) — la connessione resta aperta,
    il proxy ci scrive lui quando serve, noi non dobbiamo fare nulla di attivo.

    BUG FIX: il matchmaking lancia il proxy con subprocess.Popen(...), che ritorna SUBITO —
    prima ancora che il proxy abbia finito di avviarsi e iniziato ad ascoltare sulla sua porta
    TCP. Se ci proviamo nella minuscola finestra prima che sia pronto, la connessione viene
    rifiutata. Ritentiamo per qualche secondo prima di arrenderci davvero, invece di fallire
    al primo colpo."""
    deadline = time.time() + connect_timeout
    proxy_sock = None
    last_error = None
    while time.time() < deadline:
        try:
            proxy_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            proxy_sock.settimeout(2)
            proxy_sock.connect((proxy_host, proxy_port))
            break
        except Exception as e:
            last_error = e
            try:
                proxy_sock.close()
            except Exception:
                pass
            proxy_sock = None
            time.sleep(0.3)

    if proxy_sock is None:
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

    # Primo messaggio del proxy: l'indirizzo del primary ATTUALE.
    info = _read_line()
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
                # BUG FIX: creare un socket NUOVO qui (porta locale nuova, casuale) faceva
                # rifiutare la richiesta come "non autorizzata" dal nuovo primary, perche' la
                # sua lista expected_players (decisa dal matchmaking all'inizio, condivisa con
                # il vecchio primary) valida i client per indirizzo — e la porta locale
                # sarebbe cambiata. Non tocchiamo il socket: restiamo sulla STESSA porta di
                # sempre, cambiamo solo a CHI mandiamo la richiesta di ingresso.
                nm.host_ip = new_host
                nm.host_port = new_port
                nm.last_host_seen = time.time()
                nm.sock.sendto(f"JOIN_REQUEST|{my_id}".encode(), (new_host, new_port))

    threading.Thread(target=_listen_for_failover, daemon=True).start()


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