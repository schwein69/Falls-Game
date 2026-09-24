import os
import sys

_client_dir = os.path.dirname(os.path.abspath(__file__))
_shared_dir = os.path.join(_client_dir, "..", "shared")
_module_dir = os.path.join(_client_dir, "module")  

for _p in (_client_dir, _shared_dir, _module_dir):
    if os.path.isdir(_p):
        sys.path.insert(0, _p)

from ursina import *
from model.game_model import GameModel
from view.game_view import GameView
from controller.game_controller import GameController

app = Ursina()

model = GameModel()
view = GameView(model)
controller = GameController(model, view)
view.controller = controller


def update():
    controller.update()


def input(key):
    controller.input(key)


if __name__ == '__main__':
    window.borderless = False
    window.title = "Falls Game"
    window.exit_button.visible = False
    invoke(view.show_main_menu_screen, delay=0.1)
    app.run()