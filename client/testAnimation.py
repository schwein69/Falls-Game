
from ursina import *
from direct.actor.Actor import Actor

app = Ursina()

parent_entity = Entity(model = "collisionModel",Collider = "collisionModel",visible = False)

# actor = Actor("characterOptimized.gltf")
# actor.reparentTo(parent_entity)
# actor.loop("walk")

EditorCamera()

app.run()
