import socket
import threading
import queue
import json
import time
from config import P2P_PORT, MAX_PLAYERS, SPAWN_HEIGHT, HEARTBEAT_INTERVAL, HEARTBEAT_TIMEOUT, REJOIN_TIMER

REJOIN_LOAD_TIMEOUT = 30   

class Room:
    """SOLO host/server: chi c'e' nella stanza e in che stato. Un client e' il suo (ip, porta UDP)."""

    def __init__(self):
        self.clients = {}           # (ip, porta UDP) -> player_id
        self.last_seen = {}         # (ip, porta) -> istante dell'ultimo pacchetto ricevuto
        self.silent = {}            # (ip, porta) -> (player_id, istante): in pausa, in attesa di rientro
        self.rejoining = {}         # player_id -> istante: rientrato, sta caricando il livello (gli altri aspettano)
        self.expected = {}          # Online: (ip, porta) -> player_id, decisi dal matchmaking
        self.pending_ids = set()    # P2P: id che possono rientrare dopo una migrazione dell'host
        self.next_id = 1            # P2P: prossimo id da assegnare
        self.host_plays = False     # True per l'host P2P (e' anche un giocatore), False per il server dedicato
        self.match_started = False  # True dal via: solo allora si controlla chi e' silenzioso
        self.on_join = None         # callback(pid, addr): manda lo stato a chi entra

    def is_full(self):
        return len(self.clients) + 1 + (1 if self.host_plays else 0) > MAX_PLAYERS

    def addr_of(self, pid, ip=None):
        """L'indirizzo di un giocatore (con `ip`: solo se e' registrato da quell'IP), o None."""
        return next((a for a, p in self.clients.items() if p == pid and (ip is None or a[0] == ip)), None)

    def forget(self, addr, pid):
        """Toglie un client da tutte le tabelle."""
        self.clients.pop(addr, None)
        self.last_seen.pop(addr, None)
        self.silent.pop(addr, None)
        self.rejoining.pop(pid, None)

    def move(self, old_addr, new_addr, pid):
        """Il giocatore rientra da un nuovo processo (nuova porta): sostituisce il vecchio indirizzo."""
        self.forget(old_addr, pid)
        self.clients[new_addr] = pid
        self.last_seen[new_addr] = time.time()
        self.rejoining[pid] = time.time()


class OnlineStatus:
    """SOLO client Online: a che punto e' il collegamento (matchmaking, poi proxy)."""

    def __init__(self):
        self.status = "idle"        # idle -> connecting -> queued -> matched -> error
        self.error = None
        self.proxy_sock = None      # connessione al proxy: resta aperta per seguire un failover


class NetworkManager:
    def __init__(self):
        self.sock = None
        self.join_tcp_sock = None           # solo host: socket TCP per l'ingresso
        self.running = False
        self.player_id = None
        self.is_host = False
        self.host_ip = None                 # l'host/server a cui siamo collegati
        self.host_port = P2P_PORT           # (se siamo host: la nostra porta)
        self.join_rejected = None           # None se tutto ok, altrimenti il motivo del rifiuto
        self.last_host_seen = time.time()   # solo client: ultimo segnale dall'host
        self.allow_host_migration = False   # P2P: se l'host sparisce se ne elegge un altro
        self.leaving_deliberately = False   # True: stop() manda LEAVE (definitivo) invece di DISCONNECT

        self.room = Room()                  # solo host/server
        self.online = OnlineStatus()        # solo client Online

        self.msg_queue = queue.Queue()      # messaggi ricevuti, letti dal gioco con process_queue
        self.lock = threading.RLock()
        self._tick_thread = None
        self._heartbeat_thread = None

    # ---------------- collegamento ----------------
    def prepare_local_socket(self):
        """Crea il socket UDP locale (se manca) e ne restituisce la porta."""
        if self.sock is None:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind(('', 0))
        return self.sock.getsockname()[1]

    def join_network(self, ip, port, rejoin_id=None):
        """Ci uniamo come client a un host (P2P) o a un server dedicato (Online)."""
        self.is_host = False
        self.host_ip = ip
        self.host_port = port
        self.last_host_seen = time.time()
        was_already_running = self.running
        my_udp_port = self.prepare_local_socket()
        self.running = True
        if not was_already_running:
            threading.Thread(target=self.receive_loop, daemon=True).start()
        threading.Thread(target=self._tcp_join_handshake, args=(ip, port, my_udp_port, rejoin_id),
                         daemon=True).start()

    def _tcp_join_handshake(self, ip, port, my_udp_port, rejoin_id, max_seconds=15):
        """Via TCP manda la nostra porta UDP e aspetta l'id assegnato, o un rifiuto esplicito."""
        deadline = time.time() + max_seconds
        req = json.dumps({"udp_port": my_udp_port, "rejoin_id": rejoin_id}).encode()
        last_error = None
        while self.running and self.join_rejected is None and time.time() < deadline:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
                    tcp.settimeout(5)
                    tcp.connect((ip, port))
                    tcp.sendall(req)
                    data = tcp.recv(1024).decode()
                resp = json.loads(data)
                if "error" in resp:
                    self.join_rejected = {"ROOM_FULL": "La stanza e' piena.",
                                          "REJOIN_EXPIRED": "Il tempo per rientrare e' scaduto.",
                                          }.get(resp["error"], resp["error"])
                    print(f"[Network] {self.join_rejected}")
                    return
                self.player_id = resp["id"]
                print(f"[Network] Connesso con successo. Il mio ID: {self.player_id}")
                return
            except Exception as e:
                last_error = e
                time.sleep(0.5)
        if self.running and self.join_rejected is None:
            self.join_rejected = f"Impossibile connettersi all'host: {last_error}"
            print(f"[Network] {self.join_rejected}")

    def setup_host(self, port=None, player_id=0):
        """Diventa host/server: un socket UDP e uno TCP sulla STESSA porta. Dopo una migrazione
        il promosso passa il proprio id, per non cambiare identita'."""
        self.is_host = True
        bind_port = port if port is not None else P2P_PORT
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.join_tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.join_tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind(('', bind_port))
            self.join_tcp_sock.bind(('', bind_port))
            self.join_tcp_sock.listen(8)
        except OSError:
            self.sock.close()
            self.join_tcp_sock.close()
            self.sock = self.join_tcp_sock = None
            raise
        self.host_port = bind_port
        self.player_id = player_id
        self.running = True
        threading.Thread(target=self.receive_loop, daemon=True).start()
        threading.Thread(target=self._accept_join_loop, daemon=True).start()
        print(f"[Network] Host/server avviato sulla porta {bind_port} (player_id={player_id})")

    # ---------------- ingresso (solo host) ----------------
    def _accept_join_loop(self):
        """Accetta le connessioni TCP in ingresso, ognuna in un thread a parte."""
        while self.running:
            try:
                conn, addr = self.join_tcp_sock.accept()
            except OSError:
                break
            threading.Thread(target=self._handle_join_connection, args=(conn, addr), daemon=True).start()

    def _handle_join_connection(self, conn, addr):
        """Una richiesta di ingresso, dall'accept alla chiusura: risponde {"id"} oppure {"error"}."""
        room = self.room
        new_id = None
        client_addr = None
        existing_pids = []
        try:
            conn.settimeout(5)
            req = json.loads(conn.recv(1024).decode().strip())
            rejoin_id = req.get("rejoin_id")
            client_addr = (addr[0], req["udp_port"])

            with self.lock:
                if rejoin_id is not None and rejoin_id in room.pending_ids:   # rientro dopo una migrazione
                    room.clients[client_addr] = rejoin_id
                    room.pending_ids.discard(rejoin_id)
                    conn.sendall(json.dumps({"id": rejoin_id}).encode())
                    print(f"[Host] Player {rejoin_id} rientrato dopo migrazione dell'host.")
                    if room.on_join:
                        room.on_join(rejoin_id, client_addr)
                    return

                if rejoin_id is not None:    # nuovo processo (porta nuova) che chiede di essere "il giocatore X"
                    old_addr = room.addr_of(rejoin_id, ip=addr[0])    # stesso IP: protezione leggera
                    if old_addr is not None:
                        was_paused = old_addr in room.silent
                        room.move(old_addr, client_addr, rejoin_id)
                        conn.sendall(json.dumps({"id": rejoin_id}).encode())
                        print(f"[Host] Player {rejoin_id} e' rientrato ({old_addr[1]} -> {client_addr[1]}).")
                        if room.on_join:
                            room.on_join(rejoin_id, client_addr)
                        if not was_paused:                 # gli altri aspettano "rejoin_done": non vanno avanti mentre carica
                            self._announce_pause(True, rejoin_id)
                        return

                if client_addr in room.clients:            # richiesta ripetuta: stessa risposta
                    conn.sendall(json.dumps({"id": room.clients[client_addr]}).encode())
                    return

                if rejoin_id is not None and not room.expected:   # P2P: quel posto non esiste piu'
                    conn.sendall(json.dumps({"error": "REJOIN_EXPIRED"}).encode())
                    return

                if room.is_full():
                    print(f"[Host] Stanza piena, connessione rifiutata da {client_addr}")
                    conn.sendall(json.dumps({"error": "ROOM_FULL"}).encode())
                    return

                if room.expected:                          # Online: entrano solo gli indirizzi attesi
                    if client_addr not in room.expected:
                        print(f"[Host] Connessione non autorizzata rifiutata da {client_addr}")
                        conn.sendall(json.dumps({"error": "UNAUTHORIZED"}).encode())
                        return
                    new_id = room.expected[client_addr]
                else:
                    new_id = room.next_id
                    room.next_id += 1

                existing_pids = list(room.clients.values())
                if room.host_plays:
                    existing_pids.append(self.player_id)
                room.clients[client_addr] = new_id
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
        for existing_pid in existing_pids:                 # al nuovo arrivato: chi c'e' gia'. Agli altri: lui
            self.send_to(client_addr, "spawn_player", existing_pid, 0, SPAWN_HEIGHT, 0)
        spawn_payload = {"method": "spawn_player", "args": [new_id, 0, SPAWN_HEIGHT, 0], "sender_id": 0}
        self.msg_queue.put(spawn_payload)
        self.send_raw(json.dumps(spawn_payload))
        if room.on_join:
            room.on_join(new_id, client_addr)

    # ---------------- invio ----------------
    def send_message(self, method_name, *args):
        """Client: verso l'host/server. Host: verso tutti i client. Mittente: il nostro id."""
        if self.player_id is None and not self.is_host:
            return
        self.send_raw(json.dumps({"method": method_name, "args": args, "sender_id": self.player_id}))

    def send_raw(self, msg: str):
        # Dopo stop() non si manda piu' nulla: un heartbeat dopo il "DISCONNECT" sembrerebbe un ritorno.
        if not self.sock or not self.running:
            return
        encoded = msg.encode()
        try:
            if self.is_host:
                with self.lock:
                    for addr in self.room.clients.keys():
                        self.sock.sendto(encoded, addr)
            elif self.host_ip:
                self.sock.sendto(encoded, (self.host_ip, self.host_port))
        except Exception as e:
            print(f"[Network] Errore invio: {e}")

    def send_to(self, addr, method_name, *args):
        """Un messaggio a UN solo client (per indirizzo): lo stato per chi si e' appena unito o riconnesso."""
        if not self.sock:
            return
        data = {"method": method_name, "args": args, "sender_id": self.player_id}
        try:
            self.sock.sendto(json.dumps(data).encode(), addr)
        except Exception as e:
            print(f"[Network] Errore invio a {addr}: {e}")

    def broadcast_message(self, method_name, *args, exclude_pid=None):
        """Un nuovo messaggio dall'host/server a tutti i client (opzionalmente escluso un player_id)."""
        if not self.sock:
            return
        raw = json.dumps({"method": method_name, "args": args, "sender_id": self.player_id}).encode()
        with self.lock:
            for addr, pid in self.room.clients.items():
                if pid != exclude_pid:
                    self.sock.sendto(raw, addr)

    def relay_broadcast(self, raw_msg, exclude_addr):
        """Rilancia un messaggio gia' serializzato a tutti i client tranne il mittente."""
        with self.lock:
            for addr in self.room.clients.keys():
                if addr != exclude_addr:
                    self.sock.sendto(raw_msg.encode(), addr)

    def start_broadcast_tick(self, get_payload_fn, method_name="state_snapshot", interval=0.05):
        """Thread che a intervalli fissi trasmette a tutti lo stato di get_payload_fn() (le posizioni)."""
        if self._tick_thread is not None:
            return

        def _tick_loop():
            while self.running:
                time.sleep(interval)
                if not self.running:
                    break
                payload = get_payload_fn()
                if payload:
                    self.broadcast_message(method_name, payload)

        self._tick_thread = threading.Thread(target=_tick_loop, daemon=True)
        self._tick_thread.start()

    def start_heartbeat(self, interval=HEARTBEAT_INTERVAL):
        """Solo host: segnale di vita ai client (cosi' notano se sparisce) e controllo di chi e' silenzioso."""
        if self._heartbeat_thread is not None:
            return

        def _loop():
            while self.running:
                time.sleep(interval)
                if not self.running:
                    break
                if self.is_host:
                    self.broadcast_message("host_heartbeat")
                    self.check_client_liveness()

        self._heartbeat_thread = threading.Thread(target=_loop, daemon=True)
        self._heartbeat_thread.start()

    # ---------------- presenza e pausa (solo host) ----------------
    def _announce_pause(self, is_paused, pid):
        """Pausa/ripresa per tutti. L'host non riceve i propri broadcast: se e' anche un giocatore
        (P2P) la mettiamo nella sua coda, altrimenti continuerebbe a giocare."""
        self.broadcast_message("game_pause", is_paused, pid)
        if self.room.host_plays:
            self.msg_queue.put({"method": "game_pause", "args": [is_paused, pid], "sender_id": 0})

    def check_client_liveness(self, timeout=HEARTBEAT_TIMEOUT, grace_period=REJOIN_TIMER):
        """Solo host: un client silenzioso oltre 'timeout' mette in pausa tutti per 'grace_period' s,
        poi viene rimosso. Non gira in lobby (match_started), dove un client non manda nulla."""
        room = self.room
        if not room.match_started:
            return
        now = time.time()
        with self.lock:
            for addr, pid in list(room.clients.items()):
                if addr in room.silent or pid in room.rejoining:
                    continue                                   # gia' segnalato, o sta caricando il livello
                last_seen = room.last_seen.get(addr, now)
                if now - last_seen > timeout:
                    print(f"[Host] Player {pid} sembra disconnesso (silenzio da {now - last_seen:.1f}s). "
                          f"Metto in pausa la partita, {grace_period}s per rientrare...")
                    room.silent[addr] = (pid, now)
                    self._announce_pause(True, pid)

            for addr, (pid, silent_since) in list(room.silent.items()):
                if now - silent_since > grace_period:
                    print(f"[Host] Player {pid} non e' rientrato in tempo. Rimosso dalla partita.")
                    self._remove_client(addr, pid)

            for pid, since in list(room.rejoining.items()):
                if now - since > REJOIN_LOAD_TIMEOUT:
                    print(f"[Host] Player {pid} non ha finito di rientrare in {REJOIN_LOAD_TIMEOUT}s. Rimosso dalla partita.")
                    self._remove_client(room.addr_of(pid), pid)

    def _remove_client(self, addr, pid):
        """Rimozione definitiva per tempo scaduto: riprende la partita e avvisa tutti che e' uscito."""
        self.room.forget(addr, pid)
        self._announce_pause(False, pid)
        leave_payload = {"method": "player_left", "args": [pid], "sender_id": self.player_id}
        self.msg_queue.put(leave_payload)
        self.send_raw(json.dumps(leave_payload))

    def host_alive(self, timeout=HEARTBEAT_TIMEOUT):
        """Solo client: True se abbiamo sentito l'host negli ultimi 'timeout' secondi."""
        return self.is_host or (time.time() - self.last_host_seen) < timeout

    # ---------------- ricezione ----------------
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
        room = self.room
        if not self.is_host:
            self.last_host_seen = time.time()
        try:
            msg = data_bytes.decode().strip()
            if msg in ("DISCONNECT", "LEAVE"):
                self.handle_internal_messages(msg, addr)
                return

            payload = json.loads(msg)
            if not self.is_host and payload.get("method") == "host_heartbeat":
                self.send_message("client_heartbeat")

            if self.is_host:
                claimed_pid = payload.get("sender_id")             # anti-spoofing: il mittente e' chi dice di essere?
                real_pid = room.clients.get(addr)
                if real_pid is None or claimed_pid != real_pid:
                    print(f"[Host] Pacchetto rifiutato da {addr}: dichiara pid={claimed_pid}, "
                          f"registrato come pid={real_pid}")
                    return
                room.last_seen[addr] = time.time()
                if addr in room.silent:                            # ha ripreso a parlare: e' tornato
                    print(f"[Host] Player {real_pid} e' rientrato.")
                    del room.silent[addr]
                    self._announce_pause(False, real_pid)
                if payload.get("method") == "rejoin_done":         # ha finito di caricare: si riparte per tutti
                    if room.rejoining.pop(real_pid, None) is not None:
                        self._announce_pause(False, real_pid)
                    return
                if payload.get("method") == "spawn_player":        # i client parlano solo con l'host: lo rilancia agli altri
                    self.relay_broadcast(msg, exclude_addr=addr)

            if payload.get("sender_id") != self.player_id:
                self.msg_queue.put(payload)
        except Exception:
            pass

    def handle_internal_messages(self, msg, addr):
        """"LEAVE": uscita volontaria, definitiva. "DISCONNECT": finestra chiusa; in partita pausa e
        REJOIN_TIMER s per rientrare (la rimozione la fa check_client_liveness), fuori partita esce subito."""
        room = self.room
        if not self.is_host or addr not in room.clients or msg not in ("DISCONNECT", "LEAVE"):
            return
        pid = room.clients[addr]
        if msg == "DISCONNECT" and room.match_started:
            if addr not in room.silent:
                print(f"[Host] Player {pid} ha chiuso la finestra, {REJOIN_TIMER}s per rientrare.")
                room.silent[addr] = (pid, time.time())
                self._announce_pause(True, pid)
            return
        was_paused = addr in room.silent or pid in room.rejoining
        room.forget(addr, pid)
        print(f"[Host] Player {pid} ha lasciato la partita.")
        if was_paused:                                             # la pausa era per lui: si riparte
            self._announce_pause(False, pid)
        leave_payload = {"method": "player_left", "args": [pid], "sender_id": 0}
        self.msg_queue.put(leave_payload)
        self.send_raw(json.dumps(leave_payload))

    def process_queue(self, message_handler_func):
        while not self.msg_queue.empty():
            message_handler_func(self.msg_queue.get_nowait())

    def stop(self):
        self.running = False
        for owner, name in ((self, "join_tcp_sock"), (self.online, "proxy_sock")):
            sock = getattr(owner, name)
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
                setattr(owner, name, None)
        if self.sock:
            try:
                if not self.is_host and self.host_ip:
                    goodbye = "LEAVE" if self.leaving_deliberately else "DISCONNECT"
                    self.sock.sendto(goodbye.encode(), (self.host_ip, self.host_port))
            except Exception:
                pass
            self.sock.close()