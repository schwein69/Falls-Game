# Configuration constants for the project
import os
MATCHMAKING_TCP_PORT = 9001       # Matchmaking server TCP port
DASHBOARD_PORT = 8081             # Dashboard HTTP di sola lettura
GAME_INSTANCE_BASE_PORT = 10000   # Starting port for game instances
P2P_PORT = 12345                  # Default P2P port for game instances
MAX_PLAYERS = 3                   # Max players per game lobby

SPAWN_HEIGHT = 45

# Heartbeat settings 
HEARTBEAT_INTERVAL = 1
HEARTBEAT_TIMEOUT = 5
REJOIN_GRACE_PERIOD = 15
REJOIN_TIMER = 10
MATCHMAKING_HOSTNAME = os.environ.get("MATCHMAKING_HOSTNAME", "127.0.0.1")
# Host/IP reachable by remote game clients. Defaults to matchmaking host for simple deployments.
GAME_PUBLIC_HOST = os.environ.get("GAME_PUBLIC_HOST", MATCHMAKING_HOSTNAME)
SERVER_BIND = ""  # Bind to all interfaces
LOG_PREFIX = "[Server]"