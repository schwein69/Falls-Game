import time
from ursina import *
from ursina import application

from player import RemotePlayer
import rejoin_store


AUTHORITY_METHODS = ("update_pos", "block_step", "player_died") 


class MessageHandlers:
    def _host_authority(self):
        m = self.model
        return m.game_authority if m.network_manager and m.network_manager.is_host else None

    def _to_authority(self, method, *args):
        """Manda una richiesta all'autorita': applicata subito se siamo noi l'host, altrimenti via rete."""
        nm = self.model.network_manager
        if not nm or nm.player_id is None:
            return
        if nm.is_host:
            self._apply_to_authority(nm.player_id, method, [nm.player_id, *args])
        else:
            nm.send_message(method, nm.player_id, *args)

    def _apply_to_authority(self, sender, method, args):
        auth = self._host_authority()
        if auth is None:
            return
        nm = self.model.network_manager
        _, events = auth.apply(sender, method, args, internal=sender in (0, nm.player_id))   
        for name, event_args in events:
            getattr(self, "on_" + name)(*event_args)
            nm.broadcast_message(name, *event_args)

    def request_block_step(self, block_id):
        self._to_authority("block_step", block_id)

    def request_player_died(self):
        self._to_authority("player_died")

    def handle_message(self, data):
        method = str(data.get("method"))
        nm = self.model.network_manager
        if method == "player_left" and nm and nm.is_host and data.get("sender_id") not in (0, nm.player_id):
            return                                                       
        if method in AUTHORITY_METHODS:
            self._apply_to_authority(data.get("sender_id"), method, data.get("args", []))
            return
        handler = getattr(self, "on_" + method, None)
        if handler is not None:
            handler(*data.get("args", []))

    # ---------------- giocatori e pavimento ----------------
    def on_spawn_player(self, pid, x, y, z, status="alive", matchId=None):
        m = self.model
        nm = m.network_manager
        if nm and pid != nm.player_id:
            m.connected_ids.add(pid)
        if m.floors is not None and pid != nm.player_id:
            if pid in m.other_players:
                m.other_players[pid].position = Vec3(x, y, z)
            else:
                print(f"[Game] Spawning Remote Player {pid}")
                m.other_players[pid] = RemotePlayer(position=Vec3(x, y, z), username=f"Player {pid}")

    def on_state_snapshot(self, positions):
        """Posizioni di tutti, a intervalli fissi."""
        m = self.model
        nm = m.network_manager
        for pid_str, pos in positions.items():
            pid = int(pid_str)
            if nm and pid == nm.player_id:
                continue                                  
            if pid in m.other_players:
                m.other_players[pid].position = Vec3(*pos)
            elif m.level_loaded:
                self.on_spawn_player(pid, *pos)

    def on_block_destroyed(self, block_id, pid):
        """Distrugge il blocco; l'effetto sul giocatore vale solo se l'ha calpestato lui."""
        m = self.model
        cube = m.floors.blocks_by_id.get(block_id) if m.floors is not None else None
        if cube is None:
            return
        nm = m.network_manager
        cube.on_authoritative_step(m.player if nm and pid == nm.player_id else None)

    def on_sync_state(self, destroyed_ids, positions, floor_seed=None):
        m = self.model
        rejoining = getattr(m, 'rejoin_pending', False)
        last = None
        if rejoining:
            m.current_floor_seed = floor_seed
            self._build_world(floor_seed, m.network_manager.player_id)
            last = positions.get(str(m.network_manager.player_id))
        under = self._blocks_under(Vec3(*last)) if last is not None else set()
        if m.floors is not None:
            for block_id in destroyed_ids:
                cube = m.floors.blocks_by_id.get(block_id)
                if cube is not None and not cube.is_disappearing and not cube.has_activated:
                    if block_id in under:
                        cube.on_authoritative_step(None)
                    else:
                        cube.force_destroy()
        if rejoining:
            self._finish_rejoin(last)
        self.on_state_snapshot(positions)

    # ---------------- vita, morte e vittoria ----------------
    def on_player_removed(self, pid):
        other = self.model.other_players.pop(pid, None)
        if other is not None:
            destroy(other)

    def on_game_won(self, winner_pid):
        m = self.model
        if m.game_over:
            return
        application.resume()                  
        m.game_over = True
        if m.network_manager is not None:
            rejoin_store.clear(m.network_manager.player_id)
        m.lobby_open = False
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
            message = "Hai vinto!" if nm and winner_pid == nm.player_id else f"Ha vinto il giocatore {winner_pid}!"
        Text(message, scale=2.5, origin=(0, 0), position=(0, 0.1), parent=camera.ui, color=color.gold)
        Button('Torna al menu', y=-0.15, scale=(0.3, 0.08), parent=camera.ui, on_click=self.reset_floor)

    def on_player_left(self, pid):
        m = self.model
        m.connected_ids.discard(pid)
        if pid in m.other_players:
            print(f"[Game] Player {pid} Left")
            destroy(m.other_players.pop(pid))
        if m.level_loaded and m.network_manager:                       
            self._apply_to_authority(m.network_manager.player_id, "player_left", [pid])

    # ---------------- ciclo della partita ----------------
    def on_start_game(self, seed=None):
        m = self.model
        print("[Game] Host started the game")
        m.lobby_open = False
        m.current_floor_seed = seed
        if not m.network_manager.is_host:
            m.host_player_id = 0
        self.load_level(seed)

    def on_player_ready(self, pid):
        """Solo l'host: quando tutti sono pronti parte il conto alla rovescia."""
        m = self.model
        nm = m.network_manager
        if not (nm and nm.is_host) or getattr(m, 'countdown_started', False):
            return
        m.ready_pids.add(pid)
        print(f"[Game] Player {pid} pronto ({len(m.ready_pids)}/{m.expected_ready_count})")
        if len(m.ready_pids) >= m.expected_ready_count:
            self._begin_countdown()

    def on_start_countdown(self):
        if self._countdown_end is None and not self.model.level_loaded:  
            self._countdown_end = time.time() + 3
            self._countdown_shown = None
            print("[Game] Countdown 3-2-1 avviato.")

    def on_game_pause(self, is_paused, pid=None):
        if getattr(self.model, 'rejoin_pending', False):
            return                            
        self._destroy_tagged('pause_text')
        if is_paused:
            Text(f"Waiting for Player {pid} to reconnect...", scale=1.5, origin=(0, 0), y=0.1,
                 parent=camera.ui, tag='pause_text')
            application.pause()
        else:
            print("[Game] RESUMED!")
            self._rejoin_wait_until = None
            application.resume()

    def on_host_left(self):
        print("[Game] L'host ha chiuso la partita.")
        self.reset_floor()
        Text("L'host ha chiuso la partita.", scale=1.3, origin=(0, 0), y=0.1, parent=camera.ui, color=color.orange)