from ursina import *

class InGameGui(Entity):
    def __init__(self, username, player_count=1, player_entity=None):
        super().__init__()
        random_color = color.random_color()
        
        # Nameplate above player
        self.namePlate = Text(
            text=username,
            parent=player_entity,
            scale=5,
            color=random_color,
            origin=(0,-5),
            billboard=True  # Ensure text always faces the camera
        )
        
        # UI Container for Controls
        self.controls_container = Entity(
            parent=camera.ui,
            model='quad',
            color=color.rgba(154, 59, 59, 0.00),  # Semi-transparent
            position=Vec2(0.6, -0.4),
            scale=Vec2(0.20, 0.15)
        )
        
        # Jump Icon and Text
        self.jump_icon = Entity(
            parent=self.controls_container,
            model='quad',
            texture='spaceIcon.png',
            position=Vec2(-0.3, 0.1),
            scale=Vec2(0.5, 0.5)
        )
        self.jump_text = Text(
            text="Jump",
            parent=self.controls_container,
            position=Vec2(-0.4, -0.2),
            scale=3,
            color=color.red
        )
        
        # Dash Icon and Text
        self.dash_icon = Entity(
            parent=self.controls_container,
            model='quad',
            texture='dashIcon.png',
            position=Vec2(0.3, 0.1),
            scale=Vec2(0.5, 0.5)
        )
        self.dash_text = Text(
            text="Dash",
            parent=self.controls_container,
            position=Vec2(0.2, -0.2),
            scale=3,
            color=color.red
        )
        
        # Display number of players in the game
        self.player_count_text = Text(
            text=f"Players: {player_count}",
            position=Vec2(-0.8, 0.45),
            scale=1
        )