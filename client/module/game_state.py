# Entita' della partita corrente
player = None
floors = None
sky = None

# Giocatori
connected_ids = set()
other_players = {}  # player_id -> RemotePlayer instance

# UI
guiElements = []
player_labels = []
lobby_buttons = []

# Stato della partita
game_over = False
game_paused = False
level_loaded = False
lobby_open = False
player_died_reported = False
current_floor_seed = None
migrating = False

# Rete (i tipi sono solo indicativi: vengono assegnati dai moduli screens.py/migration.py)
network_manager = None       # NetworkManager | None
broadcaster = None           # DiscoveryBroadcaster | None
current_listener = None      # DiscoveryListener | None
game_authority = None        # GameAuthority | None