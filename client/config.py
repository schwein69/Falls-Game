# Configuration constants for the project
TOKEN = "UYqTPCRpqu5htrYL41qDPRLRjA34"
MATCHMAKING_TCP_PORT = 9001       # Matchmaking server TCP port
GAME_INSTANCE_BASE_PORT = 10000            # Starting port for game instances
P2P_PORT = 12345                # Default P2P port for game instances
MAX_PLAYERS = 4                  # Max players per game lobby

# Heartbeat settings (seconds)
HEARTBEAT_INTERVAL = 1
HEARTBEAT_TIMEOUT = 5
REJOIN_GRACE_PERIOD = 15
REJOIN_TIMER = 10
MATCHMAKING_HOSTNAME = "fallsgame.dedyn.io"  # Hostname for matchmaking server
SERVER_BIND = ""  # Bind to all interfaces
LOG_PREFIX = "[MM]"