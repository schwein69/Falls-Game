import os
MATCHMAKING_TCP_PORT = 9001       # Matchmaking server TCP port
MATCHMAKING_DISCOVERY_PORT = 15001   # UDP port where the matchmaking server announces itself (P2P uses 15000)
GAME_INSTANCE_BASE_PORT = 10000            # Starting port for game instances (1 porta per partita, TCP+UDP)
P2P_PORT = 12345                # Default P2P port for game instances
MAX_PLAYERS = 2                  # Max players per game lobby
SPAWN_HEIGHT = 50

HEARTBEAT_INTERVAL = 1
HEARTBEAT_TIMEOUT = 5
REJOIN_GRACE_PERIOD = 15
REJOIN_TIMER = 10
MATCHMAKING_HOSTNAME = os.environ.get("MATCHMAKING_HOSTNAME", "")
SERVER_BIND = ""  
LOG_PREFIX = "[MM]"
MAX_ACTIVE_GAMES = 5    
RUN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "run")   # i log delle partite (game_<porta>.log)
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")   
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))
GAME_KEY_PREFIX = "game:"