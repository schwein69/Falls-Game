import socket
import threading
import time
from config import *

class NetworkManager:
    def __init__(self, mode='p2p'):
        self.mode = mode
        self.sock = None

        # clients: map client_ip -> (player_id, client_port)
        # on host: stores connected clients' IPs and the port they used to contact host
        self.clients = {}

        # player_ips: map player_id -> ip (used by both host and clients)
        self.player_ips = {}

        self.player_id = None
        self.receive_callbacks = []
        self.running = False
        self.host_ip = None
        self.is_host = False
        self.lock = threading.Lock()

        # ensure attribute exists for clients too; host may override when binding
        # default to configured P2P port (from config.py)
        self.host_port = P2P_PORT

    # ---------- SHARED INTERFACE ----------
    def start(self):
        if self.mode == 'p2p':
            self.start_p2p()
        elif self.mode == 'online':
            self.start_client_server()
        else:
            raise ValueError("Invalid mode: choose 'p2p' or 'online'")

    def send(self, msg):
        if not self.sock:
            return
        try:
            if self.mode == 'p2p':
                if self.is_host:
                    # host -> forward to each client using their source port
                    with self.lock:
                        for ip, (pid, port) in list(self.clients.items()):
                            try:
                                self.sock.sendto(msg.encode(), (ip, port))
                            except Exception as e:
                                print(f"[NetworkManager] Broadcast error to {ip}:{port}: {e}")
                else:
                    # client -> send to host (use known host_port)
                    if self.host_ip:
                        target_port = self.host_port or P2P_PORT
                        self.sock.sendto(msg.encode(), (self.host_ip, target_port))

            elif self.mode == 'online':
                if self.host_ip:
                    self.sock.sendto(msg.encode(), (self.host_ip, MATCHMAKING_TCP_PORT))

        except Exception as e:
            print(f"[NetworkManager] Send error: {e}")

    def stop(self):
        # Stop loops first
        self.running = False

        # Notify clients if host
        if self.is_host:
            try:
                self.broadcast_message("HOST_LEFT")
            except Exception:
                pass

        # Client disconnect message
        if not self.is_host and self.sock and self.host_ip:
            try:
                self.sock.sendto("DISCONNECT".encode(), (self.host_ip, self.host_port or P2P_PORT))
            except Exception:
                pass

        # Wait briefly to allow loops to exit
        time.sleep(0.1)

        # Close socket safely
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def add_receive_callback(self, cb):
        self.receive_callbacks.append(cb)

    # ---------- P2P LOCAL ----------
    def start_p2p(self):
        try:
            self.setup_host(P2P_PORT)
        except Exception:
            # fallback to acting as client on localhost
            self.join_host("127.0.0.1")

    def setup_host(self, start_port=P2P_PORT):
        self.is_host = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        port = start_port
        while True:
            try:
                self.sock.bind(('', port))
                self.host_port = port
                break
            except OSError as e:
                # if port in use, try next port
                if getattr(e, "winerror", None) == 10048 or e.errno == 98:
                    port += 1
                else:
                    raise e

        # reset host state
        self.clients = {}
        self.player_ips = {}
        self.player_id = 0
        self.player_ips[0] = self.get_local_ip()
        self.running = True

        threading.Thread(target=self.host_receive_loop, daemon=True).start()
        print(f"[Host] Hosting on {self.get_local_ip()}:{self.host_port}")

    def join_host(self, host_ip, host_port=None):
        """
        Join a host. Optionally provide host_port (if known). If host_port is None
        we'll default to P2P_PORT until the host sends an ASSIGN_ID response that
        contains the actual port used by the host.
        """
        self.is_host = False
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('', 0))  # ephemeral client port
        self.host_ip = host_ip
        if host_port is not None:
            self.host_port = host_port
        else:
            # fallback default port until host replies with actual port
            self.host_port = P2P_PORT

        self.running = True
        threading.Thread(target=self.client_receive_loop, daemon=True).start()

        # send join request to the host
        try:
            self.sock.sendto("JOIN_REQUEST".encode(), (host_ip, self.host_port))
        except Exception as e:
            print(f"[NetworkManager] Failed to send JOIN_REQUEST to {host_ip}:{self.host_port}: {e}")

        start_time = time.time()
        while self.player_id is None and time.time() - start_time < 5:
            time.sleep(0.1)

        if self.player_id is None:
            raise Exception("Failed to join host")

    # ---------- RECEIVE LOOPS ----------
    def host_receive_loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(1024)
                msg = data.decode().strip()

                # addr is (ip, port)
                addr_ip, addr_port = addr

                if msg == "DISCONNECT":
                    if addr_ip in self.clients:
                        pid, _ = self.clients.pop(addr_ip)
                        self.player_ips.pop(pid, None)
                        print(f"[Host] Player {pid} disconnected ({addr_ip})")
                        self.broadcast_player_list()

                elif msg == "JOIN_REQUEST":
                    # store client's ip and port so we can send future messages to the correct client port
                    if addr_ip not in self.clients and len(self.clients) < MAX_PLAYERS - 1:
                        new_id = len(self.clients) + 1
                        self.clients[addr_ip] = (new_id, addr_port)
                        self.player_ips[new_id] = addr_ip
                        ips_msg = ",".join(f"{pid}:{ip}" for pid, ip in self.player_ips.items())
                        response = f"ASSIGN_ID|{new_id}|{ips_msg}|{self.host_port}"
                        self.sock.sendto(response.encode(), addr)
                        print(f"[Host] Player {new_id} connected from {addr_ip}:{addr_port}")
                        self.broadcast_player_list()

                else:
                    # any other message from a client -> broadcast to other clients
                    # the sender_ip is addr_ip so we can avoid echoing back
                    self.broadcast_message(msg, sender_ip=addr_ip)

            except OSError as e:
                if not self.running or getattr(e, "winerror", None) == 10038:
                    break
                print("Host receive error:", e)

            except Exception as e:
                print("Host receive error:", e)

    def client_receive_loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(1024)
                msg = data.decode().strip()
                self.handle_message(msg)
            except OSError as e:
                if not self.running or getattr(e, "winerror", None) == 10038:
                    break
                print("Client receive error:", e)
            except Exception as e:
                print("Client receive error:", e)

    # ---------- BROADCASTS ----------
    def broadcast_message(self, msg, sender_ip=None):
        """
        Host broadcasts a message to all connected clients (excluding sender_ip).
        Uses the client's source port recorded in self.clients.
        """
        with self.lock:
            for ip, (pid, port) in list(self.clients.items()):
                if ip != sender_ip:
                    try:
                        self.sock.sendto(msg.encode(), (ip, port))
                    except Exception as e:
                        print(f"[Host] Broadcast error to {ip}:{port}: {e}")

    def broadcast_player_list(self):
        ips_msg = ",".join(f"{pid}:{ip}" for pid, ip in self.player_ips.items())
        msg = f"PLAYER_LIST|{ips_msg}"
        with self.lock:
            for ip, (pid, port) in list(self.clients.items()):
                try:
                    self.sock.sendto(msg.encode(), (ip, port))
                except Exception as e:
                    print(f"[Host] Player list send error to {ip}:{port}: {e}")

    # ---------- MESSAGE HANDLING ----------
    def handle_message(self, msg):
        msg = msg.strip()

        if msg.startswith("ASSIGN_ID"):
            parts = msg.split('|')
            # Format (host): ASSIGN_ID|<new_id>|<pid1:ip1,pid2:ip2,...>|<host_port>
            try:
                if len(parts) >= 2:
                    self.player_id = int(parts[1])

                if len(parts) >= 3 and parts[2]:
                    ips = parts[2].split(',')
                    self.player_ips = {}
                    for pair in ips:
                        pid_str, ip = pair.split(':')
                        self.player_ips[int(pid_str)] = ip

                # host may tell us which port it's listening on (useful if host incremented start port)
                if len(parts) >= 4 and parts[3]:
                    try:
                        self.host_port = int(parts[3])
                    except ValueError:
                        pass

                print(f"[Client] Received ASSIGN_ID: player_id={self.player_id}, host_port={self.host_port}")

            except Exception as e:
                print("[Client] Error parsing ASSIGN_ID:", e)

        elif msg.startswith("PLAYER_LIST"):
            try:
                ips = msg.split('|', 1)[1].split(',')
                self.player_ips = {}
                for pair in ips:
                    if not pair:
                        continue
                    pid_str, ip = pair.split(':')
                    self.player_ips[int(pid_str)] = ip
            except Exception as e:
                print("[Client] Error parsing PLAYER_LIST:", e)

        elif msg == "HOST_LEFT":
            self.running = False
            for cb in self.receive_callbacks:
                try:
                    cb("HOST_LEFT")
                except Exception:
                    pass
        
        else:
            for cb in self.receive_callbacks:
                try:
                    cb(msg)
                except Exception:
                    pass

    # ---------- ONLINE CLIENT-SERVER ----------
    def start_client_server(self):
        try:
            self.join_server(MATCHMAKING_HOSTNAME)
        except Exception as e:
            print(f"[NetworkManager] Unable to connect to matchmaking server: {e}")
            self.running = False
            return

    def join_server(self, server_ip):
        self.host_ip = server_ip
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('', 0))
        self.running = True
        threading.Thread(target=self.client_receive_loop, daemon=True).start()

        try:
            self.sock.sendto("JOIN_REQUEST".encode(), (server_ip, MATCHMAKING_TCP_PORT))
        except Exception as e:
            self.running = False
            raise Exception(f"Failed to send JOIN_REQUEST: {e}")

        start_time = time.time()
        while self.player_id is None and time.time() - start_time < 5:
            time.sleep(0.1)

        if self.player_id is None:
            self.running = False
            raise Exception("Failed to connect to server: no response")

    # ---------- UTIL ----------
    def get_local_ip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        except Exception:
            ip = "127.0.0.1"
        finally:
            s.close()
        return ip
