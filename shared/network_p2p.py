from network import NetworkManager
from network_discovery import DiscoveryBroadcaster, DiscoveryListener
from game_authority import GameAuthority
from config import P2P_PORT


def start_p2p_host():
    """Diventiamo host di una partita P2P locale: siamo noi il punto di smistamento E un
    giocatore. Ritorna (network_manager, game_authority, broadcaster).

    Se la porta e' gia' occupata (probabilmente un'altra istanza del gioco sta gia' facendo da
    host su questa stessa macchina), ci uniamo invece come client: in quel caso game_authority e
    broadcaster tornano None, perche' l'autorita' della partita e' quell'altra istanza."""
    nm = NetworkManager()
    nm.allow_host_migration = True
    try:
        nm.setup_host()
    except OSError:
        nm.join_network("127.0.0.1", P2P_PORT)
        return nm, None, None
    nm.host_is_player = True  # l'host P2P e' anche un giocatore vero, a differenza del server online

    authority = GameAuthority()
    nm.on_client_joined = lambda pid, addr: nm.send_to(
        addr, "sync_state", authority.already_destroyed_blocks(), authority.snapshot_payload())
    nm.start_broadcast_tick(authority.snapshot_payload)
    nm.start_heartbeat()

    broadcaster = DiscoveryBroadcaster("0")
    broadcaster.start()

    return nm, authority, broadcaster


def join_p2p_host(ip):
    nm = NetworkManager()
    nm.allow_host_migration = True
    nm.join_network(ip, P2P_PORT)
    return nm


# ------------------------------------------------------------------
# Migrazione dell'host
# ------------------------------------------------------------------
def promote_to_host(old_network_manager, floor_seed, destroyed_blocks, player_positions, survivor_ids):
    """Un client normale diventa il nuovo host dopo che il vecchio e' sparito.

    Mantiene lo STESSO player_id di prima (fondamentale: gli altri sopravvissuti lo conoscono
    gia' con quell'id) e pre-autorizza il rientro degli altri sopravvissuti tramite il loro id
    precedente — vedi NetworkManager.pending_migration_ids — non tramite il loro indirizzo, che
    cambiera' non appena si riconnetteranno con un nuovo socket.

    Ricostruisce lo stato di gioco (seed del pavimento, blocchi gia' distrutti, posizioni note)
    a partire da quello che QUESTO client aveva gia' visto prima che l'host sparisse: e' un
    "best effort", non un travaso perfetto.

    Ritorna (network_manager, game_authority, broadcaster), stessa forma di start_p2p_host."""
    my_id = old_network_manager.player_id
    old_network_manager.stop()

    nm = NetworkManager()
    nm.allow_host_migration = True
    nm.pending_migration_ids = set(survivor_ids) - {my_id}
    nm.setup_host(port=P2P_PORT, player_id=my_id)
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

    broadcaster = DiscoveryBroadcaster(str(my_id))
    broadcaster.start()

    return nm, authority, broadcaster


def reconnect_to_new_host(my_id, new_host_ip):
    """Un sopravvissuto (non eletto) si ricollega al nuovo host trovato sulla LAN, chiedendo di
    riottenere lo stesso player_id di prima (vedi join_network(..., rejoin_id=...))."""
    nm = NetworkManager()
    nm.allow_host_migration = True
    nm.join_network(new_host_ip, P2P_PORT, rejoin_id=my_id)
    return nm


def start_host_discovery_scan():
    """Avvia un DiscoveryListener per cercare un host P2P sulla LAN. Usato sia dal normale
    flusso di 'Join Game' (main.py, scan_lan_for_hosts) sia durante la ricerca del nuovo host
    dopo una migrazione. Il chiamante deve interrogare periodicamente listener.get_hosts() e
    chiamare listener.stop() quando ha finito."""
    listener = DiscoveryListener()
    listener.start()
    return listener