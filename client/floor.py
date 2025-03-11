import ursina
from ursina import *

GRID_SIZE = 7
FLOOR_HEIGHT = 10
FLOOR_COUNT = 5 
CUBE_SCALE = 2
BLOCK_TYPES = ['normal', 'speed', 'superjump']

# Block probabilities
BLOCK_PROBABILITIES = [0.95, 0.025, 0.025]

def get_random_block_type():
    return random.choices(BLOCK_TYPES, weights=BLOCK_PROBABILITIES, k=1)[0]

class FloorCube(Entity):
    def __init__(self, position,block_type):
        super().__init__(
            position=position,
            scale=CUBE_SCALE,
            model="cube",
            #collider="box",
            texture = "white_cube"
        )
        self.block_type = block_type
        self.color = color.white if block_type == 'normal' else (color.green if block_type == 'speed' else color.blue)
        self.has_activated = False  
        self.activation_distance = 2.0  
        self.collider_added = False  
        self.collider = None  
        self.is_disappearing = False  # Flag to track whether the block is disappearing

    def distance_xz(a, b):
        return ((a.x - b.x)**2 + (a.z - b.z)**2)**0.5

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

    
    def disappear(self):
        self.is_disappearing = True
        self.animate('y', self.y - 0.25, duration=1)
        self.animate('color', color.gray, duration =1)
       # invoke(setattr, self, 'collider', None, delay=2)
        destroy(self, delay=1.5)
    
    def on_step(self, player):
        if self.is_disappearing:
            return
        
        if not self.has_activated and self.collider_added:
                if self.block_type == 'speed':
                    self.activate_speed(player)
                elif self.block_type == 'superjump':
                    self.super_jump(player)
                self.has_activated = True
                self.disappear()
        return
    
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
        self.terrain = Entity(model=None, collider=None)
        self.floor_cubes = []
        self.generate_floor()
        
    def generate_floor(self):
        """Generate the floor with random block types."""
        for floor in range(FLOOR_COUNT):
            y = floor * FLOOR_HEIGHT  # Set the height for each floor
            for z in range(-GRID_SIZE, GRID_SIZE):
                for x in range(-GRID_SIZE, GRID_SIZE):
                    block = FloorCube(Vec3(x * CUBE_SCALE, y, z * CUBE_SCALE), get_random_block_type())
                    block.parent = self.terrain
                    self.floor_cubes.append(block)  # Add the block to the list

        # terrain.combine()
        # terrain.collider = 'mesh'
        # terrain.texture = 'white_cube'

    def resetGame(self):
        """Reset the floor by destroying all floor cubes and regenerating the floor."""
        # Destroy all floor cubes
        for block in self.floor_cubes:
            if block:  # Check if the block exists
                destroy(block)
        self.floor_cubes.clear()  # Clear the list of floor cubes

        # Destroy the terrain entity
        if self.terrain:
            destroy(self.terrain)

        # Regenerate the floor
        self.terrain = Entity(model=None, collider=None)
        self.generate_floor()

                

