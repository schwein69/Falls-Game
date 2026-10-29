import time
from ursina import *
from ursina import application

from player import RemotePlayer
import rejoin_store


AUTHORITY_METHODS = ("update_pos", "block_step", "player_died")   


class MessageHandlers:
    # The GameAuthority (the rules) if we are the host/server, otherwise None.
    def _host_authority(self):
        m = self.model
        return m.game_authority if m.network_manager and m.network_manager.is_host else None

    # Send a request to the authority: applied directly if we are the host, sent over the network otherwise.
    def _to_authority(self, method, *args):
        nm = self.model.network_manager
        if not nm or nm.player_id is None:
            return
        if nm.is_host:
            self._apply_to_authority(nm.player_id, method, [nm.player_id, *args])
        else:
            nm.send_message(method, nm.player_id, *args)

    # Host only: the rules live in GameAuthority.apply (same ones as the online server).
    # Every resulting event is shown on our screen and broadcast to the others.
    def _apply_to_authority(self, sender, method, args):
        auth = self._host_authority()
        if auth is None:
            return
        nm = self.model.network_manager
        _, events = auth.apply(sender, method, args, internal=sender in (0, nm.player_id))  
        for name, event_args in events:
            getattr(self, "on_" + name)(*event_args)      # show it here
            nm.broadcast_message(name, *event_args)       # and tell the others

    # Called by the blocks when the local player steps on one: the authority decides if it breaks.
    def request_block_step(self, block_id):
        self._to_authority("block_step", block_id)

    # Called when the local player dies: the authority decides what happens next.
    def request_player_died(self):
        self._to_authority("player_died")

    # Player requests (position, block, death) go to the authority with the sender checked.
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

    # Players and floor 
    # Network events 
    def on_spawn_player(self, pid, x, y, z, status="alive", matchId=None):
        m = self.model
        nm = m.network_manager
        if nm and pid != nm.player_id:
            m.connected_ids.add(pid)
        if m.floors is not None and pid != nm.player_id:
            if pid in m.other_players:
                m.other_players[pid].position = m.other_players[pid].target_position = Vec3(x, y, z)
            else:
                print(f"[Game] Spawning Remote Player {pid}")
                m.other_players[pid] = RemotePlayer(position=Vec3(x, y, z), username=f"Player {pid}")

    # Positions of everybody, sent at fixed intervals.
    def on_state_snapshot(self, positions):
        m = self.model
        nm = m.network_manager
        for pid_str, pos in positions.items():
            pid = int(pid_str)
            if nm and pid == nm.player_id:
                continue                                  
            if pid in m.other_players:
                m.other_players[pid].target_position = Vec3(*pos)
            elif m.level_loaded:
                self.on_spawn_player(pid, *pos)           

    # The authority says a block broke: destroy it; the effect on the player applies only to who stepped on it.
    def on_block_destroyed(self, block_id, pid):
        m = self.model
        cube = m.floors.blocks_by_id.get(block_id) if m.floors is not None else None
        if cube is None:
            return
        nm = m.network_manager
        cube.on_authoritative_step(m.player if nm and pid == nm.player_id else None)

    # Full state sent to who joins or rejoins: rebuild the floor, break the blocks
    # already destroyed, then place everybody. The block under our last position falls "with us on it".
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
                        cube.on_authoritative_step(None)   # we were on it: normal fall
                    else:
                        cube.force_destroy()               # already gone for the others: remove it at once
        if rejoining:
            self._finish_rejoin(last)
        self.on_state_snapshot(positions)

    # Life, death and victory 
    # A player is out of the match: remove his RemotePlayer from the scene.
    def on_player_removed(self, pid):
        other = self.model.other_players.pop(pid, None)
        if other is not None:
            destroy(other)

    # End of the match screen. winner_pid None = no winner (e.g. everybody dies together).
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

    # A player disconnected for good: remove him, and if we are the host let the authority handle it too.
    def on_player_left(self, pid):
        m = self.model
        m.connected_ids.discard(pid)
        if pid in m.other_players:
            print(f"[Game] Player {pid} Left")
            destroy(m.other_players.pop(pid))
        if m.level_loaded and m.network_manager:                      
            self._apply_to_authority(m.network_manager.player_id, "player_left", [pid])

    # Match cycle 
    # The host started the game: load the level with the shared seed (same floor for everybody).
    def on_start_game(self, seed=None):
        m = self.model
        print("[Game] Host started the game")
        m.lobby_open = False
        m.current_floor_seed = seed
        if not m.network_manager.is_host:
            m.host_player_id = 0
        self.load_level(seed)

    # Host only: a player finished loading. When everybody is ready, start the countdown.
    def on_player_ready(self, pid):
        m = self.model
        nm = m.network_manager
        if not (nm and nm.is_host) or getattr(m, 'countdown_started', False):
            return
        m.ready_pids.add(pid)
        print(f"[Game] Player {pid} pronto ({len(m.ready_pids)}/{m.expected_ready_count})")
        if len(m.ready_pids) >= m.expected_ready_count:
            self._begin_countdown()

    # The countdown started: show it and let the players move after 3 seconds.
    def on_start_countdown(self):
        if self._countdown_end is None and not self.model.level_loaded:
            self._countdown_end = time.time() + 3
            self._countdown_shown = None
            print("[Game] Countdown 3-2-1 avviato.")

    # The host paused the match: show a message and stop the game loop. When the host resumes, the game continues.
    def on_game_pause(self, is_paused, pid=None):
        if getattr(self.model, 'rejoin_pending', False):
            return                            # who is still rejoining is not part of the pause
        self._destroy_tagged('pause_text')
        if is_paused:
            Text(f"Waiting for Player {pid} to reconnect...", scale=1.5, origin=(0, 0), y=0.1,
                 parent=camera.ui, tag='pause_text')
            application.pause()
        else:
            print("[Game] RESUMED!")
            self._rejoin_wait_until = None
            application.resume()

    # The host closed the match: back to the menu with a message.
    def on_host_left(self):
        print("[Game] L'host ha chiuso la partita.")
        self.reset_floor()
        Text("L'host ha chiuso la partita.", scale=1.3, origin=(0, 0), y=0.1, parent=camera.ui, color=color.orange)