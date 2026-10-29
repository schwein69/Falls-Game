import socket
import threading
import time

BROADCAST_PORT = 15000
BROADCAST_INTERVAL = 1.0 
DISCOVERY_TIMEOUT = 0.5  

# Docker's virtual networks.
# The listener ignores them: they are not hosts on the real LAN.
def _is_docker_virtual_network(ip):
    parts = ip.split('.')
    if len(parts) != 4:
        return False
    try:
        octets = [int(p) for p in parts]
    except ValueError:
        return False
    return octets[0] == 172 and 16 <= octets[1] <= 31

# HOST once per second it shouts "GAME_HOST:<player id>:<game port>" to the whole LAN,
class DiscoveryBroadcaster:
    def __init__(self, host_player_id, game_port, port=BROADCAST_PORT):
        self.host_player_id = host_player_id
        self.game_port = game_port
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
            msg = f"GAME_HOST:{self.host_player_id}:{self.game_port}".encode()
            try:
                self.sock.sendto(msg, (self.broadcast_ip, self.port))      # to every PC on the LAN
            except Exception as e:
                print(f"[DiscoveryBroadcaster] Broadcast failed: {e}")
            try:
                self.sock.sendto(msg, ('127.0.0.1', self.port))            # test purpose: multiple clients on the same PC
            except Exception:
                pass
            time.sleep(BROADCAST_INTERVAL)

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except:
            pass

# CLIENT side.
class DiscoveryListener:
    HOST_TIMEOUT = 2.0 

    # Open the UDP socket on the discovery port.
    def __init__(self, port=BROADCAST_PORT):
        self.port = port
        self.running = False
        # hosts: (ip, game_port) -> (player_id, game_port, last_seen_time)
        self.hosts = {}
        self.bind_failed = False

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)    # allow other sockets on the same port
        try:
            self.sock.bind(('', self.port))                # '' = receive on every network interface
            self.sock.settimeout(DISCOVERY_TIMEOUT)        
        except OSError as e:
            print(f"[DiscoveryListener] Impossibile fare il bind sulla porta {self.port}: {e}.")
            self.bind_failed = True

    # Start two background threads: one that receives the announcements, one that removes the hosts gone silent.
    def start(self):
        if self.bind_failed:
            return
        self.running = True
        threading.Thread(target=self._listen_loop, daemon=True).start()
        threading.Thread(target=self._cleanup_loop, daemon=True).start()

    # Runs in a thread: wait for a packet, check it is "GAME_HOST:<id>:<port>" and save the host with the
    # current time. Every new announcement from the same host just refreshes that time.
    def _listen_loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(1024)
                msg = data.decode()
                if msg.startswith("GAME_HOST:"):
                    try:
                        parts = msg.split(':')
                        player_id = int(parts[1])
                        game_port = int(parts[2])
                        if _is_docker_virtual_network(addr[0]):
                            continue                       
                        now = time.time()
                        self.hosts[(addr[0], game_port)] = (player_id, game_port, now)   # the key is the sender's IP + game port
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

    # Runs in a thread: once per second, delete the hosts not heard for more than HOST_TIMEOUT.
    def _cleanup_loop(self):
        while self.running:
            now = time.time()
            to_delete = []
            for key, (player_id, game_port, last_seen) in self.hosts.items():
                if now - last_seen > self.HOST_TIMEOUT:
                    to_delete.append(key)
            for key in to_delete:
                print(f"[DiscoveryListener] Removing timed-out host {key}")
                del self.hosts[key]
            time.sleep(1.0)

    # a list of (ip, host player id, game port) of the hosts currently alive.
    def get_hosts(self):
        return [(ip, player_id, game_port) for (ip, _), (player_id, game_port, _) in self.hosts.items()]

    # Stop listening
    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except:
            pass