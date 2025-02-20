from ursina import *
from ursina.shaders import lit_with_shadows_shader


class Lights:
        def __init__(self):
            self.pivot = Entity()
            self.direc_light = DirectionalLight(parent=self.pivot, y=2, z=3, shadows=True, rotation=(45, -45, 45))
            self.amb_light = AmbientLight(parent=self.pivot, color = color.rgba(100, 100, 100, 0.1))


