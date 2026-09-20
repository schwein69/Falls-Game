from ursina import *
from ursina.prefabs.input_field import InputField 
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
from network_discovery import DiscoveryListener
import network_p2p
import network_online
import game_state
from usefulFunctions import clear_all_ui_elements, display_error_message_screen
from rpc_handlers import rpc_start_game


# SESSIONE P2P
def start_local_host_session():
    clear_all_ui_elements()
    game_state.network_manager, game_state.game_authority, game_state.broadcaster = (
        network_p2p.start_p2p_host())
    invoke(show_lobby_screen, delay=0.5)


def start_local_client_session():
    clear_all_ui_elements()
    Text("Searching for LAN hosts...", scale=1.2, y=0.2, origin=(0, 0), parent=camera.ui)
    invoke(scan_lan_for_hosts, delay=0.5)


def scan_lan_for_hosts():
    clear_all_ui_elements()
    title = Text("Available Hosts on LAN", scale=1.2, y=0.3, origin=(0, 0), parent=camera.ui)
    game_state.guiElements.append(title)

    listener = DiscoveryListener()
    listener.start()
    game_state.current_listener = listener
    host_buttons = []

    def refresh_host_buttons():
        if not listener.running:
            if not host_buttons or not isinstance(host_buttons[-1], Text):
                # bind della discovery fallito (es. un altro client sta gia' ascoltando su
                # questa macchina).
                msg = Text("Ricerca automatica non disponibile su questa macchina.",
                           y=0.15, scale=0.7, origin=(0, 0), parent=camera.ui)
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
    ip = ip_or_getter() if callable(ip_or_getter) else ip_or_getter
    if not ip:
        return

    listener.stop()
    clear_all_ui_elements()
    try:
        game_state.network_manager = network_p2p.join_p2p_host(ip)
        invoke(show_lobby_screen, delay=0.5)
    except Exception:
        display_error_message_screen("Connection failed.", lambda: show_local_mode_menu())


# SESSIONE ONLINE (matchmaking + server dedicato)
def start_online_client_session():
    """Avvia la modalita' Online: ci connettiamo al matchmaking server (config.MATCHMAKING_HOSTNAME)
    e attendiamo di essere abbinati. Il matchmaking spawna una game instance dedicata
    (server/game_instance.py) quando si raggiungono MAX_PLAYERS in coda, e ci comunica dove
    connetterci; a quel punto entriamo in lobby come un qualsiasi client. L'autorita' di gioco
    per questa modalita' vive nella game instance, non qui (game_authority resta None)."""
    clear_all_ui_elements()

    status_text = Text("Connessione al matchmaking server...", scale=1.2, origin=(0, 0),
                        position=(0, 0.1), parent=camera.ui)
    back_btn = Button('Annulla', y=-0.2, scale=(0.3, 0.08), parent=camera.ui,
                       on_click=lambda: cancel_online_session())
    game_state.guiElements.extend([status_text, back_btn])

    game_state.network_manager = network_online.start_online_session()
    invoke(poll_matchmaking_status, game_state.network_manager, status_text, delay=0.5)


def poll_matchmaking_status(nm, status_text):
    # Se nel frattempo l'utente ha annullato o e' tornato al menu, questa istanza di
    # NetworkManager non e' piu' quella attiva: fermiamo il polling.
    if game_state.network_manager is not nm:
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
    if game_state.network_manager:
        game_state.network_manager.stop()
        game_state.network_manager = None
    show_main_menu_screen()


# LOBBY
def show_lobby_screen():
    game_state.lobby_open = True
    clear_all_ui_elements()

    def refresh_lobby():
        if not game_state.lobby_open: return
        for lbl in game_state.player_labels: destroy(lbl)
        for btn in game_state.lobby_buttons: destroy(btn)
        game_state.player_labels.clear()
        game_state.lobby_buttons.clear()

        title = Text("Lobby", scale=1.2, y=0.4, parent=camera.ui)
        game_state.player_labels.append(title)

        nm = game_state.network_manager
        all_ids = sorted({nm.player_id} | game_state.connected_ids)
        for i, pid in enumerate(all_ids):
            is_me = pid == nm.player_id
            label = f"Player {pid}" + (" (tu)" if is_me else "")
            entry = Text(label, y=0.3 - i * 0.06, color=color.gold if is_me else color.white,
                         parent=camera.ui)
            game_state.player_labels.append(entry)

        list_bottom_y = 0.3 - len(all_ids) * 0.06

        if nm.is_host:
            def begin_game():
                nm.send_rpc("start_game", game_state.game_authority.floor_seed)
                rpc_start_game(game_state.game_authority.floor_seed)  # Call locally for host

            start_btn = Button("Start Game", y=list_bottom_y - 0.12, scale=(0.3, 0.08),
                               parent=camera.ui, on_click=begin_game)
            game_state.lobby_buttons.append(start_btn)

        back = Button("Leave", y=list_bottom_y - 0.24, scale=(0.3, 0.08), parent=camera.ui, on_click=leave_lobby)
        game_state.lobby_buttons.append(back)
        invoke(refresh_lobby, delay=2.0)

    refresh_lobby()


def leave_lobby():
    game_state.lobby_open = False
    if game_state.network_manager:
        game_state.network_manager.stop()
        game_state.network_manager = None
    if game_state.broadcaster:
        game_state.broadcaster.stop()
        game_state.broadcaster = None
    game_state.connected_ids.clear()
    clear_all_ui_elements()
    show_main_menu_screen()


# MENU PRINCIPALE
def show_main_menu_screen():
    clear_all_ui_elements()
    Text('Falls Game', scale=2, origin=(0, 0), position=(0, 0.3), parent=camera.ui)
    Button('Local Play (P2P)', scale=(0.3, 0.1), position=(0, 0.1), parent=camera.ui, on_click=show_local_mode_menu)
    Button('Online Play (Server)', scale=(0.3, 0.1), position=(0, -0.1), parent=camera.ui, on_click=start_online_client_session)


def show_local_mode_menu():
    clear_all_ui_elements()
    Text("Local Game Mode", position=(0, 0.3), origin=(0, 0), scale=1.5, parent=camera.ui)
    Button('Host Game', scale=(0.3, 0.1), position=(0, 0.1), parent=camera.ui, on_click=start_local_host_session)
    Button('Join Game', scale=(0.3, 0.1), position=(0, -0.05), parent=camera.ui, on_click=start_local_client_session)
    Button('Back', scale=(0.3, 0.1), position=(0, -0.2), parent=camera.ui, on_click=show_main_menu_screen)