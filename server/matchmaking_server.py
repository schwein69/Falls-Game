import threading
import uuid
from logic_server import logic_server

player_queue = []
lock = threading.Lock()
MATCH_SIZE = 2

def queue_player(conn, addr):
    with lock:
        player_queue.append((conn, addr))

def matchmaking_loop():
    while True:
        with lock:
            if len(player_queue) >= MATCH_SIZE:
                match_id = str(uuid.uuid4())
                players = [player_queue.pop(0) for _ in range(MATCH_SIZE)]
                threading.Thread(target=logic_server, args=(match_id, players), daemon=True).start()
        time.sleep(1)
