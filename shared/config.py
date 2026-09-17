# Configuration constants for the project
MATCHMAKING_TCP_PORT = 9001       # Matchmaking server TCP port
DASHBOARD_PORT = 8081              # Dashboard HTTP di sola lettura (porta 8080 spesso occupata
                                    # da altri servizi/processi di sistema su Windows)
GAME_INSTANCE_BASE_PORT = 10000            # Starting port for game instances
P2P_PORT = 12345                # Default P2P port for game instances
MAX_PLAYERS = 4                  # Max players per game lobby
SPAWN_HEIGHT = 45

# Heartbeat settings (seconds)
HEARTBEAT_INTERVAL = 1
HEARTBEAT_TIMEOUT = 5
REJOIN_GRACE_PERIOD = 15
REJOIN_TIMER = 10
MATCHMAKING_HOSTNAME = "fallsgame.dedyn.io"  # Hostname for matchmaking server
SERVER_BIND = ""  # Bind to all interfaces
LOG_PREFIX = "[MM]"