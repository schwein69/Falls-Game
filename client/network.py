import socket
import threading
import queue
import json
from config import * # Ensure P2P_PORT, SERVER_IP, SERVER_PORT, MAX_PLAYERS are here

class NetworkManager:
    def __init__(self, mode='p2p'):
        self.mode = mode # 'p2p' or 'online'
        self.sock = None
        self.running = False
        
        # Identity
        self.player_id = None
        self.host_ip = None
        self.host_port = P2P_PORT
        self.is_host = False
        
        # Connected Clients (Only used if self.is_host == True)
        self.clients = {} 
        
        self.msg_queue = queue.Queue()
        self.lock = threading.Lock()

    # ================================
    # START LOGIC
    # ================================
    def start(self):
        if self.mode == 'p2p':
            self.start_p2p()
        elif self.mode == 'online':
            self.start_client_server()

    def start_p2p(self):
        """Attempts to host; if port is busy, joins as client."""
        try:
            self.setup_host()
        except OSError:
            # Fallback to local join if port is taken
            self.join_network("127.0.0.1", P2P_PORT)

    def start_client_server(self):
        """Connects to a dedicated external server."""
        # print(f"[Network] Connecting to Dedicated Server at {SERVER_IP}")
        # self.join_network(SERVER_IP, SERVER_PORT)

    def join_network(self, ip, port):
        self.is_host = False
        self.host_ip = ip
        self.host_port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('', 0)) # Bind to any available local port
        self.running = True
        threading.Thread(target=self.receive_loop, daemon=True).start()
        
        # Initial Handshake
        self.sock.sendto("JOIN_REQUEST".encode(), (self.host_ip, self.host_port))

    def setup_host(self):
        self.is_host = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('', P2P_PORT))
        self.player_id = 0
        self.running = True
        threading.Thread(target=self.receive_loop, daemon=True).start()
        print(f"[Network] P2P Host started on Port {P2P_PORT}")

    # ================================
    # DATA TRANSMISSION
    # ================================
    def send_rpc(self, method_name, *args):
        if self.player_id is None and not self.is_host:
            return # Don't send until we have an ID

        data = {
            "method": method_name,
            "args": args,
            "sender_id": self.player_id
        }
        self.send_raw(json.dumps(data))

    def send_raw(self, msg: str):
        if not self.sock: return
        encoded = msg.encode()
        
        try:
            if self.is_host:
                # P2P Host: Send to every registered client
                with self.lock:
                    for ip, (pid, port) in self.clients.items():
                        self.sock.sendto(encoded, (ip, port))
            else:
                # Client (P2P or Online): Always send to the 'Host' (Server)
                if self.host_ip:
                    self.sock.sendto(encoded, (self.host_ip, self.host_port))
        except Exception as e:
            print(f"[Network] Send Error: {e}")

    # ================================
    # RECEIVE LOGIC
    # ================================
    def receive_loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(8192) # Increased buffer
                self.on_receive_data(data, addr)
            except Exception as e:
                if self.running: print(f"Receive Loop Error: {e}")
                break

    def on_receive_data(self, data_bytes, addr):
        try:
            msg = data_bytes.decode().strip()

            # Handle non-JSON management strings
            if any(s in msg for s in ["JOIN_REQUEST", "ASSIGN_ID", "DISCONNECT"]):
                self.handle_internal_messages(msg, addr)
                return

            payload = json.loads(msg)
            
            # 1. Server/Host Relay Logic
            if self.is_host:
                # Broadcast to others, exclude the person who sent it
                self.relay_broadcast(msg, exclude_ip=addr[0])
            
            # 2. Local Processing
            if payload.get("sender_id") != self.player_id:
                self.msg_queue.put(payload)

        except Exception:
            pass 

    def relay_broadcast(self, raw_msg, exclude_ip):
        with self.lock:
            for ip, (pid, port) in self.clients.items():
                if ip != exclude_ip:
                    self.sock.sendto(raw_msg.encode(), (ip, port))

    def handle_internal_messages(self, msg, addr):
        addr_ip, addr_port = addr
        
        if self.is_host:
            if "JOIN_REQUEST" in msg:
                if len(self.clients) < MAX_PLAYERS:
                    # 1. Assign new ID
                    new_id = len(self.clients) + 1
                    
                    # 2. Tell the NEW player their ID
                    self.sock.sendto(f"ASSIGN_ID|{new_id}|{P2P_PORT}".encode(), addr)
                    
                    # 3. Tell the NEW player about EVERYONE else (including the Host)
                    # Send Host (ID 0)
                    host_spawn = {"method": "spawn_player", "args": [0, 0, 5, 0], "sender_id": 0}
                    self.sock.sendto(json.dumps(host_spawn).encode(), addr)
                    
                    # Send all other connected clients
                    for ip, (pid, port) in self.clients.items():
                        other_spawn = {"method": "spawn_player", "args": [pid, 0, 5, 0], "sender_id": 0}
                        self.sock.sendto(json.dumps(other_spawn).encode(), addr)

                    # 4. Add new player to host list and notify EXISTING players
                    self.clients[addr_ip] = (new_id, addr_port)
                    
                    broadcast_payload = {"method": "spawn_player", "args": [new_id, 0, 5, 0], "sender_id": 0}
                    self.msg_queue.put(broadcast_payload) # Spawn on Host's screen
                    self.relay_broadcast(json.dumps(broadcast_payload), exclude_ip=addr_ip) # Spawn on others

        else: # Client Side
            if msg.startswith("ASSIGN_ID"):
                parts = msg.split('|')
                self.player_id = int(parts[1])
                print(f"[Network] Joined successfully. My ID: {self.player_id}")

    def process_queue(self, rpc_handler_func):
        while not self.msg_queue.empty():
            rpc_handler_func(self.msg_queue.get_nowait())

    def stop(self):
        self.running = False
        if self.sock:
            self.sock.close()