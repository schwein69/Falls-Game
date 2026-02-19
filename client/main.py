import os
import redis
from ursina import *
from config import *
from player import Player, RemotePlayer
from floor import *
from splashScreen import SplashScreen
from usefulFunctions import *
from network import NetworkManager
from network_discovery import DiscoveryBroadcaster, DiscoveryListener
import threading    
import time

# --------------------------
# GLOBAL GAME STATE
# --------------------------
app = Ursina()
r = redis.Redis(host='localhost', port=6379, db=0) # For matchmaking server communication
loading_screen = None
player = None
floors = None
sky = None
other_players = {}  # player_id -> RemotePlayer instance
guiElements = []
player_labels = []
lobby_buttons = []


game_over = False
level_loaded = False
lobby_open = False

network_manager: NetworkManager = None
broadcaster: DiscoveryBroadcaster = None
current_listener: DiscoveryListener = None

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
def rpc_spawn_player(pid, x, y, z, status="alive" , matchId=None):
    global other_players
    if network_manager and pid == network_manager.player_id:
        return
    if pid in other_players:
        return
    
    print(f"[Game] Spawning Remote Player {pid}")
    r.set(f"player:{pid}:pos:status:matchId", f"{x},{y},{z}", status, matchId)
    # Use RemotePlayer class for others
    new_p = RemotePlayer(position=Vec3(x, y, z), username=f"Player {pid}")
    other_players[pid] = new_p
    
# This is called when we receive a position update from another player    
def rpc_update_pos(pid, x, y, z,matchId=None):
    global other_players
    if pid in other_players:
        # Update remote player position
        other_players[pid].position = Vec3(x, y, z)
        r.set(f"player:{pid}:pos:matchId", f"{x},{y},{z}",matchId)
    elif pid != network_manager.player_id:
        # If player not found, spawn them
        rpc_spawn_player(pid, x, y, z, matchId)

# This is called when we receive a player left message. We destroy their RemotePlayer instance and remove them from other_players dict.
def rpc_player_left(pid):
    global other_players
    if pid in other_players:
        print(f"[Game] Player {pid} Left")
        destroy(other_players[pid])
        del other_players[pid]
        r.delete(f"player:{pid}:pos:status:matchId")
        r.delete(f"status:player:{pid}")

# This is called when the host starts the game. We set lobby_open to False and load the level for everyone. The host also calls this locally when they click "Start Game" in the lobby.        
def rpc_start_game():
    global lobby_open
    print("[Game] Host started the game")
    lobby_open = False
    loadLevel()

def rpc_handle_disconnection(pid):
    print(f"[Game] Player {pid} disconnected. Game Paused. Waiting {REJOIN_TIMER}s...")
    r.set(f"status:player:{pid}", "awaiting_rejoin",ex=REJOIN_GRACE_PERIOD)  
    
    start_time = time.time()
    rejoined = False
    
    current_status = r.get(f"status:player:{pid}")
    if current_status and current_status.decode() == "awaiting_rejoin":
        print(f"[Game] Player {pid} failed to rejoin in time. Marking as left.")
        rpc_player_left(pid)
    else:
        print(f"[Game] Player {pid} rejoined successfully.")
def on_client_lost(player_id, match_id):
    # Avviamo il thread di attesa senza bloccare il server principale
    t = threading.Thread(target=rpc_handle_disconnection, args=(player_id, match_id))
    t.start()        
# Map string names to functions
RPC_REGISTRY = {
    "spawn_player": rpc_spawn_player,
    "update_pos": rpc_update_pos,
    "player_left": rpc_player_left,
    "start_game": rpc_start_game,
    "rejoin_player": on_client_lost 
}

def handle_rpc(data):
    """Dispatches JSON data to functions"""
    method = data.get("method")
    args = data.get("args", [])
    if method in RPC_REGISTRY:
        RPC_REGISTRY[method](*args)

# --------------------------
# 4. GAME LOADING 
# --------------------------
def loadLevel():
    global player, floors, sky, level_loaded, game_over
    clear_all_ui_elements()

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
    if player: destroy(player)
    if floors: destroy(floors)
    if sky: destroy(sky)
    for p in other_players.values(): destroy(p)
    
    other_players.clear()
    player = None
    floors = None
    sky = None
    level_loaded = False
    game_over = False

    invoke(show_main_menu_screen, delay=1)

# --------------------------
# NETWORK SESSION CONTROLS
# --------------------------
def start_local_host_session():
    global network_manager, broadcaster
    clear_all_ui_elements()
    
    network_manager = NetworkManager(mode="p2p")
    network_manager.setup_host()

    broadcaster = DiscoveryBroadcaster("0")
    broadcaster.start()

    invoke(show_lobby_screen, delay=0.5)

def start_online_client_session():
    """Directly connect to a dedicated server IP from config."""
    global network_manager
    clear_all_ui_elements()
    Text("Connecting to Dedicated Server...", scale=1.2, origin=(0,0), parent=camera.ui)
    
    network_manager = NetworkManager(mode="online")
    network_manager.start() # This calls join_network(SERVER_IP, SERVER_PORT)
    
    invoke(show_lobby_screen, delay=1.0)

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
        if not listener.running: return
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
                    on_click=Func(connect_to_p2p_host, ip, listener)
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

def connect_to_p2p_host(ip, listener):
    global network_manager
    listener.stop()
    clear_all_ui_elements()
    network_manager = NetworkManager(mode="p2p")
    try:
        network_manager.join_network(ip, P2P_PORT)
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

        # Show yourself
        me = Text(f"You: Player {network_manager.player_id}", y=0.3, color=color.gold, parent=camera.ui)
        player_labels.append(me)

        # Show others (Host sees clients dict, Client sees others via RPC spawn)
        # For simplicity in lobby, we just count connected peers
        count = len(network_manager.clients) if network_manager.is_host else len(other_players)
        status = Text(f"Players in Lobby: {count + 1}", y=0.2, scale=1, parent=camera.ui)
        player_labels.append(status)

        if network_manager.is_host:
            def begin_game():
                network_manager.send_rpc("start_game")
                rpc_start_game() # Call locally for host

            start_btn = Button("Start Game", y=-0.3, scale=(0.3, 0.08),
                               parent=camera.ui, on_click=begin_game)
            lobby_buttons.append(start_btn)

        back = Button("Leave", y=-0.4, scale=(0.3, 0.08), parent=camera.ui, on_click=leave_lobby)
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
    clear_all_ui_elements()
    show_main_menu_screen()

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
    global game_over, level_loaded

    if network_manager:
        # Crucial: Process messages from the network thread in the main thread
        network_manager.process_queue(handle_rpc)

    if not level_loaded or player is None or game_over:
        return

    # Physics and Game Logic
    for entity in scene.entities:
        if isinstance(entity, FloorCube):
            entity.updateColliders(player)

    hit = player.intersects()
    # if hit.hit and isinstance(hit.entity, FloorCube):
    #     hit.entity.on_step(player)

    if player.world_position.y < -20:
        player.gameOver()
        game_over = True
        invoke(resetFloor, delay=2)

    # Broadcast position every frame
    if network_manager and network_manager.player_id is not None:
        network_manager.send_rpc("update_pos", network_manager.player_id, 
                                 player.x, player.y, player.z)

def input(key):
    if key == 'escape' and level_loaded:
        resetFloor()

if __name__ == '__main__':
    window.borderless = False
    window.title = "Fall Guys Ursina"
    window.exit_button.visible = False
    invoke(show_main_menu_screen, delay=0.1)
    app.run()