import ursina
from ursina import *
import os
from ursina.prefabs.first_person_controller import FirstPersonController
from inGameGui import InGameGui

class Player(FirstPersonController):
    def __init__(self, position: Vec3, username: str):
        super().__init__(
            position=position,
            model= "collisionModel", 
            jump_height=2.5,
            jump_duration=0.4,
            # origin_y= -0.2,
            collider="capsule",
            speed=5
        )

        random_color = color.rgb(random.random(), random.random(), random.random())
        self.cursor.color = random_color
        self.gui = InGameGui(username=username, player_entity=self)
        self.death_message_shown = False
        self.can_dash = True  
        self.is_jumping = False  

        # Load animations 
        self.idleAnimation = FrameAnimation3d("idle_", fps=24, loop=True, autoplay=True,
                                             frame_times=50, texture="mini_material_baseColor",origin_y= -0.2)
        self.walkingAnimation = FrameAnimation3d("untitled_", fps=24, loop=True, autoplay=True,
                                                  frame_times=50, texture="mini_material_baseColor",origin_y= -0.2)
        self.runningAnimation = FrameAnimation3d("run_", fps=24, loop=True, autoplay=True,
                                                 frame_times=50, texture="mini_material_baseColor",origin_y= -0.2)
        self.jumpingAnimation = FrameAnimation3d("jump_", fps=24, loop=True, autoplay=True,
                                                 frame_times=50, texture="mini_material_baseColor",origin_y= -0.2)


    def update(self):
        if self.gui and self.gui.namePlate:
            self.gui.namePlate.world_position = self.world_position + Vec3(0, 1.5, 0)

        if self.grounded:
            self.can_dash = True

        is_jumping = not self.grounded

        is_walking = held_keys['w'] or held_keys['a'] or held_keys['s'] or held_keys['d']

        # # Determine which animation to play based on speed
        if not is_walking:
            self.switch_animation(self.idleAnimation)
        elif is_walking and self.speed > 5:
            self.switch_animation(self.runningAnimation)
        elif is_walking and self.speed <= 5:
            self.switch_animation(self.walkingAnimation)

        if is_jumping:
            self.switch_animation(self.jumpingAnimation)
            
        if mouse.left and self.can_dash and is_jumping:
            direction = Vec3(self.forward.x, 0, self.forward.z).normalized()
            dash_distance = 5
            dash_duration = 0.3
            self.switch_animation(self.jumpingAnimation)
            self.animate_position(self.position + direction * dash_distance, duration=dash_duration, curve=curve.linear)
            self.can_dash = False
        else:
            super().update()

    def switch_animation(self, new_animation):
        self.model = new_animation

    def gameOver(self):
        if not self.death_message_shown:
            self.death_message_shown = True
            Text(
                text="Game over!",
                origin=Vec2(0, 0),
                scale=3
            )