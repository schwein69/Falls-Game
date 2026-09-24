from ursina import *
from ursina.prefabs.first_person_controller import FirstPersonController
from inGameUi import InGameGui
import random

ANIMATION_FILES = {
    "idle": "idle_",
    "walking": "untitled_",
    "running": "run_",
    "jumping": "jump_",
}
ANIMATION_TEXTURE = "mini_material_baseColor"


class AnimatedCharacterMixin:

    def load_animations(self, frame_times=None):
        self.idleAnimation = None
        self.walkingAnimation = None
        self.runningAnimation = None
        self.jumpingAnimation = None
        try:
            kwargs = dict(fps=24, loop=True, autoplay=True, texture=ANIMATION_TEXTURE,
                          parent=self, origin_y=-0.2)
            if frame_times is not None:
                kwargs["frame_times"] = frame_times
            self.idleAnimation = FrameAnimation3d(ANIMATION_FILES["idle"], **kwargs)
            self.walkingAnimation = FrameAnimation3d(ANIMATION_FILES["walking"], **kwargs)
            self.runningAnimation = FrameAnimation3d(ANIMATION_FILES["running"], **kwargs)
            self.jumpingAnimation = FrameAnimation3d(ANIMATION_FILES["jumping"], **kwargs)
            self.switch_animation(self.idleAnimation)
        except Exception as e:
            print(f"Caricamento animazioni fallito: {e}")

    def switch_animation(self, new_animation):
        for anim in (self.idleAnimation, self.walkingAnimation, self.runningAnimation, self.jumpingAnimation):
            if anim is not None:
                anim.enabled = (anim == new_animation)


class Player(FirstPersonController, AnimatedCharacterMixin):
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
        if getattr(self, 'cursor', None) is not None:
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

        self.load_animations(frame_times=50)

    def update(self):
        # Nameplate
        if getattr(self, 'gui', None) is not None and getattr(self.gui, 'namePlate', None) is not None:
            self.gui.namePlate.world_position = self.world_position + Vec3(0, 1.5, 0)

        if self.grounded:
            self.can_dash = True
        is_jumping = not self.grounded
        is_walking = held_keys['w'] or held_keys['a'] or held_keys['s'] or held_keys['d']

        # Animation Switching
        if is_jumping and self.jumpingAnimation is not None:
            self.switch_animation(self.jumpingAnimation)
        elif not is_walking and self.idleAnimation is not None:
            self.switch_animation(self.idleAnimation)
        elif is_walking and self.speed > 5 and self.runningAnimation is not None:
            self.switch_animation(self.runningAnimation)
        elif is_walking and self.speed <= 5 and self.walkingAnimation is not None:
            self.switch_animation(self.walkingAnimation)

        # Dash
        if mouse.left and self.can_dash and is_jumping:
            direction = Vec3(self.forward.x, 0, self.forward.z).normalized()
            self.animate_position(self.position + direction * 5, duration=0.3, curve=curve.linear)
            self.can_dash = False

        super().update()

    def switch_animation(self, new_animation):
        AnimatedCharacterMixin.switch_animation(self, new_animation)
        if self.model != new_animation:
            self.model = new_animation

    def gameOver(self):
        if not self.death_message_shown:
            self.death_message_shown = True
            Text(text="Game over!", origin=Vec2(0, 0), scale=3)


# REMOTE PLAYER
class RemotePlayer(Entity, AnimatedCharacterMixin):
    def __init__(self, position: Vec3, username: str):
        super().__init__(
            position=position,
            model=None,
            collider="capsule",
            scale=1
        )
        self.username = username
        self.name_tag = Text(text=username, parent=self, y=2.5, scale=5, billboard=True, color=color.white)

        self.prev_pos = position

        self.load_animations()

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