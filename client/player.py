import ursina
from ursina import *
import os
from ursina.prefabs.first_person_controller import FirstPersonController

ICONSCALE = Vec2(0.05, 0.05)

class Player(FirstPersonController):
    def __init__(self, position: ursina.Vec3, username: str):
        super().__init__(
            position=position,
            model='sphere',#os.path.join("assets", "fallguys.obj"),
            jump_height=2.5,
            jump_duration=0.4,
            origin_y=-0.5,
            collider="box",
            speed=5
        )
        # Generate a random RGB color
        random_color = ursina.color.rgb(random.random(), random.random(), random.random())
        self.cursor.color = random_color
        self.color = random_color

        # Player nameplate
        self.namePlate = Text(
            text=username,
            parent=self,
            position=self.position,  # Position above the player
            scale=5,
            color=random_color,
            origin=(0, 0),
            billboard=True  # Ensure text always faces the camera
        )

        # Create a Quad entity as the container for the jump and dash controls
        self.controls_container = Entity(
            parent=ursina.camera.ui,
            model='quad',
            #color=color.rgba(255, 255, 255, 100),  # Semi-transparent black background
            position=Vec2(0.6, -0.4),  # Position of the container
            scale=Vec2(0.20, 0.15)  # Scale of the container
        )

        # Jump icon (spacebar)
        self.jump_icon = Entity(
            parent=self.controls_container,
            model='quad',
            texture='spaceIcon.png',  # Replace with your spacebar icon texture
            position=Vec2(-0.3, 0.1),  # Positioned relative to the container
            scale=Vec2(0.5, 0.5)
        )

        # Text under the jump icon
        self.jump_text = Text(
            text="Jump",
            parent=self.controls_container,
            position=Vec2(-0.3, -0.1),  # Positioned below the jump icon
            scale=3,
            color=color.black
        )

        # Dash icon (mouse click)
        self.dash_icon = Entity(
            parent=self.controls_container,
            model='quad',
            texture='dashIcon.png',  # Replace with your mouse click icon texture
            position=Vec2(0.3, 0.1),  # Positioned relative to the container
            scale=Vec2(0.5, 0.5)
        )

        # Text under the dash icon
        self.dash_text = Text(
            text="Dash",
            parent=self.controls_container,
            position=Vec2(0.3, -0.1),  # Positioned below the dash icon
            scale=3,
            color=color.black
        )
        
        # Display number of players in the game
        self.player_count_text = Text(
            text="Players: 1",
            position=Vec2(-0.8, 0.45),  # Top left corner
            scale=1
        )

        self.health = 100
        self.death_message_shown = False
        self.is_jumping = False  # Track if player is jumping
        self.can_dash = True  # Allows dashing once per jump


    def update(self):
        
        self.namePlate.world_position = self.world_position + Vec3(0, 1.5, 0)
        
         # If the player is on the ground, reset dash ability
        if self.grounded:
            self.can_dash = True
            
        is_jumping = not self.grounded  # True if player is NOT on the ground
        
        # If jumping and left mouse is clicked, boost in camera direction
        if ursina.mouse.left and self.can_dash and is_jumping :
            direction = ursina.Vec3(self.forward.x, 0, self.forward.z).normalized()  # Get forward direction
            dash_distance = 5  # Adjust how far the dash moves
            dash_duration = 0.3  # Time it takes to complete the dash
            # Animate player's position smoothly
            self.animate_position(self.position + direction * dash_distance, duration=dash_duration, curve=ursina.curve.linear)
            self.can_dash = False  # Disable dashing until grounded again
        else:
            super().update()
               # Check if the player's y position is below -20
        if self.y < -20:
            self.gameOver()

        
        
    def gameOver(self):
     if not self.death_message_shown:
            self.death_message_shown = True
            ursina.Text(
                text="Game over!",
                origin=ursina.Vec2(0, 0),
                scale=3
            )


