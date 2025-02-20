from ursina import *
import socket
import json
import asyncio
#import websockets

app = Ursina()

# 游戏 UI 类
class GameUI(Entity):
    def __init__(self):
        super().__init__()
        self.countdown_text = Text(text="", position=(0, 0.4), scale=2, color=color.yellow)
        self.remaining_text = Text(text="", position=(0, 0.3), scale=1.5, color=color.green)

    def update_countdown(self, time_left):
        self.countdown_text.text = f"倒计时: {time_left}"

    def update_remaining(self, remaining):
        self.remaining_text.text = f"剩余玩家: {remaining}"

game_ui = GameUI()

# 连接 WebSocket 监听服务器消息
""" async def listen_server():
    async with websockets.connect("ws://127.0.0.1:7000") as websocket:
        await websocket.send(player_id)  # 发送玩家 ID

        async for message in websocket:
            data = json.loads(message)
            if data["event"] == "countdown":
                game_ui.update_countdown(data["time"])
            elif data["event"] == "update_players":
                game_ui.update_remaining(data["remaining"])
            elif data["event"] == "game_over":
                game_ui.countdown_text.text = f"游戏结束，胜者: {data['winner']}" """

# 连接匹配服务器
""" def join_match():
    global player_id
    player_id = input("输入玩家ID: ")
    
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect(("127.0.0.1", 5000))
        s.sendall(json.dumps({"player_id": player_id, "mmr": 1000}).encode())
        response = json.loads(s.recv(1024).decode())
        print(response["message"]) """

# 玩家控制
class Player(Entity):
    def __init__(self, **kwargs):
        super().__init__(model='cube', color=color.red, scale=(1, 1, 1), **kwargs)
        self.velocity = Vec3(0, 0, 0)

    def update(self):
        self.velocity.y -= 0.02  # 重力
        if held_keys['a']: self.x -= 0.1
        if held_keys['d']: self.x += 0.1
        if held_keys['space'] and self.y <= 0:  # 跳跃
            self.velocity.y = 0.3
        self.y += self.velocity.y

player = Player()

# 创建平台
platforms = [Entity(model='cube', color=color.green, scale=(2, 0.1, 2), position=(x, y, 0))
             for y in range(3, 0, -1) for x in range(-5, 5)]

def update():
    for platform in platforms:
        if player.intersects(platform).hit:
            invoke(platform.disable, delay=1)  # 1秒后消失

# 启动 WebSocket 监听
""" asyncio.get_event_loop().run_until_complete(listen_server())

join_match() """
app.run()
