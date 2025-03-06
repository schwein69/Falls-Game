from numpy import *
from ursina import *

def get_random_position():
    x = random.randint(-5, 5)
    y = 30
    z = random.randint(-5, 5)
    return Vec3(x, y, z)