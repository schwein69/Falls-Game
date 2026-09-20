"""
Migrazione dell'host (solo P2P): se l'host sparisce, i client sopravvissuti eleggono in
autonomia un nuovo host (l'id piu' basso tra chi e' rimasto — criterio deterministico che tutti
calcolano allo stesso modo, senza bisogno di negoziare) e la partita continua.
"""

from ursina import *
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
import network_p2p
import game_state
from usefulFunctions import display_error_message_screen


def check_host_migration():
    nm = game_state.network_manager
    if game_state.migrating or not nm:
        return
    if not nm.allow_host_migration or nm.is_host:
        return  # non siamo un client P2P (o siamo gia' l'host): nulla da controllare
    if nm.host_alive():
        return

    game_state.migrating = True
    print("[Game] Host non risponde. Avvio la migrazione...")
    handle_host_migration()


def handle_host_migration():
    old_nm = game_state.network_manager
    my_id = old_nm.player_id
    survivor_ids = set(game_state.other_players.keys()) | {my_id}
    new_host_id = min(survivor_ids)

    status = Text("Host perso: elezione in corso...", scale=1.2, origin=(0, 0), y=0.15,
                  parent=camera.ui, tag='migration_text')

    if new_host_id == my_id:
        floors = game_state.floors
        destroyed_ids = [b.block_id for b in (floors.floor_cubes if floors is not None else []) if b.has_activated]
        positions = {pid: [rp.x, rp.y, rp.z] for pid, rp in game_state.other_players.items()}
        if game_state.player is not None:
            positions[my_id] = [game_state.player.x, game_state.player.y, game_state.player.z]

        game_state.network_manager, game_state.game_authority, game_state.broadcaster = (
            network_p2p.promote_to_host(old_nm, game_state.current_floor_seed, destroyed_ids,
                                         positions, survivor_ids))
        print(f"[Game] Siamo il nuovo host (player {my_id}).")
        finish_migration(status)
    else:
        listener = network_p2p.start_host_discovery_scan()
        invoke(try_find_new_host, old_nm, my_id, listener, status, 10, delay=1.0)


def try_find_new_host(old_nm, my_id, listener, status, attempts_left):
    hosts = listener.get_hosts()
    if hosts:
        new_ip, _ = hosts[0]
        listener.stop()
        game_state.network_manager = network_p2p.reconnect_to_new_host(my_id, new_ip)
        print(f"[Game] Nuovo host trovato: {new_ip}. Riconnesso.")
        finish_migration(status)
    elif attempts_left > 0:
        invoke(try_find_new_host, old_nm, my_id, listener, status, attempts_left - 1, delay=1.0)
    else:
        listener.stop()
        destroy(status)
        from game_lifecycle import resetFloor
        display_error_message_screen(
            "Impossibile trovare il nuovo host. La connessione con la partita e' andata persa.",
            lambda: resetFloor())


def finish_migration(status):
    destroy(status)
    game_state.migrating = False