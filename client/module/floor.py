import ursina
from ursina import *

GRID_SIZE = 7          # each floor goes from -7 to 6 on X and Z: 14 x 14 = 196 blocks
FLOOR_HEIGHT = 10      # vertical distance between two floors
FLOOR_COUNT = 5        # number of floors, one above the other (5 x 196 = 980 blocks)
CUBE_SCALE = 2.5       # side of a block
BLOCK_TYPES = ['normal', 'speed', 'superjump']

# Block probabilities
BLOCK_PROBABILITIES = [0.95, 0.025, 0.025]


def get_random_block_type():
    """Pick a block type. It is random, but the seed makes it the same on every client (see apply_floor_seed)."""
    return random.choices(BLOCK_TYPES, weights=BLOCK_PROBABILITIES, k=1)[0]


def distance_xz(a, b):
    """2D distance on the X-Z plane (height Y is ignored), used to turn colliders on and off."""
    return ((a.x - b.x) ** 2 + (a.z - b.z) ** 2) ** 0.5


def apply_floor_seed(seed):
    """Seed the random generator. The host sends the seed in start_game, so every client builds the same floor.
    It must be called before Floor() is created."""
    if seed is not None:
        random.seed(seed)


class FloorCube(Entity):
    """One block of the arena. It disappears after a player steps on it."""

    def __init__(self, position, block_type, block_id):
        super().__init__(
            position=position,
            scale=CUBE_SCALE,
            model="cube",
            #collider="box",       
            texture = "floortxt"
        )
        self.block_type = block_type
        self.block_id = block_id           # same on every client: it is how the authority names a block

        # White = normal, green = speed, blue = super jump
        self.color = color.white if block_type == 'normal' else (color.green if block_type == 'speed' else color.blue)
        self.has_activated = False         # the authority already confirmed the step on this block
        self.activation_distance = 2.0    
        self.collider_added = False
        self.collider = None
        self.is_disappearing = False       # Flag to track whether the block is disappearing
        self.step_requested = False        
    def updateColliders(self, player):
        """Keep a collider only on the blocks near the player: 980 colliders at the same time would be too heavy."""
        buffer_distance = self.activation_distance + 1
        squared_buffer_distance = buffer_distance      
        distance = distance_xz(player.position, self.position)
        if distance <= squared_buffer_distance and not self.collider_added:
            self.collider = "box"  # Add collider dynamically
            self.collider_added = True

        # Remove collider if the player is far away and the collider has been added
        elif distance > squared_buffer_distance and self.collider_added:
            self.collider = None  
            self.collider_added = False

    def is_player_standing_on(self, player):
        """True if the player is close enough on X-Z and his feet are at the height of the top face."""
        if distance_xz(player.position, self.position) > self.activation_distance:
            return False
        top_y = self.y + CUBE_SCALE / 2
        return abs(player.y - top_y) < 1.0

    def disappear(self):
        """Visual effect: the block sinks a little and turns gray for 1 s, then it is removed."""
        self.is_disappearing = True
        self.animate('y', self.y - 0.20, duration=1)
        self.animate('color', color.gray, duration=1)
        destroy(self, delay=1.5)

    def force_destroy(self):
        """Remove the block at once, without animation (used to apply the state received with sync_state)."""
        self.is_disappearing = True
        self.has_activated = True
        destroy(self)

    def on_authoritative_step(self, local_player):
        """The authority said this block is destroyed. local_player is the player who stepped on it, only if it is us."""
        if self.is_disappearing or self.has_activated:
            return                                    
        if local_player is not None:                   # bonus effects apply only to the player who stepped
            if self.block_type == 'speed':
                self.activate_speed(local_player)
            elif self.block_type == 'superjump':
                self.super_jump(local_player)
        self.has_activated = True
        self.disappear()

    def activate_speed(self, player):
        """Speed +2 for 2 seconds. It runs only if the speed is the normal one (5), so bonuses do not add up."""
        if player.speed == 5:
            player.speed += 2
            invoke(setattr, player, 'speed', player.speed - 2, delay=2)    # back to 5 after 2 s

    def super_jump(self, player):
        """Push the player forward and up with a short animation."""
        # Get the forward direction
        direction = ursina.Vec3(player.forward.x, 0, player.forward.z).normalized()
        dash_distance = 5
        dash_duration = 0.4
        target_height = 3.5

        # Get the player's current position
        original_position = player.position

        # Calculate the new position (forward motion + vertical jump)
        new_position = original_position + direction * dash_distance  # Move player forward
        new_position.y = original_position.y + target_height  # Set new y value for the jump height

        # Animate the player's position to the target position
        player.animate_position(new_position, duration=dash_duration, curve=ursina.curve.linear)




class Floor(Entity):
    """All the blocks of the arena. Created after apply_floor_seed, so the layout is identical on every client."""

    def __init__(self):
        super().__init__()
        self.terrain = Entity(model=None, collider=None, parent=self)   # empty parent of all the blocks
        self.floor_cubes = []
        self.blocks_by_id = {}  # block_id -> FloorCube, to apply quickly the events of the authority
                                # (block_destroyed, sync_state)
        self.generate_floor()

    def generate_floor(self):
        """Generate the floor with random block types."""
        next_id = 0                                       # ids are assigned in the same order everywhere
        for floor in range(FLOOR_COUNT):
            y = floor * FLOOR_HEIGHT  # Set the height for each floor
            for z in range(-GRID_SIZE, GRID_SIZE):
                for x in range(-GRID_SIZE, GRID_SIZE):
                    block = FloorCube(Vec3(x * CUBE_SCALE, y, z * CUBE_SCALE), get_random_block_type(), next_id)
                    block.parent = self.terrain
                    self.floor_cubes.append(block)  # Add the block to the list
                    self.blocks_by_id[next_id] = block
                    next_id += 1

    def resetGame(self):
        """Reset the floor by destroying all floor cubes and regenerating the floor"""
        # Destroy all floor cubes
        for block in self.floor_cubes:
            if block is not None:
                destroy(block)
        self.floor_cubes.clear() 
        self.blocks_by_id.clear()

        # Destroy the terrain entity
        if self.terrain is not None:
            destroy(self.terrain)

        # Regenerate the floor
        self.terrain = Entity(model=None, collider=None, parent=self)
        self.generate_floor()