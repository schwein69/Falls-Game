import random


class GameAuthority:
    def __init__(self):
        self.floor_seed = random.randint(0, 2 ** 31 - 1)
        self.destroyed_blocks = set()   # block_id (int) gia' distrutti
        self.player_positions = {}      # pid -> (x, y, z)
        self.known_players = set()      # tutti i pid mai visti in questa partita
        self.dead_players = set()       # pid gia' caduti/eliminati
        self.game_won = False           # evita di dichiarare due volte una vittoria

    # Vittoria: vince l'ultimo giocatore rimasto vivo
    def handle_player_died(self, pid):
        """Un giocatore e' caduto/eliminato. Ritorna (partita_finita, vincitore_o_None) — vedi
        _check_win per i dettagli."""
        self.known_players.add(pid)
        self.dead_players.add(pid)
        self.player_positions.pop(pid, None)
        return self._check_win()

    def _check_win(self):
        if self.game_won:
            return False, None
        alive = self.known_players - self.dead_players
        if len(self.known_players) > 1 and len(alive) == 1:
            self.game_won = True
            return True, next(iter(alive))
        if len(alive) == 0 and len(self.known_players) > 0:
            self.game_won = True
            return True, None
        return False, None

    # Floor
    def handle_block_step(self, pid, block_id):
        if block_id is None:
            return None
        if block_id in self.destroyed_blocks:
            return None
        self.destroyed_blocks.add(block_id)
        return {"block_id": block_id, "pid": pid}

    def already_destroyed_blocks(self):
        """Usato per il 'catch-up' di chi si unisce o si riconnette a partita in corso."""
        return list(self.destroyed_blocks)

    # ------------------------------------------------------------------
    # Posizioni
    # ------------------------------------------------------------------
    def handle_update_pos(self, pid, x, y, z):
        self.known_players.add(pid)
        self.player_positions[pid] = (x, y, z)

    def remove_player(self, pid):
        """Un giocatore si disconnette/lascia la partita. Ritorna il pid del vincitore se
        questo lascia un solo giocatore rimasto (non contato come 'morto': semplicemente non
        e' piu' tra quelli in gara), altrimenti None."""
        self.player_positions.pop(pid, None)
        self.known_players.discard(pid)
        self.dead_players.discard(pid)
        return self._check_win()

    def snapshot_payload(self):
        """Stato consolidato di TUTTI i giocatori noti, da trasmettere a intervalli fissi."""
        return {pid: list(pos) for pid, pos in self.player_positions.items()}