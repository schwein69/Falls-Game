import ursina
from ursina import *
import os
from ursina.prefabs.first_person_controller import FirstPersonController

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
        # self.gun = ursina.Entity(
        #     parent=ursina.camera.ui,
        #     position=ursina.Vec2(0.6, -0.45),
        #     scale=ursina.Vec3(0.1, 0.2, 0.65),
        #     rotation=ursina.Vec3(-20, -20, -5),
        #     model="cube",
        #     texture="white_cube",
        #     color=ursina.color.color(0, 0, 0.4)
        # )

        self.healthbar_pos = ursina.Vec2(0, 0.45)
        self.healthbar_size = ursina.Vec2(0.8, 0.04)
        self.healthbar_bg = ursina.Entity(
            parent=ursina.camera.ui,
            model="quad",
            color=ursina.color.rgb(255, 0, 0),
            position=self.healthbar_pos,
            scale=self.healthbar_size
        )
        self.healthbar = ursina.Entity(
            parent=ursina.camera.ui,
            model="quad",
            color=ursina.color.rgb(0, 255, 0),
            position=self.healthbar_pos,
            scale=self.healthbar_size
        )


        self.nameplate = Text(
            text=username,
            parent=self,
            position=self.position,  # Position above the player
            scale=5,
            color=random_color,
            origin=(0, 0),
            billboard=True  # Ensure text always faces the camera
        )

        self.health = 100
        self.death_message_shown = False
        self.is_jumping = False  # Track if player is jumping
        self.can_dash = True  # Allows dashing once per jump


    def update(self):
        
        self.nameplate.world_position = self.world_position + Vec3(0, 1.5, 0)
        
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

        
        
    def death(self):
        self.death_message_shown = True

        ursina.destroy(self.gun)
        self.rotation = 0
        self.camera_pivot.world_rotation_x = -45
        self.world_position = ursina.Vec3(0, 7, -35)
        self.cursor.color = ursina.color.rgb(0, 0, 0, a=0)

        ursina.Text(
            text="You are dead!",
            origin=ursina.Vec2(0, 0),
            scale=3
        )

