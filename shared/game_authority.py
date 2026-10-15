import random


class GameAuthority:
    def __init__(self):
        self.floor_seed = random.randint(0, 2 ** 31 - 1)
        self.destroyed_blocks = set()   # block_id (int) gia' distrutti
        self.player_positions = {}      # pid -> (x, y, z)
        self.known_players = set()      
        self.dead_players = set()       # pid eliminati
        self.left_players = set()       # pid usciti dalla partita
        self.game_won = False           

    # Vittoria: vince l'ultimo giocatore rimasto vivo
    def handle_player_died(self, pid):
        """Un giocatore e' caduto/eliminato. Ritorna (partita_finita, vincitore_o_None)"""
        self.known_players.add(pid)
        self.dead_players.add(pid)
        self.player_positions.pop(pid, None)
        return self._check_win()

    def _check_win(self):
        """Ritorna (partita_finita: bool, vincitore: pid o None)."""
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
        """Un client segnala di essere passato sul blocco block_id."""
        if block_id is None:
            return None
        if block_id in self.destroyed_blocks:
            return None
        self.destroyed_blocks.add(block_id)
        return {"block_id": block_id, "pid": pid}

    def already_destroyed_blocks(self):
        return list(self.destroyed_blocks)

    # Posizioni
    def handle_update_pos(self, pid, x, y, z):
        self.known_players.add(pid)
        self.player_positions[pid] = (x, y, z)

    def remove_player(self, pid):
        """Un giocatore si disconnette/lascia la partita. Ritorna (partita_finita,
        vincitore_o_None)."""
        self.known_players.add(pid)
        self.left_players.add(pid)
        self.player_positions.pop(pid, None)
        return self._check_win()

    def snapshot_payload(self):
        return {pid: list(pos) for pid, pos in self.player_positions.items()}

    def apply(self, sender, method, args, internal=False):
        """Applica una richiesta ("update_pos", "block_step", "player_died", "player_left")."""
        pid = args[0] if internal and args else sender
        out = pid in self.dead_players or pid in self.left_players       
        events = []
        try:
            if method == "update_pos":
                _, x, y, z = args
                if not out and all(isinstance(v, (int, float)) for v in (x, y, z)):
                    self.handle_update_pos(pid, x, y, z)
                return False, events          # le posizioni viaggiano negli snapshot, non servono eventi

            if method == "block_step":
                _, block_id = args
                if out or not isinstance(block_id, int):
                    return False, events
                result = self.handle_block_step(pid, block_id)
                if result:
                    events.append(("block_destroyed", (result["block_id"], result["pid"])))
                return result is not None, events

            if method == "player_died":
                if out:
                    return False, events
                game_over, winner = self.handle_player_died(pid)
                events.append(("player_removed", (pid,)))
            elif method == "player_left":
                game_over, winner = self.remove_player(pid)
            else:
                return False, events
            if game_over:
                events.append(("game_won", (winner,)))
            return True, events
        except (ValueError, TypeError):       # argomenti sbagliati: li ignoro
            return False, []

    def to_dict(self):
        """Serializza lo stato dell'autorita' in un dizionario, da trasmettere a un backup."""
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
        """Ricostruisce un'autorita' da uno stato replicato. Usato dal backup
        quando viene promosso a primary."""
        auth = cls()
        auth.floor_seed = data["floor_seed"]
        auth.destroyed_blocks = set(data["destroyed_blocks"])
        auth.player_positions = {int(pid): tuple(pos) for pid, pos in data["player_positions"].items()}
        auth.known_players = set(data["known_players"])
        auth.dead_players = set(data["dead_players"])
        auth.left_players = set(data["left_players"])
        auth.game_won = data["game_won"]
        return auth