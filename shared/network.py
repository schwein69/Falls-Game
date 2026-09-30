import socket
import threading
import queue
import json
import time
from config import P2P_PORT, MAX_PLAYERS, SPAWN_HEIGHT, HEARTBEAT_INTERVAL, HEARTBEAT_TIMEOUT, REJOIN_TIMER

class NetworkManager:
    
    AUTO_RELAY_METHODS = {"spawn_player", "player_left", "game_pause"}

    def __init__(self):
        self.sock = None
        self.join_tcp_sock = None  # SOLO host: socket TCP per l'ingresso, vedi setup_host
        self.running = False
        self.player_id = None
        self.join_rejected = None  # None finche' tutto ok; stringa col motivo se rifiutati (es. stanza piena)
        self.host_ip = None
        self.host_port = P2P_PORT
        self.is_host = False

        # Client connessi. (ip, porta) -> player_id
        self.clients = {}
        self.next_client_id = 1

      
        self.client_last_seen = {}
        # (ip,porta) -> (player_id, timestamp_in_pausa): client sospettati disconnessi, in
        # attesa che si facciano risentire entro REJOIN_TIMER secondi prima di rimuoverli.
        self.disconnected_clients = {}

        self.host_is_player = False

        self.expected_players = {}
        self.on_client_joined = None

        self.matchmaking_status = "idle"  # idle -> connecting -> queued -> matched -> error
        self.matchmaking_error = None

     
        self.allow_host_migration = False
        self.last_host_seen = time.time()
        self.pending_migration_ids = set()

        self.msg_queue = queue.Queue()
        self.lock = threading.RLock()
        self._tick_thread = None
        self._heartbeat_thread = None

        self.proxy_sock = None

        self.match_in_progress = False

    def prepare_local_socket(self):
        """Crea (se non esiste gia') il socket UDP locale e ne restituisce la porta assegnata.
        Usato in modalita' online per conoscere la nostra porta PRIMA di comunicarla al
        matchmaking server (vedi network_online.py)."""
        if self.sock is None:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind(('', 0))
        return self.sock.getsockname()[1]

    def join_network(self, ip, port, rejoin_id=None):
        """Ci uniamo come client a un host (P2P locale oppure server online dedicato).

        rejoin_id: usato SOLO durante una migrazione dell'host (vedi network_p2p.py). Se
        valorizzato, segnaliamo al nuovo host "ero gia' il giocatore X, ridammi lo stesso id"
        invece di farci assegnare un id nuovo di zecca — fondamentale perche' gli altri
        sopravvissuti ci conoscono gia' con quell'id.

        L'ingresso vero e proprio passa da una connessione TCP dedicata (_tcp_join_handshake):
        apriamo il nostro socket UDP di gioco SUBITO (ci serve la porta per dirla all'host), ma
        l'id ce lo diciamo con TCP, che garantisce la consegna senza bisogno di ritentare a
        mano un pacchetto che potrebbe perdersi."""
        self.is_host = False
        self.host_ip = ip
        self.host_port = port
        self.last_host_seen = time.time()

        was_already_running = self.running
        if self.sock is None:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind(('', 0))
        my_udp_port = self.sock.getsockname()[1]

        self.running = True
        if not was_already_running:
            threading.Thread(target=self.receive_loop, daemon=True).start()
        threading.Thread(target=self._tcp_join_handshake, args=(ip, port, my_udp_port, rejoin_id),
                         daemon=True).start()

    def _tcp_join_handshake(self, ip, port, my_udp_port, rejoin_id, max_seconds=15):
        """Si connette via TCP all'host, manda la nostra porta UDP (e l'eventuale id di
        rientro), e aspetta la risposta con l'id assegnato — o un rifiuto esplicito (stanza
        piena, non autorizzato)."""
        succeeded = False
        deadline = time.time() + max_seconds
        req = json.dumps({"udp_port": my_udp_port, "rejoin_id": rejoin_id}).encode()
        last_error = None
        while self.running and not succeeded and self.join_rejected is None \
                and time.time() < deadline:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
                    tcp.settimeout(5)
                    tcp.connect((ip, port))
                    tcp.sendall(req)
                    data = tcp.recv(1024).decode()
                resp = json.loads(data)
                if "error" in resp:
                    self.join_rejected = ("La stanza e' piena." if resp["error"] == "ROOM_FULL"
                                          else resp["error"])
                    print(f"[Network] {self.join_rejected}")
                    return
                self.player_id = resp["id"]
                succeeded = True
                print(f"[Network] Connesso con successo. Il mio ID: {self.player_id}")
                return
            except Exception as e:
                last_error = e
                time.sleep(0.5)

        if self.running and not succeeded and self.join_rejected is None:
            self.join_rejected = f"Impossibile connettersi all'host: {last_error}"
            print(f"[Network] {self.join_rejected}")

    def setup_host(self, port=None, player_id=0):
        """Diventiamo host/server. 'port' e' opzionale: se non specificato usa la porta P2P
        fissa. Il server online dedicato passa invece la
        porta assegnata dinamicamente dal matchmaking server.

        Apriamo DUE socket sulla STESSA porta: uno UDP e uno TCP."""
        self.is_host = True
        bind_port = port if port is not None else P2P_PORT

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind(('', bind_port))
        except OSError:
            self.sock.close()
            self.sock = None
            raise

        self.join_tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.join_tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.join_tcp_sock.bind(('', bind_port))
            self.join_tcp_sock.listen(8)
        except OSError:
            self.sock.close()
            self.sock = None
            self.join_tcp_sock.close()
            self.join_tcp_sock = None
            raise

        self.host_port = bind_port
        self.player_id = player_id
        self.running = True
        threading.Thread(target=self.receive_loop, daemon=True).start()
        threading.Thread(target=self._accept_join_loop, daemon=True).start()
        print(f"[Network] Host/server avviato sulla porta {bind_port} (player_id={player_id})")

    def _accept_join_loop(self):
        """SOLO host: accetta connessioni TCP in ingresso, una alla volta, e delega ognuna a
        un thread separato (_handle_join_connection)."""
        while self.running:
            try:
                conn, addr = self.join_tcp_sock.accept()
            except OSError:
                break
            threading.Thread(target=self._handle_join_connection, args=(conn, addr), daemon=True).start()

    def _handle_join_connection(self, conn, addr):
        """Gestisce UNA richiesta di ingresso, dall'accept alla chiusura."""
        new_id = None
        client_addr = None
        existing_pids = []
        try:
            conn.settimeout(5)
            data = conn.recv(1024).decode().strip()
            req = json.loads(data)
            udp_port = req["udp_port"]
            rejoin_id = req.get("rejoin_id")
            client_addr = (addr[0], udp_port)

            with self.lock:
                if rejoin_id is not None and rejoin_id in self.pending_migration_ids:
                    self.clients[client_addr] = rejoin_id
                    self.pending_migration_ids.discard(rejoin_id)
                    conn.sendall(json.dumps({"id": rejoin_id}).encode())
                    print(f"[Host] Player {rejoin_id} rientrato dopo migrazione dell'host.")
                    if self.on_client_joined:
                        self.on_client_joined(rejoin_id, client_addr)
                    return

                if client_addr in self.clients:
                    conn.sendall(json.dumps({"id": self.clients[client_addr]}).encode())
                    return

                total_players_if_accepted = len(self.clients) + 1 + (1 if self.host_is_player else 0)
                if total_players_if_accepted > MAX_PLAYERS:
                    print(f"[Host] Stanza piena, connessione rifiutata da {client_addr}")
                    conn.sendall(json.dumps({"error": "ROOM_FULL"}).encode())
                    return

                if self.expected_players:
                    if client_addr not in self.expected_players:
                        print(f"[Host] Connessione non autorizzata rifiutata da {client_addr}")
                        conn.sendall(json.dumps({"error": "UNAUTHORIZED"}).encode())
                        return
                    new_id = self.expected_players[client_addr]
                else:
                    new_id = self.next_client_id
                    self.next_client_id += 1

                existing_pids = list(self.clients.values())
                if self.host_is_player:
                    existing_pids.append(self.player_id)

                self.clients[client_addr] = new_id
                conn.sendall(json.dumps({"id": new_id}).encode())

        except Exception as e:
            print(f"[Host] Errore nella richiesta di ingresso da {addr}: {e}")
            return
        finally:
            try:
                conn.close()
            except Exception:
                pass

        if new_id is None:
            return

        for existing_pid in existing_pids:
            self.send_to(client_addr, "spawn_player", existing_pid, 0, SPAWN_HEIGHT, 0)

        spawn_payload = {"method": "spawn_player", "args": [new_id, 0, SPAWN_HEIGHT, 0], "sender_id": 0}
        self.msg_queue.put(spawn_payload)
        self.send_raw(json.dumps(spawn_payload))

        if self.on_client_joined:
            self.on_client_joined(new_id, client_addr)

    # INVIO DATI
    def send_rpc(self, method_name, *args):
        """Da usare per mandare un RPC verso l'host/server (se siamo client) oppure verso tutti
        i client registrati (se siamo host/server) con il NOSTRO id come mittente."""
        if self.player_id is None and not self.is_host:
            return
        data = {"method": method_name, "args": args, "sender_id": self.player_id}
        self.send_raw(json.dumps(data))

    def send_raw(self, msg: str):
        if not self.sock:
            return
        encoded = msg.encode()
        try:
            if self.is_host:
                with self.lock:
                    for addr in self.clients.keys():
                        self.sock.sendto(encoded, addr)
            else:
                if self.host_ip:
                    self.sock.sendto(encoded, (self.host_ip, self.host_port))
        except Exception as e:
            print(f"[Network] Errore invio: {e}")

    def send_to(self, addr, method_name, *args):
        """Manda un RPC a UN SOLO client specifico (per indirizzo). Usato dall'autorita' di
        gioco per il 'catch-up' di chi si e' appena unito o riconnesso."""
        if not self.sock:
            return
        data = {"method": method_name, "args": args, "sender_id": self.player_id}
        try:
            self.sock.sendto(json.dumps(data).encode(), addr)
        except Exception as e:
            print(f"[Network] Errore invio a {addr}: {e}")

    def broadcast_relay(self, raw_msg, exclude_addr=None):
        """Rilancia un messaggio GIA' SERIALIZZATO a tutti i client, escludendo opzionalmente
        un indirizzo (tipicamente il mittente originale)."""
        if not self.is_host or not self.sock:
            return
        with self.lock:
            for addr in self.clients.keys():
                if addr != exclude_addr:
                    self.sock.sendto(raw_msg.encode(), addr)

    def broadcast_rpc(self, method_name, *args, exclude_pid=None):
        """Costruisce un NUOVO RPC (con sender_id = noi, l'host/server) e lo manda a tutti i
        client, opzionalmente escludendo un player_id. Usato dall'autorita' di gioco per
        comunicare eventi che ha appena deciso lei stessa (es. block_destroyed, state_snapshot)."""
        if not self.sock:
            return
        data = {"method": method_name, "args": args, "sender_id": self.player_id}
        raw = json.dumps(data).encode()
        with self.lock:
            for addr, pid in self.clients.items():
                if pid != exclude_pid:
                    self.sock.sendto(raw, addr)

    def start_broadcast_tick(self, get_payload_fn, method_name="state_snapshot", interval=0.05):
        """Avvia un thread che, a intervalli fissi, chiede a get_payload_fn()
        lo stato corrente e lo trasmette a tutti i client con broadcast_rpc. E' cosi' che
        l'autorita' (host P2P o server online) ridistribuisce le posizioni: a cadenza fissa,
        invece che ad ogni singolo pacchetto ricevuto da ogni singolo client."""
        if self._tick_thread is not None:
            return

        def _tick_loop():
            while self.running:
                time.sleep(interval)
                if not self.running:
                    break
                payload = get_payload_fn()
                if payload:
                    self.broadcast_rpc(method_name, payload)

        self._tick_thread = threading.Thread(target=_tick_loop, daemon=True)
        self._tick_thread.start()

    def start_heartbeat(self, interval=HEARTBEAT_INTERVAL):
        """SOLO lato host: manda un piccolo segnale di vita a tutti i client a intervalli
        regolari, cosi' possono accorgersi se l'host sparisce (vedi host_alive) anche quando
        non c'e' ancora nessuno stato di gioco da trasmettere (es. in lobby, prima che la
        partita inizi). Sullo STESSO intervallo, controlla anche se qualche CLIENT e' rimasto
        silenzioso troppo a lungo — vedi check_client_liveness."""
        if self._heartbeat_thread is not None:
            return

        def _loop():
            while self.running:
                time.sleep(interval)
                if not self.running:
                    break
                if self.is_host:
                    self.broadcast_rpc("host_heartbeat")
                    self.check_client_liveness()

        self._heartbeat_thread = threading.Thread(target=_loop, daemon=True)
        self._heartbeat_thread.start()

    def check_client_liveness(self, timeout=HEARTBEAT_TIMEOUT, grace_period=REJOIN_TIMER):
        """SOLO lato host: rileva un client rimasto silenzioso.
        Se il silenzio supera 'timeout' secondi, mette
        in pausa la partita per tutti e gli da' 'grace_period' secondi per farsi risentire
        (basta che riprenda a mandare pacchetti dalla STESSA porta — non serve nessun
        protocollo di rientro esplicito, un blip di rete si risolve da solo, vedi
        on_receive_data). Se il tempo scade senza notizie, lo rimuove definitivamente.
        """
        if not self.match_in_progress:
            return
        now = time.time()
        with self.lock:
            for addr, pid in list(self.clients.items()):
                if addr in self.disconnected_clients:
                    continue  # gia' segnalato, aspettiamo che rientri o scada la grazia
                last_seen = self.client_last_seen.get(addr, now)
                if now - last_seen > timeout:
                    print(f"[Host] Player {pid} sembra disconnesso (silenzio da {now - last_seen:.1f}s). "
                          f"Metto in pausa la partita, {grace_period}s per rientrare...")
                    self.disconnected_clients[addr] = (pid, now)
                    self.broadcast_rpc("game_pause", True, pid)

            for addr in list(self.disconnected_clients.keys()):
                pid, disconnected_at = self.disconnected_clients[addr]
                if now - disconnected_at > grace_period:
                    print(f"[Host] Player {pid} non e' rientrato in tempo. Rimosso dalla partita.")
                    del self.disconnected_clients[addr]
                    self.clients.pop(addr, None)
                    self.client_last_seen.pop(addr, None)
                    self.broadcast_rpc("game_pause", False, pid)
                    leave_payload = {"method": "player_left", "args": [pid], "sender_id": self.player_id}
                    self.msg_queue.put(leave_payload)
                    self.send_raw(json.dumps(leave_payload))

    def host_alive(self, timeout=HEARTBEAT_TIMEOUT):
        """SOLO lato client: True se abbiamo sentito l'host negli ultimi 'timeout' secondi.
        Non applicabile se siamo noi l'host (ritorna sempre True in quel caso)."""
        if self.is_host:
            return True
        return (time.time() - self.last_host_seen) < timeout

    # RICEZIONE DATI
    def receive_loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(8192)
                self.on_receive_data(data, addr)
            except ConnectionResetError as e:
                if self.running:
                    print(f"[Network] Pacchetto UDP scartato (errore transitorio innocuo): {e}")
                continue
            except Exception as e:
                if self.running:
                    print(f"Errore nel receive loop: {e}")
                break

    def on_receive_data(self, data_bytes, addr):
        if not self.is_host:
            # Siamo client
            self.last_host_seen = time.time()
        try:
            msg = data_bytes.decode().strip()

            if msg == "DISCONNECT":
                self.handle_internal_messages(msg, addr)
                return

            payload = json.loads(msg)

            if self.is_host:
                # Validazione anti-spoofing.
                claimed_pid = payload.get("sender_id")
                real_pid = self.clients.get(addr)
                if real_pid is None or claimed_pid != real_pid:
                    print(f"[Host] Pacchetto rifiutato da {addr}: dichiara pid={claimed_pid}, "
                          f"registrato come pid={real_pid}")
                    return

                self.client_last_seen[addr] = time.time()
                if addr in self.disconnected_clients:
                    print(f"[Host] Player {real_pid} e' rientrato.")
                    del self.disconnected_clients[addr]
                    self.broadcast_rpc("game_pause", False, real_pid)

                if payload.get("method") in self.AUTO_RELAY_METHODS:
                    self.relay_broadcast(msg, exclude_addr=addr)

            if payload.get("sender_id") != self.player_id:
                self.msg_queue.put(payload)

        except Exception:
            pass

    def relay_broadcast(self, raw_msg, exclude_addr):
        with self.lock:
            for addr in self.clients.keys():
                if addr != exclude_addr:
                    self.sock.sendto(raw_msg.encode(), addr)

    def handle_internal_messages(self, msg, addr):
        """L'unico messaggio 'di servizio' rimasto su UDP e' DISCONNECT: l'ingresso (join, id,
        rifiuti) e' passato interamente alla connessione TCP dedicata (vedi
        _handle_join_connection). DISCONNECT resta qui perche' arriva da chi e' GIA' entrato,
        su una connessione UDP gia' stabilita — non serve nessuna affidabilita' in piu': se si
        perde, il silenzio lo scopre comunque check_client_liveness poco dopo."""
        if self.is_host:
            if msg == "DISCONNECT" and addr in self.clients:
                pid = self.clients.pop(addr)
                self.client_last_seen.pop(addr, None)
                self.disconnected_clients.pop(addr, None)
                print(f"[Host] Player {pid} ha lasciato la partita.")
                leave_payload = {"method": "player_left", "args": [pid], "sender_id": 0}
                self.msg_queue.put(leave_payload)
                self.send_raw(json.dumps(leave_payload))

    def process_queue(self, rpc_handler_func):
        while not self.msg_queue.empty():
            rpc_handler_func(self.msg_queue.get_nowait())

    def stop(self):
        self.running = False
        if self.join_tcp_sock:
            try:
                self.join_tcp_sock.close()
            except Exception:
                pass
            self.join_tcp_sock = None
        if self.proxy_sock:
            try:
                self.proxy_sock.close()
            except Exception:
                pass
            self.proxy_sock = None
        if self.sock:
            try:
                if not self.is_host and self.host_ip:
                    self.sock.sendto("DISCONNECT".encode(), (self.host_ip, self.host_port))
            except Exception:
                pass
            self.sock.close()