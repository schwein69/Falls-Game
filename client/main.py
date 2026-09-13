import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))

from ursina import *
from config import *
from player import Player, RemotePlayer
from floor import *
from splashScreen import SplashScreen
from usefulFunctions import *
from network import NetworkManager
from network_discovery import DiscoveryBroadcaster, DiscoveryListener
import network_p2p
import network_online
from game_authority import GameAuthority

# --------------------------
# GLOBAL GAME STATE
# --------------------------
app = Ursina()
loading_screen = None
player = None
floors = None
sky = None
connected_ids = set()
other_players = {}  # player_id -> RemotePlayer instance
guiElements = []
player_labels = []
lobby_buttons = []


game_over = False
game_paused = False
level_loaded = False
lobby_open = False

network_manager: NetworkManager = None
broadcaster: DiscoveryBroadcaster = None
current_listener: DiscoveryListener = None

game_authority: GameAuthority = None

current_floor_seed = None
migrating = False

player_died_reported = False

# --------------------------
# UI elements
# --------------------------
def clear_all_ui_elements():
    for e in camera.ui.children:
        destroy(e)
    guiElements.clear()
    player_labels.clear()
    lobby_buttons.clear()

def display_error_message_screen(message, on_close_callback):
    clear_all_ui_elements()
    msg = Text(message, scale=1.2, origin=(0, 0), position=(0, 0.1),
               parent=camera.ui, color=color.red)
    btn = Button('Back', scale=(0.3, 0.1), position=(0, -0.1),
                 parent=camera.ui, on_click=on_close_callback)
    guiElements.extend([msg, btn])

# --------------------------
# RPC HANDLERS
# --------------------------
# This is called when we receive a spawn RPC for another player. We create a RemotePlayer instance for them and store it in other_players dict.
def rpc_spawn_player(pid, x, y, z, status="alive", matchId=None):
    global other_players, connected_ids
    if network_manager and pid != network_manager.player_id:
       connected_ids.add(pid)

    if level_loaded:
        if pid not in other_players and pid != network_manager.player_id:
            print(f"[Game] Spawning Remote Player {pid}")
            new_p = RemotePlayer(position=Vec3(x, y, z), username=f"Player {pid}")
            other_players[pid] = new_p

def rpc_update_pos(pid, x, y, z):
    """Segnalazione della NOSTRA posizione all'autorita' di gioco (host P2P o game instance
    online). Non sposta piu' direttamente i giocatori remoti sullo schermo: quello lo fa
    rpc_state_snapshot, ricevuto a intervalli fissi dall'autorita'. Qui, se siamo noi l'host
    P2P, ci limitiamo a registrare il dato nell'autorita'; se non lo siamo, questo pacchetto
    non dovrebbe arrivarci comunque (i client mandano update_pos solo all'host/server)."""
    if game_authority is not None and network_manager and network_manager.is_host:
        game_authority.handle_update_pos(pid, x, y, z)

def rpc_state_snapshot(positions):
    """Stato consolidato di TUTTI i giocatori, ricevuto a intervalli fissi dall'autorita' di
    gioco (host P2P o game instance online). positions e' un dict {pid: [x,y,z]} — le chiavi
    arrivano come stringhe perche' JSON serializza sempre le chiavi dict come testo."""
    global other_players
    for pid_str, pos in positions.items():
        pid = int(pid_str)
        if network_manager and pid == network_manager.player_id:
            continue  # non spostiamo noi stessi in base al nostro stesso pacchetto
        if pid in other_players:
            other_players[pid].position = Vec3(*pos)
        elif level_loaded:
            rpc_spawn_player(pid, *pos)

def rpc_block_step(pid, block_id):
    """SOLO l'host P2P valida questa richiesta (e' lui l'autorita' quando giochiamo in locale;
    in modalita' online arriva direttamente al processo server/game_instance.py, non a noi).
    Un client normale non dovrebbe mai ricevere questo RPC (i client lo mandano solo all'host),
    ma per sicurezza controlliamo comunque prima di agire."""
    if not (network_manager and network_manager.is_host and game_authority is not None):
        return
    result = game_authority.handle_block_step(pid, block_id)
    if result is None:
        return  # gia' distrutto da qualcun altro: richiesta ignorata (idempotenza)
    # L'host e' anche un giocatore: applichiamo l'evento localmente (l'host non riceve mai i
    # propri broadcast via rete) E lo trasmettiamo a tutti gli altri.
    apply_block_destroyed(result["block_id"], result["pid"])
    network_manager.broadcast_rpc("block_destroyed", result["block_id"], result["pid"])

def rpc_block_destroyed(block_id, pid):
    """Evento CANONICO: l'autorita' ha confermato che questo blocco e' stato distrutto.
    Applicato da tutti i client (per l'host P2P, questo handler gestisce solo la ricezione
    dal network: l'host applica l'evento direttamente in rpc_block_step, vedi sopra)."""
    apply_block_destroyed(block_id, pid)

def apply_block_destroyed(block_id, pid):
    """Distrugge visivamente il blocco corrispondente e applica l'eventuale effetto
    (velocita'/super salto) SOLO se il giocatore che lo ha attivato siamo noi."""
    # NOTA: "is None" e non "not floors"/"not cube" — con gli Entity di Ursina, il controllo di
    # verita' implicito (bool(entity)) delega a Panda3D e solleva TypeError se l'oggetto C++
    # sottostante non e' ancora costruito o e' gia' stato distrutto, invece di dare semplicemente
    # False. Va sempre confrontato esplicitamente con None.
    if floors is None:
        return
    cube = floors.blocks_by_id.get(block_id)
    if cube is None:
        return
    is_me = network_manager and pid == network_manager.player_id
    cube.on_authoritative_step(player if is_me else None)

def request_player_died():
    """Il giocatore locale e' caduto/eliminato: lo segnaliamo all'autorita' di gioco (host P2P
    o game instance online), che tiene il conto di chi e' ancora in gara e decide quando la
    partita e' finita (ultimo rimasto vivo)."""
    if not network_manager or network_manager.player_id is None:
        return
    if network_manager.is_host:
        rpc_player_died(network_manager.player_id)
    else:
        network_manager.send_rpc("player_died", network_manager.player_id)

def rpc_player_died(pid):
    """SOLO l'host/server valida chi e' ancora vivo (e' lui l'autorita'). Un client normale non
    dovrebbe mai ricevere questo RPC (i client lo mandano solo all'host), ma per sicurezza
    controlliamo comunque prima di agire."""
    if not (network_manager and network_manager.is_host and game_authority is not None):
        return
    winner_pid = game_authority.handle_player_died(pid)
    if winner_pid is not None:
        # L'host e' anche un giocatore: applichiamo l'evento localmente (l'host non riceve mai
        # i propri broadcast via rete) E lo trasmettiamo a tutti gli altri.
        apply_game_won(winner_pid)
        network_manager.broadcast_rpc("game_won", winner_pid)

def rpc_game_won(winner_pid):
    """Evento CANONICO: l'autorita' ha decretato un vincitore. Applicato da tutti (per l'host
    P2P questo handler gestisce solo la ricezione dal network: l'host applica l'evento
    direttamente in rpc_player_died, vedi sopra)."""
    apply_game_won(winner_pid)

def apply_game_won(winner_pid):
    """Mostra la schermata di fine partita a tutti — sia a chi ha vinto sia a chi e' gia' caduto
    in attesa. Riusa il flag game_over gia' esistente per congelare il resto della logica di
    gioco (vedi update()), cosi' non serve un flag separato."""
    global game_over, player
    if game_over:
        return  # vittoria gia' mostrata, non farlo due volte
    game_over = True
    # BUG FIX: durante il gioco il FirstPersonController blocca il cursore del mouse (per
    # guardarsi intorno) — senza sbloccarlo qui, il cursore resta invisibile/vincolato al centro
    # dello schermo e non si riesce a cliccare il bottone "Torna al menu" qui sotto.
    mouse.locked = False
    mouse.visible = True

    # BUG FIX: prima clear_all_ui_elements() veniva chiamata QUI, prima di distruggere il
    # player. Il cursore del FirstPersonController pero' e' figlio di camera.ui: quella
    # chiamata lo distruggeva "di nascosto" senza che il player (che pensa di possederlo ancora)
    # ne sapesse nulla. Al click su "Torna al menu", resetFloor() prova a distruggere il player,
    # il cui on_destroy() tenta di disabilitare un cursore GIA' distrutto -> crash Panda3D
    # ("NodePath gia' singleton/vuoto"). Distruggiamo il player PRIMA (che ripulisce anche il
    # proprio cursore/HUD nel modo corretto), poi puliamo il resto della UI.
    if player is not None:
        destroy(player)
        player = None

    clear_all_ui_elements()
    is_me = network_manager and winner_pid == network_manager.player_id
    message = "Hai vinto!" if is_me else f"Ha vinto il giocatore {winner_pid}!"
    Text(message, scale=2.5, origin=(0, 0), position=(0, 0.1), parent=camera.ui, color=color.gold)
    Button('Torna al menu', y=-0.15, scale=(0.3, 0.08), parent=camera.ui, on_click=resetFloor)

def rpc_sync_state(destroyed_ids, positions):
    """Ricevuto solo da chi si e' appena unito o riconnesso a una partita gia' in corso: ci
    mette in pari con lo stato che l'autorita' ha gia' accumulato (blocchi gia' consumati dagli
    altri, posizioni correnti)."""
    if floors is not None:
        for block_id in destroyed_ids:
            cube = floors.blocks_by_id.get(block_id)
            if cube is not None and not cube.is_disappearing and not cube.has_activated:
                cube.force_destroy()
    rpc_state_snapshot(positions)

# This is called when we receive a player left message. We destroy their RemotePlayer instance and remove them from other_players dict.
def rpc_player_left(pid):
    global other_players
    if pid in other_players:
        print(f"[Game] Player {pid} Left")
        destroy(other_players[pid])
        del other_players[pid]
    if game_authority is not None:
        # Chi si disconnette non "muore": esce e basta dal conteggio. Se questo lascia un solo
        # giocatore in gara, vince comunque lui (la partita non avrebbe piu' senso altrimenti).
        winner_pid = game_authority.remove_player(pid)
        if winner_pid is not None and network_manager:
            apply_game_won(winner_pid)
            network_manager.broadcast_rpc("game_won", winner_pid)

# This is called when the host starts the game. We set lobby_open to False and load the level for everyone. The host also calls this locally when they click "Start Game" in the lobby.
def rpc_start_game(seed=None):
    global lobby_open, current_floor_seed
    print("[Game] Host started the game")
    lobby_open = False
    current_floor_seed = seed
    loadLevel(seed)
    # After level loads, spawn everyone who was waiting in the lobby
    for pid in connected_ids:
        rpc_spawn_player(pid, 0, 5, 0)


# This is called to pause the game when a player disconnects and resume when they rejoin. The host can also call this to force pause/resume for testing.
def rpc_game_pause(is_paused, pid=None):
    global game_paused
    game_paused = is_paused
    if is_paused:
        Text(f"Waiting for Player {pid} to reconnect...", scale=1.5, origin=(0,0), y=0.1, tag='pause_text')
    else:
        print("[Game] RESUMED!")
        for t in scene.entities:
            if hasattr(t, 'tag') and t.tag == 'pause_text':
                destroy(t)

# Map string names to functions
RPC_REGISTRY = {
    "spawn_player": rpc_spawn_player,
    "update_pos": rpc_update_pos,
    "state_snapshot": rpc_state_snapshot,
    "block_step": rpc_block_step,
    "block_destroyed": rpc_block_destroyed,
    "sync_state": rpc_sync_state,
    "player_left": rpc_player_left,
    "player_died": rpc_player_died,
    "game_won": rpc_game_won,
    "start_game": rpc_start_game,
    "game_pause": rpc_game_pause,
}

def handle_rpc(data):
    """Dispatches JSON data to functions"""
    method = data.get("method")
    args = data.get("args", [])
    if method in RPC_REGISTRY:
        RPC_REGISTRY[method](*args)

def request_block_step(block_id):
    """Il giocatore locale segnala di essere passato su un blocco. NON lo distruggiamo subito:
    e' solo una RICHIESTA all'autorita' di gioco (host P2P o game instance online), che decide
    se accettarla (idempotente: il primo a segnalarlo vince) e lo comunica a tutti con
    block_destroyed. Cosi' due giocatori che calpestano lo stesso blocco quasi insieme vedono
    lo stesso esito, invece che ognuno decidere per conto proprio."""
    if not network_manager or network_manager.player_id is None:
        return
    if network_manager.is_host:
        # Siamo noi l'autorita' (host P2P): processiamo subito la nostra stessa richiesta,
        # esattamente come farebbe se ci arrivasse da un client via rete.
        rpc_block_step(network_manager.player_id, block_id)
    else:
        network_manager.send_rpc("block_step", network_manager.player_id, block_id)

# --------------------------
# GAME LOADING
# --------------------------
def loadLevel(floor_seed=None):
    global player, floors, sky, level_loaded, game_over, player_died_reported
    clear_all_ui_elements()
    player_died_reported = False

    # Seed condiviso: garantisce che TUTTI i client generino esattamente lo stesso layout di
    # blocchi (tipo e posizione), invece di tirare a caso ciascuno per conto proprio.
    apply_floor_seed(floor_seed)
    floors = Floor()
    sky = Entity(
        model="sphere",
        texture=os.path.join("assets", "sky.png"),
        scale=9999,
        double_sided=True
    )

    start_pos = get_random_position()
    player = Player(start_pos, f"Player {network_manager.player_id}")
    camera.z = -5
    game_over = False

    invoke(gameStart, delay=0.1)

def gameStart():
    global level_loaded
    level_loaded = True
    player.gravity = 1

    # Notify others of our spawn
    if network_manager:
        network_manager.send_rpc("spawn_player", network_manager.player_id,
                                 player.position.x, player.position.y, player.position.z)

def resetFloor():
    global player, floors, sky, game_over, level_loaded, other_players
    global network_manager, game_authority, broadcaster, current_listener

    if player is not None: destroy(player)
    if floors is not None: destroy(floors)
    if sky is not None: destroy(sky)
    for p in other_players.values(): destroy(p)

    other_players.clear()
    connected_ids.clear()
    player = None
    floors = None
    sky = None
    level_loaded = False
    game_over = False

    # Chiudiamo del tutto la sessione di rete. PRIMA questa funzione lasciava network_manager,
    # game_authority e broadcaster vivi in background anche tornando al menu: un "Host Game"
    # successivo creava una SECONDA sessione mentre quella vecchia (thread di ricezione UDP,
    # tick delle posizioni, heartbeat) restava attiva inutilmente. Stessa pulizia gia' fatta da
    # leave_lobby() quando si esce dalla lobby prima ancora che la partita inizi.
    if network_manager is not None:
        network_manager.stop()
        network_manager = None
    game_authority = None
    if broadcaster is not None:
        broadcaster.stop()
        broadcaster = None
    if current_listener is not None:
        current_listener.stop()
        current_listener = None

    invoke(show_main_menu_screen, delay=1)

# --------------------------
# NETWORK SESSION CONTROLS
# --------------------------
# ==========================================================
# SESSIONE P2P (rete locale, nessun server dedicato)
# ==========================================================
def start_local_host_session():
    global network_manager, broadcaster, game_authority
    clear_all_ui_elements()

    # Tutta l'orchestrazione specifica del P2P (setup_host, creazione della GameAuthority
    # perche' l'host e' anche un giocatore, discovery broadcaster) vive in network_p2p.py.
    network_manager, game_authority, broadcaster = network_p2p.start_p2p_host()

    invoke(show_lobby_screen, delay=0.5)

# ==========================================================
# SESSIONE ONLINE (matchmaking + server dedicato)
# ==========================================================
def start_online_client_session():
    """Avvia la modalita' Online: ci connettiamo al matchmaking server (config.MATCHMAKING_HOSTNAME)
    e attendiamo di essere abbinati. Il matchmaking spawna una game instance dedicata
    (server/game_instance.py) quando si raggiungono MAX_PLAYERS in coda, e ci comunica dove
    connetterci; a quel punto entriamo in lobby come un qualsiasi client. L'autorita' di gioco
    per questa modalita' vive nella game instance, non qui (game_authority resta None)."""
    global network_manager
    clear_all_ui_elements()

    status_text = Text("Connessione al matchmaking server...", scale=1.2, origin=(0, 0),
                        position=(0, 0.1), parent=camera.ui)
    back_btn = Button('Annulla', y=-0.2, scale=(0.3, 0.08), parent=camera.ui,
                       on_click=lambda: cancel_online_session())
    guiElements.extend([status_text, back_btn])

    # Tutta l'orchestrazione specifica dell'online (handshake TCP col matchmaking) vive in
    # network_online.py; qui leggiamo solo lo stato via network_manager.matchmaking_status.
    network_manager = network_online.start_online_session()

    invoke(poll_matchmaking_status, network_manager, status_text, delay=0.5)

def poll_matchmaking_status(nm, status_text):
    # Se nel frattempo l'utente ha annullato o e' tornato al menu, questa istanza di
    # NetworkManager non e' piu' quella attiva: fermiamo il polling.
    if network_manager is not nm:
        return

    status = nm.matchmaking_status
    if status == "matched":
        invoke(show_lobby_screen, delay=0.2)
        return
    elif status == "error":
        display_error_message_screen(
            f"Connessione al matchmaking fallita: {nm.matchmaking_error}",
            lambda: show_main_menu_screen())
        return

    label = {
        "connecting": "Connessione al matchmaking server...",
        "queued": "In coda, in attesa di altri giocatori...",
    }.get(status, "...")
    if status_text:
        status_text.text = label
    invoke(poll_matchmaking_status, nm, status_text, delay=0.5)

def cancel_online_session():
    global network_manager
    if network_manager:
        network_manager.stop()
        network_manager = None
    show_main_menu_screen()

# --- P2P: scoperta host via LAN + connessione manuale via IP (vedi network_p2p.join_p2p_host) ---
def start_local_client_session():
    clear_all_ui_elements()
    Text("Searching for LAN hosts...", scale=1.2, y=0.2, origin=(0,0), parent=camera.ui)
    invoke(scan_lan_for_hosts, delay=0.5)

def scan_lan_for_hosts():
    global current_listener
    clear_all_ui_elements()
    title = Text("Available Hosts on LAN", scale=1.2, y=0.3, origin=(0,0), parent=camera.ui)
    guiElements.append(title)

    listener = DiscoveryListener()
    listener.start()
    current_listener = listener
    host_buttons = []

    def refresh_host_buttons():
        if not listener.running:
            if not host_buttons or not isinstance(host_buttons[-1], Text):
                # bind della discovery fallito (es. un altro client sta gia' ascoltando su
                # questa macchina).
                msg = Text("Ricerca automatica non disponibile su questa macchina.",
                           y=0.15, scale=0.7, origin=(0,0), parent=camera.ui)
                host_buttons.append(msg)
            return
        for b in host_buttons: destroy(b)
        host_buttons.clear()

        hosts = listener.get_hosts()
        if not hosts:
            msg = Text("No hosts found yet...", y=0.15, scale=0.8, parent=camera.ui)
            host_buttons.append(msg)
        else:
            for i, (ip, pid) in enumerate(hosts):
                btn = Button(
                    text=f"Join Host ({ip})", scale=(0.4, 0.08),
                    position=(0, 0.15 - i * 0.1), parent=camera.ui,
                    on_click=Func(connect_to_p2p_host, lambda ip=ip: ip, listener)
                )
                host_buttons.append(btn)

        back = Button("Back", y=-0.3, scale=(0.3, 0.08), parent=camera.ui,
                      on_click=lambda: back_to_scan_host_menu(listener))
        host_buttons.append(back)
        invoke(refresh_host_buttons, delay=1.5)

    refresh_host_buttons()

def back_to_scan_host_menu(listener):
    listener.stop()
    clear_all_ui_elements()
    show_local_mode_menu()

def connect_to_p2p_host(ip_or_getter, listener):
    global network_manager
    # ip_or_getter puo' essere una stringa (host scoperto via LAN) o una funzione senza argomenti
    # che restituisce il valore CORRENTE del campo IP manuale (valutata solo al click, cosi'
    # prendiamo quello che l'utente ha digitato e non il valore iniziale del campo).
    ip = ip_or_getter() if callable(ip_or_getter) else ip_or_getter
    if not ip:
        return

    listener.stop()
    clear_all_ui_elements()
    try:
        network_manager = network_p2p.join_p2p_host(ip)
        invoke(show_lobby_screen, delay=0.5)
    except Exception:
        display_error_message_screen("Connection failed.", lambda: show_local_mode_menu())

# --------------------------
# LOBBY SYSTEM
# --------------------------
def show_lobby_screen():
    global lobby_open
    lobby_open = True
    clear_all_ui_elements()

    def refresh_lobby():
        if not lobby_open: return
        for lbl in player_labels: destroy(lbl)
        for btn in lobby_buttons: destroy(btn)
        player_labels.clear()
        lobby_buttons.clear()

        title = Text("Lobby", scale=1.2, y=0.4, parent=camera.ui)
        player_labels.append(title)

        # Elenco giocatori: usiamo connected_ids, che si popola per TUTTI (host e client) man
        # mano che qualcuno entra, gia' durante la lobby — a differenza di other_players, che
        # resta vuoto finche' la partita non parte davvero (si popola solo con level_loaded=True),
        # quindi prima un client normale vedeva sempre e solo se stesso nel conteggio.
        all_ids = sorted({network_manager.player_id} | connected_ids)
        for i, pid in enumerate(all_ids):
            is_me = pid == network_manager.player_id
            label = f"Player {pid}" + (" (tu)" if is_me else "")
            entry = Text(label, y=0.3 - i * 0.06, color=color.gold if is_me else color.white,
                         parent=camera.ui)
            player_labels.append(entry)

        list_bottom_y = 0.3 - len(all_ids) * 0.06

        if network_manager.is_host:
            def begin_game():
                network_manager.send_rpc("start_game", game_authority.floor_seed)
                rpc_start_game(game_authority.floor_seed)  # Call locally for host

            start_btn = Button("Start Game", y=list_bottom_y - 0.12, scale=(0.3, 0.08),
                               parent=camera.ui, on_click=begin_game)
            lobby_buttons.append(start_btn)

        back = Button("Leave", y=list_bottom_y - 0.24, scale=(0.3, 0.08), parent=camera.ui, on_click=leave_lobby)
        lobby_buttons.append(back)
        invoke(refresh_lobby, delay=2.0)

    refresh_lobby()

def leave_lobby():
    global lobby_open, broadcaster, network_manager
    lobby_open = False
    if network_manager:
        network_manager.stop()
        network_manager = None
    if broadcaster:
        broadcaster.stop()
        broadcaster = None
    # Altrimenti una sessione futura mostrerebbe nella lobby anche i giocatori di questa,
    # ormai abbandonata: connected_ids non veniva mai ripulito.
    connected_ids.clear()
    clear_all_ui_elements()
    show_main_menu_screen()

# --------------------------
# MIGRAZIONE DELL'HOST (solo P2P)
# --------------------------
# Se l'host P2P sparisce, i client sopravvissuti eleggono in autonomia un nuovo host (l'id piu'
# basso tra chi e' rimasto: e' un criterio deterministico che tutti calcolano allo stesso modo,
# senza bisogno di negoziare) e la partita continua invece di finire di colpo.
def check_host_migration():
    global migrating
    if migrating or not network_manager:
        return
    if not network_manager.allow_host_migration or network_manager.is_host:
        return  # non siamo un client P2P (o siamo gia' l'host): nulla da controllare
    if network_manager.host_alive():
        return

    migrating = True
    print("[Game] Host non risponde. Avvio la migrazione...")
    handle_host_migration()

def handle_host_migration():
    global network_manager, game_authority, broadcaster

    old_nm = network_manager
    my_id = old_nm.player_id
    # Chi e' ancora presente, secondo quello che sapevamo PRIMA che l'host sparisse: noi stessi
    # piu' chiunque avessimo gia' come RemotePlayer. E' un criterio deterministico: ogni
    # sopravvissuto calcola la stessa lista e lo stesso vincitore, senza doversi coordinare.
    survivor_ids = set(other_players.keys()) | {my_id}
    new_host_id = min(survivor_ids)

    status = Text("Host perso: elezione in corso...", scale=1.2, origin=(0, 0), y=0.15,
                  parent=camera.ui, tag='migration_text')

    if new_host_id == my_id:
        # Tocca a noi diventare il nuovo host. Ricostruiamo lo stato dai dati che avevamo
        # gia' visto (blocchi gia' spariti sul nostro pavimento, ultima posizione nota di
        # ognuno) invece di ripartire da zero.
        destroyed_ids = [b.block_id for b in (floors.floor_cubes if floors is not None else []) if b.has_activated]
        positions = {pid: [rp.x, rp.y, rp.z] for pid, rp in other_players.items()}
        if player is not None:
            positions[my_id] = [player.x, player.y, player.z]

        network_manager, game_authority, broadcaster = network_p2p.promote_to_host(
            old_nm, current_floor_seed, destroyed_ids, positions, survivor_ids)
        print(f"[Game] Siamo il nuovo host (player {my_id}).")
        finish_migration(status)
    else:
        # Aspettiamo che il nuovo host (un altro sopravvissuto) inizi a farsi vedere sulla LAN,
        # e ci ricolleghiamo a lui chiedendo di riavere lo stesso id di prima.
        listener = network_p2p.start_host_discovery_scan()
        invoke(try_find_new_host, old_nm, my_id, listener, status, 10, delay=1.0)

def try_find_new_host(old_nm, my_id, listener, status, attempts_left):
    global network_manager
    hosts = listener.get_hosts()
    if hosts:
        new_ip, _ = hosts[0]
        listener.stop()
        network_manager = network_p2p.reconnect_to_new_host(my_id, new_ip)
        print(f"[Game] Nuovo host trovato: {new_ip}. Riconnesso.")
        finish_migration(status)
    elif attempts_left > 0:
        invoke(try_find_new_host, old_nm, my_id, listener, status, attempts_left - 1, delay=1.0)
    else:
        listener.stop()
        destroy(status)
        display_error_message_screen(
            "Impossibile trovare il nuovo host. La connessione con la partita e' andata persa.",
            lambda: resetFloor())

def finish_migration(status):
    global migrating
    destroy(status)
    migrating = False

# --------------------------
# MAIN MENU
# --------------------------
def show_main_menu_screen():
    clear_all_ui_elements()
    Text('Falls Game', scale=2, origin=(0, 0), position=(0, 0.3), parent=camera.ui)
    Button('Local Play (P2P)', scale=(0.3,0.1), position=(0,0.1), parent=camera.ui, on_click=show_local_mode_menu)
    Button('Online Play (Server)', scale=(0.3,0.1), position=(0,-0.1), parent=camera.ui, on_click=start_online_client_session)

def show_local_mode_menu():
    clear_all_ui_elements()
    Text("Local Game Mode", position=(0,0.3), origin=(0,0), scale=1.5, parent=camera.ui)
    Button('Host Game', scale=(0.3,0.1), position=(0,0.1), parent=camera.ui, on_click=start_local_host_session)
    Button('Join Game', scale=(0.3,0.1), position=(0,-0.05), parent=camera.ui, on_click=start_local_client_session)
    Button('Back', scale=(0.3,0.1), position=(0,-0.2), parent=camera.ui, on_click=show_main_menu_screen)

# --------------------------
# GAME UPDATE LOOP
# --------------------------
def update():
    global game_over, level_loaded, player_died_reported

    if network_manager:
        network_manager.process_queue(handle_rpc)
        check_host_migration()

    if not level_loaded or player is None or game_over or game_paused:
        return

    # Physics and Game Logic: aggiorniamo i collider e rileviamo se siamo sopra un blocco non
    # ancora attivato. NOTA: non distruggiamo piu' il blocco qui direttamente (prima questo era
    # codice morto: "player.intersects()" veniva chiamato ma il risultato era commentato). Ora
    # mandiamo una RICHIESTA all'autorita' di gioco (vedi request_block_step), che decide se
    # accettarla e lo comunica a tutti — cosi' tutti i giocatori vedono lo stesso esito.
    for entity in scene.entities:
        if isinstance(entity, FloorCube):
            entity.updateColliders(player)
            if (entity.collider_added and not entity.has_activated
                    and not entity.step_requested and not entity.is_disappearing):
                if entity.is_player_standing_on(player):
                    entity.step_requested = True
                    request_block_step(entity.block_id)

    # Caduti sotto la mappa: prima qui finiva subito la partita per TUTTI (game_over locale +
    # ritorno al menu dopo 2s), a prescindere da quanti altri giocatori fossero ancora vivi. Ora
    # segnaliamo solo la nostra caduta all'autorita' di gioco, che decide se la partita e' finita
    # (siamo rimasti l'unico vivo? vince l'altro) oppure se deve continuare finche' non resta un
    # solo superstite.
    if not player_died_reported and player.world_position.y < -20:
        player_died_reported = True
        player.gameOver()
        request_player_died()

    # BUG FIX: se siamo l'host, request_player_died() sopra puo' decretare una vittoria in modo
    # SINCRONO (rpc_player_died -> apply_game_won), che distrugge il player e lo mette a None
    # nello stesso istante — ma il controllo "esci se non c'e' il player" in cima a questa
    # funzione e' gia' stato superato per QUESTO frame. Senza ricontrollare qui, le righe
    # sotto proverebbero ad accedere a player.x su un player ormai None -> crash.
    if game_over or player is None:
        return

    # Segnaliamo la nostra posizione all'autorita' di gioco ogni frame.
    if network_manager and network_manager.player_id is not None:
        network_manager.send_rpc("update_pos", network_manager.player_id,
                                 player.x, player.y, player.z)
        if network_manager.is_host and game_authority is not None:
            # L'host non riceve mai i propri pacchetti via rete (send_raw non fa loopback):
            # alimentiamo l'autorita' direttamente con la nostra posizione.
            game_authority.handle_update_pos(network_manager.player_id, player.x, player.y, player.z)

def input(key):
    if key == 'escape' and level_loaded:
        resetFloor()

if __name__ == '__main__':
    window.borderless = False
    window.title = "Fall Guys Ursina"
    window.exit_button.visible = False
    invoke(show_main_menu_screen, delay=0.1)
    app.run()