"""Connect 4 engine. Run: python3 -m unittest discover -s tests."""
import copy
import json
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import connect4 as c4
import server

INITIAL = copy.deepcopy(server.state)
c4.SYNC_BOT = True


def players(n):
    return {p: {"name": f"P{p}", "color": "#fff", "score": 0, "connected": True} for p in range(1, n + 1)}


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.g = c4.fresh()
        self.players = players(4)

    def act(self, pid, action, **data):
        return c4.act(self.g, self.players, pid, action, data)

    def mover(self, m):
        return c4.dropper(m["sides"][m["turn"]])

    def play(self, m, cols):
        for col in cols:
            self.assertIsNone(self.act(self.mover(m), "c4Drop", col=col)[0])

    def test_duels_pair_everyone(self):
        self.act(None, "c4Start")
        self.assertEqual(self.g["phase"], "playing")
        self.assertEqual(len(self.g["matches"]), 2)
        seated = sorted(p for m in self.g["matches"] for s in m["sides"] for p in s["pids"])
        self.assertEqual(seated, [1, 2, 3, 4])

    def test_odd_player_gets_the_computer(self):
        self.players = players(3)
        self.act(None, "c4Start")
        kinds = sorted(s["kind"] for m in self.g["matches"] for s in m["sides"])
        self.assertEqual(kinds, ["bot", "player", "player", "player"])

    def test_vertical_win_scores_and_ends_round(self):
        self.players = players(2)
        self.act(None, "c4Start")
        m = self.g["matches"][0]
        red = self.mover(m)
        self.play(m, [3, 4, 3, 4, 3, 4, 3])
        self.assertEqual(m["result"], "win")
        self.assertEqual(m["winner"], 0)
        self.assertEqual(len(m["line"]), 4)
        self.assertEqual(self.players[red]["score"], c4.WIN_PTS)
        self.assertEqual(self.g["phase"], "done")
        rows = c4.standings(self.g, self.players)
        self.assertEqual((rows[0]["pid"], rows[0]["w"], rows[0]["pts"]), (red, 1, 3))

    def test_diagonal_win(self):
        board = [[0] * 6 for _ in range(7)]
        for c, h in enumerate([1, 2, 3, 4]):
            for r in range(h):
                board[c][r] = 2
            board[c][h - 1] = 1
        self.assertEqual(len(c4._line_through(board, 3, 3, 4)), 4)

    def test_turns_and_full_columns_are_enforced(self):
        self.players = players(2)
        self.act(None, "c4Start")
        m = self.g["matches"][0]
        waiting = c4.dropper(m["sides"][1])
        self.assertEqual(self.act(waiting, "c4Drop", col=0)[0], "It's not your turn.")
        self.play(m, [0, 0, 0, 0, 0, 0])
        self.assertEqual(self.act(self.mover(m), "c4Drop", col=0)[0], "That column is full.")

    def test_draw_on_a_full_board(self):
        self.players = players(2)
        self.g["cfg"]["size"] = "6x5"
        self.act(None, "c4Start")
        m = self.g["matches"][0]
        # fill columns in pairs (0,1), (2,3), (4,5) with a pattern that never lines up 4
        order = []
        for a, b in ((0, 1), (2, 3), (4, 5)):
            seq = [a, b] * 2 + [b, a] * 2 + [a, b]
            order += seq
        for col in order:
            if m["result"]:
                break
            self.act(self.mover(m), "c4Drop", col=col)
        self.assertEqual(m["result"], "draw")
        self.assertTrue(all(p["score"] == c4.DRAW_PTS for p in self.players.values()))

    def test_teams_rotate_droppers(self):
        self.g["cfg"]["format"] = "teams"
        self.act(None, "c4Start")
        m = self.g["matches"][0]
        self.assertEqual(sorted(len(s["pids"]) for s in m["sides"]), [2, 2])
        first = self.mover(m)
        self.play(m, [0, 1])
        self.assertNotEqual(self.mover(m), first)          # teammate's turn now
        self.assertEqual(c4.dropper(m["sides"][m["turn"]]) in m["sides"][m["turn"]]["pids"], True)

    def test_team_picks_are_respected(self):
        self.g["cfg"]["format"] = "teams"
        for p in (1, 2, 3):
            self.act(p, "c4Team", team=1)
        self.act(None, "c4Start")
        red = next(s for s in self.g["matches"][0]["sides"] if s["name"] == "Team Red")
        self.assertEqual(sorted(red["pids"]), [1, 2, 3])

    def test_bot_takes_its_turn(self):
        self.players = players(1)
        self.g["cfg"].update(format="bot", bot="medium")
        self.act(None, "c4Start")
        m = self.g["matches"][0]
        if m["sides"][0]["kind"] != "bot":
            self.act(1, "c4Drop", col=3)
        before = m["moves"]
        self.assertTrue(c4.tick(self.g, self.players, now=time.time() + 5))
        self.assertEqual(m["moves"], before + 1)
        self.assertEqual(m["sides"][m["turn"]]["kind"], "player")

    def test_medium_and_hard_always_block(self):
        m = {"cols": 7, "rows": 6, "need": 4, "turn": 1,
             "board": [[1, 1, 1, 0, 0, 0], [2, 0, 0, 0, 0, 0], [2, 0, 0, 0, 0, 0]] + [[0] * 6] * 4}
        for level in ("medium", "hard"):
            for _ in range(20):
                self.assertEqual(c4.bot_move(m, level), 0)

    def test_bot_takes_a_win(self):
        m = {"cols": 7, "rows": 6, "need": 4, "turn": 0,
             "board": [[1, 1, 1, 0, 0, 0]] + [[2, 0, 0, 0, 0, 0]] * 3 + [[0] * 6] * 3}
        for level in c4.BOTS[1:]:
            self.assertEqual(c4.bot_move(m, level), 0)

    def test_player_who_leaves_is_replaced_by_the_computer(self):
        self.players = players(2)
        self.act(None, "c4Start")
        m = self.g["matches"][0]
        gone = self.mover(m)
        del self.players[gone]
        self.assertTrue(c4.tick(self.g, self.players, now=time.time() + 5))
        self.assertIn("bot", [s["kind"] for s in m["sides"]])
        self.assertEqual(m["moves"], 1)                # and it already moved for them

    def test_move_clock_auto_drops(self):
        self.players = players(2)
        self.g["cfg"]["timer"] = 15
        self.act(None, "c4Start")
        m = self.g["matches"][0]
        c4.tick(self.g, self.players, now=time.time() + 16)
        self.assertEqual(m["moves"], 1)
        self.assertIn("Time", m["auto"])

    def test_settings_validation_and_lobby(self):
        self.assertIsNone(self.act(None, "c4Set", key="size", value="9x7")[0])
        self.assertIsNotNone(self.act(None, "c4Set", key="size", value="99x99")[0])
        self.assertIsNone(self.act(None, "c4Set", key="connect", value="5")[0])
        self.assertEqual(self.g["cfg"]["connect"], 5)
        self.act(None, "c4Start")
        self.assertIsNotNone(self.act(None, "c4Set", key="bot", value="easy")[0])
        self.act(None, "c4Lobby")
        self.assertEqual((self.g["phase"], self.g["matches"]), ("lobby", []))

    def test_public_puts_my_board_first(self):
        self.act(None, "c4Start")
        mine = next(m["id"] for m in self.g["matches"] if 4 in [p for s in m["sides"] for p in s["pids"]])
        pub = c4.public(self.g, self.players, 4)
        self.assertEqual(pub["matches"][0]["id"], mine)
        self.assertEqual(pub["me"]["match"], mine)
        self.assertLess(len(json.dumps(pub)), 8000)


class ServerTests(unittest.TestCase):
    def setUp(self):
        server.state.clear()
        server.state.update(copy.deepcopy(INITIAL))

    def test_host_only_and_dispatch(self):
        self.assertTrue(server.do_select("connect4")["ok"])
        server.state["players"][1] = {"name": "A", "color": "#fff", "score": 0, "token": "t", "connected": True}
        self.assertEqual(server.do_input({"pid": 1, "token": "t", "action": "c4Start"})["error"], "not_host")
        self.assertTrue(server.do_input({"action": "c4Start"}, is_host=True)["ok"])
        pub = server.public_state(1)["connect4"]
        self.assertEqual(pub["phase"], "playing")
        m = pub["matches"][0]
        self.assertEqual([s["kind"] for s in m["sides"]].count("bot"), 1)


if __name__ == "__main__":
    unittest.main()
