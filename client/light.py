from ursina import *
from ursina.shaders import lit_with_shadows_shader


class Lights:
        def __init__(self):
            self.pivot = Entity()
            self.direc_light = DirectionalLight(parent=self.pivot, y=2, z=3, shadows=True, rotation=(45, -45, 45))
            self.amb_light = AmbientLight(parent=self.pivot, color = color.rgba(100, 100, 100, 0.1))


from ursina import *


class Cylinder(Pipe):
    def __init__(self, resolution=8, radius=.5, start=0, height=1, direction=(0,1,0), mode='triangle', **kwargs):
        super().__init__(
            base_shape=Circle(resolution=resolution, radius=.5),
            origin=(0,0),
            path=((0,start,0), Vec3(direction) * (height+start)),
            thicknesses=((radius*2, radius*2),),
            mode=mode,
            **kwargs
            )


if __name__ == '__main__':
    app = Ursina()
    Entity(model=Cylinder(16, start=-.5), color=color.hsv(60,1,1,.3))
    origin = Entity(model='quad', color=color.orange, scale=(5, .05))
    ed = EditorCamera(rotation_speed = 200, panning_speed=200)
    app.run()