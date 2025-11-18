import socket
import threading
import time

# --- Constants ---
BROADCAST_PORT = 15000
BROADCAST_INTERVAL = 1.0  # seconds
DISCOVERY_TIMEOUT = 0.5   # socket timeout for listener

# --- Broadcaster Class ---
class DiscoveryBroadcaster:
    def __init__(self, host_player_id, port=BROADCAST_PORT):
        self.host_player_id = host_player_id
        self.port = port
        self.running = False
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.broadcast_ip = '<broadcast>'

    def start(self):
        self.running = True
        threading.Thread(target=self._broadcast_loop, daemon=True).start()

    def _broadcast_loop(self):
        while self.running:
            msg = f"GAME_HOST:{self.host_player_id}"
            try:
                self.sock.sendto(msg.encode(), (self.broadcast_ip, self.port))
            except Exception as e:
                print(f"[DiscoveryBroadcaster] Broadcast failed: {e}")
            time.sleep(BROADCAST_INTERVAL)

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except:
            pass

# --- Listener Class with Timeout Cleanup ---
class DiscoveryListener:
    HOST_TIMEOUT = 2.0  # seconds after which a host is considered gone

    def __init__(self, port=BROADCAST_PORT):
        self.port = port
        self.running = False
        # hosts: ip -> (player_id, last_seen_time)
        self.hosts = {}
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('', self.port))
        self.sock.settimeout(DISCOVERY_TIMEOUT)

    def start(self):
        self.running = True
        threading.Thread(target=self._listen_loop, daemon=True).start()
        threading.Thread(target=self._cleanup_loop, daemon=True).start()

    def _listen_loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(1024)
                msg = data.decode()
                if msg.startswith("GAME_HOST:"):
                    try:
                        player_id = int(msg.split(':')[1])
                        now = time.time()
                        self.hosts[addr[0]] = (player_id, now)
                    except:
                        continue
            except socket.timeout:
                continue
            except OSError as e:
                if not self.running:
                    break
                else:
                    print(f"[DiscoveryListener] Unexpected OSError: {e}")
                    break
            except Exception as e:
                print(f"[DiscoveryListener] General Error: {e}")
                break

    def _cleanup_loop(self):
        while self.running:
            now = time.time()
            to_delete = []
            for ip, (player_id, last_seen) in self.hosts.items():
                if now - last_seen > self.HOST_TIMEOUT:
                    to_delete.append(ip)
            for ip in to_delete:
                print(f"[DiscoveryListener] Removing timed-out host {ip}")
                del self.hosts[ip]
            time.sleep(1.0)

    def get_hosts(self):
        # Return list of (ip, player_id) for hosts still active
        return [(ip, player_id) for ip, (player_id, _) in self.hosts.items()]

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except:
            pass