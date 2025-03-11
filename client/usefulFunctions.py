from numpy import *
from ursina import *
from floor import FLOOR_COUNT, FLOOR_HEIGHT, GRID_SIZE
def get_random_position():
    x = random.randint(-GRID_SIZE, GRID_SIZE)
    y = FLOOR_COUNT * FLOOR_HEIGHT
    z = random.randint(-GRID_SIZE, GRID_SIZE)
    return Vec3(x, y, z)

def to_first_person():
    """Switch to first-person camera view."""
    camera.position = (0, 0, 0)

def to_third_person():
    """Switch to third-person camera view."""
    camera.position = (0, 0, -5)
