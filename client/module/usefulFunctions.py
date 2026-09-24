import random as _rng_module
from ursina import *
from floor import GRID_SIZE, CUBE_SCALE
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
from config import SPAWN_HEIGHT

_spawn_rng = _rng_module.Random()

def get_random_position():
    x = _spawn_rng.randint(-GRID_SIZE, GRID_SIZE - 1) * CUBE_SCALE
    y = SPAWN_HEIGHT
    z = _spawn_rng.randint(-GRID_SIZE, GRID_SIZE - 1) * CUBE_SCALE
    return Vec3(x, y, z)

def to_first_person():
    """Switch to first-person camera view."""
    camera.position = (0, 0, 0)

def to_third_person():
    """Switch to third-person camera view."""
    camera.position = (0, 0, -5)
    
