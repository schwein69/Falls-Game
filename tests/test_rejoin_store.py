import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

_here = os.path.dirname(os.path.abspath(__file__))
for _sub in ("shared", "server", "client","client/module"):
    sys.path.insert(0, os.path.join(_here, "..", _sub))


import rejoin_store as rs


class TestRejoinStore(unittest.TestCase):
    def setUp(self):
        # Before each test: use a new empty temp folder instead of the real .rejoin folder.
        self.dir = tempfile.mkdtemp()
        p = patch.object(rs, "_DIR", self.dir)                
        p.start()
        self.addCleanup(p.stop)

    def age(self, player_id, seconds):
        # Make a saved file look `seconds` old, without waiting.
        path = rs._path(player_id)
        with open(path) as f:
            data = json.load(f)
        data["timestamp"] = time.time() - seconds # fake the timestamp
        with open(path, "w") as f:
            json.dump(data, f)

    def test_save_then_load_after_a_moment(self):
        # What we save can be read back after a few seconds, with the same data.
        rs.save(4, "Anna", "online")
        self.age(4, 3)                                         
        data = rs.load()
        self.assertEqual((data["player_id"], data["name"], data["mode"]), (4, "Anna", "online"))

    def test_fresh_save_is_ignored(self):
        # A file just written is ignored: the game is probably still open.
        rs.save(4, "Anna", "p2p")
        self.assertIsNone(rs.load())

    def test_too_old_save_is_deleted_and_not_returned(self):
        # A file older than the rejoin time is not returned and is deleted.
        rs.save(4, "Anna", "p2p")
        self.age(4, rs.MAX_AGE + 1)
        self.assertIsNone(rs.load())
        self.assertFalse(os.path.exists(rs._path(4)), "il tempo per rientrare e' finito: via il file")

    def test_most_recent_valid_save_wins(self):
        # With two valid files, the most recent one is chosen.
        rs.save(1, "Vecchio", "p2p")
        rs.save(2, "Nuovo", "online")
        self.age(1, 6)
        self.age(2, 3)
        self.assertEqual(rs.load()["player_id"], 2)

    def test_nothing_saved_returns_none(self):
        # With no files, there is nothing to rejoin.
        self.assertIsNone(rs.load())

    def test_clear_removes_only_that_player(self):
        # Clearing a player deletes only his file, not the others.
        rs.save(1, "A", "p2p")
        rs.save(2, "B", "p2p")
        rs.clear(1)
        self.assertFalse(os.path.exists(rs._path(1)))
        self.assertTrue(os.path.exists(rs._path(2)))

    def test_clear_of_missing_or_none_does_not_fail(self):
        # Clearing a file that does not exist, or with no id, gives no error.
        rs.clear(77)
        rs.clear(None)

    def test_save_overwrites_the_same_player_without_leftovers(self):
        # Saving the same player twice keeps one file with the new data and no temp files.
        rs.save(1, "A", "p2p")
        rs.save(1, "B", "online")
        self.assertEqual(os.listdir(self.dir), ["player_1.json"], "niente .tmp rimasti")
        self.age(1, 3)
        self.assertEqual(rs.load()["name"], "B")

    def test_window_for_rejoining_matches_the_server_grace_period(self):
        # The file must last longer than the server waits, and the "too fresh" limit must be shorter than that.
        from config import REJOIN_TIMER
        self.assertGreater(rs.MAX_AGE, REJOIN_TIMER)
        self.assertLess(rs.MIN_STALE, rs.MAX_AGE)


if __name__ == "__main__":
    unittest.main()