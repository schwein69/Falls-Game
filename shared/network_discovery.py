import socket
import threading
import time

BROADCAST_PORT = 15000
BROADCAST_INTERVAL = 1.0  # seconds
DISCOVERY_TIMEOUT = 0.5   # socket timeout for listener

def _is_docker_virtual_network(ip):
    parts = ip.split('.')
    if len(parts) != 4:
        return False
    try:
        octets = [int(p) for p in parts]
    except ValueError:
        return False
    return octets[0] == 172 and 16 <= octets[1] <= 31

def _local_ipv4_addresses():
    """IPv4 address of each network card of this PC."""
    found = set()
    try:
        found.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except Exception:                      
        pass
    try:                                   # the address of the card used for the default route
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 9))    
        found.add(probe.getsockname()[0])
        probe.close()
    except Exception:
        pass
    return sorted(ip for ip in found if not ip.startswith(("127.", "169.254.", "0.")))


class DiscoveryBroadcaster:
    def __init__(self, host_player_id, game_port, port=BROADCAST_PORT):
        self.host_player_id = host_player_id
        self.game_port = game_port
        self.port = port
        self.running = False
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.broadcast_ip = '<broadcast>'
        self.card_socks = {}               # local ip -> socket bound to that network card

    def start(self):
        self.running = True
        threading.Thread(target=self._broadcast_loop, daemon=True).start()

    def _broadcast_loop(self):
        while self.running:
            msg = f"GAME_HOST:{self.host_player_id}:{self.game_port}".encode()
            try:
                self.sock.sendto(msg, (self.broadcast_ip, self.port))
            except Exception as e:
                print(f"[DiscoveryBroadcaster] Broadcast failed: {e}")
            try:
                self.sock.sendto(msg, ('127.0.0.1', self.port))
            except Exception:
                pass
            self._announce_on_every_card(msg)
            time.sleep(BROADCAST_INTERVAL)

    def _announce_on_every_card(self, msg):
        ips = _local_ipv4_addresses()
        for ip in list(self.card_socks):                 
            if ip not in ips:
                self.card_socks.pop(ip).close()
        for ip in ips:
            if ip not in self.card_socks:
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    s.bind((ip, 0))
                    self.card_socks[ip] = s
                    print(f"[DiscoveryBroadcaster] Annuncio anche dalla scheda di rete {ip}")
                except OSError:
                    continue
            for target in ("255.255.255.255", ip.rsplit(".", 1)[0] + ".255"): 
                try:
                    self.card_socks[ip].sendto(msg, (target, self.port))
                except OSError:
                    pass

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except:
            pass
        for s in list(self.card_socks.values()):
            try:
                s.close()
            except Exception:
                pass

# Listener Class with Timeout Cleanup 
class DiscoveryListener:
    HOST_TIMEOUT = 2.0  # seconds after which a host is considered gone

    def __init__(self, port=BROADCAST_PORT):
        self.port = port
        self.running = False
        # hosts: ip -> (player_id, game_port, last_seen_time)
        self.hosts = {}
        self.bind_failed = False

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind(('', self.port))
            self.sock.settimeout(DISCOVERY_TIMEOUT)
        except OSError as e:
            print(f"[DiscoveryListener] Impossibile fare il bind sulla porta {self.port}: {e}.")
            self.bind_failed = True

    def start(self):
        if self.bind_failed:
            return
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
                        parts = msg.split(':')
                        player_id = int(parts[1])
                        game_port = int(parts[2])
                        if _is_docker_virtual_network(addr[0]):
                            continue
                        now = time.time()
                        self.hosts[(addr[0], game_port)] = (player_id, game_port, now)
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
            for key, (player_id, game_port, last_seen) in self.hosts.items():
                if now - last_seen > self.HOST_TIMEOUT:
                    to_delete.append(key)
            for key in to_delete:
                print(f"[DiscoveryListener] Removing timed-out host {key}")
                del self.hosts[key]
            time.sleep(1.0)

    def get_hosts(self):
        return [(ip, player_id, game_port) for (ip, _), (player_id, game_port, _) in self.hosts.items()]

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except:
            pass