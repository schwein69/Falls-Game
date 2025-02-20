import ursina
from ursina import *
from numpy import floor

GRID_SIZE = 18
FLOOR_HEIGHT = 10
FLOOR_COUNT = 3  
CUBE_SCALE = 1.5

class FloorCube(ursina.Entity):
    def __init__(self, position):
        super().__init__(
            position=position,
            scale=CUBE_SCALE,
            model="cube",
            collider="box"
        )
        


class Floor:
    def __init__(self):
        terrain = Entity(model=None, collider=None)
        for floor in range(FLOOR_COUNT):
          y = floor * FLOOR_HEIGHT  # Set the height for each floor
          grid_size = GRID_SIZE - floor * 5  # Increase grid size for each lower floor
          for z in range(-grid_size, grid_size):
              for x in range(-grid_size, grid_size):
                block = FloorCube(ursina.Vec3(x * CUBE_SCALE, y, z * CUBE_SCALE))
                block.parent = terrain
        terrain.combine()
        terrain.collider = 'mesh'
        terrain.texture = 'white_cube'

               


