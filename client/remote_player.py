from ursina import *
from client.inGameGui import InGameGui
from client.usefulFunctions import get_random_position
from utils import lerp_vec3
from player import Player


class RemotePlayer(Entity):
    """Placeholder for remote-controlled players"""
    def __init__(self, player_id, username, color=color.white):
        super().__init__()
        self.player_id = player_id
        self.username = username
        self.model = "collisionModel"
        self.color = color
        self.collider = "capsule"
        self.scale = (1, 2, 1)
        self.position = Vec3(0, 0, 0)
        self.target_pos = Vec3(0, 0, 0)
        self.target_rot = Vec3(0, 0, 0)

        # Add name tag above player
        self.gui = InGameGui(username=username, player_entity=self)

    def update_state(self, pos, rot):
        self.target_pos = Vec3(pos)
        self.target_rot = Vec3(rot)

    def interpolate(self, dt):
        self.position = lerp_vec3(self.position, self.target_pos, dt * 8)
        self.rotation = lerp_vec3(self.rotation, self.target_rot, dt * 10)


class GameState:
    def __init__(self, network_manager, on_game_over):
        self.network = network_manager
        self.on_game_over = on_game_over
        self.local_player = None
        self.players = {}  # player_id -> Player or RemotePlayer
        self.running = False
        self.dead = False

    def start(self):
        self.running = True
        self.dead = False

        pid = self.network.player_id
        start_pos = get_random_position()
        username = f"Player{pid + 1}"

        # Create the actual player for this client
        self.local_player = Player(position=start_pos, username=username)
        self.local_player.name = f"player_{pid}"
        self.players[pid] = self.local_player

        # Create dummy players for the others
        for i in range(4):
            if i != pid:
                dummy = RemotePlayer(i, username=f"Player{i + 1}")
                self.players[i] = dummy

        self.network.add_receive_callback(self.handle_network_message)

    def update(self, dt):
        if not self.running or self.dead:
            return

        # Send own position and rotation
        pos = self.local_player.position
        rot = self.local_player.rotation
        msg = f"POS|{self.network.player_id}|{pos.x:.2f},{pos.y:.2f},{pos.z:.2f}|{rot.x:.2f},{rot.y:.2f},{rot.z:.2f}"
        self.network.send(msg)

        # Interpolate remote players
        for pid, p in self.players.items():
            if pid != self.network.player_id and isinstance(p, RemotePlayer):
                p.interpolate(dt)

        # Detect if local player has fallen
        if self.local_player.y < -20:
            self.network.send(f"DAMAGE|{self.network.player_id}")
            self.local_player.gameOver()
            self.dead = True
            invoke(self.on_game_over, delay=3)

    def handle_network_message(self, msg):
        if msg.startswith("POS|"):
            try:
                parts = msg.split('|')
                pid = int(parts[1])
                if pid == self.network.player_id:
                    return
                pos = list(map(float, parts[2].split(',')))
                rot = list(map(float, parts[3].split(',')))
                player = self.players.get(pid)
                if player:
                    player.update_state(pos, rot)
            except:
                pass

        elif msg.startswith("DAMAGE|"):
            try:
                pid = int(msg.split('|')[1])
                player = self.players.get(pid)
                if player:
                    player.hp -= 1
                    print(f"Player {pid} took damage. HP = {player.hp}")
            except:
                pass
