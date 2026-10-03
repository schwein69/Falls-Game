import os
import sys

_client_dir = os.path.dirname(os.path.abspath(__file__))
_shared_dir = os.path.join(_client_dir, "..", "shared")
_module_dir = os.path.join(_client_dir, "module")  

for _p in (_client_dir, _shared_dir, _module_dir):
    if os.path.isdir(_p):
        sys.path.insert(0, _p)

from ursina import *
from ursina import application
from pathlib import Path
from model.game_model import GameModel
from view.game_view import GameView
from controller.game_controller import GameController

app = Ursina()

application.asset_folder = Path(_client_dir)
application.models_compressed_folder = application.asset_folder / 'models_compressed/'
application.textures_compressed_folder = application.asset_folder / 'textures_compressed/'
application.scenes_folder = application.asset_folder / 'scenes/'
application.scripts_folder = application.asset_folder / 'scripts/'
application.fonts_folder = application.asset_folder / 'fonts/'
if hasattr(application, 'compressed_models_folder'):
    application.compressed_models_folder = application.models_compressed_folder

model = GameModel()
view = GameView(model)
controller = GameController(model, view)
view.controller = controller


def update():
    controller.update()


def input(key):
    controller.input(key)


def _on_app_exit():
    """BUG FIX: chiudere la finestra con la X o Alt+F4 terminava il processo di colpo, senza
    mai mandare il messaggio di addio (DISCONNECT) all'host — che quindi continuava a crederci
    connessi, a mandarci pacchetti per niente, e su Windows questo scatenava un diluvio di
    errori che confondeva la sua capacita' di riconoscere chi fosse ancora vivo tra TUTTI gli
    altri giocatori, non solo noi (pausa non immediata, falsi "disconnessi" a catena).

    base.exitFunc e' la funzione che Panda3D chiama SEMPRE quando la finestra si chiude,
    qualunque sia il modo (X, Alt+F4, chiusura da codice) — a differenza di agganciare il
    singolo evento "click sulla X", che puo' non scattare in tempo o venire scavalcato da un
    altro gestore interno a Panda3D (ShowBase chiude la finestra e basta, se arriva prima).
    """
    print(f"[DEBUG] _on_app_exit chiamato, network_manager presente: {model.network_manager is not None}")
    if model.network_manager is not None:
        model.network_manager.stop()
        print("[DEBUG] stop() completato")


base.exitFunc = _on_app_exit


if __name__ == '__main__':
    window.borderless = False
    window.title = "Fall Guys Ursina"
    window.exit_button.visible = False
    invoke(view.show_main_menu_screen, delay=0.1)
    app.run()