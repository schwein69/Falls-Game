import ursina
from ursina import *
from numpy import floor

GRID_SIZE = 18
FLOOR_HEIGHT = 10
FLOOR_COUNT = 3  
CUBE_SCALE = 1.5
BLOCK_TYPES = ['normal', 'speed', 'superjump']

# Block probabilities
BLOCK_PROBABILITIES = [0.95, 0.025, 0.025]

def get_random_block_type():
    return random.choices(BLOCK_TYPES, weights=BLOCK_PROBABILITIES, k=1)[0]

class FloorCube(ursina.Entity):
    def __init__(self, position,block_type):
        super().__init__(
            position=position,
            scale=CUBE_SCALE,
            model="plane",
            collider="box",
            texture = "white_cube"
        )
        self.block_type = block_type
        self.color = color.white if block_type == 'normal' else (color.green if block_type == 'speed' else color.blue)
        self.has_activated = False  # Flag to track whether the effect was activated
        
    def disappear(self):
        self.animate('color', color.clear, duration=1)
        invoke(setattr, self, 'collider', None, delay=1)
        destroy(self, delay=1.5)
    
    def on_step(self, player):
        # if not self.has_activated:
        #         if self.block_type == 'speed':
        #             self.activate_speed(player)
        #         elif self.block_type == 'superjump':
        #             self.super_jump(player)
        #         self.has_activated = True
        #         self.disappear()
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




class Floor:
    def __init__(self):
        terrain = Entity(model=None, collider=None)
        for floor in range(FLOOR_COUNT):
          y = floor * FLOOR_HEIGHT  # Set the height for each floor
          grid_size = GRID_SIZE - floor * 5  # Increase grid size for each lower floor
          for z in range(-grid_size, grid_size):
              for x in range(-grid_size, grid_size):
                block = FloorCube(ursina.Vec3(x * CUBE_SCALE, y, z * CUBE_SCALE), get_random_block_type())
                block.parent = terrain
        # terrain.combine()
        # terrain.collider = 'mesh'
        # terrain.texture = 'white_cube'

               

