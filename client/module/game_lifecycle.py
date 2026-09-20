import os
from ursina import *
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
from config import SPAWN_HEIGHT
from player import Player
from floor import Floor, FLOOR_COUNT, FLOOR_HEIGHT, apply_floor_seed
from usefulFunctions import get_random_position
import game_state
from usefulFunctions import clear_all_ui_elements


def loadLevel(floor_seed=None):
    clear_all_ui_elements()
    game_state.player_died_reported = False

    apply_floor_seed(floor_seed)
    game_state.floors = Floor()
    game_state.sky = Entity(
        model="sphere",
        texture=os.path.join("assets", "sky.png"),
        scale=9999,
        double_sided=True
    )

    start_pos = get_random_position()

    top_floor_y = (FLOOR_COUNT - 1) * FLOOR_HEIGHT
    for cube in game_state.floors.floor_cubes:
        if abs(cube.x - start_pos.x) < 0.01 and abs(cube.z - start_pos.z) < 0.01 and abs(cube.y - top_floor_y) < 0.01:
            cube.collider = "box"
            cube.collider_added = True
            break

    game_state.player = Player(start_pos, f"Player {game_state.network_manager.player_id}")

    game_state.player.gravity = 0
    camera.z = -5
    game_state.game_over = False

    invoke(gameStart, delay=0.1)


def gameStart():
    game_state.level_loaded = True
    game_state.player.gravity = 1

    from rpc_handlers import rpc_spawn_player

    for pid in game_state.connected_ids:
        if pid not in game_state.other_players and pid != game_state.network_manager.player_id:
            rpc_spawn_player(pid, 0, SPAWN_HEIGHT, 0)

    # Notify others of our spawn
    if game_state.network_manager:
        p = game_state.player
        game_state.network_manager.send_rpc("spawn_player", game_state.network_manager.player_id,
                                             p.position.x, p.position.y, p.position.z)


def resetFloor():
    if game_state.player is not None: destroy(game_state.player)
    if game_state.floors is not None: destroy(game_state.floors)
    if game_state.sky is not None: destroy(game_state.sky)
    for p in game_state.other_players.values(): destroy(p)

    game_state.other_players.clear()
    game_state.connected_ids.clear()
    game_state.player = None
    game_state.floors = None
    game_state.sky = None
    game_state.level_loaded = False
    game_state.game_over = False

    if game_state.network_manager is not None:
        game_state.network_manager.stop()
        game_state.network_manager = None
    game_state.game_authority = None
    if game_state.broadcaster is not None:
        game_state.broadcaster.stop()
        game_state.broadcaster = None
    if game_state.current_listener is not None:
        game_state.current_listener.stop()
        game_state.current_listener = None

    from screen import show_main_menu_screen
    invoke(show_main_menu_screen, delay=1)