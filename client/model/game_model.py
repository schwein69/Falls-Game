class GameModel:
    def __init__(self):
        # Entita' della partita corrente
        self.player = None
        self.floors = None
        self.sky = None

        # Giocatori
        self.connected_ids = set()
        self.other_players = {}  # player_id -> RemotePlayer instance

        # UI
        self.gui_elements = []
        self.player_labels = []
        self.lobby_buttons = []

        # Stato della partita
        self.game_over = False
        self.game_paused = False
        self.level_loaded = False
        self.lobby_open = False
        self.player_died_reported = False
        self.current_floor_seed = None
        self.migrating = False

        # Rete
        self.network_manager = None   # NetworkManager | None
        self.broadcaster = None       # DiscoveryBroadcaster | None
        self.current_listener = None  # DiscoveryListener | None
        self.game_authority = None    # GameAuthority | None

    def is_in_active_match(self):
        return self.level_loaded or self.floors is not None or self.player is not None