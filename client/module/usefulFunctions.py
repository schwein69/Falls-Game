import random as _rng_module
from ursina import *
from floor import GRID_SIZE, CUBE_SCALE
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
import game_state
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
    

def clear_all_ui_elements():
    for e in camera.ui.children:
        destroy(e)
    game_state.guiElements.clear()
    game_state.player_labels.clear()
    game_state.lobby_buttons.clear()


def display_error_message_screen(message, on_close_callback):
    clear_all_ui_elements()
    msg = Text(message, scale=1.2, origin=(0, 0), position=(0, 0.1),
               parent=camera.ui, color=color.red)
    btn = Button('Back', scale=(0.3, 0.1), position=(0, -0.1),
                 parent=camera.ui, on_click=on_close_callback)
    game_state.guiElements.extend([msg, btn])