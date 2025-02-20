import os
import sys
import socket
import threading
from ursina import Ursina
from player import Player
from floor import *
from light import *
from ursina.shaders import lit_with_shadows_shader 

def to_first_person():
    camera.position = (0, 0, 0)

def to_third_person():
    camera.position = (0, 0, -5)

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

# def update():
#     hit_info = player.intersects(floor)
#     if hit_info.hit and isinstance(hit_info.entity, FloorCube):
#         hit_info.entity.on_step(player)
def update():
    hit_info = player.intersects()
    if hit_info.hit and isinstance(hit_info.entity, FloorCube):
        hit_info.entity.on_step(player)
# def update():
#     if player.intersects(floor).hit:
#         print("Player is colliding with block!")

def input(key):
    if key == '1':
        to_first_person()
    if key == '3':
        to_third_person()
app.run()



