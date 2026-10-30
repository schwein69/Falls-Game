import sys
import os
import json
import time
import argparse
import threading
import redis

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))

from network_online import start_dedicated_server
from game_authority import GameAuthority
from config import LOG_PREFIX, REDIS_HOST, REDIS_PORT, GAME_KEY_PREFIX

# One game_instance = one process = one match. 

JOIN_TIMEOUT_SECONDS = 20       # max wait for the players to connect; after that we start with who is here
IDLE_SHUTDOWN_SECONDS = 3       # nobody connected for this long -> the match is over, close the process
STATUS_POLL_INTERVAL = 0.1      
SNAPSHOT_INTERVAL = 0.025       
STATUS_TTL = 3                  # the Redis key of the game expires after 3 s unless it is renewed


def make_message_handler(nm, authority, ready_state):
    """Build the function that handles every message the clients send to the server."""
    def handle(payload):
        method = payload.get("method")
        args = payload.get("args", [])
        sender = payload.get("sender_id")
        internal = sender is not None and sender in (0, nm.player_id)

        if method == "player_ready":                                    
            if ready_state["countdown_started"]:
                return                                                 
            ready_state["ready_pids"].add(sender)
            print(f"{LOG_PREFIX} Player {sender} pronto "
                  f"({len(ready_state['ready_pids'])}/{ready_state['expected_count']})")
            if len(ready_state["ready_pids"]) >= ready_state["expected_count"]:   
                ready_state["countdown_started"] = True
                nm.room.match_started = True                            
                nm.broadcast_message("start_countdown")
            return

        _, events = authority.apply(sender, method, args, internal)
        for name, event_args in events:
            nm.broadcast_message(name, *event_args)

    return handle


def keep_game_key(r, key, info, stop):
    """Thread: every second rewrite the Redis key of this game, so the matchmaking knows it is alive.
    If the process dies, nobody renews the key and it disappears after STATUS_TTL seconds."""
    while not stop.is_set():
        try:
            r.set(key, json.dumps(info), ex=STATUS_TTL)
        except redis.RedisError:
            pass                                  
        stop.wait(1.0)                             


def run_game(port, players_info):
    """Start the server on `port`, publish the game on Redis, play, then clean everything up."""
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    r.ping()                                     
    nm = start_dedicated_server(port, players_info)
    key = GAME_KEY_PREFIX + str(port)             
    info = {"port": port, "players": [p[0] for p in players_info], "pid": os.getpid(), "start_ts": time.time()}
    stop = threading.Event()                     
    key_thread = threading.Thread(target=keep_game_key, args=(r, key, info, stop), daemon=True)
    key_thread.start()
    try:
        play(nm, GameAuthority(), players_info)
    except KeyboardInterrupt:
        pass                                       
    finally:
        stop.set()
        key_thread.join()                          
        try:
            r.delete(key)                          
        except redis.RedisError:
            pass                                   
        nm.stop()


def play(nm, authority, players_info):
    """Whole life of a match: wait for the players, start, run until everybody has left."""
    expected_count = len(players_info)

    def on_client_joined(pid, addr):             
        nm.send_to(addr, "sync_state", authority.already_destroyed_blocks(), authority.snapshot_payload(),
                   authority.floor_seed)           
    nm.room.on_join = on_client_joined

    print(f"{LOG_PREFIX}[Server] Avviato. Seed pavimento: {authority.floor_seed}. "
          f"Attendo {expected_count} giocatori: {players_info}")

    ready_state = {"ready_pids": set(), "countdown_started": False, "expected_count": expected_count}
    message_handler = make_message_handler(nm, authority, ready_state)

    start_wait = time.time()
    seen_any, empty_since = False, None
    while len(nm.room.clients) < expected_count:
        nm.process_queue(message_handler)
        if nm.room.clients:                                         
            seen_any, empty_since = True, None
        elif seen_any:                                             
            empty_since = empty_since or time.time()
            if time.time() - empty_since > IDLE_SHUTDOWN_SECONDS:
                print(f"{LOG_PREFIX}[Server] Tutti usciti prima dell'inizio. Chiudo.")
                return
        if time.time() - start_wait > JOIN_TIMEOUT_SECONDS:
            print(f"{LOG_PREFIX}[Server] Timeout: solo {len(nm.room.clients)}/{expected_count} "
                  f"giocatori connessi. Avvio comunque con chi c'e'.")
            break
        time.sleep(STATUS_POLL_INTERVAL)

    if len(nm.room.clients) == 0:
        print(f"{LOG_PREFIX}[Server] Nessun giocatore si e' connesso. Chiudo.")
        return

    ready_state["expected_count"] = len(nm.room.clients)
    print(f"{LOG_PREFIX}[Server] {len(nm.room.clients)} giocatori connessi. Via!")
    nm.broadcast_message("start_game", authority.floor_seed)
    nm.start_broadcast_tick(authority.snapshot_payload, interval=SNAPSHOT_INTERVAL)  

    if (not ready_state["countdown_started"]
            and len(ready_state["ready_pids"]) >= ready_state["expected_count"]):
        ready_state["countdown_started"] = True
        nm.room.match_started = True
        nm.broadcast_message("start_countdown")

    READY_SAFETY_NET_SECONDS = 10                  
    countdown_deadline = time.time() + READY_SAFETY_NET_SECONDS

    last_seen_player_time = time.time()
    while True:
        nm.process_queue(message_handler)
        if not ready_state["countdown_started"] and time.time() > countdown_deadline:
            print(f"{LOG_PREFIX}[Server] Non tutti pronti dopo {READY_SAFETY_NET_SECONDS}s, "
                  f"avvio comunque (rete di sicurezza).")
            ready_state["countdown_started"] = True
            nm.room.match_started = True
            nm.broadcast_message("start_countdown")
        time.sleep(STATUS_POLL_INTERVAL)
        if len(nm.room.clients) > 0:
            last_seen_player_time = time.time()
        elif time.time() - last_seen_player_time > IDLE_SHUTDOWN_SECONDS:    
            print(f"{LOG_PREFIX}[Server] Tutti i giocatori se ne sono andati. Chiudo.")
            return


def main():
    """Entry point: the matchmaking runs `game_instance.py --udp-port N --players '<json list>'`."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--udp-port", type=int, required=True)
    parser.add_argument("--players", required=True)          
    args = parser.parse_args()
    run_game(args.udp_port, json.loads(args.players))


if __name__ == "__main__":
    main()