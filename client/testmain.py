import os
import sys
import socket
import threading
from ursina import Ursina
from player import Player
from floor import *
from light import *
from ursina.shaders import lit_with_shadows_shader 


def main_game():
    global app  # Ensure app is accessible
    app = ursina.Ursina()
    ursina.window.borderless = False
    ursina.window.title = "Fall guys ursina"
    ursina.window.exit_button.visible = False
   
    floor = Floor()
    sky = ursina.Entity(
        model="sphere",
        texture=os.path.join("assets", "sky.png"),
        scale=9999,
        double_sided=True
    )

    player = Player(ursina.Vec3(0, 30, 0))
    player.shade = lit_with_shadows_shader  

    camera.z = -5
    prev_pos = player.world_position
    prev_dir = player.world_rotation_y
    enemies = []



    app.run()


main_game()  # Start game only if run directly
