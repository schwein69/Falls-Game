from network import NetworkManager
from network_discovery import DiscoveryBroadcaster, DiscoveryListener
from game_authority import GameAuthority
from config import P2P_PORT

MAX_LOCAL_HOSTS = 10

def _bind_to_first_free_port(nm, player_id=0):
    for offset in range(MAX_LOCAL_HOSTS):
        candidate_port = P2P_PORT + offset
        try:
            nm.setup_host(port=candidate_port, player_id=player_id)
            return candidate_port
        except OSError:
            continue
    return None


def start_p2p_host():
    """Diventiamo host di una partita P2P locale: siamo noi il punto di smistamento E un
    giocatore. Ritorna (network_manager, game_authority, broadcaster)."""
    nm = NetworkManager()
    nm.allow_host_migration = True
    bound_port = _bind_to_first_free_port(nm)
    if bound_port is None:
        return nm, None, None
    nm.room.host_plays = True  

    authority = GameAuthority()
    nm.room.on_join = lambda pid, addr: nm.send_to(
        addr, "sync_state", authority.already_destroyed_blocks(), authority.snapshot_payload(),
        authority.floor_seed)
    nm.start_broadcast_tick(authority.snapshot_payload)
    nm.start_heartbeat()

    broadcaster = DiscoveryBroadcaster("0", bound_port)
    broadcaster.start()

    return nm, authority, broadcaster


def join_p2p_host(ip, port, rejoin_id=None):
    nm = NetworkManager()
    nm.allow_host_migration = True
    nm.join_network(ip, port, rejoin_id=rejoin_id)    # rejoin_id: l'id di chi rientra (vedi rejoin_store)
    return nm


# Migrazione dell'host
def promote_to_host(old_network_manager, floor_seed, destroyed_blocks, player_positions, survivor_ids):
    """Un client normale diventa il nuovo host dopo che il vecchio e' sparito.
    Ricostruisce lo stato di gioco (seed del pavimento, blocchi gia' distrutti, posizioni note)"""
    my_id = old_network_manager.player_id
    old_network_manager.stop()

    nm = NetworkManager()
    nm.allow_host_migration = True
    nm.room.pending_ids = set(survivor_ids) - {my_id}
    bound_port = _bind_to_first_free_port(nm, player_id=my_id)
    if bound_port is None:
        return nm, None, None
    nm.room.host_plays = True

    authority = GameAuthority()
    if floor_seed is not None:
        authority.floor_seed = floor_seed
    authority.destroyed_blocks = set(destroyed_blocks)
    authority.player_positions = dict(player_positions)

    nm.room.on_join = lambda pid, addr: nm.send_to(
        addr, "sync_state", authority.already_destroyed_blocks(), authority.snapshot_payload(),
        authority.floor_seed)
    nm.start_broadcast_tick(authority.snapshot_payload)
    nm.start_heartbeat()

    broadcaster = DiscoveryBroadcaster(str(my_id), bound_port)
    broadcaster.start()

    return nm, authority, broadcaster


def reconnect_to_new_host(my_id, new_host_ip, new_host_port):
    nm = NetworkManager()
    nm.allow_host_migration = True
    nm.join_network(new_host_ip, new_host_port, rejoin_id=my_id)
    return nm


def start_host_discovery_scan():
    """Avvia un DiscoveryListener per cercare un host P2P sulla LAN."""
    listener = DiscoveryListener()
    listener.start()
    return listener