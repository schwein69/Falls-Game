import os
MATCHMAKING_TCP_PORT = 9001       # Matchmaking server TCP port
DASHBOARD_PORT = 8081              # Dashboard HTTP di sola lettura 
GAME_INSTANCE_BASE_PORT = 10000            # Starting port for game instances
P2P_PORT = 12345                # Default P2P port for game instances
MAX_PLAYERS = 3                  # Max players per game lobby
SPAWN_HEIGHT = 50

HEARTBEAT_INTERVAL = 1
HEARTBEAT_TIMEOUT = 5
REJOIN_GRACE_PERIOD = 15
REJOIN_TIMER = 15
LOCAL_TEST = True   
MATCHMAKING_HOSTNAME = os.environ.get("MATCHMAKING_HOSTNAME", "127.0.0.1" if LOCAL_TEST else "fallsgame.hopto.org")
SERVER_BIND = ""  
LOG_PREFIX = "[MM]"
MAX_ACTIVE_GAMES = 1   