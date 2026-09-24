"""
Matchmaking server with embedded plain-text HTTP dashboard on port 8081.

- TCP matchmaking server accepts clients, groups players into games.
- Starts game instances on new ports.
- Dashboard available at http://<host>:8081/ showing queue and active games.
"""

import socket
import threading
import subprocess
import json
import time
import sys
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
from config import *

status_file = os.path.join(os.path.dirname(__file__), "status.json")

pending_clients = []  # list of (player_id, client_ip, udp_port, tcp_conn)
active_games = {}     # port -> {players: [...], start_ts: ...}
next_game_port = GAME_INSTANCE_BASE_PORT
port_lock = threading.Lock()  # FIX: c'era una "o" di troppo alla fine di questa riga


def write_status():
    """Write matchmaking status to JSON file for dashboard."""
    try:
        data = {
            "pending": [{"id": p[0], "ip": p[1], "udp_port": p[2]} for p in pending_clients],
            "active_games": {str(port): info for port, info in active_games.items()},
            "timestamp": time.time(),
        }
        with open(status_file, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"{LOG_PREFIX} Failed writing status: {e}")


def _wait_for_proxy_ready(tcp_port, timeout=5.0):
    """Prova a connettersi brevemente alla porta TCP del proxy appena lanciato, per verificare
    che sia davvero pronto ad accettare client prima di notificarli. Se non ce la fa entro
    'timeout' secondi, va avanti comunque (i client hanno il loro ritentativo come rete di
    sicurezza, vedi network_online._connect_via_proxy)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.connect(("127.0.0.1", tcp_port))
            return True
        except Exception:
            time.sleep(0.1)
    print(f"{LOG_PREFIX} Attenzione: il proxy sulla porta {tcp_port} non risulta pronto dopo "
          f"{timeout}s, procedo comunque.")
    return False


def handle_client(conn, addr):
    """Handle a single matchmaking client connection."""
    global next_game_port
    try:
        data = conn.recv(2048).decode()
        info = json.loads(data)
        player_id = info.get("id")
        udp_port = info.get("udp_port")
        client_ip = addr[0]
        print(f"{LOG_PREFIX} New matchmaking connection: {player_id}@{client_ip}:{udp_port}")

        with port_lock:
            pending_clients.append((player_id, client_ip, udp_port, conn))
            write_status()
            if len(pending_clients) >= MAX_PLAYERS:
                group = pending_clients[:MAX_PLAYERS]
                del pending_clients[:MAX_PLAYERS]

                # Ogni partita ora si prende un BLOCCO di 10 porte, non piu' una sola: il proxy
                # ha bisogno di una porta TCP per i client, PIU' quattro porte UDP/TCP interne
                # per la coppia primary/backup (vedi server/proxy.py). Il margine (10 invece di
                # 4) lascia spazio a future estensioni senza dover ritoccare questa logica.
                proxy_tcp_port = next_game_port
                udp_base_port = next_game_port + 1
                next_game_port += 10
                players_info = [(pid, ip, udp) for (pid, ip, udp, _) in group]
                players_json = json.dumps(players_info)

                print(f"{LOG_PREFIX} Avvio il proxy (porta TCP {proxy_tcp_port}, blocco UDP da "
                      f"{udp_base_port}) per i giocatori: {[p[0] for p in players_info]}")
                proxy_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxy.py")
                subprocess.Popen([sys.executable, proxy_path,
                                   str(proxy_tcp_port), players_json, str(udp_base_port)])

                # Il proxy viene avviato in modo asincrono (subprocess.Popen ritorna subito, ben
                # prima che il proxy abbia finito di avviarsi e messo in ascolto la sua porta
                # TCP). Aspettiamo brevemente che sia davvero pronto prima di notificare i
                # client — riduce il rischio che qualcuno ci provi nella finestra sbagliata (i
                # client hanno comunque un loro ritentativo lato client, questa e' solo una
                # difesa in piu', non l'unica).
                _wait_for_proxy_ready(proxy_tcp_port)

                active_games[proxy_tcp_port] = {
                    "players": [p[0] for p in players_info],
                    "start_ts": time.time(),
                }
                write_status()

                for (pid, ip, udp, c) in group:
                    try:
                        # Il formato del messaggio non cambia (host:porta) — cambia solo cosa il
                        # client ci fa: ora e' la porta TCP del PROXY, non piu' la porta UDP del
                        # server di gioco. Vedi network_online.py per l'handshake col proxy.
                        msg = f"GAME:{MATCHMAKING_HOSTNAME}:{proxy_tcp_port}"
                        c.sendall(msg.encode())
                        c.close()
                    except Exception as e:
                        print(f"{LOG_PREFIX} Failed to notify client {pid}: {e}")
            else:
                try:
                    conn.sendall(b"WAIT")
                except:
                    pass
    except Exception as e:
        print(f"{LOG_PREFIX} Error handling matchmaking client: {e}")
        try:
            conn.close()
        except:
            pass
    finally:
        write_status()


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send_response(200)
            # FIX: text/html invece di text/plain, altrimenti il browser non interpreta il tag
            # <meta http-equiv="refresh"> qui sotto e lo mostra come testo invece di eseguirlo.
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()

            try:
                with open(status_file, "r") as f:
                    status = json.load(f)
            except Exception as e:
                self.wfile.write(f"Failed to load status.json: {e}\n".encode())
                return

            pending = status.get("pending", [])
            active_games_data = status.get("active_games", {})
            timestamp = status.get("timestamp", 0)
            timestr = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))

            lines = []
            lines.append(f"Matchmaking Queue: {len(pending)} player(s)")
            if pending:
                lines.append("Pending Players:")
                for p in pending:
                    lines.append(f" - {p['id']} @ {p['ip']}:{p['udp_port']}")
            lines.append("")
            lines.append(f"Active Games: {len(active_games_data)}")
            if active_games_data:
                for port, info in active_games_data.items():
                    player_list = ", ".join(str(p) for p in info.get("players", []))
                    lines.append(f" - Port {port}: {player_list}")
            lines.append("")
            lines.append(f"Last update: {timestr}")
            lines.append("Page refreshes every 5 seconds.")

            # FIX: prima veniva scritto solo il testo grezzo (nessun refresh vero, solo la
            # scritta che lo diceva). Ora avvolgiamo lo stesso testo in una paginetta HTML con
            # <meta http-equiv="refresh" content="5">, che fa ricaricare la pagina da sola.
            html_page = (
                "<html><head><meta http-equiv=\"refresh\" content=\"5\"></head>"
                "<body><pre>" + "\n".join(lines) + "</pre></body></html>"
            )
            self.wfile.write(html_page.encode())

        elif self.path == "/status.json":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            try:
                with open(status_file, "r") as f:
                    self.wfile.write(f.read().encode())
            except Exception as e:
                self.wfile.write(json.dumps({"error": str(e)}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        return


def run_dashboard_server():
    server_address = ("", 8081) 
    httpd = ThreadedHTTPServer(server_address, DashboardHandler)
    print(f"{LOG_PREFIX} Dashboard HTTP server running on port 8081")
    httpd.serve_forever()


def start_matchmaking():
    write_status()
    threading.Thread(target=run_dashboard_server, daemon=True).start()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((SERVER_BIND, MATCHMAKING_TCP_PORT))
        s.listen()
        print(f"{LOG_PREFIX} Matchmaking server listening on {SERVER_BIND}:{MATCHMAKING_TCP_PORT}")
        while True:
            conn, addr = s.accept()
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()


if __name__ == "__main__":
    start_matchmaking()