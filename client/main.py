import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "module"))

from ursina import *
from floor import FloorCube
import game_state
from rpc_handlers import handle_rpc, request_block_step, request_player_died
from migration import check_host_migration
from game_lifecycle import resetFloor
from screen import show_main_menu_screen

app = Ursina()


# --------------------------
# GAME UPDATE LOOP
# --------------------------
def update():
    nm = game_state.network_manager
    if nm:
        nm.process_queue(handle_rpc)
        check_host_migration()

    if not game_state.level_loaded or game_state.player is None or game_state.game_over or game_state.game_paused:
        return

    player = game_state.player

    for entity in scene.entities:
        if isinstance(entity, FloorCube):
            entity.updateColliders(player)
            if (entity.collider_added and not entity.has_activated
                    and not entity.step_requested and not entity.is_disappearing):
                if entity.is_player_standing_on(player):
                    entity.step_requested = True
                    request_block_step(entity.block_id)

    if not game_state.player_died_reported and player.world_position.y < -20:
        game_state.player_died_reported = True
        player.gameOver()
        request_player_died()

    if game_state.game_over or game_state.player is None:
        return

    # Segnaliamo la nostra posizione all'autorita' di gioco ogni frame.
    if nm and nm.player_id is not None:
        nm.send_rpc("update_pos", nm.player_id, player.x, player.y, player.z)
        if nm.is_host and game_state.game_authority is not None:
            game_state.game_authority.handle_update_pos(nm.player_id, player.x, player.y, player.z)
            from rpc_handlers import rpc_state_snapshot
            rpc_state_snapshot(game_state.game_authority.snapshot_payload())


def input(key):
    if key == 'escape' and game_state.level_loaded:
        resetFloor()


if __name__ == '__main__':
    window.borderless = False
    window.title = "Fall Guys Ursina"
    window.exit_button.visible = False
    invoke(show_main_menu_screen, delay=0.1)
    app.run()