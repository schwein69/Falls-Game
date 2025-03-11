import os
import sys
import socket
import threading
import ursina
from player import Player
from floor import *
from direct.stdpy import thread  # we need threading to load entities in the background (this is specific to ursina, standard threading wouldn't work)
from ursina import *
from splashScreen import SplashScreen
from usefulFunctions import *

loading_screen = None
local_play_button = None
online_play_button = None
game_title_text = None
player = None  # Declare player as a global variable
floor = None  # Declare floor as a global variable
sky = None # Declare sky as a global variable
game_over = False # Declare game over as a global variable
level_loaded = False

def loadLevel():
    global loading_screen, player, floor, sky , level_loaded ,game_over  # Reference global variables
    destroy(loading_screen)  # delete the loading screen when finished
    game_over = False,
    level_loaded = True
    floor = Floor()
    sky = ursina.Entity(
        model="sphere",
        texture=os.path.join("assets", "sky.png"),
        scale=9999,
        double_sided=True
    )

    player = Player(get_random_position(), "Luca")

    camera.z = -5
    prev_pos = player.world_position
    prev_dir = player.world_rotation_y
    enemies = []

    
    

def showLoadingScreen():
    global loading_screen, local_play_button, online_play_button, game_title_text  # Referencing all global entities to destroy them
    # Destroy previous menu elements before showing loading screen
    destroy(local_play_button)
    destroy(online_play_button)
    destroy(game_title_text)
    ursina_splash = SplashScreen()
    # add a custom splash screen after the first one
    ursina_splash.on_destroy = Func(SplashScreen, 'shore')
    # loading_screen  = Entity(model='quad', texture='ursina_logo')
    # Wait for 3 seconds before loading the level
    ursina.invoke(loadLevel, delay=2)

def showMenu():
    global local_play_button, online_play_button, game_title_text  # Global references to menu entities
    # Create menu buttons and title text
    game_title_text = ursina.Text(text='Falls Game', scale=2, origin=(0, 0), position=(0, 0.3))
    local_play_button = ursina.Button(text='Local Play', scale=(0.3, 0.1), position=(0, 0.1), on_click=showLoadingScreen)
    online_play_button = ursina.Button(text='Online Play', scale=(0.3, 0.1), position=(0, -0.1))  # Placeholder

def resetPlayer():
    global player, floor, loading_screen, local_play_button, online_play_button, game_title_text, game_over, level_loaded, sky
    player = None  # Clear the player reference
    destroy(sky)
    sky = None  # Clear the sky reference
    game_over = False  # Reset the game_over flag
    level_loaded = False  # Reset the level_loaded flag
    
def resetFloor():
    global floor
    floor.resetGame()
    floor = None  
    showMenu()
def update():
    global player, floor, game_over, level_loaded
    
    if game_over == True:
        return
    if level_loaded == False:
        return
        
    if not player or not floor:  # Prevent running update logic if player is None
        return
    
    for entity in scene.entities:
        if isinstance(entity, FloorCube):
            entity.updateColliders(player)
            
    if player and floor:
        hit_info = player.intersects()
        if hit_info.hit and isinstance(hit_info.entity, FloorCube):
            hit_info.entity.on_step(player)
            
    if player.world_position.y < -20:
        player.gameOver()
  
        
        


def input(key):
    global player  
    if key == '1':
        to_first_person()
    if key == '3':
        to_third_person()


if __name__ == '__main__':
    app = Ursina()
    window.borderless = False
    window.title = "Fall guys ursina"
    window.exit_button.visible = False
    window.entity_counter.visible = True
    window.collider_counter.visible = False

    showMenu()
    
    app.run()