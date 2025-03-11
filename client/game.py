from ursina import *
from floor import *
from player import Player
from usefulFunctions import *
class Game:
    def __init__(self):
        self.player = None
        self.floor = None
        self.sky = None
        self.game_over = False
        self.level_loaded = False

    def load_level(self):
        self.game_over = False
        self.level_loaded = True

        # Initialize floor and sky
        self.floor = Floor()
        self.sky = Entity(
            model="sphere",
            texture=os.path.join("assets", "sky.png"),
            scale=9999,
            double_sided=True
        )

        # Initialize player
        self.player = Player(get_random_position(), "Luca")

        # Set up camera
        camera.z = -5

    def reset(self):
        self.player = None
        destroy(self.sky)
        self.sky = None
        self.game_over = False
        self.level_loaded = False

    def update(self):
        if self.game_over or not self.level_loaded or not self.player or not self.floor:
            return

        # Update collisions
        for entity in scene.entities:
            if isinstance(entity, FloorCube):
                entity.update_colliders(self.player)

        # Check for player falling
        if self.player.world_position.y < -20:
            self.player.game_over()
            self.game_over = True