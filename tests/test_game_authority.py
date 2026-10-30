import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
from game_authority import GameAuthority

# Who wins, and when the match ends.
class TestWinCondition(unittest.TestCase):
    # With two players, when one dies the other wins.
    def test_two_players_last_one_alive_wins(self):
        auth = GameAuthority()
        auth.handle_update_pos(0, 0, 0, 0)
        auth.handle_update_pos(1, 0, 0, 0)
        game_over, winner = auth.handle_player_died(0)
        self.assertTrue(game_over)
        self.assertEqual(winner, 1)

    # Playing alone: the match ends when you die, with no winner.
    def test_solo_death_ends_game_without_winner(self):
        auth = GameAuthority()
        auth.handle_update_pos(0, 0, 0, 0)
        game_over, winner = auth.handle_player_died(0)
        self.assertTrue(game_over)
        self.assertIsNone(winner)

    # With three players the match goes on until only one is left.
    def test_three_players_game_continues_until_one_left(self):
        auth = GameAuthority()
        for pid in (0, 1, 2):
            auth.handle_update_pos(pid, 0, 0, 0)

        game_over, winner = auth.handle_player_died(0)
        self.assertFalse(game_over)                 # two are still alive: the match continues
        self.assertIsNone(winner)

        game_over, winner = auth.handle_player_died(1)
        self.assertTrue(game_over)                  # only player 2 is left
        self.assertEqual(winner, 2)

    # A duplicated death message must not declare a second victory.
    def test_win_declared_only_once(self):
        auth = GameAuthority()
        auth.handle_update_pos(0, 0, 0, 0)
        auth.handle_update_pos(1, 0, 0, 0)
        auth.handle_player_died(0)
        game_over, winner = auth.handle_player_died(0)      # same death again
        self.assertFalse(game_over)
        self.assertIsNone(winner)

    # A disconnected player does not "die", but if only one is left the match ends anyway.
    def test_disconnect_that_leaves_one_player_also_ends_game(self):
        auth = GameAuthority()
        auth.handle_update_pos(0, 0, 0, 0)
        auth.handle_update_pos(1, 0, 0, 0)
        game_over, winner = auth.remove_player(0)
        self.assertTrue(game_over)
        self.assertEqual(winner, 1)


# Blocks: the authority decides who stepped on a block first.
class TestBlockStep(unittest.TestCase):
    # The first claim on a block wins and returns the event to send.
    def test_first_claim_on_a_block_wins(self):
        auth = GameAuthority()
        result = auth.handle_block_step(pid=1, block_id=42)
        self.assertEqual(result, {"block_id": 42, "pid": 1})

    # Two players step on the same block almost together: only the first counts.
    def test_second_claim_on_same_block_is_ignored(self):
        auth = GameAuthority()
        auth.handle_block_step(pid=1, block_id=42)
        result = auth.handle_block_step(pid=2, block_id=42)     # arrived later, same block
        self.assertIsNone(result)

    # Different blocks do not interfere: both claims are accepted.
    def test_different_blocks_both_succeed(self):
        auth = GameAuthority()
        r1 = auth.handle_block_step(pid=1, block_id=1)
        r2 = auth.handle_block_step(pid=2, block_id=2)
        self.assertIsNotNone(r1)
        self.assertIsNotNone(r2)

    # The destroyed blocks are remembered, to send them to who joins or rejoins (sync_state).
    def test_already_destroyed_blocks_used_for_catchup(self):
        auth = GameAuthority()
        auth.handle_block_step(pid=1, block_id=5)
        auth.handle_block_step(pid=1, block_id=9)
        self.assertCountEqual(auth.already_destroyed_blocks(), [5, 9])


# Positions: what goes into the periodic state_snapshot.
class TestPositionTracking(unittest.TestCase):
    # The snapshot contains the last known position of each player.
    def test_snapshot_contains_known_positions(self):
        auth = GameAuthority()
        auth.handle_update_pos(1, 1.0, 2.0, 3.0)
        payload = auth.snapshot_payload()
        self.assertEqual(payload, {1: [1.0, 2.0, 3.0]})

    # Real bug: a dead player stayed in the snapshot forever, a frozen "ghost" for everybody.
    def test_dead_player_position_removed_from_future_snapshots(self):
        auth = GameAuthority()
        auth.handle_update_pos(0, 0, 0, 0)
        auth.handle_update_pos(1, 5, 5, 5)
        auth.handle_player_died(1)
        payload = auth.snapshot_payload()
        self.assertNotIn(1, payload)
        self.assertIn(0, payload)

    # A player who leaves is removed from the snapshots too.
    def test_removed_player_position_cleared(self):
        auth = GameAuthority()
        auth.handle_update_pos(0, 0, 0, 0)
        auth.handle_update_pos(1, 5, 5, 5)
        auth.remove_player(1)
        payload = auth.snapshot_payload()
        self.assertNotIn(1, payload)


if __name__ == "__main__":
    unittest.main()