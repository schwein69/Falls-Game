import time
from ursina import *
from ursina import application

from player import RemotePlayer
import rejoin_store


class MessageHandlers:
    # ---------------- invio e smistamento ----------------
    def _host_authority(self):
        """La GameAuthority se siamo l'host/server (solo lui valida), altrimenti None."""
        m = self.model
        return m.game_authority if m.network_manager and m.network_manager.is_host else None

    def _to_authority(self, local_handler, method, *args):
        """Manda una richiesta all'autorita': applicata subito se siamo noi l'host, altrimenti via rete."""
        nm = self.model.network_manager
        if not nm or nm.player_id is None:
            return
        if nm.is_host:
            local_handler(nm.player_id, *args)
        else:
            nm.send_message(method, nm.player_id, *args)

    def request_block_step(self, block_id):
        self._to_authority(self.on_block_step, "block_step", block_id)

    def request_player_died(self):
        self._to_authority(self.on_player_died, "player_died")

    def handle_message(self, data):
        """Un messaggio dalla rete, {"method": "block_step", "args": [...]}, chiama self.on_block_step(*args)."""
        handler = getattr(self, "on_" + str(data.get("method")), None)
        if handler is not None:
            handler(*data.get("args", []))

    # ---------------- giocatori e pavimento ----------------
    def on_spawn_player(self, pid, x, y, z, status="alive", matchId=None):
        """Un altro giocatore e' entrato: creiamo (o spostiamo) la sua RemotePlayer."""
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

    def on_update_pos(self, pid, x, y, z):
        auth = self._host_authority()
        if auth is not None:
            auth.handle_update_pos(pid, x, y, z)

    def on_state_snapshot(self, positions):
        """Posizioni di tutti, a intervalli fissi. Le chiavi arrivano come stringhe (JSON)."""
        m = self.model
        nm = m.network_manager
        for pid_str, pos in positions.items():
            pid = int(pid_str)
            if nm and pid == nm.player_id:
                continue                                  # noi non ci spostiamo con il nostro pacchetto
            if pid in m.other_players:
                m.other_players[pid].position = Vec3(*pos)
            elif m.level_loaded:
                self.on_spawn_player(pid, *pos)

    def on_block_step(self, pid, block_id):
        """Solo l'host valida: un blocco gia' distrutto da altri viene ignorato."""
        auth = self._host_authority()
        result = auth.handle_block_step(pid, block_id) if auth is not None else None
        if result is None:
            return
        self.on_block_destroyed(result["block_id"], result["pid"])
        self.model.network_manager.broadcast_message("block_destroyed", result["block_id"], result["pid"])

    def on_block_destroyed(self, block_id, pid):
        """Distrugge il blocco; l'effetto sul giocatore vale solo se l'ha calpestato lui."""
        m = self.model
        cube = m.floors.blocks_by_id.get(block_id) if m.floors is not None else None
        if cube is None:
            return
        nm = m.network_manager
        cube.on_authoritative_step(m.player if nm and pid == nm.player_id else None)

    def on_sync_state(self, destroyed_ids, positions, floor_seed=None):
        """Ci mette in pari con lo stato dell'autorita'. Al rientro e' anche la prima volta che
        sappiamo il seed: costruiamo qui il mondo e ci rimettiamo dove eravamo."""
        m = self.model
        rejoining = getattr(m, 'rejoin_pending', False)
        last = None
        if rejoining:
            m.current_floor_seed = floor_seed
            self._build_world(floor_seed, m.network_manager.player_id)
            last = positions.get(str(m.network_manager.player_id))
        # I blocchi sotto di noi stanno ancora sparendo per gli altri: con force_destroy() rientreremmo nel vuoto.
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
    def on_player_died(self, pid):
        """Solo l'host/server decide chi e' ancora vivo."""
        auth = self._host_authority()
        if auth is None:
            return
        game_over, winner_pid = auth.handle_player_died(pid)
        self.on_player_removed(pid)
        self.model.network_manager.broadcast_message("player_removed", pid)
        if game_over:
            self.on_game_won(winner_pid)
            self.model.network_manager.broadcast_message("game_won", winner_pid)

    def on_player_removed(self, pid):
        other = self.model.other_players.pop(pid, None)
        if other is not None:
            destroy(other)

    def on_game_won(self, winner_pid):
        """Schermata di fine partita. winner_pid None = nessun vincitore (es. muoiono tutti insieme)."""
        m = self.model
        if m.game_over:
            return
        application.resume()                  # in pausa i pulsanti non funzionano
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
        if m.level_loaded and m.game_authority is not None:
            game_over, winner_pid = m.game_authority.remove_player(pid)
            if game_over and m.network_manager:
                self.on_game_won(winner_pid)
                m.network_manager.broadcast_message("game_won", winner_pid)

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
        """Parte il 3-2-1, poi la gravita'. A orologio e non con invoke(delay): dopo un caricamento
        lungo il primo time.dt e' enorme e il "3" durerebbe un frame."""
        if self._countdown_end is None and not self.model.level_loaded:   # un secondo annuncio non lo riavvia
            self._countdown_end = time.time() + 3
            self._countdown_shown = None
            print("[Game] Countdown 3-2-1 avviato.")

    def on_game_pause(self, is_paused, pid=None):
        """application.pause() ferma tutto (anche il movimento): alla ripresa ognuno e' dov'era.
        La rete gira da update_network (ignore_paused), cosi' la ripresa arriva comunque."""
        if getattr(self.model, 'rejoin_pending', False):
            return                            # chi sta ancora rientrando non fa parte della pausa
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