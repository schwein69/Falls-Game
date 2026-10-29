import json, os, socket, sys, tempfile, threading, time, unittest, re
from unittest.mock import patch

_here = os.path.dirname(os.path.abspath(__file__))
for _sub in ("shared", "server", ""):
    sys.path.insert(0, os.path.join(_here, "..", _sub))
    
import matchmaking_server as mm
import game_instance
from game_authority import GameAuthority

BASE = mm.GAME_INSTANCE_BASE_PORT      # first port used by the game servers


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        time.sleep(0.03)
    return cond()                       # True as soon as the condition is true, False if time runs out


class FakeRedis:
    """A fake Redis made of a dictionary."""
    def __init__(self):
        self.data = {}
    def set(self, key, value, ex=None):
        self.data[key] = value
    def get(self, key):
        return self.data.get(key)
    def exists(self, key):
        return int(key in self.data)
    def delete(self, key):
        self.data.pop(key, None)
    def scan_iter(self, pattern):       
        return [k for k in list(self.data) if k.startswith(pattern.rstrip("*"))]


class FakeConn:
    """A fake connection: remembers what the server sends, instead of using a real socket."""
    def __init__(self):
        self.sent = []
    def sendall(self, data):
        self.sent.append(data)
    def close(self):
        pass


class Base(unittest.TestCase):
    """The matchmaking with clean state and the fake Redis."""
    def setUp(self):
        # Before each test: fake Redis, 2 players per match, temp log folder, empty queue and empty games.
        self.redis, self.socks, self.threads = FakeRedis(), [], []
        for p in (patch.object(mm, "r", self.redis), patch.object(mm, "MAX_PLAYERS", 2),
                  patch.object(mm, "RUN_DIR", tempfile.mkdtemp()), patch.object(mm, "pending_clients", []),
                  patch.dict(mm.active_processes, {}, clear=True), patch.dict(mm.active_games, {}, clear=True)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.close_all)

    def close_all(self):
        [s.close() for s in self.socks]
        [t.join(3) for t in self.threads]

    def connect(self, pid, **extra):
        """A player connects to the matchmaking: returns his socket. The server side runs in a thread."""
        player, server = socket.socketpair()
        player.settimeout(3)
        self.socks += [player, server]
        player.sendall(json.dumps({"id": pid, "udp_port": 5000, **extra}).encode())
        t = threading.Thread(target=mm.handle_client, args=(server, ("127.0.0.1", 40000 + pid)), daemon=True)
        t.start()
        self.threads.append(t)
        return player

    @staticmethod
    def read_until(sock, text):
        # Read from the socket until `text` shows up.
        data = b""
        while text.encode() not in data:
            chunk = sock.recv(256)
            if not chunk:
                break
            data += chunk
        return data.decode()


class TestStartGame(Base):
    def test_failure_sends_server_full_and_frees_the_port(self):
        # If the game server does not start (or Popen fails), players get SERVER_FULL and the port is freed.
        def popen_fails(*a, **k):
            raise OSError("no python")
        for running, popen in ((False, lambda *a, **k: object()), (True, popen_fails)):
            players = [FakeConn(), FakeConn()]
            group = [(i + 1, "127.0.0.1", 5001 + i, c) for i, c in enumerate(players)]
            mm.active_processes[BASE] = None            # reserved port, as handle_client does
            with patch.object(mm, "_wait_until_running", lambda *a, **k: running), \
                    patch.object(mm.subprocess, "Popen", popen):
                mm._start_game(group, BASE)
            self.assertEqual(players[0].sent, [b"SERVER_FULL"])
            self.assertEqual((mm.active_processes, mm.active_games), ({}, {}))


class TestQueue(Base):
    def test_players_wait_then_get_the_game_address(self):
        # The first player waits; when the second arrives, both get the game address and the port is reserved.
        def fake_start(group, port):
            for *_, conn in group:
                conn.sendall(f"GAME:h:{port}".encode())
        with patch.object(mm, "_start_game", fake_start):
            a = self.connect(1)
            self.assertEqual(self.read_until(a, "WAIT"), "WAIT")
            b = self.connect(2)
            self.assertIn(f"GAME:h:{BASE}", self.read_until(a, "GAME"))
            self.assertIn(f"GAME:h:{BASE}", self.read_until(b, "GAME"))
        self.assertEqual(mm.pending_clients, [])
        self.assertIn(BASE, mm.active_processes, "the port is reserved")

    def test_same_id_twice_is_refused(self):
        # A second player with the same id is refused (ID_COLLISION); the first stays in the queue.
        self.read_until(self.connect(1), "WAIT")
        self.assertEqual(self.read_until(self.connect(1), "ID_COLLISION"), "ID_COLLISION")
        self.assertEqual([p[0] for p in mm.pending_clients], [1])

    def test_cancel_and_closing_remove_the_player(self):
        # A player who sends CANCEL, or just closes the connection, is removed from the queue.
        a = self.connect(1)
        self.read_until(a, "WAIT")
        a.sendall(b"CANCEL")
        self.assertTrue(wait_for(lambda: mm.pending_clients == []))
        b = self.connect(2)
        self.read_until(b, "WAIT")
        b.close()
        self.assertTrue(wait_for(lambda: mm.pending_clients == []))

    def test_server_full_when_no_port_is_free(self):
        # If all the game ports are taken, a full group gets SERVER_FULL.
        mm.active_processes[BASE] = object()
        with patch.object(mm, "MAX_ACTIVE_GAMES", 1):
            a = self.connect(1)
            self.read_until(a, "WAIT")
            b = self.connect(2)
            self.assertEqual(self.read_until(a, "SERVER_FULL"), "SERVER_FULL")
            self.assertEqual(self.read_until(b, "SERVER_FULL"), "SERVER_FULL")

    def test_rejoin_lookup(self):
        # Asking to rejoin gives the game address if the player is in a running match, otherwise NOT_FOUND.
        mm.active_games[BASE] = {"players": [10, 11], "start_ts": 0}
        self.assertEqual(mm.find_game_of(11), f"GAME:{mm.MATCHMAKING_HOSTNAME}:{BASE}")
        self.assertEqual(mm.find_game_of(99), "NOT_FOUND")
        answer = self.read_until(self.connect(0, rejoin_id=10), "GAME")   
        self.assertIn(str(BASE), answer)
    
    def test_make_two_matches_and_one_waits(self):
    # 7 players, 3 per match: two games start and the last player stays in the queue.
        def fake_start(group, port):
            for *_, conn in group:
                conn.sendall(f"GAME:h:{port}".encode())
        ports = {}
        with patch.object(mm, "_start_game", fake_start), patch.object(mm, "MAX_PLAYERS", 3), \
                patch.object(mm, "MAX_ACTIVE_GAMES", 5):
            players = {}
            for pid in range(1, 8):
                players[pid] = self.connect(pid)                      
                if pid % 3 != 0:
                    self.assertEqual(self.read_until(players[pid], "WAIT"), "WAIT")
                elif pid <= 6:
                    for member in (pid - 2, pid - 1, pid):             # the whole group gets the address
                        text = self.read_until(players[member], "GAME:h:")
                        ports[member] = int(re.search(r"GAME:h:(\d+)", text).group(1))
        self.assertEqual({ports[i] for i in (1, 2, 3)}, {BASE})
        self.assertEqual({ports[i] for i in (4, 5, 6)}, {BASE + 1})
        self.assertEqual([p[0] for p in mm.pending_clients], [7], "the seventh player is still in the queue")
        self.assertEqual(sorted(mm.active_processes), [BASE, BASE + 1])

class TestRestart(Base):
    def test_running_game_is_adopted_after_restart(self):
        # After a matchmaking restart, a running game (its key is on Redis) is taken back and its port stays busy.
        info = {"port": BASE, "players": [BASE + 1], "pid": 1, "start_ts": 0}
        self.redis.set(mm.GAME_KEY_PREFIX + str(BASE), json.dumps(info))
        mm.adopt_running_games()
        self.assertEqual(list(mm.active_games), [BASE])
        self.assertNotEqual(mm._allocate_game_port(), BASE, "the port stays busy")
        self.assertIsNone(mm.active_processes[BASE].poll())              # None = still running
        self.redis.delete(mm.GAME_KEY_PREFIX + str(BASE))                # the game server closes the match
        self.assertEqual(mm.active_processes[BASE].poll(), 0)            # now it counts as finished


class FakeRoom:
    def __init__(self):
        self.clients, self.match_started, self.on_join = {}, False, None


class FakeNM:
    """A fake NetworkManager: remembers the messages sent and does nothing else."""
    player_id = 0
    def __init__(self):
        self.room, self.sent = FakeRoom(), []
    def broadcast_message(self, method, *args):
        self.sent.append(method)
    def send_to(self, addr, method, *args):
        self.sent.append(method)
    def start_broadcast_tick(self, *args, **kwargs):
        pass
    def process_queue(self, handler):
        pass


class TestGameInstance(unittest.TestCase):
    def setUp(self):
        # Before each test: short waiting times, so the tests are fast, and a fake network and state.
        for name, value in (("IDLE_SHUTDOWN_SECONDS", 0.3), ("STATUS_POLL_INTERVAL", 0.02),
                            ("JOIN_TIMEOUT_SECONDS", 1.0)):
            p = patch.object(game_instance, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.nm, self.auth = FakeNM(), GameAuthority()

    def test_clients_cannot_act_for_someone_else(self):
        # The server uses the real sender, not the id written in the message.
        ready = {"ready_pids": set(), "countdown_started": False, "expected_count": 2}
        handle = game_instance.make_message_handler(self.nm, self.auth, ready)
        handle({"method": "player_died", "args": [7], "sender_id": 5})
        handle({"method": "update_pos", "args": [7, 1, 2, 3], "sender_id": 6})
        self.assertEqual(self.auth.dead_players, {5})
        self.assertEqual(list(self.auth.player_positions), [6])

    def test_everybody_left_before_the_start_closes_early(self):
        # If everybody leaves before the start, the game server closes at once and does not wait for the full timeout.
        self.nm.room.clients = {("h", 1): 1}
        t = threading.Thread(target=game_instance.play, args=(self.nm, self.auth, [(1, "h", 1), (2, "h", 2)]))
        t.start()
        time.sleep(0.2)
        self.nm.room.clients.clear()
        t.join(3)
        self.assertFalse(t.is_alive(), "it does not wait for the whole join timeout")
        self.assertNotIn("start_game", self.nm.sent)


if __name__ == "__main__":
    unittest.main()