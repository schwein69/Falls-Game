"""
Test unitari per GameAuthority : la logica pura
delle regole di gioco (vittoria, blocchi, posizioni). Si lanciano con:

    python -m unittest tests.test_game_authority -v
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
from game_authority import GameAuthority


class TestWinCondition(unittest.TestCase):
    def test_two_players_last_one_alive_wins(self):
        auth = GameAuthority()
        auth.handle_update_pos(0, 0, 0, 0)
        auth.handle_update_pos(1, 0, 0, 0)
        game_over, winner = auth.handle_player_died(0)
        self.assertTrue(game_over)
        self.assertEqual(winner, 1)

    def test_solo_death_ends_game_without_winner(self):
        """Giocando da soli, la partita non finiva mai perche' serviva
        almeno un altro giocatore 'conosciuto' per dichiarare una vittoria."""
        auth = GameAuthority()
        auth.handle_update_pos(0, 0, 0, 0)
        game_over, winner = auth.handle_player_died(0)
        self.assertTrue(game_over)
        self.assertIsNone(winner)

    def test_three_players_game_continues_until_one_left(self):
        auth = GameAuthority()
        for pid in (0, 1, 2):
            auth.handle_update_pos(pid, 0, 0, 0)

        game_over, winner = auth.handle_player_died(0)
        self.assertFalse(game_over)  # ne restano ancora 2 vivi: la partita continua
        self.assertIsNone(winner)

        game_over, winner = auth.handle_player_died(1)
        self.assertTrue(game_over)  # resta solo il player 2
        self.assertEqual(winner, 2)

    def test_win_declared_only_once(self):
        auth = GameAuthority()
        auth.handle_update_pos(0, 0, 0, 0)
        auth.handle_update_pos(1, 0, 0, 0)
        auth.handle_player_died(0)
        # Se per qualche motivo arrivasse un secondo evento di morte per lo stesso giocatore
        # (es. messaggio duplicato via rete), non deve dichiarare una seconda "vittoria".
        game_over, winner = auth.handle_player_died(0)
        self.assertFalse(game_over)
        self.assertIsNone(winner)

    def test_disconnect_that_leaves_one_player_also_ends_game(self):
        """Chi si disconnette non 'muore', ma se lascia un solo giocatore in gara la partita
        finisce comunque (altrimenti non avrebbe piu' senso continuare)."""
        auth = GameAuthority()
        auth.handle_update_pos(0, 0, 0, 0)
        auth.handle_update_pos(1, 0, 0, 0)
        game_over, winner = auth.remove_player(0)
        self.assertTrue(game_over)
        self.assertEqual(winner, 1)


class TestBlockStep(unittest.TestCase):
    def test_first_claim_on_a_block_wins(self):
        auth = GameAuthority()
        result = auth.handle_block_step(pid=1, block_id=42)
        self.assertEqual(result, {"block_id": 42, "pid": 1})

    def test_second_claim_on_same_block_is_ignored(self):
        """Due giocatori che calpestano lo stesso blocco quasi insieme: solo il primo conta."""
        auth = GameAuthority()
        auth.handle_block_step(pid=1, block_id=42)
        result = auth.handle_block_step(pid=2, block_id=42)  # arrivato dopo, stesso blocco
        self.assertIsNone(result)

    def test_different_blocks_both_succeed(self):
        auth = GameAuthority()
        r1 = auth.handle_block_step(pid=1, block_id=1)
        r2 = auth.handle_block_step(pid=2, block_id=2)
        self.assertIsNotNone(r1)
        self.assertIsNotNone(r2)

    def test_already_destroyed_blocks_used_for_catchup(self):
        auth = GameAuthority()
        auth.handle_block_step(pid=1, block_id=5)
        auth.handle_block_step(pid=1, block_id=9)
        self.assertCountEqual(auth.already_destroyed_blocks(), [5, 9])


class TestPositionTracking(unittest.TestCase):
    def test_snapshot_contains_known_positions(self):
        auth = GameAuthority()
        auth.handle_update_pos(1, 1.0, 2.0, 3.0)
        payload = auth.snapshot_payload()
        self.assertEqual(payload, {1: [1.0, 2.0, 3.0]})

    def test_dead_player_position_removed_from_future_snapshots(self):
        """Bug reale trovato: la posizione di un giocatore morto restava per sempre nello
        snapshot, mostrando un 'fantasma' congelato a tutti gli altri."""
        auth = GameAuthority()
        auth.handle_update_pos(0, 0, 0, 0)
        auth.handle_update_pos(1, 5, 5, 5)
        auth.handle_player_died(1)
        payload = auth.snapshot_payload()
        self.assertNotIn(1, payload)
        self.assertIn(0, payload)

    def test_removed_player_position_cleared(self):
        auth = GameAuthority()
        auth.handle_update_pos(0, 0, 0, 0)
        auth.handle_update_pos(1, 5, 5, 5)
        auth.remove_player(1)
        payload = auth.snapshot_payload()
        self.assertNotIn(1, payload)


if __name__ == "__main__":
    unittest.main()