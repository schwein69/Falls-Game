from ursina import *
from network_discovery import DiscoveryListener


class GameView:
    def __init__(self, model, controller=None):
        self.model = model
        self.controller = controller

    # Utilita'
    def clear_all_ui_elements(self):
        for e in camera.ui.children:
            destroy(e)
        self.model.gui_elements.clear()
        self.model.player_labels.clear()
        self.model.lobby_buttons.clear()

    def display_error_message_screen(self, message, on_close_callback):
        self.clear_all_ui_elements()
        msg = Text(message, scale=1.2, origin=(0, 0), position=(0, 0.1),
                   parent=camera.ui, color=color.red)
        btn = Button('Back', scale=(0.3, 0.1), position=(0, -0.1),
                     parent=camera.ui, on_click=on_close_callback)
        self.model.gui_elements.extend([msg, btn])

    # Menu principale
    def show_main_menu_screen(self):
        self.clear_all_ui_elements()
        c = self.controller
        Text('Falls Game', scale=2, origin=(0, 0), position=(0, 0.3), parent=camera.ui)
        Button('Local Play (P2P)', scale=(0.3, 0.1), position=(0, 0.1), parent=camera.ui,
               on_click=self.show_local_mode_menu)
        Button('Online Play (Server)', scale=(0.3, 0.1), position=(0, -0.1), parent=camera.ui,
               on_click=c.start_online_client_session)

    def show_local_mode_menu(self):
        self.clear_all_ui_elements()
        c = self.controller
        Text("Local Game Mode", position=(0, 0.3), origin=(0, 0), scale=1.5, parent=camera.ui)
        Button('Host Game', scale=(0.3, 0.1), position=(0, 0.1), parent=camera.ui,
               on_click=c.start_local_host_session)
        Button('Join Game', scale=(0.3, 0.1), position=(0, -0.05), parent=camera.ui,
               on_click=c.start_local_client_session)
        Button('Back', scale=(0.3, 0.1), position=(0, -0.2), parent=camera.ui,
               on_click=self.show_main_menu_screen)

    # Ricerca host LAN (P2P)
    def show_lan_host_scan_screen(self, listener: DiscoveryListener):
        self.clear_all_ui_elements()
        c = self.controller
        title = Text("Available Hosts on LAN", scale=1.2, y=0.3, origin=(0, 0), parent=camera.ui)
        self.model.gui_elements.append(title)
        host_buttons = []

        def refresh_host_buttons():
            if not listener.running:
                if not host_buttons or not isinstance(host_buttons[-1], Text):
                    msg = Text("",
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
                        on_click=Func(c.connect_to_p2p_host, lambda ip=ip: ip, listener)
                    )
                    host_buttons.append(btn)

            back = Button("Back", y=-0.3, scale=(0.3, 0.08), parent=camera.ui,
                          on_click=lambda: c.back_to_scan_host_menu(listener))
            host_buttons.append(back)
            invoke(refresh_host_buttons, delay=1.5)

        refresh_host_buttons()

    # Online: schermata di attesa matchmaking
    def show_online_connecting_screen(self, on_cancel):
        status_text = Text("Connessione al matchmaking server...", scale=1.2, origin=(0, 0),
                           position=(0, 0.1), parent=camera.ui)
        back_btn = Button('Annulla', y=-0.2, scale=(0.3, 0.08), parent=camera.ui,
                          on_click=lambda: on_cancel())
        return status_text, back_btn

    def update_matchmaking_status_text(self, status_text, status):
        label = {
            "connecting": "Connessione al matchmaking server...",
            "queued": "In coda, in attesa di altri giocatori...",
        }.get(status, "...")
        if status_text:
            status_text.text = label

    # Lobby
    def show_lobby_screen(self):
        m, c = self.model, self.controller
        m.lobby_open = True
        self.clear_all_ui_elements()

        def refresh_lobby():
            if not m.lobby_open: return
            for lbl in m.player_labels: destroy(lbl)
            for btn in m.lobby_buttons: destroy(btn)
            m.player_labels.clear()
            m.lobby_buttons.clear()

            title = Text("Lobby", scale=1.2, y=0.4, parent=camera.ui)
            m.player_labels.append(title)

            nm = m.network_manager
            all_ids = sorted({nm.player_id} | m.connected_ids)
            for i, pid in enumerate(all_ids):
                is_me = pid == nm.player_id
                label = f"Player {pid}" + (" (tu)" if is_me else "")
                entry = Text(label, y=0.3 - i * 0.06, color=color.gold if is_me else color.white,
                            parent=camera.ui)
                m.player_labels.append(entry)

            list_bottom_y = 0.3 - len(all_ids) * 0.06

            if nm.is_host:
                start_btn = Button("Start Game", y=list_bottom_y - 0.12, scale=(0.3, 0.08),
                                   parent=camera.ui, on_click=c.begin_game)
                m.lobby_buttons.append(start_btn)

            back = Button("Leave", y=list_bottom_y - 0.24, scale=(0.3, 0.08), parent=camera.ui,
                         on_click=c.leave_lobby)
            m.lobby_buttons.append(back)
            invoke(refresh_lobby, delay=2.0)

        refresh_lobby()