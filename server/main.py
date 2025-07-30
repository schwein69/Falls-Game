import socket
import threading
from matchmaking_server import queue_player, matchmaking_loop
from client_handler import try_resume

def handle_client(conn, addr):
    ip = addr[0]
    if not try_resume(conn, ip):
        conn.sendall(b"[QUEUE] Waiting for match...\n")
        queue_player(conn, addr)

def start_server(host='0.0.0.0', port=12345):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((host, port))
    server.listen()
    print(f"[Server] Listening on {host}:{port}")

    threading.Thread(target=matchmaking_loop, daemon=True).start()

    while True:
        conn, addr = server.accept()
        threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()

if __name__ == "__main__":
    start_server()
