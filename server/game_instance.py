import sys
import os
import json
import time
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))

from network_online import start_dedicated_server
from game_authority import GameAuthority
from config import LOG_PREFIX

JOIN_TIMEOUT_SECONDS = 30
IDLE_SHUTDOWN_SECONDS = 3
STATUS_POLL_INTERVAL = 0.1
SNAPSHOT_INTERVAL = 0.05       # 20 Hz: cadenza con cui ridistribuiamo le posizioni ai client
REPLICATION_INTERVAL = 0.2     # ogni quanto scrivo lo stato al supervisor
STATE_PREFIX = "@STATE "       # stesse righe speciali che legge supervisor.py
READY_LINE = "@READY"


# Gestione dei messaggi dei client
def make_message_handler(nm, authority, ready_state, on_change):
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

        changed, events = authority.apply(sender, method, args, internal)  
        for name, event_args in events:
            nm.broadcast_message(name, *event_args)
        if changed:
            on_change()

    return handle


def run_as_active_server(port, players_info, authority, log_tag, version=0):
    nm = start_dedicated_server(port, players_info)
    expected_count = len(players_info)
    state = {"v": version, "last": 0.0}

    def emit_state():
        """Scrive su stdout l'ultimo stato: il supervisor lo tiene per farci ripartire se crolliamo."""
        state["v"] += 1
        state["last"] = time.time()
        print(STATE_PREFIX + json.dumps({"v": state["v"], "t": state["last"], "state": authority.to_dict()}), flush=True)

    print(READY_LINE, flush=True)           # siamo in ascolto: il supervisor puo' avvisare i client
    emit_state()                            # subito, cosi' c'e' gia' il seed della partita

    def on_client_joined(pid, addr):
        nm.send_to(addr, "sync_state", authority.already_destroyed_blocks(), authority.snapshot_payload(),
                   authority.floor_seed)
    nm.room.on_join = on_client_joined

    print(f"{LOG_PREFIX}{log_tag} Avviato. Seed pavimento: {authority.floor_seed}. "
          f"Attendo {expected_count} giocatori: {players_info}")

    ready_state = {"ready_pids": set(), "countdown_started": False, "expected_count": expected_count}
    message_handler = make_message_handler(nm, authority, ready_state, emit_state)

    start_wait = time.time()
    while len(nm.room.clients) < expected_count:
        nm.process_queue(message_handler)
        if time.time() - start_wait > JOIN_TIMEOUT_SECONDS:
            print(f"{LOG_PREFIX}{log_tag} Timeout: solo {len(nm.room.clients)}/{expected_count} "
                  f"giocatori connessi. Avvio comunque con chi c'e'.")
            break
        time.sleep(STATUS_POLL_INTERVAL)

    if len(nm.room.clients) == 0:
        print(f"{LOG_PREFIX}{log_tag} Nessun giocatore si e' connesso. Chiudo.")
        nm.stop()
        return

    ready_state["expected_count"] = len(nm.room.clients)
    print(f"{LOG_PREFIX}{log_tag} {len(nm.room.clients)} giocatori connessi. Via!")
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
    try:
        while True:
            nm.process_queue(message_handler)
            if time.time() - state["last"] >= REPLICATION_INTERVAL:
                emit_state()
            if not ready_state["countdown_started"] and time.time() > countdown_deadline:
                print(f"{LOG_PREFIX}{log_tag} Non tutti pronti dopo {READY_SAFETY_NET_SECONDS}s, "
                      f"avvio comunque (rete di sicurezza).")
                ready_state["countdown_started"] = True
                nm.room.match_started = True
                nm.broadcast_message("start_countdown")
            time.sleep(STATUS_POLL_INTERVAL)
            if len(nm.room.clients) > 0:
                last_seen_player_time = time.time()
            elif time.time() - last_seen_player_time > IDLE_SHUTDOWN_SECONDS:
                print(f"{LOG_PREFIX}{log_tag} Tutti i giocatori se ne sono andati. Chiudo.")
                break
    except KeyboardInterrupt:
        pass
    finally:
        nm.stop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--udp-port", type=int, required=True)
    parser.add_argument("--players", required=True)
    parser.add_argument("--restore", action="store_true", help="riparti dallo stato che arriva su stdin")
    args = parser.parse_args()
    players_info = json.loads(args.players)

    if args.restore:
        saved = json.loads(sys.stdin.readline())
        authority = GameAuthority.from_dict(saved["state"])
        print(f"{LOG_PREFIX} Riparto dallo stato v{saved['v']}.")
        run_as_active_server(args.udp_port, players_info, authority, "[Server ripartito]", saved["v"])
    else:
        run_as_active_server(args.udp_port, players_info, GameAuthority(), "[Server]")


if __name__ == "__main__":
    main()