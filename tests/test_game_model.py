import os
import sys
import unittest

_here = os.path.dirname(os.path.abspath(__file__))
for _sub in ("shared", "server", "client", "client/model"):
    sys.path.insert(0, os.path.join(_here, "..", _sub))


from game_model import GameModel


class TestGameModel(unittest.TestCase):
    def test_starts_with_no_match_and_no_network(self):
        # A new model is empty: no player, no floor, no network, no other players, all flags off.
        m = GameModel()
        self.assertIsNone(m.player)
        self.assertIsNone(m.floors)
        self.assertIsNone(m.network_manager)
        self.assertIsNone(m.game_authority)
        self.assertEqual((m.connected_ids, m.other_players), (set(), {}))
        self.assertFalse(any([m.game_over, m.game_paused, m.level_loaded, m.lobby_open,
                              m.player_died_reported, m.migrating]))

    def test_not_in_active_match_at_start(self):
        # A new model says "no match in progress".
        self.assertFalse(GameModel().is_in_active_match())

    def test_each_of_level_floors_player_means_in_match(self):
        # If any one of level, floors or player is set, we are in a match.
        for attr, value in (("level_loaded", True), ("floors", object()), ("player", object())):
            m = GameModel()
            setattr(m, attr, value)
            self.assertTrue(m.is_in_active_match(), attr)

    def test_back_to_menu_means_not_in_match(self):
        # After clearing level, floors and player (back to menu), we are no longer in a match.
        m = GameModel()
        m.level_loaded, m.floors, m.player = True, object(), object()
        m.level_loaded, m.floors, m.player = False, None, None
        self.assertFalse(m.is_in_active_match())

    def test_instances_do_not_share_state(self):
        # Two models are independent: changing one does not change the other.
        a, b = GameModel(), GameModel()
        a.connected_ids.add(1)
        a.other_players[1] = "x"
        a.gui_elements.append("g")
        self.assertEqual((b.connected_ids, b.other_players, b.gui_elements), (set(), {}, []))


if __name__ == "__main__":
    unittest.main()