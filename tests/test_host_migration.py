import itertools
import os
import sys
import time
import unittest
from unittest.mock import patch

_here = os.path.dirname(os.path.abspath(__file__))
for _sub in ("shared", "server", ""):
    sys.path.insert(0, os.path.join(_here, "..", _sub))
import network
import network_p2p
from network_p2p import NetworkManager

_ports = itertools.count(21100)     # every test file has its own range of ports


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.03)
    return False


def drain(nm, timeout=0.5):
    """Wait and return the messages that arrived."""
    got, end = [], time.time() + timeout
    while time.time() < end:
        while not nm.msg_queue.empty():
            got.append(nm.msg_queue.get_nowait())
        time.sleep(0.02)
    return got


class TestMigration(unittest.TestCase):
    def setUp(self):
        # Before each test: its own port, a list of things to stop at the end, and a limit of 10 players.
        self.port = next(_ports) + 1
        self.stops = []
        # the promoted client looks for a free port from P2P_PORT upwards: every test starts from its own base
        for p in (patch.object(network, "MAX_PLAYERS", 10),
                  patch.object(network_p2p, "P2P_PORT", 30000 + 20 * (self.port - 21100))):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: [s() for s in reversed(self.stops)])

    def host(self):
        # Start a P2P host that allows migration.
        h = NetworkManager()
        h.allow_host_migration = True
        h.setup_host(port=self.port)
        h.room.host_plays = True
        self.stops.append(h.stop)
        return h

    def client(self):
        # Helper: start a client, join the host and wait until it gets its id.
        c = NetworkManager()
        c.allow_host_migration = True
        c.join_network("127.0.0.1", self.port)
        self.stops.append(c.stop)
        self.assertTrue(wait_for(lambda: c.player_id is not None))
        return c

    @staticmethod
    def crash(h):
        h.running = False
        h.sock.close()
        h.join_tcp_sock.close()

    def promote(self, c, survivors, seed=7, blocks=(), positions=None):
        # Helper: client `c` becomes the new host, restarting from the state it saw.
        nm, authority, bc = network_p2p.promote_to_host(c, floor_seed=seed, destroyed_blocks=list(blocks),
                                                        player_positions=positions or {}, survivor_ids=survivors)
        self.stops += [nm.stop, bc.stop]
        return nm, authority

    def reconnect(self, pid, new_host):
        # A client rejoins the new host asking for its old id, and waits for it.
        nm = network_p2p.reconnect_to_new_host(pid, "127.0.0.1", new_host.host_port)
        self.stops.append(nm.stop)
        self.assertTrue(wait_for(lambda: nm.player_id is not None), "rejoin failed")
        return nm

    def test_clients_notice_the_host_is_gone(self):
        # While the host sends heartbeats, clients see it alive; after it crashes, they notice it is gone.
        h = self.host()
        h.start_heartbeat(interval=0.1)
        c = self.client()
        time.sleep(0.4)
        self.assertTrue(c.host_alive(timeout=0.5))
        self.crash(h)
        self.assertTrue(wait_for(lambda: not c.host_alive(timeout=0.5), 2))

    def test_lowest_id_is_the_first_to_join(self):
        # Clients get ids 1, 2, 3 in order of arrival, so the first one is the lowest.
        self.host()
        self.assertEqual([self.client().player_id for _ in range(3)], [1, 2, 3])

    def test_promoted_client_keeps_id_and_state(self):
        # The promoted client keeps its id and the match state (seed, blocks, positions) and waits only for the others.
        h = self.host()
        c1, c2 = self.client(), self.client()
        self.crash(h)
        nm, auth = self.promote(c1, {c1.player_id, c2.player_id}, seed=777, blocks=[3, 4],
                                positions={c2.player_id: (4, 5, 6)})
        self.assertEqual(nm.player_id, c1.player_id, "it must not become 0")
        self.assertTrue(nm.is_host and nm.room.host_plays and nm.allow_host_migration)
        self.assertEqual((auth.floor_seed, auth.destroyed_blocks), (777, {3, 4}))
        self.assertEqual(auth.player_positions[c2.player_id], (4, 5, 6))
        self.assertTrue(nm.room.closed, "match already started: no strangers")
        self.assertEqual(nm.room.pending_ids, {c2.player_id}, "it waits for the others, not for itself")

    def test_reconnect_gets_same_id_and_the_state(self):
        # Who reconnects to the new host gets the same id, the saved state, and the new host keeps sending positions.
        h = self.host()
        c1, c2 = self.client(), self.client()
        old = c2.player_id
        self.crash(h)
        nm, _ = self.promote(c1, {c1.player_id, old}, seed=99, blocks=[7, 8], positions={old: (1.0, 2.0, 3.0)})
        c2.stop()
        again = self.reconnect(old, nm)
        self.assertEqual(again.player_id, old)
        self.assertEqual(nm.room.pending_ids, set())
        msgs = drain(again, 0.6)
        sync = [m for m in msgs if m["method"] == "sync_state"][0]["args"]
        self.assertEqual((sorted(sync[0]), sync[1][str(old)], sync[2]), ([7, 8], [1.0, 2.0, 3.0], 99))
        self.assertIn("state_snapshot", [m["method"] for m in msgs], "the new host keeps broadcasting")

    def test_strangers_and_unknown_ids_are_refused(self):
        # After a migration a new player and a made-up id are both refused.
        h = self.host()
        c1 = self.client()
        self.crash(h)
        nm, _ = self.promote(c1, {c1.player_id, 2})
        new = network_p2p.join_p2p_host("127.0.0.1", nm.host_port)
        liar = network_p2p.reconnect_to_new_host(55, "127.0.0.1", nm.host_port)
        self.stops += [new.stop, liar.stop]
        self.assertTrue(wait_for(lambda: new.join_rejected and liar.join_rejected))
        self.assertIn("gia' iniziata", new.join_rejected)
        self.assertEqual((new.player_id, liar.player_id), (None, None))

    def test_the_new_host_can_crash_too(self):
        # The new host can crash too: the next lowest id takes over and the last one rejoins it.
        h = self.host()
        c1, c2, c3 = self.client(), self.client(), self.client()
        ids = {c1.player_id, c2.player_id, c3.player_id}
        self.crash(h)
        h2, _ = self.promote(c1, ids, seed=5, blocks=[1])
        c2.stop(); c3.stop()
        c2b, c3b = self.reconnect(c2.player_id, h2), self.reconnect(c3.player_id, h2)
        self.crash(h2)                                          # the new host crashes too
        h3, auth3 = self.promote(c2b, ids - {c1.player_id}, seed=5, blocks=[1, 2])
        self.assertEqual((h3.player_id, auth3.floor_seed), (c2.player_id, 5), "now the lowest id left is 2")
        c3b.stop()
        self.assertEqual(self.reconnect(c3.player_id, h3).player_id, c3.player_id)


if __name__ == "__main__":
    unittest.main()