import ursina
from ursina import *

GRID_SIZE = 7
FLOOR_HEIGHT = 10
FLOOR_COUNT = 5 
CUBE_SCALE = 2.5
BLOCK_TYPES = ['normal', 'speed', 'superjump']

# Block probabilities
BLOCK_PROBABILITIES = [0.95, 0.025, 0.025]

def get_random_block_type():
    return random.choices(BLOCK_TYPES, weights=BLOCK_PROBABILITIES, k=1)[0]

def distance_xz(a, b):
    """Distanza 2D sul piano X-Z (ignora l'altezza Y), usata per attivare/disattivare i collider."""
    return ((a.x - b.x) ** 2 + (a.z - b.z) ** 2) ** 0.5

def apply_floor_seed(seed):
    if seed is not None:
        random.seed(seed)

class FloorCube(Entity):
    def __init__(self, position, block_type, block_id):
        super().__init__(
            position=position,
            scale=CUBE_SCALE,
            model="cube",
            #collider="box",
            texture = "floortxt"
        )
        self.block_type = block_type
        self.block_id = block_id  # ID stabile, deciso in ordine di generazione: e' con questo
                                   # numero che client e autorita' si riferiscono allo STESSO
                                   # blocco fisico, indipendentemente da chi lo ha calpestato.
        self.color = color.white if block_type == 'normal' else (color.green if block_type == 'speed' else color.blue)
        self.has_activated = False  
        self.activation_distance = 2.0  
        self.collider_added = False  
        self.collider = None  
        self.is_disappearing = False  # Flag to track whether the block is disappearing
        self.step_requested = False   # Evita di rimandare piu' volte la stessa richiesta

    def updateColliders(self, player):
            buffer_distance = self.activation_distance + 1
            squared_buffer_distance = buffer_distance 
            distance = distance_xz(player.position, self.position)
            if distance <= squared_buffer_distance and not self.collider_added:
                self.collider = "box"  # Add collider dynamically
                self.collider_added = True

            # Remove collider if the player is far away and the collider has been added
            elif distance > squared_buffer_distance and self.collider_added:
                self.collider = None  # Remove collider dynamically
                self.collider_added = False

    def is_player_standing_on(self, player):
        if distance_xz(player.position, self.position) > self.activation_distance:
            return False
        top_y = self.y + CUBE_SCALE / 2
        return abs(player.y - top_y) < 1.0

    def disappear(self):
        self.is_disappearing = True
        self.animate('y', self.y - 0.20, duration=1)
        self.animate('color', color.gray, duration=1)
        destroy(self, delay=1.5)

    def force_destroy(self):
        self.is_disappearing = True
        self.has_activated = True
        destroy(self)

    def on_authoritative_step(self, local_player):
        if self.is_disappearing or self.has_activated:
            return
        if local_player is not None:
            if self.block_type == 'speed':
                self.activate_speed(local_player)
            elif self.block_type == 'superjump':
                self.super_jump(local_player)
        self.has_activated = True
        self.disappear()

    def activate_speed(self, player):
        if player.speed == 5:
            player.speed += 2
            invoke(setattr, player, 'speed', player.speed - 2, delay=2)
            
    def super_jump(self, player):
        # Get the forward direction (without the y component)
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
    def __init__(self):
        super().__init__()
        self.terrain = Entity(model=None, collider=None, parent=self)
        self.floor_cubes = []
        self.blocks_by_id = {}  # block_id -> FloorCube, per applicare rapidamente gli eventi
                                 # dell'autorita' di gioco (block_destroyed, sync_state)
        self.generate_floor()
        
    def generate_floor(self):
        """Generate the floor with random block types."""
        next_id = 0
        for floor in range(FLOOR_COUNT):
            y = floor * FLOOR_HEIGHT  # Set the height for each floor
            for z in range(-GRID_SIZE, GRID_SIZE):
                for x in range(-GRID_SIZE, GRID_SIZE):
                    block = FloorCube(Vec3(x * CUBE_SCALE, y, z * CUBE_SCALE), get_random_block_type(), next_id)
                    block.parent = self.terrain
                    self.floor_cubes.append(block)  # Add the block to the list
                    self.blocks_by_id[next_id] = block
                    next_id += 1

        # terrain.combine()
        # terrain.collider = 'mesh'
        # terrain.texture = 'white_cube'

    def resetGame(self):
        """Reset the floor by destroying all floor cubes and regenerating the floor."""
        # Destroy all floor cubes
        for block in self.floor_cubes:
            if block is not None:
                destroy(block)
        self.floor_cubes.clear()  # Clear the list of floor cubes
        self.blocks_by_id.clear()

        # Destroy the terrain entity
        if self.terrain is not None:
            destroy(self.terrain)

        # Regenerate the floor
        self.terrain = Entity(model=None, collider=None, parent=self)
        self.generate_floor()