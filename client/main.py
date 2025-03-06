import os
import sys
import socket
import threading
import ursina
from player import Player
from floor import *
from light import *
from ursina.shaders import lit_with_shadows_shader 
from direct.stdpy import thread # we need threading to load entities in the background (this is specific to ursina, standard threading wouldn't work)
from ursina import *
from splashScreen import SplashScreen

loading_screen = None
local_play_button = None
online_play_button = None
game_title_text = None
        
def loadLevel():
    global loading_screen
    floor = Floor()
    sky = ursina.Entity(
        model="sphere",
        texture=os.path.join("assets", "sky.png"),
        scale=9999,
        double_sided=True
    )

    player = Player(ursina.Vec3(0, 30, 0),"Luca")
    player.shade = lit_with_shadows_shader  

    camera.z = -5
    prev_pos = player.world_position
    prev_dir = player.world_rotation_y
    enemies = []
    destroy(loading_screen) # delete the loading screen when finished
  
def showLoadingScreen():
    global loading_screen, local_play_button, online_play_button, game_title_text  # Referencing all global entities to destroy them
    # Destroy previous menu elements before showing loading screen
    destroy(local_play_button)
    destroy(online_play_button)
    destroy(game_title_text)
    ursina_splash = SplashScreen()
    # add a custom splash screen after the first one
    ursina_splash.on_destroy = Func(SplashScreen, 'shore')
    #loading_screen  = Entity(model='quad', texture='ursina_logo')
    # Wait for 3 seconds before loading the level
    ursina.invoke(loadLevel, delay=2)
    
  

def showMenu():
    global local_play_button, online_play_button, game_title_text  # Global references to menu entities
    # Create menu buttons and title text
    game_title_text = ursina.Text(text='Falls Game', scale=2, origin=(0, 0), position=(0, 0.3))
    local_play_button = ursina.Button(text='Local Play', scale=(0.3, 0.1), position=(0, 0.1), on_click=showLoadingScreen)
    online_play_button = ursina.Button(text='Online Play', scale=(0.3, 0.1), position=(0, -0.1))  # Placeholder
  
# Create the start menu
if __name__ == '__main__':
    app = Ursina()
    ursina.window.borderless = False
    ursina.window.title = "Falls Game - Ursina"
    ursina.window.exit_button.visible = False

    showMenu()

    app.run()
