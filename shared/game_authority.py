import random


class GameAuthority:
    def __init__(self):
        self.floor_seed = random.randint(0, 2 ** 31 - 1)
        self.destroyed_blocks = set()   # block_id (int) gia' distrutti
        self.player_positions = {}      # pid -> (x, y, z)
        self.known_players = set()      # SOLO CRESCE: tutti i pid mai visti in questa partita
        self.dead_players = set()       # pid caduti/eliminati (morte)
        self.left_players = set()       # pid usciti (disconnessione) — distinto dai morti
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
        """Ritorna (partita_finita: bool, vincitore: pid o None).

        Un vincitore viene dichiarato solo se resta esattamente UN giocatore vivo tra ALMENO
        due che hanno mai partecipato (l'ultimo-rimasto-vivo ha senso solo se c'era qualcun
        altro con cui competere). Se non resta NESSUNO vivo — es. giocando DA SOLI, o per
        coincidenza muoiono tutti — la partita finisce comunque, senza un vincitore specifico."""
        if self.game_won:
            return False, None
        gone = self.dead_players | self.left_players
        alive = self.known_players - gone
        if len(self.known_players) > 1 and len(alive) == 1:
            self.game_won = True
            return True, next(iter(alive))
        if len(alive) == 0 and len(self.known_players) > 0:
            self.game_won = True
            return True, None
        return False, None

    # Pavimento
    def handle_block_step(self, pid, block_id):
        """Un client segnala di essere passato sul blocco block_id. Ritorna il payload da
        trasmettere a tutti se e' la prima volta che viene distrutto, altrimenti None
        (richiesta ignorata: qualcun altro lo ha gia' consumato, o l'id non e' valido)."""
        if block_id is None:
            return None
        if block_id in self.destroyed_blocks:
            return None
        self.destroyed_blocks.add(block_id)
        return {"block_id": block_id, "pid": pid}

    def already_destroyed_blocks(self):
        """Usato per il 'catch-up' di chi si unisce o si riconnette a partita in corso."""
        return list(self.destroyed_blocks)

    # Posizioni
    def handle_update_pos(self, pid, x, y, z):
        self.known_players.add(pid)
        self.player_positions[pid] = (x, y, z)

    def remove_player(self, pid):
        """Un giocatore si disconnette/lascia la partita. Ritorna (partita_finita,
        vincitore_o_None) — non e' contato come 'morto', semplicemente non e' piu' tra quelli
        in gara."""
        self.known_players.add(pid)
        self.left_players.add(pid)
        self.player_positions.pop(pid, None)
        return self._check_win()

    def snapshot_payload(self):
        """Stato consolidato di TUTTI i giocatori noti, da trasmettere a intervalli fissi."""
        return {pid: list(pos) for pid, pos in self.player_positions.items()}

    # Replica primary -> backup: serializzazione dello stato completo
    def to_dict(self):
        """Tutto lo stato in una forma serializzabile in JSON, per replicarlo dal primary al
        backup passivo. Gli insiemi (set) diventano liste — JSON non ha un tipo 'set'."""
        return {
            "floor_seed": self.floor_seed,
            "destroyed_blocks": list(self.destroyed_blocks),
            "player_positions": {str(pid): list(pos) for pid, pos in self.player_positions.items()},
            "known_players": list(self.known_players),
            "dead_players": list(self.dead_players),
            "left_players": list(self.left_players),
            "game_won": self.game_won,
        }

    @classmethod
    def from_dict(cls, data):
        """Ricostruisce un'autorita' da uno stato replicato (vedi to_dict). Usato dal backup
        quando viene promosso a primary, per ripartire dall'ULTIMO stato ricevuto invece che da
        zero."""
        auth = cls()
        auth.floor_seed = data["floor_seed"]
        auth.destroyed_blocks = set(data["destroyed_blocks"])
        auth.player_positions = {int(pid): tuple(pos) for pid, pos in data["player_positions"].items()}
        auth.known_players = set(data["known_players"])
        auth.dead_players = set(data["dead_players"])
        auth.left_players = set(data["left_players"])
        auth.game_won = data["game_won"]
        return auth