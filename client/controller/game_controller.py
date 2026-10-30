import math
import os
import time
from ursina import *
from ursina import application

from config import SPAWN_HEIGHT
from player import Player
from floor import Floor, FloorCube, FLOOR_COUNT, FLOOR_HEIGHT, CUBE_SCALE, apply_floor_seed, distance_xz
from usefulFunctions import get_random_position
import network_p2p
import network_online
import rejoin_store
from .message_handler import MessageHandlers


class ControllerHelpers:
    """Small helpers: on-screen text and shutdown of network and threads."""

    def _say(self, text, y, tag=None, scale=1.2):
        """Centered text on screen. With a tag it can be removed later with _destroy_tagged."""
        return Text(text, scale=scale, origin=(0, 0), y=y, parent=camera.ui, tag=tag)

    def _destroy_tagged(self, tag):
        """Remove the temporary on-screen messages with this tag (pause, countdown, connecting...)."""
        for t in list(scene.entities):
            if getattr(t, 'tag', None) == tag:
                destroy(t)

    def _drop_network(self, deliberate=False):
        """Stop the network manager. deliberate=True: we sent LEAVE."""
        nm = self.model.network_manager
        if nm:
            if deliberate:
                nm.leaving_deliberately = True
            nm.stop()
            self.model.network_manager = None

    def _stop_threads(self, *names):
        for name in names:
            thread = getattr(self.model, name)
            if thread is not None:
                thread.stop()
                setattr(self.model, name, None)

    def _leave_network(self):
        """Close the network for good: no pause and no rejoin. The host warns the others first."""
        nm = self.model.network_manager
        if nm:
            if nm.is_host:
                nm.broadcast_message("host_left")
            rejoin_store.clear(nm.player_id)
            self._drop_network(deliberate=True)


class Sessions:
    """Menus -> P2P host/join, online matchmaking, and the lobby."""

    def start_local_host_session(self):
        m = self.model
        self.view.clear_all_ui_elements()
        m.network_manager, m.game_authority, m.broadcaster = network_p2p.start_p2p_host()
        if m.game_authority is None:         
            self._drop_network()
            self.view.display_error_message_screen(
                "Esiste gia' una sessione host attiva su questo PC. "
                "Usa 'Join Game' per unirti a quella, oppure chiudila prima di crearne una nuova.",
                self.view.show_local_mode_menu)
            return
        invoke(self.view.show_lobby_screen, delay=0.5)

    def start_local_client_session(self):
        self.view.clear_all_ui_elements()
        Text("Searching for LAN hosts...", scale=1.2, y=0.2, origin=(0, 0), parent=camera.ui)
        invoke(self.scan_lan_for_hosts, delay=0.5)

    def scan_lan_for_hosts(self):
        listener = network_p2p.start_host_discovery_scan()
        self.model.current_listener = listener
        self.view.show_lan_host_scan_screen(listener)

    def back_to_scan_host_menu(self, listener):
        listener.stop()
        self.view.show_local_mode_menu()

    def connect_to_p2p_host(self, ip_or_getter, port_or_getter, listener):
        """ip and port can be values or functions that return them."""
        m = self.model
        ip = ip_or_getter() if callable(ip_or_getter) else ip_or_getter
        port = port_or_getter() if callable(port_or_getter) else port_or_getter
        if not ip:
            return
        listener.stop()
        self.view.clear_all_ui_elements()
        self._say("Connessione in corso...", 0.1, 'p2p_connecting_text')
        try:
            m.network_manager = network_p2p.join_p2p_host(ip, port)
        except Exception:
            self.view.display_error_message_screen("Connection failed.", self.view.show_local_mode_menu)
            return
        invoke(self.poll_p2p_join_outcome, m.network_manager, time.time(), delay=0.2)

    def _fail_p2p_join(self, message):
        self._destroy_tagged('p2p_connecting_text')
        self._drop_network()
        self.view.display_error_message_screen(message, self.view.show_local_mode_menu)

    def poll_p2p_join_outcome(self, nm, started_at, timeout=15):
        if self.model.network_manager is not nm:
            return                         
        if nm.player_id is not None:
            self._destroy_tagged('p2p_connecting_text')
            self.view.show_lobby_screen()
        elif nm.join_rejected is not None:
            self._fail_p2p_join(nm.join_rejected)
        elif time.time() - started_at > timeout:
            self._fail_p2p_join("Impossibile connettersi all'host: nessuna risposta.")
        else:
            invoke(self.poll_p2p_join_outcome, nm, started_at, timeout, delay=0.2)

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
            return                            
        status = nm.online.status
        if status == "matched":
            if not m.is_in_active_match():
                invoke(self._show_lobby_if_waiting, delay=0.2)
        elif status == "error":
            self.view.display_error_message_screen(
                f"Connessione al matchmaking fallita: {nm.online.error}", self.view.show_main_menu_screen)
        else:
            self.view.update_matchmaking_status_text(status_text, status)
            invoke(self.poll_matchmaking_status, nm, status_text, delay=0.5)

    def _show_lobby_if_waiting(self):
        """Show the lobby."""
        if self.model.network_manager is not None and not self.model.is_in_active_match():
            self.view.show_lobby_screen()

    def cancel_online_session(self):
        self._drop_network()
        self.view.show_main_menu_screen()

    def begin_game(self):
        m = self.model
        m.network_manager.room.closed = True  # no new players from now on, only rejoins
        self._stop_threads("broadcaster")     # stop announcing the game on the LAN
        m.network_manager.send_message("start_game", m.game_authority.floor_seed)
        self.on_start_game(m.game_authority.floor_seed)

    def leave_lobby(self):
        m = self.model
        m.lobby_open = False
        self._leave_network()
        self._stop_threads("broadcaster")
        m.connected_ids.clear()
        self.view.clear_all_ui_elements()
        self.view.show_main_menu_screen()


class MatchFlow:
    """Start of the match (load level, ready, 3-2-1), the game world and the way back to the menu."""

    def load_level(self, floor_seed=None):
        m = self.model
        self.view.clear_all_ui_elements()
        self._build_world(floor_seed, m.network_manager.player_id)

        nm = m.network_manager                
        nm.send_message("spawn_player", nm.player_id, m.player.position.x, m.player.position.y, m.player.position.z)
        for pid in m.connected_ids:
            if pid not in m.other_players and pid != nm.player_id:
                self.on_spawn_player(pid, 0, SPAWN_HEIGHT, 0)

        if nm.is_host:                        # the host starts the countdown when everybody is ready
            m.ready_pids = set()
            m.expected_ready_count = len(nm.room.clients) + 1    
            m.countdown_started = False
            invoke(self._countdown_safety_net, delay=10)
        self._wait_for_ground_then_ready()

    def _wait_for_ground_then_ready(self, attempts_left=40):
        """Collider preparation."""
        m = self.model
        if m.player is None:
            return
        hit = raycast(m.player.world_position + Vec3(0, 0.5, 0), Vec3(0, -1, 0), distance=20, ignore=[m.player])
        if hit.hit or attempts_left <= 0:
            nm = m.network_manager
            if nm.is_host:
                self.on_player_ready(nm.player_id)
            else:
                nm.send_message("player_ready", nm.player_id)
        else:
            invoke(self._wait_for_ground_then_ready, attempts_left - 1, delay=0.05)

    def _begin_countdown(self):
        """Send countdown."""
        self.model.countdown_started = True
        self.model.network_manager.broadcast_message("start_countdown")
        self.on_start_countdown()

    def _countdown_safety_net(self):
        """Reserve: if the host does not start the countdown after 10s, it starts anyway."""
        m = self.model
        if not (m.network_manager and m.network_manager.is_host) or getattr(m, 'countdown_started', False):
            return
        print("[Game] Non tutti pronti dopo 10s, avvio comunque.")
        self._begin_countdown()

    def _update_countdown(self):
        if self._countdown_end is None:
            return
        remaining = math.ceil(self._countdown_end - time.time())
        if remaining == self._countdown_shown:
            return
        self._countdown_shown = remaining
        self._destroy_tagged('countdown_text')
        if remaining > 0:
            self._say(str(remaining), 0.15, 'countdown_text', scale=4)
        else:
            self._countdown_end = None
            self.game_start()

    def game_start(self):
        m = self.model
        if m.player is None:
            return                            
        m.level_loaded = True
        m.player.gravity = 1
        print("[Game] Via! (gravita' accesa)")
        if m.network_manager:
            m.network_manager.room.match_started = True

    def _build_world(self, floor_seed, player_id):
        """Floors, sky and our player, held still up high."""
        m = self.model
        m.player_died_reported = False
        apply_floor_seed(floor_seed)
        m.floors = Floor()
        m.sky = Entity(model="sphere", texture=os.path.join("assets", "sky.png"), scale=9999, double_sided=True)
        top_floor_y = (FLOOR_COUNT - 1) * FLOOR_HEIGHT
        for cube in m.floors.floor_cubes:     # only the top floor has colliders at the beginning for minimalize resources
            if abs(cube.y - top_floor_y) < 0.01:
                cube.collider = "box"
                cube.collider_added = True
        m.player = Player(get_random_position() + Vec3(0, 10, 0), f"Player {player_id}")
        m.player.gravity = 0
        camera.z = -5
        m.game_over = False

    def _destroy_world(self):
        m = self.model
        for entity in (m.player, m.floors, m.sky, *m.other_players.values()):
            if entity is not None:
                destroy(entity)
        m.other_players.clear()
        m.connected_ids.clear()
        m.player = m.floors = m.sky = None
        m.level_loaded = False
        m.game_over = False
        self._countdown_end = None

    def reset_floor(self):
        """Back to the main menu: remove the world, leave the network, stop the helper threads."""
        m = self.model
        m.lobby_open = False
        application.resume()                  # in case we were paused
        self._destroy_world()
        self.view.clear_all_ui_elements() 
        self._leave_network()
        m.game_authority = None
        self._stop_threads("broadcaster", "current_listener")
        invoke(self.view.show_main_menu_screen, delay=1)


class GameLoop:
    """What runs every frame: network messages, gameplay checks and keys."""

    def update_network(self):
        nm = self.model.network_manager
        if nm:
            nm.process_queue(self.handle_message)
            self.check_host_migration()
            self._remember()
        if self._rejoin_wait_until and time.time() > self._rejoin_wait_until: 
            self._rejoin_wait_until = None
            self._destroy_tagged('pause_text')
            application.resume()
        self._update_countdown()

    def update_gameplay(self):
        m = self.model
        nm = m.network_manager
        if not m.level_loaded or m.player is None or m.game_over:
            return
        player = m.player

        # Step on a block: the host will confirm it and tell the others. The client will not send repeated requests.
        for entity in scene.entities:
            if isinstance(entity, FloorCube):
                entity.updateColliders(player)
                if (entity.collider_added and not entity.has_activated and not entity.step_requested
                        and not entity.is_disappearing and entity.is_player_standing_on(player)):
                    entity.step_requested = True
                    self.request_block_step(entity.block_id)

        # Death: if we fall below the floor, the host will confirm it and tell the others. The client will not send repeated requests.
        if not m.player_died_reported and player.world_position.y < -20:
            m.player_died_reported = True
            player.gameOver()
            self.request_player_died()
            rejoin_store.clear(nm.player_id)                 

        if m.game_over or m.player is None or not nm or nm.player_id is None:
            return

        # Send our position to the host. The host will broadcast it to everybody.
        if nm.is_host and m.game_authority is not None:
            m.game_authority.handle_update_pos(nm.player_id, player.x, player.y, player.z)
            self.on_state_snapshot(m.game_authority.snapshot_payload())
        else:
            nm.send_message("update_pos", nm.player_id, player.x, player.y, player.z)

    def input(self, key):
        if key == 'escape' and self.model.level_loaded:
            self.reset_floor()


class HostMigration:
    """P2P only: the host disappears, the survivor with the lowest id takes over."""

    def check_host_migration(self):
        m = self.model
        nm = m.network_manager
        if m.migrating or not nm or not nm.allow_host_migration or nm.is_host or nm.host_alive():
            return
        m.migrating = True
        print("[Game] Host non risponde. Avvio la migrazione...")
        self.handle_host_migration()

    def handle_host_migration(self):
        """The survivor with the lowest id becomes the new host, the others look for it on the LAN."""
        m = self.model
        old_nm = m.network_manager
        my_id = old_nm.player_id
        survivor_ids = (set(m.other_players.keys()) - {getattr(m, 'host_player_id', None)}) | {my_id}

        if len(survivor_ids) <= 1:            # nobody else is left: we win
            print("[Game] Nessun altro giocatore rimasto: la partita finisce qui.")
            self.on_game_won(my_id)
            self._drop_network()
            m.migrating = False
            return

        status = self._say("Host perso: elezione in corso...", 0.15, 'migration_text')
        if min(survivor_ids) == my_id:        # we are the new host: we pass on the state we know
            cubes = m.floors.floor_cubes if m.floors is not None else []
            destroyed_ids = [b.block_id for b in cubes if b.has_activated]
            positions = {pid: [rp.x, rp.y, rp.z] for pid, rp in m.other_players.items()}
            if m.player is not None:
                positions[my_id] = [m.player.x, m.player.y, m.player.z]
            m.network_manager, m.game_authority, m.broadcaster = network_p2p.promote_to_host(
                old_nm, m.current_floor_seed, destroyed_ids, positions, survivor_ids)
            m.host_player_id = my_id
            print(f"[Game] Siamo il nuovo host (player {my_id}).")
            self.finish_migration(status)
        else:
            listener = network_p2p.start_host_discovery_scan()
            invoke(self.try_find_new_host, old_nm, my_id, listener, status, 10, delay=1.0)

    def try_find_new_host(self, old_nm, my_id, listener, status, attempts_left):
        """Look for the new host and reconnect with the same id."""
        m = self.model
        hosts = listener.get_hosts()
        if hosts:
            new_ip, new_host_pid, new_port = hosts[0]
            listener.stop()
            m.network_manager = network_p2p.reconnect_to_new_host(my_id, new_ip, new_port)
            m.host_player_id = new_host_pid
            print(f"[Game] Nuovo host trovato: {new_ip}:{new_port}. Riconnesso.")
            self.finish_migration(status)
        elif attempts_left > 0:
            invoke(self.try_find_new_host, old_nm, my_id, listener, status, attempts_left - 1, delay=1.0)
        else:
            listener.stop()
            destroy(status)
            self.view.display_error_message_screen(
                "Impossibile trovare il nuovo host. La connessione con la partita e' andata persa.", self.reset_floor)

    def finish_migration(self, status):
        # steps asked to the old host and never confirmed will be requested again
        destroy(status)
        m = self.model
        m.migrating = False
        application.resume()
        self._destroy_tagged('pause_text')
        if m.floors is not None:
            for cube in m.floors.floor_cubes:
                if cube.step_requested and not cube.has_activated and not cube.is_disappearing:
                    cube.step_requested = False


class Rejoin:
    """Coming back to a match after an interruption (same id, state sent by the host)."""

    def _remember(self):
        """A client in a match rewrites the rejoin file once a second."""
        m = self.model
        nm = m.network_manager
        if (nm is None or nm.is_host or nm.player_id is None or m.player is None or m.game_over
                or m.player_died_reported or getattr(m, 'rejoin_pending', False)):
            return
        now = time.time()
        if now - self._last_saved < 1.0:
            return
        self._last_saved = now
        rejoin_store.save(nm.player_id, f"Player {nm.player_id}", "online" if nm.online.status != "idle" else "p2p")

    def show_rejoin_offer_or_menu(self):
        """At startup, instead of the menu: if a match was interrupted recently, offer to rejoin it."""
        saved = rejoin_store.load()
        if saved is None:
            self.view.show_main_menu_screen()
            return
        self.view.clear_all_ui_elements()
        self._rejoin_prompt = [
            self._say("Hai lasciato una partita ancora in corso.\nVuoi rientrare?", 0.15, scale=1.3),
            Button('Rientra', y=0.0, scale=(0.3, 0.08), parent=camera.ui, on_click=lambda: self._start_rejoin(saved)),
            Button('No, vai al menu', y=-0.12, scale=(0.3, 0.08), parent=camera.ui,
                   on_click=lambda: self._decline_rejoin(saved)),
        ]

    def _clear_rejoin_prompt(self):
        for e in self._rejoin_prompt:
            destroy(e)
        self._rejoin_prompt = []

    def _decline_rejoin(self, saved):
        rejoin_store.clear(saved["player_id"])
        self._clear_rejoin_prompt()
        self.view.show_main_menu_screen()

    def _start_rejoin(self, saved):
        """P2P: find the host on the LAN. Online: ask the matchmaking. We rejoin with the same id."""
        m = self.model
        self._clear_rejoin_prompt()
        self._say("Rientro in corso...", 0.1, 'rejoin_connecting_text')
        m.rejoin_pending = True
        m.host_player_id = 0
        online = saved["mode"] == "online"
        if online:
            m.network_manager = network_online.start_online_rejoin(saved["player_id"])
        self._rejoin = {"saved": saved, "started": time.time(),
                        "listener": None if online else network_p2p.start_host_discovery_scan()}
        invoke(self._poll_rejoin, delay=0.2)

    def _poll_rejoin(self):
        """Ursina hides errors raised inside invoke: show them and go back to the menu."""
        try:
            self._poll_rejoin_step()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._abort_rejoin(f"errore interno ({e})")

    # Every 0.2 s: stop the rejoin if something went wrong, otherwise keep looking for the host.
    def _poll_rejoin_step(self):
        m, r = self.model, self._rejoin
        if r is None or not getattr(m, 'rejoin_pending', False):
            return                                            # cancelled, or already completed
        problem = self._rejoin_problem()
        if problem:
            return self._abort_rejoin(problem)
        if m.network_manager is None and r["listener"] is not None:
            self._join_first_host_found()                     # P2P only
        invoke(self._poll_rejoin, delay=0.2)

    # Why the rejoin failed, or None if it is going fine.
    def _rejoin_problem(self):
        r, nm = self._rejoin, self.model.network_manager
        if nm is not None:
            if nm.join_rejected is not None:                  # host or matchmaking refused us
                return nm.join_rejected
            if nm.online.status == "error":
                return nm.online.error or "matchmaking non raggiungibile."
            if nm.player_id not in (None, r["saved"]["player_id"]):
                return "non e' stato possibile ritrovare il tuo posto."
        if time.time() - r["started"] > 20:
            return "nessuna partita da riprendere trovata."
        return None

    # P2P: join the first host seen on the LAN, with our old id.
    def _join_first_host_found(self):
        r = self._rejoin
        hosts = r["listener"].get_hosts()
        if hosts:
            ip, _, port = hosts[0]
            self.model.network_manager = network_p2p.join_p2p_host(ip, port, rejoin_id=r["saved"]["player_id"])

    def _end_rejoin(self):
        r = self._rejoin
        self.model.rejoin_pending = False
        self._rejoin = None
        self._destroy_tagged('rejoin_connecting_text')
        if r is not None and r["listener"] is not None:
            r["listener"].stop()
        return r

    def _finish_rejoin(self, last):
        """Back in place and tell the host to resume everybody."""
        m = self.model
        nm = m.network_manager
        self._end_rejoin()
        if last is not None:
            m.player.position = self._safe_position(Vec3(*last))
            for cube in m.floors.floor_cubes:                 # colliders of the nearby blocks right now:
                if not cube.is_empty():                       
                    cube.updateColliders(m.player)
        if hasattr(m.player, "air_time"):
            m.player.air_time = 0                             
        m.player.gravity = 1
        m.level_loaded = True
        application.pause()                                  
        self._say("Rientro completato...", 0.1, 'pause_text', scale=1.5)
        self._rejoin_wait_until = time.time() + 8             
        for _ in range(3):                                    
            nm.send_message("rejoin_done", nm.player_id)
        print("[Game] Rientro completato: attendo la ripresa.")

    def _abort_rejoin(self, reason):
        r = self._end_rejoin()
        if r is not None:
            rejoin_store.clear(r["saved"]["player_id"])
        self._destroy_world()
        self._drop_network(deliberate=True)
        self.view.display_error_message_screen(f"Impossibile rientrare: {reason}", self.view.show_main_menu_screen)

    def _blocks_under(self, pos):
        return {c.block_id for c in self.model.floors.floor_cubes
                if distance_xz(pos, c.position) <= c.activation_distance + 1
                and abs(pos.y - (c.y + CUBE_SCALE / 2)) < 1.5}

    def _safe_position(self, pos):
        """If there is no ground under `pos` anymore, put the player on the nearest existing block."""
        alive = [c for c in self.model.floors.floor_cubes if not c.is_empty()]

        top = lambda c: c.y + CUBE_SCALE / 2                  # height of the upper face of a block
        if any(distance_xz(pos, c.position) <= CUBE_SCALE / 2 + 0.5 and top(c) <= pos.y + 1.5 for c in alive):
            return pos                                        # there is still ground
        pool = [c for c in alive if abs(pos.y - top(c)) < 1.5] or [c for c in alive if top(c) <= pos.y + 1.5]
        if not pool:
            return pos
        best = min(pool, key=lambda c: (distance_xz(pos, c.position), -top(c)))
        print(f"[Game] Sotto la posizione di rientro non c'e' piu' terreno: ti posiamo sul blocco {best.block_id}.")
        return Vec3(best.x, top(best), best.z)


class GameController(ControllerHelpers, Sessions, MatchFlow, GameLoop, HostMigration, Rejoin, MessageHandlers):
    """The whole controller: the parts above plus the handlers of the network messages."""

    def __init__(self, model, view):
        self.model = model
        self.view = view
        self._last_saved = 0.0           # last time the rejoin file was written
        self._rejoin = None              # state of the rejoin in progress (None = no rejoin)
        self._rejoin_prompt = []         # widgets of the "do you want to rejoin?" offer
        self._rejoin_wait_until = None   # after rejoining: time limit to wait for the resume message
        self._countdown_end = None       # when the 3-2-1 ends (None = no countdown)
        self._countdown_shown = None     # the number currently on screen