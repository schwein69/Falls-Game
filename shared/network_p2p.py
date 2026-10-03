from network import NetworkManager
from network_discovery import DiscoveryBroadcaster, DiscoveryListener
from game_authority import GameAuthority
from config import P2P_PORT

MAX_LOCAL_HOSTS = 10


def _bind_to_first_free_port(nm, player_id=0):
    """Prova P2P_PORT, poi le MAX_LOCAL_HOSTS-1 porte successive. Ritorna la porta su cui e'
    riuscito a legarsi, o None se sono tutte occupate."""
    for offset in range(MAX_LOCAL_HOSTS):
        candidate_port = P2P_PORT + offset
        try:
            nm.setup_host(port=candidate_port, player_id=player_id)
            return candidate_port
        except OSError:
            continue
    return None


def start_p2p_host():
    """Diventiamo host di una partita P2P locale. Ritorna (network_manager, game_authority, broadcaster)."""
    nm = NetworkManager()
    nm.allow_host_migration = True
    bound_port = _bind_to_first_free_port(nm)
    if bound_port is None:
        return nm, None, None
    nm.host_is_player = True  

    authority = GameAuthority()
    nm.on_client_joined = lambda pid, addr: nm.send_to(
        addr, "sync_state", authority.already_destroyed_blocks(), authority.snapshot_payload())
    nm.start_broadcast_tick(authority.snapshot_payload)
    nm.start_heartbeat()

    broadcaster = DiscoveryBroadcaster("0", bound_port)
    broadcaster.start()

    return nm, authority, broadcaster


def join_p2p_host(ip, port):
    """Ci uniamo come client a un host P2P."""
    nm = NetworkManager()
    nm.allow_host_migration = True
    nm.join_network(ip, port)
    return nm


# Migrazione dell'host
def promote_to_host(old_network_manager, floor_seed, destroyed_blocks, player_positions, survivor_ids):
    """Un client normale diventa il nuovo host dopo che il vecchio e' sparito.
    Ricostruisce lo stato di gioco (seed del pavimento, blocchi gia' distrutti, posizioni note)
    a partire da quello che QUESTO client aveva gia' visto prima che l'host sparisse.
    Ritorna (network_manager, game_authority, broadcaster), stessa forma di start_p2p_host."""
    my_id = old_network_manager.player_id
    old_network_manager.stop()

    nm = NetworkManager()
    nm.allow_host_migration = True
    nm.pending_migration_ids = set(survivor_ids) - {my_id}
    bound_port = _bind_to_first_free_port(nm, player_id=my_id)
    if bound_port is None:
        return nm, None, None
    nm.host_is_player = True

    authority = GameAuthority()
    if floor_seed is not None:
        authority.floor_seed = floor_seed
    authority.destroyed_blocks = set(destroyed_blocks)
    authority.player_positions = dict(player_positions)

    nm.on_client_joined = lambda pid, addr: nm.send_to(
        addr, "sync_state", authority.already_destroyed_blocks(), authority.snapshot_payload())
    nm.start_broadcast_tick(authority.snapshot_payload)
    nm.start_heartbeat()

    broadcaster = DiscoveryBroadcaster(str(my_id), bound_port)
    broadcaster.start()

    return nm, authority, broadcaster


def reconnect_to_new_host(my_id, new_host_ip, new_host_port):
    """Un sopravvissuto si ricollega al nuovo host trovato sulla LAN, chiedendo di
    riottenere lo stesso player_id di prima (vedi join_network(..., rejoin_id=...)).
    'new_host_port' viene dalla discovery (listener.get_hosts()), 
    il nuovo host potrebbe aver dovuto usare una porta alternativa."""
    nm = NetworkManager()
    nm.allow_host_migration = True
    nm.join_network(new_host_ip, new_host_port, rejoin_id=my_id)
    return nm


def start_host_discovery_scan():
    """Avvia un DiscoveryListener per cercare un host P2P sulla LAN. Usato sia dal normale
    flusso di 'Join Game' (main.py, scan_lan_for_hosts) sia durante la ricerca del nuovo host
    dopo una migrazione. Il chiamante deve interrogare periodicamente listener.get_hosts() e
    chiamare listener.stop() quando ha finito."""
    listener = DiscoveryListener()
    listener.start()
    return listener