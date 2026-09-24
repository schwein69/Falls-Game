import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "model"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "view"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "controller"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "module"))

from ursina import *
from game_model import GameModel
from game_view import GameView
from game_controller import GameController

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
    window.title = "Fall Guys Ursina"
    window.exit_button.visible = False
    invoke(view.show_main_menu_screen, delay=0.1)
    app.run()