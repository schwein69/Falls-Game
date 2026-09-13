import socket
import threading
import queue
import json
import time
from config import P2P_PORT, MAX_PLAYERS, SPAWN_HEIGHT, HEARTBEAT_INTERVAL, HEARTBEAT_TIMEOUT

class NetworkManager:
    
    AUTO_RELAY_METHODS = {"spawn_player", "player_left", "game_pause"}

    def __init__(self):
        self.sock = None
        self.running = False
        self.player_id = None
        self.host_ip = None
        self.host_port = P2P_PORT
        self.is_host = False

        # Client connessi. (ip, porta) -> player_id
        self.clients = {}

        self.host_is_player = False

        self.expected_players = {}
        self.on_client_joined = None

        self.matchmaking_status = "idle"  # idle -> connecting -> queued -> matched -> error
        self.matchmaking_error = None

     
        self.allow_host_migration = False
        self.last_host_seen = time.time()
        self.pending_migration_ids = set()

        self.msg_queue = queue.Queue()
        self.lock = threading.Lock()
        self._tick_thread = None
        self._heartbeat_thread = None

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
        sopravvissuti ci conoscono gia' con quell'id."""
        self.is_host = False
        self.host_ip = ip
        self.host_port = port
        self.last_host_seen = time.time()

        if self.sock is None:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind(('', 0))

        self.running = True
        threading.Thread(target=self.receive_loop, daemon=True).start()
        msg = "JOIN_REQUEST" if rejoin_id is None else f"JOIN_REQUEST|{rejoin_id}"
        self.sock.sendto(msg.encode(), (self.host_ip, self.host_port))

    def setup_host(self, port=None, player_id=0):
        """Diventiamo host/server UDP. 'port' e' opzionale: se non specificato usa la porta P2P
        fissa (config.P2P_PORT, caso P2P locale). Il server online dedicato passa invece la
        porta assegnata dinamicamente dal matchmaking server.

        player_id di default e' 0 (il primo host di una partita nuova). Durante una migrazione
        dell'host (vedi network_p2p.promote_to_host) il client promosso passa il PROPRIO id
        gia' esistente, per non cambiare identita' a meta' partita."""
        self.is_host = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        bind_port = port if port is not None else P2P_PORT
        self.sock.bind(('', bind_port))
        self.host_port = bind_port
        self.player_id = player_id
        self.running = True
        threading.Thread(target=self.receive_loop, daemon=True).start()
        print(f"[Network] Host/server avviato sulla porta {bind_port} (player_id={player_id})")

    # ================================
    # INVIO DATI
    # ================================
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
        """Avvia (se non gia' avviato) un thread che, a intervalli fissi, chiede a get_payload_fn()
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
        partita inizi — start_broadcast_tick da solo non manderebbe nulla in quel momento,
        perche' un payload vuoto non viene trasmesso)."""
        if self._heartbeat_thread is not None:
            return

        def _loop():
            while self.running:
                time.sleep(interval)
                if not self.running:
                    break
                if self.is_host:
                    self.broadcast_rpc("host_heartbeat")

        self._heartbeat_thread = threading.Thread(target=_loop, daemon=True)
        self._heartbeat_thread.start()

    def host_alive(self, timeout=HEARTBEAT_TIMEOUT):
        """SOLO lato client: True se abbiamo sentito l'host negli ultimi 'timeout' secondi.
        Non applicabile se siamo noi l'host (ritorna sempre True in quel caso)."""
        if self.is_host:
            return True
        return (time.time() - self.last_host_seen) < timeout

    # ================================
    # RICEZIONE DATI
    # ================================
    def receive_loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(8192)
                self.on_receive_data(data, addr)
            except Exception as e:
                if self.running:
                    print(f"Errore nel receive loop: {e}")
                break

    def on_receive_data(self, data_bytes, addr):
        if not self.is_host:
            # Siamo client: qualunque cosa riceviamo arriva per forza dall'host (topologia a
            # stella), quindi ogni pacchetto e' un segnale di vita implicito.
            self.last_host_seen = time.time()
        try:
            msg = data_bytes.decode().strip()

            if any(s in msg for s in ["JOIN_REQUEST", "ASSIGN_ID", "DISCONNECT"]):
                self.handle_internal_messages(msg, addr)
                return

            payload = json.loads(msg)

            if self.is_host:
                # Validazione anti-spoofing: il mittente deve essere davvero il giocatore che
                # dichiara di essere (confrontato con l'indirizzo registrato al JOIN_REQUEST).
                claimed_pid = payload.get("sender_id")
                real_pid = self.clients.get(addr)
                if real_pid is None or claimed_pid != real_pid:
                    print(f"[Host] Pacchetto rifiutato da {addr}: dichiara pid={claimed_pid}, "
                          f"registrato come pid={real_pid}")
                    return

                # Relay automatico SOLO per i metodi "semplici". update_pos e block_step
                # vengono gestiti esplicitamente dal gestore RPC lato host/server (vedi
                # RPC_REGISTRY in main.py / game_instance.py), che decide se/come propagarli
                # dopo averli passati all'autorita' di gioco.
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
        if self.is_host:
            if "JOIN_REQUEST" in msg:
                parts = msg.split('|')
                migration_rejoin_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None

                # Rientro dopo una migrazione dell'host: questo giocatore esisteva GIA' in
                # partita con questo id (lo sapevamo perche' era tra i sopravvissuti al momento
                # dell'elezione, vedi network_p2p.promote_to_host). Lo ri-registriamo con lo
                # STESSO id, senza rispedire uno spawn_player: gli altri sopravvissuti hanno
                # gia' la sua RemotePlayer da prima che l'host morisse, non e' un nuovo arrivo.
                if migration_rejoin_id is not None and migration_rejoin_id in self.pending_migration_ids:
                    self.clients[addr] = migration_rejoin_id
                    self.pending_migration_ids.discard(migration_rejoin_id)
                    my_port = self.sock.getsockname()[1]
                    self.sock.sendto(f"ASSIGN_ID|{migration_rejoin_id}|{my_port}".encode(), addr)
                    print(f"[Host] Player {migration_rejoin_id} rientrato dopo migrazione dell'host.")
                    if self.on_client_joined:
                        self.on_client_joined(migration_rejoin_id, addr)
                    return

                if len(self.clients) < MAX_PLAYERS:
                    if self.expected_players:
                        if addr not in self.expected_players:
                            print(f"[Host] Connessione non autorizzata rifiutata da {addr}")
                            return
                        new_id = self.expected_players[addr]
                    else:
                        new_id = len(self.clients) + 1

                    existing_pids = list(self.clients.values())
                    if self.host_is_player:
                        existing_pids.append(self.player_id)
                    for existing_pid in existing_pids:
                        self.send_to(addr, "spawn_player", existing_pid, 0, SPAWN_HEIGHT, 0)

                    self.clients[addr] = new_id
                    my_port = self.sock.getsockname()[1]
                    self.sock.sendto(f"ASSIGN_ID|{new_id}|{my_port}".encode(), addr)

                    spawn_payload = {"method": "spawn_player", "args": [new_id, 0, SPAWN_HEIGHT, 0], "sender_id": 0}
                    self.msg_queue.put(spawn_payload)
                    self.send_raw(json.dumps(spawn_payload))

                    if self.on_client_joined:
                        self.on_client_joined(new_id, addr)

            elif msg == "DISCONNECT":
                if addr in self.clients:
                    pid = self.clients.pop(addr)
                    print(f"[Host] Player {pid} ha lasciato la partita.")
                    leave_payload = {"method": "player_left", "args": [pid], "sender_id": 0}
                    self.msg_queue.put(leave_payload)
                    self.send_raw(json.dumps(leave_payload))

        else:
            if msg.startswith("ASSIGN_ID"):
                parts = msg.split('|')
                self.player_id = int(parts[1])
                print(f"[Network] Connesso con successo. Il mio ID: {self.player_id}")

    def process_queue(self, rpc_handler_func):
        while not self.msg_queue.empty():
            rpc_handler_func(self.msg_queue.get_nowait())

    def stop(self):
        self.running = False
        if self.sock:
            try:
                if not self.is_host and self.host_ip:
                    self.sock.sendto("DISCONNECT".encode(), (self.host_ip, self.host_port))
            except Exception:
                pass
            self.sock.close()