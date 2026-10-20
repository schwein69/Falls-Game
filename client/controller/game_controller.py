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
def _guarded(fn):
    """Stampa per intero le eccezioni di update: Ursina le assorbe e il gioco si bloccherebbe muto."""
    def wrapper(self):
        try:
            fn(self)
        except Exception:
            import traceback
            print(f"[ERRORE] Eccezione in GameController.{fn.__name__}():")
            traceback.print_exc()
    wrapper.__name__ = fn.__name__
    return wrapper

class GameController(MessageHandlers):
    def __init__(self, model, view):
        self.model = model
        self.view = view
        self._last_saved = 0.0       # ultimo salvataggio del file di rientro
        self._rejoin = None          # il rientro in corso
        self._rejoin_prompt = []     # gli elementi della proposta "Rientra?"
        self._rejoin_wait_until = None   # ripiego: dopo il rientro, se la ripresa non arriva si riparte da soli
        self._countdown_end = None   # istante in cui finisce il 3-2-1
        self._countdown_shown = None

    # ---------------- utilita' ----------------
    def _destroy_tagged(self, tag):
        """Distrugge i messaggi temporanei a schermo con questo tag (pausa, countdown, connessione...)."""
        for t in list(scene.entities):
            if getattr(t, 'tag', None) == tag:
                destroy(t)

    def _leave_network(self):
        """Chiude la rete (avvisando gli altri se siamo l'host). LEAVE: definitivo, niente pausa ne' rientro."""
        m = self.model
        nm = m.network_manager
        if nm:
            if nm.is_host:
                nm.broadcast_message("host_left")
            nm.leaving_deliberately = True
            rejoin_store.clear(nm.player_id)
            nm.stop()
            m.network_manager = None

    # ---------------- avvio della partita ----------------
    def load_level(self, floor_seed=None):
        m = self.model
        self.view.clear_all_ui_elements()
        self._build_world(floor_seed, m.network_manager.player_id)

        nm = m.network_manager                # annunciamo la nostra posizione e mostriamo chi gia' conosciamo
        nm.send_message("spawn_player", nm.player_id, m.player.position.x, m.player.position.y, m.player.position.z)
        for pid in m.connected_ids:
            if pid not in m.other_players and pid != nm.player_id:
                self.on_spawn_player(pid, 0, SPAWN_HEIGHT, 0)

        if nm.is_host:                        # partenza sincronizzata: aspetta tutti i "pronto"
            m.ready_pids = set()
            m.expected_ready_count = len(nm.room.clients) + 1     # clients registrati (non connected_ids) + l'host
            m.countdown_started = False
            invoke(self._countdown_safety_net, delay=10)
        self._wait_for_ground_then_ready()

    def _wait_for_ground_then_ready(self, attempts_left=40):
        """Segnala 'pronto' solo con un collider davvero sotto i piedi (rete di sicurezza: ~2 s)."""
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
        """Manda il 3-2-1 a tutti e lo applica in locale (l'host non riceve i propri broadcast)."""
        self.model.countdown_started = True
        self.model.network_manager.broadcast_message("start_countdown")
        self.on_start_countdown()

    def _countdown_safety_net(self):
        """Dopo 10 s si parte comunque con chi c'e' (qualcuno potrebbe non confermare mai)."""
        m = self.model
        if not (m.network_manager and m.network_manager.is_host) or getattr(m, 'countdown_started', False):
            return
        print("[Game] Non tutti pronti dopo 10s, avvio comunque (rete di sicurezza).")
        self._begin_countdown()

    def _update_countdown(self):
        """Ogni frame: mostra il numero in base all'orologio, e allo scadere accende la gravita'."""
        if self._countdown_end is None:
            return
        remaining = math.ceil(self._countdown_end - time.time())
        if remaining == self._countdown_shown:
            return
        self._countdown_shown = remaining
        self._destroy_tagged('countdown_text')
        if remaining > 0:
            Text(str(remaining), scale=4, origin=(0, 0), y=0.15, parent=camera.ui, tag='countdown_text')
        else:
            self._countdown_end = None
            self.game_start()

    def game_start(self):
        m = self.model
        if m.player is None:
            return                            # nel frattempo siamo usciti
        m.level_loaded = True
        m.player.gravity = 1
        print("[Game] Via! (gravita' accesa)")
        if m.network_manager:
            m.network_manager.room.match_started = True

    def reset_floor(self):
        m = self.model
        m.lobby_open = False
        self.view.clear_all_ui_elements()
        application.resume()                  # in pausa l'invoke del menu non partirebbe
        self._destroy_world()
        self._leave_network()
        m.game_authority = None
        for attr in ("broadcaster", "current_listener"):
            if getattr(m, attr) is not None:
                getattr(m, attr).stop()
                setattr(m, attr, None)
        invoke(self.view.show_main_menu_screen, delay=1)

    # ---------------- sessioni P2P ----------------
    def start_local_host_session(self):
        m = self.model
        self.view.clear_all_ui_elements()
        m.network_manager, m.game_authority, m.broadcaster = network_p2p.start_p2p_host()
        if m.game_authority is None:
            if m.network_manager:
                m.network_manager.stop()
                m.network_manager = None
            self.view.display_error_message_screen(
                "Esiste gia' una sessione host attiva su questo PC (la porta P2P e' occupata). "
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
        m = self.model
        ip = ip_or_getter() if callable(ip_or_getter) else ip_or_getter
        port = port_or_getter() if callable(port_or_getter) else port_or_getter
        if not ip:
            return
        listener.stop()
        self.view.clear_all_ui_elements()
        Text("Connessione in corso...", scale=1.2, origin=(0, 0), y=0.1, parent=camera.ui, tag='p2p_connecting_text')
        try:
            m.network_manager = network_p2p.join_p2p_host(ip, port)
        except Exception:
            self.view.display_error_message_screen("Connection failed.", self.view.show_local_mode_menu)
            return
        invoke(self.poll_p2p_join_outcome, m.network_manager, time.time(), delay=0.2)

    def _fail_p2p_join(self, nm, message):
        self._destroy_tagged('p2p_connecting_text')
        nm.stop()
        self.model.network_manager = None
        self.view.display_error_message_screen(message, self.view.show_local_mode_menu)

    def poll_p2p_join_outcome(self, nm, started_at, timeout=15):
        m = self.model
        if m.network_manager is not nm:
            return                            # nel frattempo siamo tornati al menu
        if nm.player_id is not None:
            self._destroy_tagged('p2p_connecting_text')
            self.view.show_lobby_screen()
        elif nm.join_rejected is not None:
            self._fail_p2p_join(nm, nm.join_rejected)
        elif time.time() - started_at > timeout:
            self._fail_p2p_join(nm, "Impossibile connettersi all'host: nessuna risposta.")
        else:
            invoke(self.poll_p2p_join_outcome, nm, started_at, timeout, delay=0.2)

    # ---------------- sessioni Online ----------------
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
            return                            # nel frattempo siamo tornati al menu
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
        """Lobby 0,2 s dopo l'abbinamento, ma non sopra una partita gia' partita: la View pulisce
        tutta l'interfaccia, countdown compreso."""
        if self.model.network_manager is not None and not self.model.is_in_active_match():
            self.view.show_lobby_screen()

    def cancel_online_session(self):
        m = self.model
        if m.network_manager:
            m.network_manager.stop()
            m.network_manager = None
        self.view.show_main_menu_screen()

    # ---------------- lobby ----------------
    def begin_game(self):
        m = self.model
        m.network_manager.room.closed = True                
        if m.broadcaster:
            m.broadcaster.stop()                            
            m.broadcaster = None
        m.network_manager.send_message("start_game", m.game_authority.floor_seed)
        self.on_start_game(m.game_authority.floor_seed)     

    def leave_lobby(self):
        m = self.model
        m.lobby_open = False
        self._leave_network()
        if m.broadcaster:
            m.broadcaster.stop()
            m.broadcaster = None
        m.connected_ids.clear()
        self.view.clear_all_ui_elements()
        self.view.show_main_menu_screen()

    def check_host_migration(self):
        m = self.model
        nm = m.network_manager
        if m.migrating or not nm or not nm.allow_host_migration or nm.is_host or nm.host_alive():
            return
        m.migrating = True
        print("[Game] Host non risponde. Avvio la migrazione...")
        self.handle_host_migration()

    def handle_host_migration(self):
        m = self.model
        old_nm = m.network_manager
        my_id = old_nm.player_id
        survivor_ids = (set(m.other_players.keys()) - {getattr(m, 'host_player_id', None)}) | {my_id}

        if len(survivor_ids) <= 1:
            print("[Game] Nessun altro giocatore rimasto: la partita finisce qui.")
            self.on_game_won(my_id)
            old_nm.stop()
            m.network_manager = None
            m.migrating = False
            return

        status = Text("Host perso: elezione in corso...", scale=1.2, origin=(0, 0), y=0.15,
                      parent=camera.ui, tag='migration_text')
        if min(survivor_ids) == my_id:        # il nuovo host e' il sopravvissuto con l'id piu' basso
            destroyed_ids = [b.block_id for b in (m.floors.floor_cubes if m.floors is not None else [])
                             if b.has_activated]
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
        destroy(status)
        m = self.model
        m.migrating = False
        application.resume()
        self._destroy_tagged('pause_text')
        if m.floors is not None:
            for cube in m.floors.floor_cubes:
                if cube.step_requested and not cube.has_activated and not cube.is_disappearing:
                    cube.step_requested = False

    # ---------------- ciclo di gioco ----------------
    @_guarded
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

    @_guarded
    def update_gameplay(self):
        m = self.model
        nm = m.network_manager
        if not m.level_loaded or m.player is None or m.game_over:
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
            rejoin_store.clear(nm.player_id)                  # eliminati: niente rientro

        if m.game_over or m.player is None:
            return
        if nm and nm.player_id is not None:
            nm.send_message("update_pos", nm.player_id, player.x, player.y, player.z)
            if nm.is_host and m.game_authority is not None:
                m.game_authority.handle_update_pos(nm.player_id, player.x, player.y, player.z)
                self.on_state_snapshot(m.game_authority.snapshot_payload())

    def input(self, key):
        if key == 'escape' and self.model.level_loaded:
            self.reset_floor()

    # ---------------- il mondo di gioco ----------------
    def _build_world(self, floor_seed, player_id):
        """Pavimento, cielo e il nostro giocatore, fermo in alto (gravita' spenta fino al via)."""
        m = self.model
        m.player_died_reported = False
        apply_floor_seed(floor_seed)
        m.floors = Floor()
        m.sky = Entity(model="sphere", texture=os.path.join("assets", "sky.png"), scale=9999, double_sided=True)
        top_floor_y = (FLOOR_COUNT - 1) * FLOOR_HEIGHT
        for cube in m.floors.floor_cubes:
            if abs(cube.y - top_floor_y) < 0.01:
                cube.collider = "box"
                cube.collider_added = True
        # Spawn ben in alto, oltre il raggio di attivazione dei blocchi: niente ambiguita' nel 3-2-1.
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

    def _remember(self):
        """Ogni secondo, da client in partita, aggiorna il file di rientro."""
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
        """All'avvio, al posto del menu: se c'e' una partita interrotta da poco propone di rientrare."""
        saved = rejoin_store.load()
        if saved is None:
            self.view.show_main_menu_screen()
            return
        self.view.clear_all_ui_elements()
        self._rejoin_prompt = [
            Text("Hai lasciato una partita ancora in corso.\nVuoi rientrare?", scale=1.3, origin=(0, 0),
                 y=0.15, parent=camera.ui),
            Button('Rientra', y=0.0, scale=(0.3, 0.08), parent=camera.ui,
                   on_click=lambda: self._start_rejoin(saved)),
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
        """P2P: cerca l'host sulla LAN. Online: chiede al matchmaking dov'e' la partita.
        Poi si rientra con lo stesso id; stato e seed li manda il server."""
        m = self.model
        self._clear_rejoin_prompt()
        Text("Rientro in corso...", scale=1.2, origin=(0, 0), y=0.1, parent=camera.ui, tag='rejoin_connecting_text')
        m.rejoin_pending = True
        m.host_player_id = 0
        online = saved["mode"] == "online"
        if online:
            m.network_manager = network_online.start_online_rejoin(saved["player_id"])
        self._rejoin = {"saved": saved, "started": time.time(), "tried": set(), "error": None, "joined_at": None,
                        "listener": None if online else network_p2p.start_host_discovery_scan()}
        invoke(self._poll_rejoin, delay=0.2)

    def _poll_rejoin(self):
        """Un errore dentro un invoke Ursina lo assorbe in silenzio: qui riporta al menu con un messaggio."""
        try:
            self._poll_rejoin_step()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._abort_rejoin(f"errore interno ({e})")

    def _poll_rejoin_step(self):
        m, r = self.model, self._rejoin
        if r is None or not getattr(m, 'rejoin_pending', False):
            return                                            # annullato, oppure completato
        nm, wanted = m.network_manager, r["saved"]["player_id"]
        if nm is not None and nm.player_id is not None:       # riconosciuti: manca solo lo stato
            if nm.player_id != wanted:
                return self._abort_rejoin("non e' stato possibile ritrovare il tuo posto.")
            r["joined_at"] = r["joined_at"] or time.time()
            if time.time() - r["joined_at"] > 5:
                return self._abort_rejoin("lo stato della partita non e' arrivato.")
        elif nm is not None and nm.join_rejected is not None:
            if r["listener"] is None:                         # Online: non c'e' un altro posto da provare
                return self._abort_rejoin(nm.join_rejected)
            r["error"] = nm.join_rejected                     # P2P: non era questo l'host, si prova il prossimo
            nm.stop()
            m.network_manager = None
        elif nm is not None and nm.online.status == "error":
            return self._abort_rejoin(nm.online.error or "matchmaking non raggiungibile.")
        elif nm is None and r["listener"] is not None:        # P2P: host trovati sulla LAN
            fresh = [(ip, port) for ip, _, port in r["listener"].get_hosts() if (ip, port) not in r["tried"]]
            if fresh:
                r["tried"].add(fresh[0])
                m.network_manager = network_p2p.join_p2p_host(*fresh[0], rejoin_id=wanted)
            elif r["tried"]:                                  # provati tutti
                return self._abort_rejoin(r["error"] or "nessun host a cui rientrare.")
        if time.time() - r["started"] > 20:
            return self._abort_rejoin("nessuna partita da riprendere trovata.")
        invoke(self._poll_rejoin, delay=0.2)

    def _end_rejoin(self):
        m, r = self.model, self._rejoin
        m.rejoin_pending = False
        self._rejoin = None
        self._destroy_tagged('rejoin_connecting_text')
        if r is not None and r["listener"] is not None:
            r["listener"].stop()
        return r

    def _finish_rejoin(self, last):
        """Giocatore al suo posto: si resta fermi e si avvisa il server, che fa riprendere TUTTI insieme."""
        m = self.model
        nm = m.network_manager
        self._end_rejoin()
        if last is not None:
            m.player.position = self._safe_position(Vec3(*last))
            for cube in m.floors.floor_cubes:                 # collider dei blocchi vicini subito, non alla prima update:
                if not cube.is_empty():                       # altrimenti alla ripresa si cade attraverso il pavimento
                    cube.updateColliders(m.player)
        if hasattr(m.player, "air_time"):
            m.player.air_time = 0                             # la velocita' di caduta riparte da zero
        m.player.gravity = 1
        m.level_loaded = True
        application.pause()                                   # fermi come gli altri finche' non arriva la ripresa
        Text("Rientro completato...", scale=1.5, origin=(0, 0), y=0.1, parent=camera.ui, tag='pause_text')
        self._rejoin_wait_until = time.time() + 8             # ripiego se la ripresa (UDP) si perde
        for _ in range(3):                                    # tre copie: il server ne usa una
            nm.send_message("rejoin_done", nm.player_id)
        print("[Game] Rientro completato: attendo la ripresa.")

    def _abort_rejoin(self, reason):
        m = self.model
        r = self._end_rejoin()
        if r is not None:
            rejoin_store.clear(r["saved"]["player_id"])
        self._destroy_world()
        if m.network_manager is not None:
            m.network_manager.leaving_deliberately = True
            m.network_manager.stop()
            m.network_manager = None
        self.view.display_error_message_screen(f"Impossibile rientrare: {reason}", self.view.show_main_menu_screen)

    def _blocks_under(self, pos):
        return {c.block_id for c in self.model.floors.floor_cubes
                if distance_xz(pos, c.position) <= c.activation_distance + 1
                and abs(pos.y - (c.y + CUBE_SCALE / 2)) < 1.5}

    def _safe_position(self, pos):
        """Se sotto `pos` non c'e' piu' terreno, ti posa sul blocco esistente piu' vicino."""
        alive = [c for c in self.model.floors.floor_cubes if not c.is_empty()]

        def top(c):
            return c.y + CUBE_SCALE / 2

        if any(distance_xz(pos, c.position) <= CUBE_SCALE / 2 + 0.5 and top(c) <= pos.y + 1.5 for c in alive):
            return pos
        pool = [c for c in alive if abs(pos.y - top(c)) < 1.5] or [c for c in alive if top(c) <= pos.y + 1.5]
        if not pool:
            return pos
        best = min(pool, key=lambda c: (distance_xz(pos, c.position), -top(c)))
        print(f"[Game] Sotto la posizione di rientro non c'e' piu' terreno: ti posiamo sul blocco {best.block_id}.")
        return Vec3(best.x, top(best), best.z)