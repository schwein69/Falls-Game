import os
from ursina import *
from player import Player
from floor import Floor
from splashScreen import SplashScreen
from usefulFunctions import *
from floor import *

startGame = False
class Game(Entity):
    def __init__(self):
        super(Game, self).__init__()
        # Game state variables
        self.player = None
        self.floor = None
        self.sky = None
        self.game_over = False
        self.game_start = False

        # UI elements
        self.loading_screen = None
        self.local_play_button = None
        self.online_play_button = None
        self.game_title_text = None
        self.show_menu()

    def load_level(self):
        """Load the game level, including the player, floor, and sky."""
       

        # Destroy the loading screen
        if self.loading_screen:
            destroy(self.loading_screen)

        # Initialize floor and sky
        self.floor = Floor()
        self.sky = Entity(
            model="sphere",
            texture=os.path.join("assets", "sky.png"),
            scale=9999,
            double_sided=True
        )

        # Initialize player
        self.player = Player(get_random_position(), "Luca")
        # Set up camera
        camera.z = -5
        self.game_over = False
        invoke(setattr,self, "game_start", True, delay = 5)#TODO COORDINATE MULTIPLAY

       
       
    def show_loading_screen(self):
        """Show the loading screen and start loading the level."""
        # Destroy previous menu elements
        if self.local_play_button:
            destroy(self.local_play_button)
        if self.online_play_button:
            destroy(self.online_play_button)
        if self.game_title_text:
            destroy(self.game_title_text)

        # Show the Ursina splash screen
        ursina_splash = SplashScreen()
        ursina_splash.on_destroy = Sequence(
        Func(SplashScreen, 'shore'),  # Call the first function
        Func(self.load_level)         # Call the second function
        )
        
        

    def show_menu(self):
        """Show the main menu with play options."""
        # Create menu buttons and title text
        self.game_title_text = Text(text='Falls Game', scale=2, origin=(0, 0), position=(0, 0.3))
        self.local_play_button = Button(text='Local Play', scale=(0.3, 0.1), position=(0, 0.1), on_click=self.show_loading_screen)
        self.online_play_button = Button(text='Online Play', scale=(0.3, 0.1), position=(0, -0.1))  # Placeholder

    def reset_player(self):
        """Reset the player and related game state."""
        if self.player is not None:
            destroy(self.player)
        self.player = None

        if self.sky:
            destroy(self.sky)
        self.sky = None

        self.game_over = False
        self.game_start = False

    def reset_floor(self):
        """Reset the floor and return to the main menu."""
        if self.floor is not None :
            destroy(self.floor)
        self.floor = None


    def update(self):
        """Update the game state every frame."""
        if  self.game_over:
            print("Game over!")
            self.reset_player()
            self.reset_floor()
            self.show_menu()
            return  # Do nothing if the level is not loaded or the game is over

        if  self.player is not None and self.game_start == False:
            self.player.gravity = 0
            return
        if self.game_start == True:
            self.player.gravity = 1
            # Check for collisions with floor cubes
            for entity in scene.entities:
                if isinstance(entity, FloorCube):
                    entity.updateColliders(self.player)         

            # Check if the player intersects with the floor
            hit_info = self.player.intersects()
            if hit_info.hit and isinstance(hit_info.entity, FloorCube):
                hit_info.entity.on_step(self.player)

            # Check if the player falls off the floor
            if self.player.y < -20:
                self.player.gameOver()
                self.game_over = True

    def input(self, key):
        """Handle player input."""
        if key == '1':
            to_first_person()
        if key == '3':
            to_third_person()



# Main entry point
if __name__ == '__main__':
    app = Ursina()

    # Configure the game window
    window.borderless = False
    window.title = "Fall Guys Ursina"
    window.exit_button.visible = False
    window.entity_counter.visible = True
    window.collider_counter.visible = False

    # Initialize the game
    game = Game()
    app.run()