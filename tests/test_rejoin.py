import itertools
import os
import sys
import time
import unittest
from unittest.mock import patch

_here = os.path.dirname(os.path.abspath(__file__))
for _sub in ("shared", "server", ""):
    sys.path.insert(0, os.path.join(_here, "..", _sub))


_ports = itertools.count(20100)   # Iterator for unique port numbers for each test, starting from 20100.


def next_port():
    return next(_ports) + 1


def drain(nm, timeout=0.5):
    got = []
    end = time.time() + timeout
    while time.time() < end:
        while not nm.msg_queue.empty():
            got.append(nm.msg_queue.get_nowait())
        time.sleep(0.02)
    return got


def methods(nm, timeout=0.5):
    return [m.get("method") for m in drain(nm, timeout)]


def pauses(nm, timeout=0.5):
    return [m["args"][0] for m in drain(nm, timeout) if m.get("method") == "game_pause"]


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.03)
    return False

import network
from network import NetworkManager


class TestRejoin(unittest.TestCase):
    def setUp(self):
        # Before each test: a host in a running match, on its own port, ready to accept clients.
        p = patch.object(network, "MAX_PLAYERS", 10)
        p.start()
        self.addCleanup(p.stop)
        self.port = next_port()
        self.host = NetworkManager()
        self.host.setup_host(port=self.port)
        self.host.room.host_plays = True
        self.host.room.match_started = True
        self.all = []
        self.addCleanup(lambda: [c.stop() for c in self.all] + [self.host.stop()])

    def join(self, rejoin_id=None, wait=0.4, port=None):
        # Start a client, optionally asking to rejoin with an old id, and wait a bit.
        c = NetworkManager()
        c.join_network("127.0.0.1", port or self.port, rejoin_id=rejoin_id)
        self.all.append(c)
        time.sleep(wait)
        return c

    def test_rejoin_while_paused_keeps_id_resumes_only_when_ready_and_syncs_state(self):
        # A player closes the window and reopens it: same id, others stay paused until he finishes loading, and he gets the game state back.
        c1, c2 = self.join(), self.join()
        old_id = c1.player_id
        drain(c2, 0.2)
        self.host.room.on_join = lambda pid, addr: self.host.send_to(
            addr, "sync_state", [7], {str(pid): [1.0, 2.0, 3.0]})

        c1.stop()                                              # window closed suddenly
        self.assertIn("game_pause", methods(c2, 0.3))
        self.assertEqual(len(self.host.room.silent), 1)

        nuovo = self.join(rejoin_id=old_id)
        self.assertEqual(nuovo.player_id, old_id, "same id as before")
        self.assertEqual(len(self.host.room.silent), 0)
        self.assertEqual(len(self.host.room.clients), 2, "no ghost player left behind")
        self.assertEqual(pauses(c2, 0.4), [], "the others must not resume as soon as he joins")
        nuovo.send_message("rejoin_done", old_id)
        self.assertEqual(pauses(c2, 0.4), [False], "everyone resumes together, only after loading is finished")
        sync = [m for m in drain(nuovo, 0.2) if m["method"] == "sync_state"]
        self.assertEqual(len(sync), 1)
        self.assertEqual(sync[0]["args"][1][str(old_id)], [1.0, 2.0, 3.0])

    def test_rejoin_before_server_noticed_the_disconnect(self):
        # A player crashes and rejoins before the server noticed: he keeps his id and the old address is replaced.
        c1 = self.join()
        old_id = c1.player_id
        c1.running = False                                     # crash: no DISCONNECT is sent
        nuovo = self.join(rejoin_id=old_id)
        self.assertEqual(nuovo.player_id, old_id)
        self.assertEqual(len(self.host.room.clients), 1, "the old address was replaced")

    def test_rejoin_before_server_noticed_pauses_the_others_until_ready(self):
        # Same case, with other players: they are paused while he loads and resume when he is ready.
        c1, c2 = self.join(), self.join()
        old_id = c1.player_id
        drain(c2, 0.2)
        c1.running = False
        nuovo = self.join(rejoin_id=old_id)
        self.assertEqual(pauses(c2, 0.4), [True], "while he loads, the others must not go on")
        nuovo.send_message("rejoin_done", old_id)
        self.assertEqual(pauses(c2, 0.4), [False])

    def test_rejoin_after_grace_period_is_refused_in_p2p(self):
        # If he comes back after the waiting time is over, he is refused and gets no new id.
        c1 = self.join()
        old_id = c1.player_id
        c1.stop()
        self.host.check_client_liveness(timeout=0.1, grace_period=0.1)
        time.sleep(0.2)
        self.host.check_client_liveness(timeout=0.1, grace_period=0.1)
        self.assertEqual(len(self.host.room.clients), 0)
        nuovo = self.join(rejoin_id=old_id, wait=0)
        self.assertTrue(wait_for(lambda: nuovo.join_rejected is not None))
        self.assertIn("scaduto", nuovo.join_rejected)
        self.assertIsNone(nuovo.player_id, "during a match nobody gets a new id")

    def test_rejoin_in_online_mode_with_new_udp_port(self):
        # Online: a player rejoining from a new port is accepted, because his seat still exists.
        self.host.stop()
        self.host = NetworkManager()
        a = NetworkManager()
        pa = a.prepare_local_socket()
        self.all.append(a)
        self.host.room.expected = {("127.0.0.1", pa): 4242}
        self.host.setup_host(port=self.port + 300)
        self.host.room.match_started = True
        a.join_network("127.0.0.1", self.port + 300)
        time.sleep(0.4)
        self.assertEqual(a.player_id, 4242)
        a.stop()
        nuovo = self.join(rejoin_id=4242, wait=0.5, port=self.port + 300)
        self.assertEqual(nuovo.player_id, 4242)

    def test_online_rejoin_of_an_unknown_player_is_refused(self):
        # Online: someone asking to rejoin with an id that is not in the match is refused.
        self.host.room.expected = {("127.0.0.1", 1): 4242}
        self.host.room.closed = True                          # game server with the match already started
        stranger = self.join(rejoin_id=999, wait=0)
        self.assertTrue(wait_for(lambda: stranger.join_rejected is not None))
        self.assertIsNone(stranger.player_id)

    def test_others_wait_while_rejoiner_loads_and_it_is_not_flagged_silent(self):
        # While the rejoining player loads, he is not marked as disconnected and the others keep waiting.
        c1, c2 = self.join(), self.join()
        old_id = c1.player_id
        drain(c2, 0.2)
        c1.stop()
        self.assertEqual(pauses(c2, 0.3), [True])
        nuovo = self.join(rejoin_id=old_id)
        self.assertIn(old_id, self.host.room.rejoining)
        time.sleep(0.4)                                        # loading lasts longer than the silence timeout
        self.host.check_client_liveness(timeout=0.1, grace_period=0.1)
        self.assertEqual(len(self.host.room.silent), 0)
        self.assertEqual(len(self.host.room.clients), 2)
        self.assertEqual(pauses(c2, 0.2), [])
        nuovo.send_message("rejoin_done", old_id)
        self.assertEqual(pauses(c2, 0.4), [False])
        self.assertNotIn(old_id, self.host.room.rejoining)

    def test_rejoiner_that_leaves_while_loading_resumes_the_others(self):
        # If the rejoining player gives up while loading, he is removed and the others resume.
        c1, c2 = self.join(), self.join()
        old_id = c1.player_id
        drain(c2, 0.2)
        c1.stop()
        pauses(c2, 0.3)
        nuovo = self.join(rejoin_id=old_id)
        nuovo.leaving_deliberately = True                      # rejoin failed: he goes back to the menu
        nuovo.stop()
        time.sleep(0.3)
        got = drain(c2, 0.2)
        self.assertIn(False, [m["args"][0] for m in got if m["method"] == "game_pause"])
        self.assertIn("player_left", [m["method"] for m in got])
        self.assertEqual(len(self.host.room.rejoining), 0)

    def test_rejoiner_that_never_finishes_loading_is_removed_and_the_others_resume(self):
        # If he never says he is ready, he is removed after the loading timeout and the others resume.
        c1, c2 = self.join(), self.join()
        old_id = c1.player_id
        drain(c2, 0.2)
        c1.stop()
        pauses(c2, 0.3)
        self.join(rejoin_id=old_id)                            # he rejoins but never sends rejoin_done
        with patch.object(network, "REJOIN_LOAD_TIMEOUT", 0.2):
            time.sleep(0.3)
            self.host.check_client_liveness(timeout=5, grace_period=5)
        got = drain(c2, 0.4)
        self.assertIn(False, [m["args"][0] for m in got if m["method"] == "game_pause"])
        self.assertIn("player_left", [m["method"] for m in got])
        self.assertEqual(len(self.host.room.clients), 1)
        self.assertEqual(len(self.host.room.rejoining), 0)

    def test_rejoiner_that_crashes_again_while_loading_can_rejoin_again(self):
        # If he crashes again while loading, he can rejoin again, still with only one seat.
        c1, c2 = self.join(), self.join()
        old_id = c1.player_id
        drain(c2, 0.2)
        c1.stop()
        primo = self.join(rejoin_id=old_id)
        primo.stop()                                           # crashes again while loading
        secondo = self.join(rejoin_id=old_id)
        self.assertEqual(secondo.player_id, old_id)
        self.assertEqual(len(self.host.room.clients), 2, "he must always have just one seat")
        secondo.send_message("rejoin_done", old_id)
        self.assertEqual(pauses(c2, 0.5)[-1:], [False], "in the end everyone resumes")

    def test_two_players_rejoin_one_after_the_other(self):
        # Two players leave and both rejoin, one after the other: each gets his own id back.
        c1, c2, c3 = self.join(), self.join(), self.join()
        id1, id2 = c1.player_id, c2.player_id
        drain(c3, 0.2)
        c1.stop(); c2.stop()
        self.assertTrue(wait_for(lambda: len(self.host.room.silent) == 2))
        n1 = self.join(rejoin_id=id1)
        n2 = self.join(rejoin_id=id2)
        self.assertEqual((n1.player_id, n2.player_id), (id1, id2))
        drain(c3, 0.2)
        n1.send_message("rejoin_done", id1)
        n2.send_message("rejoin_done", id2)
        self.assertTrue(wait_for(lambda: not self.host.room.rejoining))
        self.assertEqual(len(self.host.room.clients), 3)

    def test_rejoin_still_allowed_when_room_is_closed(self):
        # Even when the room is closed to new players, someone already inside can rejoin.
        c1 = self.join()
        old_id = c1.player_id
        self.host.room.closed = True
        c1.stop()
        self.host.check_client_liveness(timeout=0.1, grace_period=10)
        again = self.join(rejoin_id=old_id, wait=0.5)
        self.assertEqual(again.player_id, old_id)


if __name__ == "__main__":
    unittest.main()