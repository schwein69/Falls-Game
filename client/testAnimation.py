
from ursina import *
from direct.actor.Actor import Actor

app = Ursina()

# parent_entity = Entity(model = "collisionModel",Collider = "collisionModel",visible = False)
parent = Entity(model="slime")

# actor = Actor("untitled.glb")
# actor.reparentTo(parent)

# actor.loop("Animation")

EditorCamera()

app.run()
