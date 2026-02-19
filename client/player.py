from ursina import *
from ursina.prefabs.first_person_controller import FirstPersonController
from inGameGui import InGameGui
import random

class Player(FirstPersonController):
    def __init__(self, position: Vec3, username: str):
        super().__init__(
            position=position,
            model="collisionModel",
            jump_height=2.5,
            jump_duration=0.4,
            collider="capsule",
            speed=5
        )

        # Random cursor color
        random_color = color.rgb(random.random(), random.random(), random.random())
        if hasattr(self, 'cursor') and self.cursor:
            self.cursor.color = random_color

        # GUI Setup
        self.gui = None
        self.namePlate = None
        try:
            self.gui = InGameGui(username=username, player_entity=self)
            self.namePlate = getattr(self.gui, "namePlate", None)
        except Exception as e:
            print(f"[WARNING] Failed to initialize InGameGui: {e}")

        # State
        self.death_message_shown = False
        self.can_dash = True
        
        self.load_animations()
     
    def load_animations(self):
        try:
            # Using the exact names you provided
            self.idleAnimation = FrameAnimation3d("idle_", fps=24, loop=True, autoplay=True, frame_times=50, texture="mini_material_baseColor", origin_y=-0.2)
            self.walkingAnimation = FrameAnimation3d("untitled_", fps=24, loop=True, autoplay=True, frame_times=50, texture="mini_material_baseColor", origin_y=-0.2)
            self.runningAnimation = FrameAnimation3d("run_", fps=24, loop=True, autoplay=True, frame_times=50, texture="mini_material_baseColor", origin_y=-0.2)
            self.jumpingAnimation = FrameAnimation3d("jump_", fps=24, loop=True, autoplay=True, frame_times=50, texture="mini_material_baseColor", origin_y=-0.2)
        except:
            print("Animations failed to load")
            self.idleAnimation = None

    def update(self):
        # Nameplate
        if getattr(self, 'gui', None) and getattr(self.gui, 'namePlate', None):
            self.gui.namePlate.world_position = self.world_position + Vec3(0, 1.5, 0)

        # Logic
        self.can_dash = self.grounded
        is_jumping = not self.grounded
        is_walking = held_keys['w'] or held_keys['a'] or held_keys['s'] or held_keys['d']

        # Animation Switching
        if is_jumping and self.jumpingAnimation:
            self.switch_animation(self.jumpingAnimation)
        elif not is_walking and self.idleAnimation:
            self.switch_animation(self.idleAnimation)
        elif is_walking and self.speed > 5 and self.runningAnimation:
            self.switch_animation(self.runningAnimation)
        elif is_walking and self.speed <= 5 and self.walkingAnimation:
            self.switch_animation(self.walkingAnimation)

        # Dash
        if mouse.left and self.can_dash and is_jumping:
            direction = Vec3(self.forward.x, 0, self.forward.z).normalized()
            self.animate_position(self.position + direction * 5, duration=0.3, curve=curve.linear)
            self.can_dash = False

        super().update()

    def switch_animation(self, new_animation):
        if self.model != new_animation:
            self.model = new_animation

    def gameOver(self):
        if not self.death_message_shown:
            self.death_message_shown = True
            Text(text="Game over!", origin=Vec2(0, 0), scale=3)


# =========================================================
# REMOTE PLAYER 
# =========================================================
class RemotePlayer(Entity):
    def __init__(self, position: Vec3, username: str):
        super().__init__(
            position=position,
            model=None,           
            collider="capsule",      
            scale=1
        )
        self.username = username
        self.name_tag = Text(text=username, parent=scene, y=2.5, scale=5, billboard=True, color=color.white)
        
        self.prev_pos = position
        
        # Load animations (Same as before)
        self.load_animations()

    def load_animations(self):
        try:
            # Note: Ensure texture names are correct
            self.idleAnimation = FrameAnimation3d("idle_", fps=24, loop=True, autoplay=True, texture="mini_material_baseColor", parent=self, origin_y=-0.2)
            self.walkingAnimation = FrameAnimation3d("untitled_", fps=24, loop=True, autoplay=True, texture="mini_material_baseColor", parent=self, origin_y=-0.2)
            self.runningAnimation = FrameAnimation3d("run_", fps=24, loop=True, autoplay=True, texture="mini_material_baseColor", parent=self, origin_y=-0.2)
            self.jumpingAnimation = FrameAnimation3d("jump_", fps=24, loop=True, autoplay=True, texture="mini_material_baseColor", parent=self, origin_y=-0.2)
            self.switch_animation(self.idleAnimation)
        except:
            print(f"Remote Animations failed to load")

    def switch_animation(self, new_animation):
        animations = [self.idleAnimation, self.walkingAnimation, self.runningAnimation, self.jumpingAnimation]
        for anim in animations:
            if anim:
                anim.enabled = (anim == new_animation)

    def update(self):
        self.name_tag.world_position = self.world_position + Vec3(0, 1.5, 0)

        move_vec = self.position - self.prev_pos
        horizontal_speed = Vec3(move_vec.x, 0, move_vec.z).length()
        
        if horizontal_speed > 0.001:
            self.look_at(self.position + Vec3(move_vec.x, 0, move_vec.z), axis='forward')
            
            self.rotation_x = 0
            self.rotation_z = 0
        # Animation Logic
        if self.position.y > 1.0 and abs(move_vec.y) > 0.01: 
             self.switch_animation(self.jumpingAnimation)
        elif horizontal_speed > 0.1:
             self.switch_animation(self.runningAnimation)
        elif horizontal_speed > 0.001:
             self.switch_animation(self.walkingAnimation)
        else:
             self.switch_animation(self.idleAnimation)

        self.prev_pos = self.position