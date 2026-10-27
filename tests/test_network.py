import itertools, json, os, socket, sys, time, unittest
from unittest.mock import patch

_here = os.path.dirname(os.path.abspath(__file__))
for _sub in ("shared", "server", ""):
    sys.path.insert(0, os.path.join(_here, "..", _sub))
import network
from network import NetworkManager

_ports = itertools.count(19100)  


def next_port():
    return next(_ports) + 1       


def drain(nm, timeout=0.5):
    """Wait `timeout` seconds and return the messages that arrived in the queue of `nm`."""
    got, end = [], time.time() + timeout
    while time.time() < end:
        while not nm.msg_queue.empty():
            got.append(nm.msg_queue.get_nowait())
        time.sleep(0.02)
    return got


def methods(nm, timeout=0.5):
    return [m.get("method") for m in drain(nm, timeout)]       # only the names of the messages


def pauses(nm, timeout=0.5):
    return [m["args"][0] for m in drain(nm, timeout) if m.get("method") == "game_pause"]   


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        time.sleep(0.03)
    return cond()                   


class Base(unittest.TestCase):
    """A host on its own port, plus a list of clients to stop at the end of the test."""
    max_players = 10                
    def setUp(self):
        # Before each test: limit of players, a new host (that also plays) and an empty list of clients.
        p = patch.object(network, "MAX_PLAYERS", self.max_players)
        p.start()
        self.addCleanup(p.stop)
        self.port, self.host, self.clients = next_port(), NetworkManager(), []
        self.host.setup_host(port=self.port)
        self.host.room.host_plays = True
        self.addCleanup(lambda: [c.stop() for c in self.clients] + [self.host.stop()])

    def join(self, wait=0.2):
        # Start a client, join the host and wait a bit.
        c = NetworkManager()
        c.join_network("127.0.0.1", self.port)
        self.clients.append(c)
        time.sleep(wait)
        return c


class TestRoster(Base):
    def test_everybody_learns_who_is_in_the_room(self):
        # Ids are 1, 2, 3 in order; the first client learns about the host, the old ones about the new one.
        c1 = self.join()
        self.assertIn("spawn_player", methods(c1))
        c2 = self.join()
        self.assertIn(c2.player_id, [m["args"][0] for m in drain(c1) if m["method"] == "spawn_player"])
        c3 = self.join()
        self.assertEqual([c1.player_id, c2.player_id, c3.player_id], [1, 2, 3])
        self.assertGreaterEqual(methods(c3).count("spawn_player"), 3, "host, client 1 and client 2")

    def test_messages_reach_the_right_people(self):
        # A client message reaches the host with the right sender; broadcasts reach everybody.
        c1, c2 = self.join(), self.join()
        drain(c1, 0.2); drain(c2, 0.2)
        c1.send_message("update_pos", c1.player_id, 1, 2, 3)
        self.assertEqual([m["sender_id"] for m in drain(self.host) if m["method"] == "update_pos"], [c1.player_id])
        c1.send_message("spawn_player", c1.player_id, 0, 5, 0)
        self.assertIn("spawn_player", methods(c2), "the host forwards a spawn to the other clients")
        self.host.broadcast_message("start_game", 42)
        self.assertIn("start_game", methods(c1))
        self.host.broadcast_message("hi", exclude_pid=c1.player_id)
        self.assertNotIn("hi", methods(c1))
        self.assertIn("hi", methods(c2))

    def test_snapshot_tick_delivers_positions_to_clients(self):
        # The host sends the positions to the clients at fixed intervals.
        c = self.join()
        drain(c, 0.2)
        self.host.start_broadcast_tick(lambda: {1: [1, 2, 3]}, interval=0.05)
        snaps = [m for m in drain(c) if m["method"] == "state_snapshot"]
        self.assertEqual(snaps[0]["args"][0], {"1": [1, 2, 3]})

class TestRoomLimits(Base):
    max_players = 3

    def test_room_full_is_refused_with_a_clear_reason(self):
        # With host + 2 clients the room is full: the third one is refused and gets no id.
        self.join(); self.join()
        third = self.join(wait=0.5)
        self.assertTrue(wait_for(lambda: third.join_rejected is not None))
        self.assertIn("piena", third.join_rejected)
        self.assertIsNone(third.player_id)


class TestLeaveAndDisconnect(Base):
    def test_window_close_during_match_pauses_then_removes_after_grace_period(self):
        # Closing the window during a match: pause at once, removal only after the waiting time.
        self.host.room.match_started = True
        c1, c2 = self.join(), self.join()
        drain(c2, 0.3)
        c1.stop()                                       # not deliberate -> DISCONNECT
        now = methods(c2, 0.3)
        self.assertIn("game_pause", now)
        self.assertNotIn("player_left", now)
        self.host.check_client_liveness(timeout=0.1, grace_period=0.2)
        time.sleep(0.3)
        self.host.check_client_liveness(timeout=0.1, grace_period=0.2)
        self.assertIn("player_left", methods(c2, 0.3))

    def test_deliberate_leave_during_match_is_immediate_and_final(self):
        # A deliberate leave (LEAVE) removes the player at once, without pausing the others.
        self.host.room.match_started = True
        c1, c2 = self.join(), self.join()
        drain(c2, 0.3)
        c1.leaving_deliberately = True
        c1.stop()
        got = methods(c2)
        self.assertIn("player_left", got)
        self.assertNotIn("game_pause", got)

    def test_window_close_in_lobby_is_immediate(self):
        # In the lobby there is no pause: who closes the window is removed at once.
        c1, c2 = self.join(), self.join()
        drain(c2, 0.3)
        c1.stop()
        got = methods(c2)
        self.assertIn("player_left", got)
        self.assertNotIn("game_pause", got)

class TestLiveness(Base):
    """A silent client pauses everybody; if he comes back in time the match resume."""

    def setUp(self):
        super().setUp()
        self.host.room.match_started = True            

    def test_silent_client_triggers_pause_then_gets_removed_if_never_returns(self):
        # Silent for too long: marked as paused; after the grace period he is removed.
        c = self.join()
        c.send_message("update_pos", c.player_id, 0, 0, 0)
        time.sleep(0.4)
        self.host.check_client_liveness(timeout=0.3, grace_period=0.5)
        self.assertIn(c.player_id, [pid for pid, _ in self.host.room.silent.values()])
        time.sleep(0.6)
        self.host.check_client_liveness(timeout=0.3, grace_period=0.5)
        self.assertNotIn(c.player_id, self.host.room.clients.values())

    def test_client_that_resumes_traffic_is_not_removed(self):
        # If a silent client sends packets again before the grace period ends, he is back and not removed.
        c = self.join()
        c.send_message("update_pos", c.player_id, 0, 0, 0)
        time.sleep(0.15)
        self.host.check_client_liveness(timeout=0.1, grace_period=5.0)
        self.assertTrue(self.host.room.silent)
        c.send_message("update_pos", c.player_id, 1, 1, 1)
        self.assertTrue(wait_for(lambda: not self.host.room.silent))
        self.assertIn(c.player_id, self.host.room.clients.values())

    def test_the_host_player_is_paused_too(self):
        # The P2P host is also a player: the pause must reach its own queue as well.
        c = self.join()
        c.send_message("update_pos", c.player_id, 0, 0, 0)
        time.sleep(0.2)
        self.host.check_client_liveness(timeout=0.1, grace_period=5.0)
        self.assertIn(True, pauses(self.host, 0.2))

    def test_heartbeat_keeps_the_host_alive_for_clients(self):
        # While the host sends heartbeats clients answer and see it alive; when it goes quiet they notice.
        c = self.join()
        self.host.start_heartbeat(interval=0.1)
        time.sleep(0.5)
        self.assertTrue(c.host_alive(timeout=0.5))
        self.assertIn("client_heartbeat", methods(self.host, 0.2))
        self.host.running = False                       # the host stops talking
        time.sleep(0.7)
        self.assertFalse(c.host_alive(timeout=0.5))


class TestAntiSpoofing(Base):
    def test_packets_from_wrong_sender_or_unknown_address_are_rejected(self):
        # A packet with a made-up sender id, or from an address that never joined, is ignored.
        c = self.join()
        fake = c.player_id + 99
        evil = json.dumps({"method": "update_pos", "args": [fake, 1, 2, 3], "sender_id": fake}).encode()
        c.sock.sendto(evil, (c.host_ip, c.host_port))
        stranger = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        stranger.sendto(evil, ("127.0.0.1", self.port))
        stranger.close()
        self.assertNotIn("update_pos", methods(self.host, 0.4))


class TestClosedRoom(Base):
    def test_new_player_refused_after_start(self):
        # P2P: after the host presses Start, a new player is refused ("already started").
        self.join()
        self.host.room.closed = True
        late = self.join(wait=0.5)
        self.assertTrue(wait_for(lambda: late.join_rejected is not None))
        self.assertIn("gia' iniziata", late.join_rejected)
        self.assertIsNone(late.player_id)


class TestNat(unittest.TestCase):
    """Online behind a NAT: the client declares a UDP port over TCP, but its packets leave from another one."""

    def setUp(self):
        # Before each test: a server that expects player 77 on a declared port, and a client whose real port differs.
        self.port, self.client, self.server = next_port(), NetworkManager(), NetworkManager()
        self.real_addr = ("127.0.0.1", self.client.prepare_local_socket())
        self.declared = self.real_addr[1] + 1000
        self.client.prepare_local_socket = lambda: self.declared     # it declares a port that is not its own
        self.client.online.status = "matched"                        # like an Online client: it sends hellos
        self.server.room.expected = {("127.0.0.1", self.declared): 77}
        self.server.setup_host(port=self.port)
        self.server.room.on_join = lambda pid, addr: self.server.send_to(addr, "sync_state", [5], {}, 123)
        self.addCleanup(lambda: (self.client.stop(), self.server.stop()))

    def test_server_learns_real_address_and_resends_state(self):
        # The server learns the real address from the first hello, drops the declared one and resends the state.
        self.client.join_network("127.0.0.1", self.port)
        self.assertTrue(wait_for(lambda: self.real_addr in self.server.room.clients, 4))
        self.assertEqual(self.server.room.clients[self.real_addr], 77)
        self.assertNotIn(("127.0.0.1", self.declared), self.server.room.clients, "no ghost entry")
        self.assertIn("sync_state", methods(self.client))
        self.client.send_message("update_pos", 77, 1, 2, 3)
        self.assertIn("update_pos", methods(self.server, 0.4))


if __name__ == "__main__":
    unittest.main()