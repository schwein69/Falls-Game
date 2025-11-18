#!/usr/bin/env python3
"""
Matchmaking server with embedded plain-text HTTP dashboard on port 8080.

- TCP matchmaking server accepts clients, groups players into games.
- Starts game instances on new ports.
- Dashboard available at http://<host>:8080/ showing queue and active games.
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
from config import *

status_file = os.path.join(os.path.dirname(__file__), "status.json")

pending_clients = []  # list of (player_id, client_ip, udp_port, tcp_conn)
active_games = {}     # port -> {players: [...], start_ts: ...}
next_game_port = GAME_INSTANCE_BASE_PORT
port_lock = threading.Lock()


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

                game_port = next_game_port
                next_game_port += 1
                players_info = [(pid, ip, udp) for (pid, ip, udp, _) in group]
                players_json = json.dumps(players_info)

                print(f"{LOG_PREFIX} Spawning game instance on port {game_port} for players: {[p[0] for p in players_info]}")
                subprocess.Popen([sys.executable, "server/game_instance.py", str(game_port), players_json])

                active_games[game_port] = {
                    "players": [p[0] for p in players_info],
                    "start_ts": time.time(),
                }
                write_status()

                for (pid, ip, udp, c) in group:
                    try:
                        msg = f"GAME:{MATCHMAKING_HOSTNAME}:{game_port}"
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
            self.send_header("Content-type", "text/plain; charset=utf-8")
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
                    player_list = ", ".join(info.get("players", []))
                    lines.append(f" - Port {port}: {player_list}")
            lines.append("")
            lines.append(f"Last update: {timestr}")
            lines.append("Page refreshes every 5 seconds.")

            self.wfile.write("\n".join(lines).encode())

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
    server_address = ("", 8080)  # Now runs on 8080
    httpd = ThreadedHTTPServer(server_address, DashboardHandler)
    print(f"{LOG_PREFIX} Dashboard HTTP server running on port 8080")
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
