import ursina
from ursina import *
import os
from ursina.prefabs.first_person_controller import FirstPersonController
from inGameGui import InGameGui

ICONSCALE = Vec2(0.05, 0.05)
class Player(FirstPersonController):
    def __init__(self, position: Vec3, username: str):
        super().__init__(
            position=position,
            model='sphere',
            jump_height=2.5,
            jump_duration=0.4,
            origin_y=-0.5,
            collider="box",
            speed=5
        )
        random_color = color.rgb(random.random(), random.random(), random.random())
        self.cursor.color = random_color
        self.color = random_color
        # self.gravity = 0
        self.gui = InGameGui(username)
        self.death_message_shown = False
        self.is_jumping = False  # Track if player is jumping
        self.can_dash = True  # Allows dashing once per jump


    def update(self):
        if self.gui and self.gui.namePlate:
            self.gui.namePlate.world_position = self.world_position + Vec3(0, 1.5, 0)
        
        if self.grounded:
            self.can_dash = True
        
        is_jumping = not self.grounded
        
        if mouse.left and self.can_dash and is_jumping:
            direction = Vec3(self.forward.x, 0, self.forward.z).normalized()
            dash_distance = 5
            dash_duration = 0.3
            self.animate_position(self.position + direction * dash_distance, duration=dash_duration, curve=curve.linear)
            self.can_dash = False
        else:
            super().update()
        
        # if self.y < -20:
        #     self.gameOver()
        
    def gameOver(self):
        if not self.death_message_shown:
            self.death_message_shown = True
            Text(
                text="Game over!",
                origin=Vec2(0, 0),
                scale=3
            )
    #         invoke(self.resetGame, delay=2)  # Wait 2 seconds before returning to menu
             
    # def resetGame(self):
    #     destroy(self)