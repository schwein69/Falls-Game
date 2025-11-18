import os
from ursina import *
from player import Player
from floor import *
from splashScreen import SplashScreen
from usefulFunctions import *
from network import NetworkManager
from network_discovery import DiscoveryBroadcaster, DiscoveryListener

# Global game state
loading_screen = None
local_play_button = None
online_play_button = None
game_title_text = None
player = None
floors = None
sky = None
game_over = False
level_loaded = False
guiElements = []
player_labels = []
player_id = 0
lobby_buttons = []
lobby_open = False
network_manager = None
broadcaster = None

def show_error_message(message, on_close):
    clear_ui()
    msg = Text(message, scale=1.2, origin=(0,0), position=(0, 0.1), parent=camera.ui, color=color.red)
    btn = Button('Back', scale=(0.3, 0.1), position=(0, -0.1), parent=camera.ui, on_click=on_close)
    guiElements.append(msg)
    guiElements.append(btn)
    

def handle_network_message(msg):
    if msg == "START_GAME":
        print("Client received start game signal.")
        global lobby_open
        lobby_open = False
        loadLevel()
        
    if msg == "HOST_LEFT":
        print("Host left. Returning to main menu.")
        def on_close():
            leave_lobby()
        show_error_message("You have been kicked from the lobby. Host left.", on_close)    

def loadLevel():
    global loading_screen, player, floors, sky, level_loaded, game_over
    
    clear_ui()  
    
    # Clear loading screen
    if loading_screen:
        destroy(loading_screen)
    
    # Initialize game environment
    floors = Floor()
    sky = Entity(
        model="sphere",
        texture=os.path.join("assets", "sky.png"),
        scale=9999,
        double_sided=True
    )
    
    # Create player
    player = Player(get_random_position(), f"Player {player_id}")

    
    # Setup camera
    camera.z = -5
    
    # Set game state
    game_over = False
    invoke(gameStart, delay=5)
    
def gameStart():
    global player, floors, game_over, level_loaded
    level_loaded = True
    player.gravity = 1
    
def showLoadingScreen():
    global loading_screen, local_play_button, online_play_button, game_title_text
    
    # Clear menu
    if local_play_button:
        destroy(local_play_button)
    if online_play_button:
        destroy(online_play_button)
    if game_title_text:
        destroy(game_title_text)
    
    # Show splash screens
    loading_screen = SplashScreen()
    loading_screen.on_destroy = Func(SplashScreen, 'shore')
    #invoke(loadLevel, delay=2)
    invoke(initNetwork, delay=2)

def initNetwork():
    global network_manager, player_id

    network_manager = NetworkManager()
    network_manager.add_receive_callback(handle_network_message)  
    try:
        print("Attempting to join existing host...")
        network_manager.join_host("127.0.0.1")
        print("Joined as client.")
    except Exception as e:
        print("No host found. Becoming host...")
        network_manager.setup_host()

    player_id = network_manager.player_id
    invoke(loadLevel, delay=2)

def start_local_network():
    global network_manager, player_id, loading_screen
    clear_ui()
    loading_screen = SplashScreen()
    loading_screen.on_destroy = Func(SplashScreen, 'shore')

    network_manager = NetworkManager()
    try:
        network_manager.setup_host()
        player_id = 0
        print("Started as host.")
    except:
        try:
            network_manager.join_host("127.0.0.1")
            player_id = network_manager.player_id
            print("Joined as client.")
        except:
            print("Failed to host or join.")
            showMenu()
            return

    invoke(show_lobby, delay=1.5)

def show_lobby():
    global lobby_open
    lobby_open = True
    clear_ui()

    def refresh_list():
        if not lobby_open:
            return

        for lbl in player_labels:
            destroy(lbl)
        player_labels.clear()

        for btn in lobby_buttons:
            destroy(btn)
        lobby_buttons.clear()

        title = Text("Lobby", scale=1.2, y=0.4, parent=camera.ui)
        player_labels.append(title)

        for i, (pid, ip) in enumerate(network_manager.player_ips.items()):
            label = Text(f"Player {pid} ({ip})", y=0.25 - i * 0.1, scale=1, parent=camera.ui)
            player_labels.append(label)

          # Host controls: broadcast to clients, then start locally
        if network_manager and getattr(network_manager, "is_host", False):
            def start_game():
                global lobby_open
                lobby_open = False   # stop refresh loop immediately

                # notify clients
                try:
                    if hasattr(network_manager, 'broadcast_message'):
                        network_manager.broadcast_message("START_GAME")
                    elif hasattr(network_manager, 'send_all'):
                        network_manager.send_all("START_GAME")
                    elif hasattr(network_manager, 'send'):
                        # best-effort: try to send to each client socket if available
                        network_manager.send("START_GAME")
                    else:
                        print("[Host] No broadcast method available on network_manager")
                except Exception as e:
                    print(f"[Host] Failed to notify clients: {e}")

                # clear lobby UI and start host locally
                clear_ui()
                # small delay to let network messages propagate
                invoke(loadLevel, delay=0.3)

            # enable start only when at least 2 connected players (host + 1 client)
            enough_players = len(network_manager.player_ips) >= 2
            start_btn = Button("Start Game", y=-0.3, scale=(0.3, 0.08), parent=camera.ui, on_click=start_game, enabled=enough_players)
            lobby_buttons.append(start_btn)

        back_btn = Button("Back", y=-0.4, scale=(0.3, 0.08), parent=camera.ui, on_click=leave_lobby)
        lobby_buttons.append(back_btn)

        invoke(refresh_list, delay=1.5)

    refresh_list()


def clear_ui():
    for e in camera.ui.children:
        destroy(e)
    guiElements.clear()
    player_labels.clear()
    lobby_buttons.clear()
    
def showMenu():
    clear_ui()
    # Create menu UI
    game_title_text = Text(
        text='Falls Game', 
        scale=2, 
        origin=(0, 0), 
        position=(0, 0.3),
        parent=camera.ui
    )
    guiElements.append(game_title_text)
    local_play_button = Button(
        text='Local Play', 
        scale=(0.3, 0.1), 
        position=(0, 0.1),
        parent=camera.ui,
        on_click=show_local_mode
    )
    guiElements.append(local_play_button)
    online_play_button = Button(
        text='Online Play', 
        scale=(0.3, 0.1), 
        position=(0, -0.1),
        parent=camera.ui
    )
    guiElements.append(online_play_button)
    
def show_local_mode():
    clear_ui()

    title = Text("Local Game Mode",  origin=(0, 0), position=(0, 0.3), scale=1.5, parent=camera.ui)
    guiElements.append(title)

    host_btn = Button(
        text='Host Game',
        scale=(0.3, 0.1),
        position=(0, 0.1),
        parent=camera.ui,
        on_click=start_local_host
    )
    guiElements.append(host_btn)

    join_btn = Button(
        text='Join Game',
        scale=(0.3, 0.1),
        position=(0, -0.05),
        parent=camera.ui,
        on_click=start_local_client
    )
    guiElements.append(join_btn)

    back_btn = Button(
        text='Back',
        scale=(0.3, 0.1),
        position=(0, -0.2),
        parent=camera.ui,
        on_click=showMenu
    )
    guiElements.append(back_btn)
    
def start_local_host():
    global network_manager, player_id, broadcaster
    clear_ui()
    network_manager = NetworkManager(mode="p2p")
    network_manager.setup_host()
    network_manager.add_receive_callback(handle_network_message)
    broadcaster = DiscoveryBroadcaster("0")
    broadcaster.start()
    player_id = 0
    print("You are the host. Waiting for players...")
    invoke(show_lobby, delay=1)
        
def start_local_client():
    clear_ui()
    title = Text("Searching for hosts...", scale=1.2, y=0.2, parent=camera.ui)
    guiElements.append(title)
    invoke(scan_for_hosts, delay=0.5)

def scan_for_hosts():
    global current_listener
    clear_ui()
    guiElements.clear()

    title = Text("Available Hosts on LAN", scale=1.2, y=0.3, parent=camera.ui)
    guiElements.append(title)

    listener = DiscoveryListener()
    listener.start()
    current_listener = listener

    host_buttons = []

    def clear_host_buttons():
        for b in host_buttons:
            destroy(b)
        host_buttons.clear()

    def stop_and_go_back():
        listener.stop()
        clear_ui()
        show_local_mode()

    def update_host_list():
        if not listener.running:
            return

        clear_host_buttons()
        hosts = listener.get_hosts()

        if not hosts:
            info = Text("No hosts found yet...", y=0.15, scale=0.8, parent=camera.ui)
            guiElements.append(info)
            host_buttons.append(info)
        else:
            for i, (ip, pid) in enumerate(hosts):
                y_pos = 0.15 - i * 0.1
                btn = Button(
                    text=f"Join Player {pid}",
                    scale=(0.4, 0.08),
                    position=(0, y_pos),
                    parent=camera.ui,
                    on_click=Func(connect_to_host, ip, listener)
                )
                host_buttons.append(btn)

        back_btn = Button("Back", y=-0.3, scale=(0.3, 0.08), parent=camera.ui, on_click=stop_and_go_back)
        guiElements.append(back_btn)
        host_buttons.append(back_btn)

        # keep scanning
        invoke(update_host_list, delay=1.5)

    update_host_list()
            

def connect_to_host(ip, listener):
    global network_manager, player_id

    listener.stop()
    clear_ui()
    network_manager = NetworkManager(mode="p2p")
    network_manager.add_receive_callback(handle_network_message)
    
    try:
        network_manager.join_host(ip)
        player_id = network_manager.player_id
        print(f"Connected to host at {ip}")
        invoke(show_lobby, delay=1)
    except Exception as e:
        print(f"Failed to join host at {ip}: {e}")
        show_error_message("Lobby does not exist or host unavailable.", back_to_scan)

def back_to_scan():
    clear_ui()
    scan_for_hosts()


def leave_lobby():
    global lobby_open, broadcaster, network_manager
    lobby_open = False
    
    # Host should notify clients before stopping
    if network_manager:
        if network_manager.is_host:
            # Inform all clients that lobby is closed
            print("[Host] Broadcasting HOST_LEFT before stopping")
            network_manager.broadcast_message("HOST_LEFT")
        network_manager.stop()
        network_manager = None
         
    if broadcaster:
        broadcaster.stop()
        broadcaster = None
        
    clear_ui()    
    showMenu()

def client_lost_host():
    print("Lost connection to host.")
    leave_lobby()
    
def update():
    global player, floors, game_over, level_loaded
    
    if game_over == True:
        return

    if player is None or floors is None:  # Prevent running update logic if player is None
        return
    if level_loaded == False:
        player.gravity = 0
        return
    
    # Update collisions
    for entity in scene.entities:
        if isinstance(entity, FloorCube):
            entity.updateColliders(player)
    
    # Check floor contact
    if player and floor:
        hit_info = player.intersects()
        if hit_info.hit and isinstance(hit_info.entity, FloorCube):
            hit_info.entity.on_step(player)
    
    # Check if player fell
    if player.world_position.y < -20:
        player.gameOver()
        game_over = True
        invoke(resetFloor, delay=2)

def input(key):
    if key == '1':
        to_first_person()
    elif key == '3':
        to_third_person()
    elif key == 'escape' and level_loaded:
        resetFloor()
def resetFloor():
    global player, floors, game_over, level_loaded
    if player:
        destroy(player)
        player = None
    if floors:
        destroy(floors)
        floors = None
    if sky:
        destroy(sky)
    
    level_loaded = False
    game_over = False
    invoke(showMenu, delay=1)
    
if __name__ == '__main__':
    # Initialize the game
    app = Ursina()
    window.borderless = False
    window.title = "Fall Guys Ursina"
    window.exit_button.visible = False
    window.entity_counter.visible = True
    window.collider_counter.visible = False

    invoke(showMenu, delay=0.1)

    app.run()