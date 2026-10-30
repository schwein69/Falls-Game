import json
import os
import sys
import time
import unittest
from unittest.mock import patch

filePath = os.path.dirname(os.path.abspath(__file__))
for _sub in ("shared", "server", ""):
    sys.path.insert(0, os.path.join(filePath, "..", _sub))
import network
from network import Room, OnlineStatus
from game_authority import GameAuthority


class TestRoom(unittest.TestCase):
    def setUp(self):
        self.room = Room()
        self.a, self.b = ("10.0.0.1", 5000), ("10.0.0.2", 5001)

    def test_starts_empty(self):
        # A new room has no players, no pauses, and the match has not started.
        self.assertEqual((self.room.clients, self.room.silent, self.room.rejoining), ({}, {}, {}))
        self.assertFalse(self.room.match_started)
        self.assertFalse(self.room.closed)
        self.assertEqual(self.room.next_id, 1)

    def test_is_full_counts_the_host_only_when_it_plays(self):
        # The room is full only when players (plus the host, if it plays too) reach the maximum.
        with patch.object(network, "MAX_PLAYERS", 3):
            self.room.clients = {self.a: 1}                 
            self.assertFalse(self.room.is_full())
            self.room.clients[self.b] = 2
            self.assertFalse(self.room.is_full())           
            self.room.host_plays = True                     
            self.assertTrue(self.room.is_full())

    def test_addr_of_finds_by_id_and_optionally_by_ip(self):
        # Find a player's address by id; if an IP is given, it must match too.
        self.room.clients = {self.a: 1, self.b: 2}
        self.assertEqual(self.room.addr_of(2), self.b)
        self.assertEqual(self.room.addr_of(2, ip="10.0.0.2"), self.b)
        self.assertIsNone(self.room.addr_of(2, ip="10.0.0.1"), "l'id c'e' ma da un altro IP")
        self.assertIsNone(self.room.addr_of(9))

    def test_forget_removes_the_player_from_every_table(self):
        # Forgetting a player removes him from all lists, and doing it twice is fine.
        r = self.room
        r.clients[self.a] = 1
        r.last_seen[self.a] = time.time()
        r.silent[self.a] = (1, time.time())
        r.rejoining[1] = time.time()
        r.forget(self.a, 1)
        self.assertEqual((r.clients, r.last_seen, r.silent, r.rejoining), ({}, {}, {}, {}))
        r.forget(self.a, 1)                                  

    def test_rebind_moves_entries_and_keeps_the_pause(self):
        # When a player's address changes, his data moves to the new address and his pause is kept.
        r = self.room
        r.clients[self.a] = 1
        r.last_seen[self.a] = 0
        r.silent[self.a] = (1, 123.0)
        r.rebind(self.a, self.b, 1)
        self.assertEqual(r.clients, {self.b: 1})
        self.assertNotIn(self.a, r.last_seen)
        self.assertEqual(r.silent, {self.b: (1, 123.0)})
        self.assertGreater(r.last_seen[self.b], 0)

    def test_move_replaces_old_address_and_marks_rejoining(self):
        # A player rejoining from a new address replaces the old one and is marked as loading.
        r = self.room
        r.clients[self.a] = 1
        r.silent[self.a] = (1, time.time())
        r.move(self.a, self.b, 1)
        self.assertEqual(r.clients, {self.b: 1})
        self.assertEqual(r.silent, {}, "chi rientra non e' piu' 'silenzioso'")
        self.assertIn(1, r.rejoining)


class TestOnlineStatus(unittest.TestCase):
    def test_default_is_idle_without_error(self):
        # A new online status starts as 'idle' with no error.
        s = OnlineStatus()
        self.assertEqual((s.status, s.error), ("idle", None))


class TestWinCondition(unittest.TestCase):
    def test_two_players_last_one_alive_wins(self):
        # With two players, when one dies the other wins.
        auth = GameAuthority()
        auth.handle_update_pos(0, 0, 0, 0)
        auth.handle_update_pos(1, 0, 0, 0)
        self.assertEqual(auth.handle_player_died(0), (True, 1))

    def test_solo_death_ends_game_without_winner(self):
        # A single player who dies ends the game with no winner.
        auth = GameAuthority()
        auth.handle_update_pos(0, 0, 0, 0)
        self.assertEqual(auth.handle_player_died(0), (True, None))

    def test_three_players_game_continues_until_one_left(self):
        # With three players the game goes on until only one is alive.
        auth = GameAuthority()
        for pid in (0, 1, 2):
            auth.handle_update_pos(pid, 0, 0, 0)
        self.assertEqual(auth.handle_player_died(0), (False, None))
        self.assertEqual(auth.handle_player_died(1), (True, 2))

    def test_win_declared_only_once(self):
        # The win is announced only once, even if the same death arrives twice.
        auth = GameAuthority()
        auth.handle_update_pos(0, 0, 0, 0)
        auth.handle_update_pos(1, 0, 0, 0)
        auth.handle_player_died(0)
        self.assertEqual(auth.handle_player_died(0), (False, None), "un secondo evento uguale non vince di nuovo")

    def test_disconnect_that_leaves_one_player_also_ends_game(self):
        # If someone leaves and only one player remains, the game ends.
        auth = GameAuthority()
        auth.handle_update_pos(0, 0, 0, 0)
        auth.handle_update_pos(1, 0, 0, 0)
        self.assertEqual(auth.remove_player(0), (True, 1))

    def test_mix_of_dead_and_left_players(self):
        # One player left and one died: the third player wins.
        auth = GameAuthority()
        for pid in (0, 1, 2):
            auth.handle_update_pos(pid, 0, 0, 0)
        auth.remove_player(0)                                # uno esce
        self.assertEqual(auth.handle_player_died(1), (True, 2), "uno e' uscito, uno morto: vince il terzo")

    def test_everyone_gone_means_game_over_without_winner(self):
        # After the win is declared, a later leave does not declare a second ending.
        auth = GameAuthority()
        for pid in (0, 1):
            auth.handle_update_pos(pid, 0, 0, 0)
        auth.remove_player(0)                                # qui vince l'1...
        self.assertTrue(auth.game_won)
        self.assertEqual(auth.remove_player(1), (False, None), "...e non si dichiara una seconda fine")


class TestBlockStep(unittest.TestCase):
    def test_first_claim_on_a_block_wins(self):
        # The first player to step on a block destroys it and gets the credit.
        self.assertEqual(GameAuthority().handle_block_step(pid=1, block_id=42), {"block_id": 42, "pid": 1})

    def test_second_claim_on_same_block_is_ignored(self):
        # A second player stepping on the same block is ignored.
        auth = GameAuthority()
        auth.handle_block_step(pid=1, block_id=42)
        self.assertIsNone(auth.handle_block_step(pid=2, block_id=42))

    def test_different_blocks_both_succeed(self):
        # Two players stepping on different blocks both succeed.
        auth = GameAuthority()
        self.assertIsNotNone(auth.handle_block_step(1, 1))
        self.assertIsNotNone(auth.handle_block_step(2, 2))

    def test_none_block_is_ignored(self):
        # A missing block id is ignored.
        self.assertIsNone(GameAuthority().handle_block_step(1, None))

    def test_already_destroyed_blocks_used_for_catchup(self):
        # The list of destroyed blocks is available to update someone who joins late.
        auth = GameAuthority()
        auth.handle_block_step(1, 5)
        auth.handle_block_step(1, 9)
        self.assertCountEqual(auth.already_destroyed_blocks(), [5, 9])


class TestPositionTracking(unittest.TestCase):
    def test_snapshot_contains_known_positions(self):
        # The snapshot contains the last known position of each player.
        auth = GameAuthority()
        auth.handle_update_pos(1, 1.0, 2.0, 3.0)
        self.assertEqual(auth.snapshot_payload(), {1: [1.0, 2.0, 3.0]})

    def test_dead_player_position_removed_from_future_snapshots(self):
        # A dead player disappears from the snapshot (no frozen ghost).
        auth = GameAuthority()
        auth.handle_update_pos(0, 0, 0, 0)
        auth.handle_update_pos(1, 5, 5, 5)
        auth.handle_player_died(1)
        payload = auth.snapshot_payload()
        self.assertNotIn(1, payload)
        self.assertIn(0, payload)

    def test_removed_player_position_cleared(self):
        # A player who left disappears from the snapshot.
        auth = GameAuthority()
        auth.handle_update_pos(0, 0, 0, 0)
        auth.handle_update_pos(1, 5, 5, 5)
        auth.remove_player(1)
        self.assertNotIn(1, auth.snapshot_payload())

    def test_floor_seed_differs_between_matches(self):
        # Each new match gets a different random floor seed.
        seeds = {GameAuthority().floor_seed for _ in range(5)}
        self.assertGreater(len(seeds), 1)


class TestApply(unittest.TestCase):
    def setUp(self):
        self.auth = GameAuthority()

    def test_block_step_returns_event_once(self):
        # Stepping on a block creates one 'block destroyed' event; the same block again does nothing.
        changed, events = self.auth.apply(5, "block_step", [5, 11])
        self.assertTrue(changed)
        self.assertEqual(events, [("block_destroyed", (11, 5))])
        self.assertEqual(self.auth.apply(6, "block_step", [6, 11]), (False, []), "gia' distrutto")

    def test_death_and_win(self):
        # A death creates 'player removed' and, if one is left, 'game won' events.
        self.auth.apply(1, "update_pos", [1, 0, 0, 0])
        self.auth.apply(2, "update_pos", [2, 0, 0, 0])
        _, events = self.auth.apply(1, "player_died", [99])      
        self.assertEqual(events, [("player_removed", (1,)), ("game_won", (2,))])
        self.assertEqual(self.auth.dead_players, {1})

    def test_sender_is_who_counts_not_the_id_in_the_args(self):
        # The real sender counts, not the id written inside the message (anti-cheat).
        self.auth.apply(5, "update_pos", [7, 1, 2, 3])
        self.assertEqual(list(self.auth.player_positions), [5])

    def test_internal_left_uses_argument(self):
        # A 'player left' made by the server itself uses the id in the message.
        self.auth.apply(0, "player_left", [7], internal=True)
        self.assertIn(7, self.auth.left_players)

    def test_not_internal_left_uses_sender(self):
        # A 'player left' from a client only affects the client who sent it.
        self.auth.apply(4, "player_left", [7])
        self.assertEqual(self.auth.left_players, {4})

    def test_dead_or_left_players_are_ignored_afterwards(self):
        # Dead or left players cannot move or destroy blocks anymore.
        self.auth.apply(1, "update_pos", [1, 0, 0, 0])
        self.auth.apply(2, "update_pos", [2, 0, 0, 0])
        self.auth.apply(1, "player_died", [1])
        self.assertEqual(self.auth.apply(1, "update_pos", [1, 9, 9, 9]), (False, []))
        self.assertEqual(self.auth.apply(1, "block_step", [1, 3]), (False, []))
        self.assertNotIn(1, self.auth.player_positions)
        self.assertNotIn(3, self.auth.destroyed_blocks)


class TestAuthorityHelpers(unittest.TestCase):
    class FakeNM:
        player_id = 1

        def __init__(self):
            self.broadcasts, self.direct = [], []

        def broadcast_message(self, name, *args):
            self.broadcasts.append((name, args))

        def send_to(self, addr, name, *args):
            self.direct.append((addr, name, args))

    def test_apply_and_broadcast_tells_everybody_and_the_host_screen(self):
        # Each event is sent to everybody, and also shown through `show`).
        auth, nm, shown = GameAuthority(), self.FakeNM(), []
        auth.apply_and_broadcast(nm, 2, "block_step", [2, 7], show=lambda name, args: shown.append(name))
        self.assertEqual(nm.broadcasts, [("block_destroyed", (7, 2))])
        self.assertEqual(shown, ["block_destroyed"])

    def test_apply_and_broadcast_without_screen_and_without_events(self):
        # The server has no `show`; a position update changes the state but sends no event.
        auth, nm = GameAuthority(), self.FakeNM()
        auth.apply_and_broadcast(nm, 2, "update_pos", [2, 1, 2, 3])
        self.assertEqual(nm.broadcasts, [])
        self.assertEqual(auth.player_positions, {2: (1, 2, 3)})

    def test_a_client_cannot_pass_for_someone_else(self):
        # Client 5 says player 7 died: only 5 counts, because 5 is not the host/server.
        auth, nm = GameAuthority(), self.FakeNM()
        auth.apply_and_broadcast(nm, 5, "player_died", [7])
        self.assertEqual(auth.dead_players, {5})

    def test_send_state_to_one_client(self):
        # Who joins gets the destroyed blocks, the positions and the floor seed, and nobody else.
        auth, nm = GameAuthority(), self.FakeNM()
        auth.handle_block_step(3, 9)
        auth.send_state_to(nm, ("h", 1))
        self.assertEqual(nm.direct, [(("h", 1), "sync_state", ([9], {}, auth.floor_seed))])
        self.assertEqual(nm.broadcasts, [])


if __name__ == "__main__":
    unittest.main()