import os
import time
from ursina import *

from config import SPAWN_HEIGHT
from player import Player, RemotePlayer
from floor import Floor, FloorCube, FLOOR_COUNT, FLOOR_HEIGHT, apply_floor_seed
from usefulFunctions import get_random_position
from network import NetworkManager
from network_discovery import DiscoveryBroadcaster, DiscoveryListener
import network_p2p
import network_online
from game_authority import GameAuthority


class GameController:
    def __init__(self, model, view):
        self.model = model
        self.view = view

        self.rpc_registry = {
            "spawn_player": self.rpc_spawn_player,
            "update_pos": self.rpc_update_pos,
            "state_snapshot": self.rpc_state_snapshot,
            "block_step": self.rpc_block_step,
            "block_destroyed": self.rpc_block_destroyed,
            "sync_state": self.rpc_sync_state,
            "player_left": self.rpc_player_left,
            "player_died": self.rpc_player_died,
            "player_removed": self.rpc_player_removed,
            "game_won": self.rpc_game_won,
            "start_game": self.rpc_start_game,
            "game_pause": self.rpc_game_pause,
        }

    # RPC: giocatori, spawn, posizione
    def rpc_spawn_player(self, pid, x, y, z, status="alive", matchId=None):
        """Ricevuto quando un altro giocatore si unisce/spawna: creiamo la sua RemotePlayer."""
        m = self.model
        nm = m.network_manager
        if nm and pid != nm.player_id:
            m.connected_ids.add(pid)

        if m.level_loaded:
            if pid not in m.other_players and pid != nm.player_id:
                print(f"[Game] Spawning Remote Player {pid}")
                new_p = RemotePlayer(position=Vec3(x, y, z), username=f"Player {pid}")
                m.other_players[pid] = new_p

    def rpc_update_pos(self, pid, x, y, z):
        """Segnalazione della posizione di un giocatore all'autorita' di gioco (host P2P o
        game instance online). Solo l'host applica questo dato alla propria GameAuthority."""
        m = self.model
        nm = m.network_manager
        if m.game_authority is not None and nm and nm.is_host:
            m.game_authority.handle_update_pos(pid, x, y, z)

    def rpc_state_snapshot(self, positions):
        """Stato consolidato di TUTTI i giocatori, applicato a intervalli fissi dall'autorita'
        di gioco. positions e' un dict {pid: [x,y,z]} — le chiavi arrivano come stringhe
        perche' JSON serializza sempre le chiavi dict come testo."""
        m = self.model
        nm = m.network_manager
        for pid_str, pos in positions.items():
            pid = int(pid_str)
            if nm and pid == nm.player_id:
                continue  # non spostiamo noi stessi in base al nostro stesso pacchetto
            if pid in m.other_players:
                m.other_players[pid].position = Vec3(*pos)
            elif m.level_loaded:
                self.rpc_spawn_player(pid, *pos)

    # RPC: floor
    def rpc_block_step(self, pid, block_id):
        """SOLO l'host P2P valida questa richiesta. Un client normale non dovrebbe mai
        riceverlo, ma per sicurezza controlliamo comunque prima di agire."""
        m = self.model
        nm = m.network_manager
        if not (nm and nm.is_host and m.game_authority is not None):
            return
        result = m.game_authority.handle_block_step(pid, block_id)
        if result is None:
            return  
        self.apply_block_destroyed(result["block_id"], result["pid"])
        nm.broadcast_rpc("block_destroyed", result["block_id"], result["pid"])

    def rpc_block_destroyed(self, block_id, pid):
        """Evento CANONICO: l'autorita' ha confermato che questo blocco e' stato distrutto."""
        self.apply_block_destroyed(block_id, pid)

    def apply_block_destroyed(self, block_id, pid):
        """Distrugge visivamente il blocco e applica l'eventuale effetto SOLO se il giocatore
        che lo ha attivato siamo noi."""
        m = self.model
        if m.floors is None:
            return
        cube = m.floors.blocks_by_id.get(block_id)
        if cube is None:
            return
        nm = m.network_manager
        is_me = nm and pid == nm.player_id
        cube.on_authoritative_step(m.player if is_me else None)

    def rpc_sync_state(self, destroyed_ids, positions):
        """Ricevuto solo da chi si e' appena unito/riconnesso a una partita in corso: ci
        mette in pari con lo stato che l'autorita' ha gia' accumulato."""
        m = self.model
        if m.floors is not None:
            for block_id in destroyed_ids:
                cube = m.floors.blocks_by_id.get(block_id)
                if cube is not None and not cube.is_disappearing and not cube.has_activated:
                    cube.force_destroy()
        self.rpc_state_snapshot(positions)

    # RPC: vita/morte, vittoria
    def request_player_died(self):
        """Il giocatore locale e' caduto/eliminato: lo segnaliamo all'autorita' di gioco."""
        m = self.model
        nm = m.network_manager
        if not nm or nm.player_id is None:
            return
        if nm.is_host:
            self.rpc_player_died(nm.player_id)
        else:
            nm.send_rpc("player_died", nm.player_id)

    def rpc_player_died(self, pid):
        """SOLO l'host/server valida chi e' ancora vivo."""
        m = self.model
        nm = m.network_manager
        if not (nm and nm.is_host and m.game_authority is not None):
            return
        game_over, winner_pid = m.game_authority.handle_player_died(pid)
        self.apply_player_removed(pid)
        nm.broadcast_rpc("player_removed", pid)
        if game_over:
            self.apply_game_won(winner_pid)
            nm.broadcast_rpc("game_won", winner_pid)

    def apply_player_removed(self, pid):
        """Rimuove il 'fantasma' visivo di un giocatore appena morto/eliminato."""
        m = self.model
        if pid in m.other_players:
            destroy(m.other_players[pid])
            del m.other_players[pid]

    def rpc_player_removed(self, pid):
        self.apply_player_removed(pid)

    def rpc_game_won(self, winner_pid):
        self.apply_game_won(winner_pid)

    def apply_game_won(self, winner_pid):
        """Mostra la schermata di fine partita. winner_pid puo' essere None (nessuno rimasto
        vivo — es. partita da soli, o coincidenza in cui muoiono tutti insieme): la partita
        finisce comunque, semplicemente senza dichiarare un vincitore specifico."""
        m = self.model
        if m.game_over:
            return
        m.game_over = True
        mouse.locked = False
        mouse.visible = True

        if m.player is not None:
            destroy(m.player)
            m.player = None

        self.view.clear_all_ui_elements()
        nm = m.network_manager
        if winner_pid is None:
            message = "Game Over"
        else:
            is_me = nm and winner_pid == nm.player_id
            message = "Hai vinto!" if is_me else f"Ha vinto il giocatore {winner_pid}!"
        Text(message, scale=2.5, origin=(0, 0), position=(0, 0.1), parent=camera.ui, color=color.gold)
        Button('Torna al menu', y=-0.15, scale=(0.3, 0.08), parent=camera.ui, on_click=self.reset_floor)

    def rpc_player_left(self, pid):
        """Ricevuto quando un giocatore si disconnette."""
        m = self.model
        if pid in m.other_players:
            print(f"[Game] Player {pid} Left")
            destroy(m.other_players[pid])
            del m.other_players[pid]
        if m.game_authority is not None:
            game_over, winner_pid = m.game_authority.remove_player(pid)
            if game_over and m.network_manager:
                self.apply_game_won(winner_pid)
                m.network_manager.broadcast_rpc("game_won", winner_pid)

    # RPC: ciclo della partita
    def rpc_start_game(self, seed=None):
        m = self.model
        print("[Game] Host started the game")
        m.lobby_open = False
        m.current_floor_seed = seed
        self.load_level(seed)

    def rpc_game_pause(self, is_paused, pid=None):
        self.model.game_paused = is_paused
        if is_paused:
            Text(f"Waiting for Player {pid} to reconnect...", scale=1.5, origin=(0, 0), y=0.1, tag='pause_text')
        else:
            print("[Game] RESUMED!")
            for t in scene.entities:
                if hasattr(t, 'tag') and t.tag == 'pause_text':
                    destroy(t)

    def handle_rpc(self, data):
        method = data.get("method")
        args = data.get("args", [])
        if method in self.rpc_registry:
            self.rpc_registry[method](*args)

    def request_block_step(self, block_id):
        m = self.model
        nm = m.network_manager
        if not nm or nm.player_id is None:
            return
        if nm.is_host:
            self.rpc_block_step(nm.player_id, block_id)
        else:
            nm.send_rpc("block_step", nm.player_id, block_id)

    # Ciclo di vita della partita
    def load_level(self, floor_seed=None):
        m = self.model
        self.view.clear_all_ui_elements()
        m.player_died_reported = False

        apply_floor_seed(floor_seed)
        m.floors = Floor()
        m.sky = Entity(model="sphere", texture=os.path.join("assets", "sky.png"),
                        scale=9999, double_sided=True)

        start_pos = get_random_position()
        top_floor_y = (FLOOR_COUNT - 1) * FLOOR_HEIGHT
        for cube in m.floors.floor_cubes:
            if (abs(cube.x - start_pos.x) < 0.01 and abs(cube.z - start_pos.z) < 0.01
                    and abs(cube.y - top_floor_y) < 0.01):
                cube.collider = "box"
                cube.collider_added = True
                break

        m.player = Player(start_pos, f"Player {m.network_manager.player_id}")
        m.player.gravity = 0
        camera.z = -5
        m.game_over = False

        invoke(self.game_start, delay=0.1)

    def game_start(self):
        m = self.model
        m.level_loaded = True
        m.player.gravity = 1

        for pid in m.connected_ids:
            if pid not in m.other_players and pid != m.network_manager.player_id:
                self.rpc_spawn_player(pid, 0, SPAWN_HEIGHT, 0)

        if m.network_manager:
            p = m.player
            m.network_manager.send_rpc("spawn_player", m.network_manager.player_id,
                                        p.position.x, p.position.y, p.position.z)

    def reset_floor(self):
        m = self.model
        if m.player is not None: destroy(m.player)
        if m.floors is not None: destroy(m.floors)
        if m.sky is not None: destroy(m.sky)
        for p in m.other_players.values(): destroy(p)

        m.other_players.clear()
        m.connected_ids.clear()
        m.player = None
        m.floors = None
        m.sky = None
        m.level_loaded = False
        m.game_over = False

        if m.network_manager is not None:
            m.network_manager.stop()
            m.network_manager = None
        m.game_authority = None
        if m.broadcaster is not None:
            m.broadcaster.stop()
            m.broadcaster = None
        if m.current_listener is not None:
            m.current_listener.stop()
            m.current_listener = None

        invoke(self.view.show_main_menu_screen, delay=1)

    # Sessioni di rete: P2P
    def start_local_host_session(self):
        m = self.model
        self.view.clear_all_ui_elements()
        m.network_manager, m.game_authority, m.broadcaster = network_p2p.start_p2p_host()
        invoke(self.view.show_lobby_screen, delay=0.5)

    def start_local_client_session(self):
        self.view.clear_all_ui_elements()
        Text("Searching for LAN hosts...", scale=1.2, y=0.2, origin=(0, 0), parent=camera.ui)
        invoke(self.scan_lan_for_hosts, delay=0.5)

    def scan_lan_for_hosts(self):
        listener = DiscoveryListener()
        listener.start()
        self.model.current_listener = listener
        self.view.show_lan_host_scan_screen(listener)

    def back_to_scan_host_menu(self, listener):
        listener.stop()
        self.view.show_local_mode_menu()

    def connect_to_p2p_host(self, ip_or_getter, listener):
        m = self.model
        ip = ip_or_getter() if callable(ip_or_getter) else ip_or_getter
        if not ip:
            return
        listener.stop()
        self.view.clear_all_ui_elements()
        try:
            m.network_manager = network_p2p.join_p2p_host(ip)
            invoke(self.view.show_lobby_screen, delay=0.5)
        except Exception:
            self.view.display_error_message_screen("Connection failed.", self.view.show_local_mode_menu)

    # Sessioni di rete: Online (matchmaking + server dedicato)
    def start_online_client_session(self):
        m = self.model
        self.view.clear_all_ui_elements()
        status_text, back_btn = self.view.show_online_connecting_screen(self.cancel_online_session)
        m.gui_elements.extend([status_text, back_btn])

        m.network_manager = network_online.start_online_session()
        invoke(self.poll_matchmaking_status, m.network_manager, status_text, delay=0.5)

    def poll_matchmaking_status(self, nm, status_text):
        m = self.model
        if m.network_manager is not nm:
            return  # nel frattempo si e' tornati al menu: questa sessione non e' piu' attiva

        status = nm.matchmaking_status
        if status == "matched":
            if not m.is_in_active_match():
                invoke(self.view.show_lobby_screen, delay=0.2)
            return
        elif status == "error":
            self.view.display_error_message_screen(
                f"Connessione al matchmaking fallita: {nm.matchmaking_error}",
                self.view.show_main_menu_screen)
            return

        self.view.update_matchmaking_status_text(status_text, status)
        invoke(self.poll_matchmaking_status, nm, status_text, delay=0.5)

    def cancel_online_session(self):
        m = self.model
        if m.network_manager:
            m.network_manager.stop()
            m.network_manager = None
        self.view.show_main_menu_screen()

    # Lobby
    def begin_game(self):
        m = self.model
        m.network_manager.send_rpc("start_game", m.game_authority.floor_seed)
        self.rpc_start_game(m.game_authority.floor_seed)  # applicato localmente per l'host

    def leave_lobby(self):
        m = self.model
        m.lobby_open = False
        if m.network_manager:
            m.network_manager.stop()
            m.network_manager = None
        if m.broadcaster:
            m.broadcaster.stop()
            m.broadcaster = None
        m.connected_ids.clear()
        self.view.clear_all_ui_elements()
        self.view.show_main_menu_screen()

    # Migrazione dell'host (solo P2P)
    def check_host_migration(self):
        m = self.model
        nm = m.network_manager
        if m.migrating or not nm:
            return
        if not nm.allow_host_migration or nm.is_host:
            return
        if nm.host_alive():
            return
        m.migrating = True
        print("[Game] Host non risponde. Avvio la migrazione...")
        self.handle_host_migration()

    def handle_host_migration(self):
        m = self.model
        old_nm = m.network_manager
        my_id = old_nm.player_id
        survivor_ids = set(m.other_players.keys()) | {my_id}
        new_host_id = min(survivor_ids)

        status = Text("Host perso: elezione in corso...", scale=1.2, origin=(0, 0), y=0.15,
                      parent=camera.ui, tag='migration_text')

        if new_host_id == my_id:
            destroyed_ids = [b.block_id for b in (m.floors.floor_cubes if m.floors is not None else [])
                              if b.has_activated]
            positions = {pid: [rp.x, rp.y, rp.z] for pid, rp in m.other_players.items()}
            if m.player is not None:
                positions[my_id] = [m.player.x, m.player.y, m.player.z]

            m.network_manager, m.game_authority, m.broadcaster = network_p2p.promote_to_host(
                old_nm, m.current_floor_seed, destroyed_ids, positions, survivor_ids)
            print(f"[Game] Siamo il nuovo host (player {my_id}).")
            self.finish_migration(status)
        else:
            listener = network_p2p.start_host_discovery_scan()
            invoke(self.try_find_new_host, old_nm, my_id, listener, status, 10, delay=1.0)

    def try_find_new_host(self, old_nm, my_id, listener, status, attempts_left):
        m = self.model
        hosts = listener.get_hosts()
        if hosts:
            new_ip, _ = hosts[0]
            listener.stop()
            m.network_manager = network_p2p.reconnect_to_new_host(my_id, new_ip)
            print(f"[Game] Nuovo host trovato: {new_ip}. Riconnesso.")
            self.finish_migration(status)
        elif attempts_left > 0:
            invoke(self.try_find_new_host, old_nm, my_id, listener, status, attempts_left - 1, delay=1.0)
        else:
            listener.stop()
            destroy(status)
            self.view.display_error_message_screen(
                "Impossibile trovare il nuovo host. La connessione con la partita e' andata persa.",
                self.reset_floor)

    def finish_migration(self, status):
        destroy(status)
        self.model.migrating = False

    # Ciclo di gioco (chiamato da main.py ogni frame)
    def update(self):
        m = self.model
        nm = m.network_manager
        if nm:
            nm.process_queue(self.handle_rpc)
            self.check_host_migration()

        if not m.level_loaded or m.player is None or m.game_over or m.game_paused:
            return

        player = m.player
        for entity in scene.entities:
            if isinstance(entity, FloorCube):
                entity.updateColliders(player)
                if (entity.collider_added and not entity.has_activated
                        and not entity.step_requested and not entity.is_disappearing):
                    if entity.is_player_standing_on(player):
                        entity.step_requested = True
                        self.request_block_step(entity.block_id)

        if not m.player_died_reported and player.world_position.y < -20:
            m.player_died_reported = True
            player.gameOver()
            self.request_player_died()

        if m.game_over or m.player is None:
            return

        if nm and nm.player_id is not None:
            nm.send_rpc("update_pos", nm.player_id, player.x, player.y, player.z)
            if nm.is_host and m.game_authority is not None:
                m.game_authority.handle_update_pos(nm.player_id, player.x, player.y, player.z)
                self.rpc_state_snapshot(m.game_authority.snapshot_payload())

    def input(self, key):
        if key == 'escape' and self.model.level_loaded:
            self.reset_floor()