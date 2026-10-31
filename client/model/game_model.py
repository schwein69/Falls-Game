class GameModel:
    def __init__(self):
        # Entities of the current match
        self.player = None
        self.floors = None
        self.sky = None

        # Players
        self.connected_ids = set()
        self.other_players = {}  # player_id -> RemotePlayer instance

        # UI
        self.gui_elements = []
        self.player_labels = []
        self.lobby_buttons = []

        # State of the match
        self.game_over = False
        self.game_paused = False
        self.level_loaded = False
        self.lobby_open = False
        self.player_died_reported = False   # sent to the authority only once
        self.current_floor_seed = None
        self.migrating = False              # P2P: host migration in progress

        # Network
        self.network_manager = None   # NetworkManager | None
        self.broadcaster = None       # DiscoveryBroadcaster | None
        self.current_listener = None  # DiscoveryListener | None
        self.game_authority = None    # GameAuthority | None

    # True while a match is running or being built.
    def is_in_active_match(self):
        return self.level_loaded or self.floors is not None or self.player is not None