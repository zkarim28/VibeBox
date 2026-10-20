"""Sudoku engine + puzzle bank. Run: python3 -m unittest discover -s tests."""
import copy
import json
import os
from pathlib import Path
import random
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sudoku
import sudoku_puzzles as sp
import server

INITIAL = copy.deepcopy(server.state)


class TempHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._path = sudoku.HISTORY_PATH
        sudoku.HISTORY_PATH = os.path.join(self.tmp.name, "history.json")

    def tearDown(self):
        sudoku.HISTORY_PATH = self._path
        self.tmp.cleanup()


class PuzzleTests(unittest.TestCase):
    def test_bank_boards_are_unique_and_graded(self):
        bank = sp.load_bank()
        for lvl in sp.LEVELS:
            self.assertGreaterEqual(len(bank[lvl]), 20, lvl)
            ids = [e["id"] for e in bank[lvl]]
            self.assertEqual(len(ids), len(set(ids)))
            for e in bank[lvl]:
                self.assertEqual(sp.count_solutions(e["p"])[0], 1, e["id"])
                self.assertEqual(sp.solve(e["p"]), e["s"], e["id"])
                self.assertEqual(sp.level_for(e["p"]), lvl, e["id"])

    def test_transform_keeps_solution_and_level(self):
        rng = random.Random(7)
        for lvl in sp.LEVELS:
            e = sp.load_bank()[lvl][0]
            p, s = sp.transform(e["p"], e["s"], rng)
            self.assertEqual(sp.solve(p), s)
            self.assertEqual(sp.level_for(p), lvl)
            self.assertTrue(all(a == "0" or a == b for a, b in zip(p, s)))

    def test_rejects_broken_boards(self):
        self.assertEqual(sp.count_solutions("11" + "0" * 79)[0], 0)
        self.assertEqual(sp.count_solutions("0" * 81)[0], 2)


class EngineTests(TempHistory):
    def setUp(self):
        super().setUp()
        self.players = {i: {"name": f"P{i}", "connected": True, "color": "#fff", "score": 0} for i in (1, 2, 3)}
        self.g = sudoku.fresh()

    def act(self, pid, action, **data):
        return sudoku.act(self.g, self.players, pid, action, data)

    def start(self, mode, level="easy"):
        self.assertIsNone(self.act(None, "sdSet", key="mode", value=mode)[0])
        self.assertIsNone(self.act(None, "sdSet", key="difficulty", value=level)[0])
        self.assertIsNone(self.act(None, "sdStart")[0])

    def solve_for(self, pid, board, wrong=0):
        sol = board["solution"]
        for i, ch in enumerate(board["puzzle"]):
            if ch == "0":
                for _ in range(wrong):
                    bad = str(int(sol[i]) % 9 + 1)
                    self.assertIs(self.act(pid, "sdPlace", i=i, d=int(bad))[1]["correct"], False)
                    wrong = 0
                self.act(pid, "sdPlace", i=i, d=int(sol[i]))

    def test_competitive_race_ranks_by_penalised_time(self):
        self.start("competitive")
        board = self.g["board"]
        for pid in (1, 2, 3):
            self.assertEqual(self.g["players"][pid]["board"], board)
        self.solve_for(1, board, wrong=2)
        self.assertEqual(self.g["phase"], "playing")
        self.solve_for(2, board)
        self.g["players"][1]["finishedAt"] = self.g["startedAt"] + 10    # 10 + 60 penalty
        self.g["players"][2]["finishedAt"] = self.g["startedAt"] + 30
        self.solve_for(3, board)
        self.g["players"][3]["finishedAt"] = self.g["startedAt"] + 20
        self.assertTrue(sudoku.tick(self.g, self.players) or self.g["phase"] == "done")
        self.assertEqual(self.g["phase"], "done")
        places = {pid: self.g["players"][pid]["place"] for pid in (1, 2, 3)}
        self.assertEqual(places, {3: 1, 2: 2, 1: 3})
        self.assertEqual(self.players[3]["score"], 300)
        hist = sudoku.load_history()
        self.assertEqual({r["player"] for r in hist}, {"P1", "P2", "P3"})
        self.assertTrue(all(r["board"] == board["id"] and r["result"] == "solved" for r in hist))

    def test_round_finishes_when_last_remaining_player_solves(self):
        self.start("competitive")
        board = self.g["board"]
        self.solve_for(1, board)
        del self.players[2], self.players[3]
        self.assertTrue(sudoku.tick(self.g, self.players))
        self.assertEqual(self.g["phase"], "done")
        results = {r["player"]: r["result"] for r in sudoku.load_history()}
        self.assertEqual(results, {"P1": "solved", "P2": "unfinished", "P3": "unfinished"})

    def test_competitive_wrong_digits_never_placed(self):
        self.start("competitive", "hard")
        board = self.g["board"]
        i = board["puzzle"].index("0")
        bad = int(board["solution"][i]) % 9 + 1
        self.act(1, "sdPlace", i=i, d=bad)
        self.assertEqual(self.g["players"][1]["cells"][i], "0")
        self.assertEqual(self.g["players"][1]["mistakes"], 1)
        given = next(k for k, ch in enumerate(board["puzzle"]) if ch != "0")
        self.assertEqual(self.act(1, "sdPlace", i=given, d=1)[0], "That's a given.")

    def test_coop_shares_one_board(self):
        self.start("coop")
        board = self.g["board"]
        blanks = [i for i, ch in enumerate(board["puzzle"]) if ch == "0"]
        for n, i in enumerate(blanks):
            self.act(1 + n % 3, "sdPlace", i=i, d=int(board["solution"][i]))
        self.assertEqual(self.g["phase"], "done")
        self.assertEqual(sum(self.g["players"][p]["placed"] for p in (1, 2, 3)), len(blanks))
        self.assertEqual(len(sudoku.load_history()), 3)

    def test_late_joiner_can_help_coop_but_only_watches_a_race(self):
        self.start("coop")
        self.players[4] = {"name": "Late", "connected": True, "color": "#000", "score": 0}
        self.assertEqual(self.act(4, "sdPlace", i=0, d=1)[0], "You're watching this round.")
        self.act(4, "sdJoin")
        self.assertIn(4, self.g["players"])

    def test_each_player_gets_own_board(self):
        self.assertIsNone(self.act(None, "sdSet", key="boards", value="each")[0])
        self.start("competitive")
        ids = {self.g["players"][p]["board"]["puzzle"] for p in (1, 2, 3)}
        self.assertEqual(len(ids), 3)
        seat = self.g["players"][2]
        self.solve_for(2, seat["board"])
        self.assertTrue(seat["finishedAt"])
        self.act(None, "sdEnd")
        rows = {r["player"]: r for r in sudoku.load_history()}
        self.assertEqual(rows["P2"]["board"], seat["board"]["id"])
        self.assertEqual(rows["P2"]["result"], "solved")
        self.assertEqual(self.g["boards"], "each")      # setting survives the round

    def test_three_lives_knocks_racer_out(self):
        self.assertIsNone(self.act(None, "sdSet", key="lives", value="3")[0])
        self.start("competitive")
        board = self.g["board"]
        i = board["puzzle"].index("0")
        bad = int(board["solution"][i]) % 9 + 1
        for n in range(3):
            err, extra = self.act(1, "sdPlace", i=i, d=bad)
        self.assertTrue(extra["out"])
        seat = self.g["players"][1]
        self.assertTrue(seat["out"])
        self.assertIsNotNone(sudoku._cant_watch(self.g, 1, 1))
        self.assertIsNone(sudoku._cant_watch(self.g, 1, 2))     # out players may spectate
        self.solve_for(2, board)
        self.solve_for(3, board)
        self.assertEqual(self.g["phase"], "done")
        self.assertNotIn("place", seat)
        self.assertEqual({r["player"]: r["result"] for r in sudoku.load_history()}["P1"], "out")

    def test_three_lives_coop_team_loses(self):
        self.act(None, "sdSet", key="lives", value=3)
        self.start("coop")
        board = self.g["board"]
        i = board["puzzle"].index("0")
        bad = int(board["solution"][i]) % 9 + 1
        for pid in (1, 2, 3):
            self.act(pid, "sdPlace", i=i, d=bad)
        self.assertEqual(self.g["phase"], "done")
        self.assertTrue(all(r["result"] == "out" for r in sudoku.load_history()))

    def test_pause_freezes_clock_and_hides_board(self):
        self.start("competitive")
        self.assertIsNone(self.act(None, "sdPause", value=True)[0])
        pub = sudoku.public(self.g, self.players, 1)
        self.assertTrue(pub["paused"])
        self.assertNotIn("puzzle", pub)
        self.assertEqual(self.act(1, "sdPlace", i=0, d=1)[0], "The game is paused.")
        start = self.g["startedAt"]
        self.g["pausedAt"] -= 100                     # pretend the pause lasted 100 s
        self.act(None, "sdPause", value=False)
        self.assertAlmostEqual(self.g["startedAt"] - start, 100, delta=1)
        self.assertEqual(self.g["pausedAt"], 0)
        self.assertIn("puzzle", sudoku.public(self.g, self.players, 1))

    def test_zen_boards_are_personal_and_unchecked(self):
        self.start("zen", "medium")
        b1, b2 = self.g["players"][1]["board"], self.g["players"][2]["board"]
        self.assertIsNot(b1, b2)
        i = b1["puzzle"].index("0")
        bad = int(b1["solution"][i]) % 9 + 1
        self.assertEqual(self.act(1, "sdPlace", i=i, d=bad), (None, {"correct": True}))
        self.assertEqual(self.g["players"][1]["mistakes"], 0)
        self.act(1, "sdPlace", i=i, d=0)
        self.solve_for(1, b1)
        self.assertTrue(self.g["players"][1]["finishedAt"])
        self.assertIsNone(self.act(1, "sdZenNew", difficulty="expert")[0])
        self.assertEqual(self.g["players"][1]["board"]["level"], "expert")
        self.assertIsNone(self.act(2, "sdZenNew", difficulty="easy")[0])
        rows = sudoku.load_history()
        self.assertEqual([(r["player"], r["result"]) for r in rows], [("P1", "solved"), ("P2", "unfinished")])

    def test_public_never_leaks_a_solution(self):
        for mode in sudoku.MODES:
            self.act(None, "sdLobby")
            self.start(mode)
            for pid in (None, 1, 2, 99):
                text = json.dumps(sudoku.public(self.g, self.players, pid))
                self.assertNotIn("solution", text)
                for seat in self.g["players"].values():
                    b = seat["board"] or self.g["board"]
                    self.assertNotIn(b["solution"], text)

    def test_never_deals_a_board_someone_already_played(self):
        level = "advanced"
        ids = [e["id"] for e in sudoku.BANK[level]]
        sudoku._record([{"board": b, "level": level, "mode": "zen", "player": "p1", "result": "solved", "t": 1}
                        for b in ids[:-1]])
        for _ in range(5):
            entry, fresh = sudoku.pick(level, ["P1  ", "Someone"])
            self.assertEqual((entry["id"], fresh), (ids[-1], True))
        sudoku._record([{"board": ids[-1], "level": level, "mode": "zen", "player": "Someone", "result": "solved", "t": 2}])
        entry, fresh = sudoku.pick(level, ["P1", "someone"])
        self.assertFalse(fresh)
        self.assertEqual(entry["id"], ids[0])      # the one played longest ago
        info = sudoku.bank_info(["p1"])
        self.assertEqual(info[level]["fresh"], 1)


class ServerTests(TempHistory):
    def setUp(self):
        super().setUp()
        server.state.clear()
        server.state.update(copy.deepcopy(INITIAL))
        server._next_pid[0] = 1
        server._kicked.clear()
        server._join_hits.clear()
        for name in ("Host", "Guest"):
            self.assertTrue(server.do_join(name, server.ROOM_CODE, "test").get("pid"))
        self.assertTrue(server.do_select("sudoku")["ok"])

    def test_host_only_controls_and_player_moves(self):
        self.assertEqual(server.do_input({"pid": 2, "action": "sdStart"})["error"], "not_host")
        self.assertTrue(server.do_input({"pid": 1, "action": "sdSet", "key": "mode", "value": "coop"}, True)["ok"])
        self.assertTrue(server.do_input({"pid": 1, "action": "sdStart"}, True)["ok"])
        g = server.state["sudoku"]
        i = g["board"]["puzzle"].index("0")
        r = server.do_input({"pid": 2, "action": "sdPlace", "i": i, "d": int(g["board"]["solution"][i])})
        self.assertEqual(r, {"ok": True, "correct": True})
        pub = server.public_state(2)["sudoku"]
        self.assertEqual(pub["cells"][i], g["board"]["solution"][i])

    def test_switching_games_logs_the_open_round(self):
        server.do_input({"pid": 1, "action": "sdStart"}, True)
        server.do_select(None)
        self.assertEqual(len(sudoku.load_history()), 2)
        self.assertEqual(server.state["sudoku"]["phase"], "lobby")


if __name__ == "__main__":
    unittest.main()


class HintChatSpectateTests(TempHistory):
    def setUp(self):
        super().setUp()
        self.players = {i: {"name": f"P{i}", "connected": True, "color": "#fff", "score": 0} for i in (1, 2)}
        self.g = sudoku.fresh()

    def act(self, pid, action, **data):
        return sudoku.act(self.g, self.players, pid, action, data)

    def start(self, mode):
        self.act(None, "sdSet", key="mode", value=mode)
        self.assertIsNone(self.act(None, "sdStart")[0])

    def test_hint_is_correct_and_costs_time_in_a_race(self):
        self.start("competitive")
        err, extra = self.act(1, "sdHint")
        self.assertIsNone(err)
        h, board = extra["hint"], self.g["board"]
        self.assertEqual(str(h["d"]), board["solution"][h["i"]])
        self.assertEqual(board["puzzle"][h["i"]], "0")
        self.assertTrue(h["text"])
        seat = self.g["players"][1]
        seat["finishedAt"] = self.g["startedAt"] + 100
        self.assertEqual(sudoku._adjusted(self.g, seat), 100 + sudoku.HINT_PENALTY)

    def test_zen_hint_points_at_a_wrong_digit_first(self):
        self.start("zen")
        b = self.g["players"][1]["board"]
        i = b["puzzle"].index("0")
        self.act(1, "sdPlace", i=i, d=int(b["solution"][i]) % 9 + 1)
        self.assertEqual(self.act(1, "sdHint")[1]["hint"], {"kind": "wrong", "i": i, "text": unittest.mock.ANY})

    def test_spectator_sees_solution_players_never_do(self):
        self.start("competitive")
        self.players[3] = {"name": "Late", "connected": True, "color": "#000", "score": 0}
        self.assertEqual(self.act(1, "sdWatch", target=2)[0],
                         "Finish your own board before watching — spectators see the solution.")
        self.assertIsNone(self.act(3, "sdWatch", target=1)[0])
        spec = sudoku.public(self.g, self.players, 3)["spectate"]
        self.assertEqual(spec["solution"], self.g["board"]["solution"])
        self.assertEqual(spec["name"], "P1")
        for pid in (1, 2, None):
            self.assertNotIn(self.g["board"]["solution"], json.dumps(sudoku.public(self.g, self.players, pid)))

    def test_coop_spectator_cannot_then_join(self):
        self.start("coop")
        self.players[3] = {"name": "Late", "connected": True, "color": "#000", "score": 0}
        self.assertIsNone(self.act(3, "sdWatch", target=1)[0])
        self.assertIn("seen the solution", self.act(3, "sdJoin")[0])
        self.assertNotIn(3, self.g["players"])

    def test_spectator_chat_hidden_from_racers_until_round_ends(self):
        self.start("competitive")
        self.players[3] = {"name": "Late", "connected": True, "color": "#000", "score": 0}
        self.act(1, "sdChat", text="good luck all")
        self.act(3, "sdWatch", target=1)
        self.act(3, "sdChat", text="top left is a 4")
        texts = lambda pid: [m["text"] for m in sudoku.public(self.g, self.players, pid)["chat"]]
        self.assertEqual(texts(2), ["good luck all"])
        self.assertEqual(texts(3), ["good luck all", "top left is a 4"])
        self.act(None, "sdEnd")
        self.assertEqual(texts(2), ["good luck all", "top left is a 4"])
        self.act(None, "sdLobby")
        self.assertEqual(len(texts(1)), 2)          # chat survives into the next round


class TableTests(TempHistory):
    def setUp(self):
        super().setUp()
        import sudoku_tables
        self.T = sudoku_tables.Tables(["#f00", "#0f0", "#00f"])
        self.a = self.T.hello("Ana", "1.1.1.1")
        self.b = self.T.hello("Ben", "1.1.1.2")

    def call(self, who, method, *args, **kw):
        return getattr(self.T, method)(who["mid"], who["token"], *args, **kw)

    def test_solo_game_starts_immediately_and_is_unlisted(self):
        r = self.call(self.a, "create", "zen", "easy", listed=False, start=True)
        st = self.call(self.a, "state", r["tid"])
        self.assertEqual(st["sudoku"]["phase"], "playing")
        self.assertTrue(st["sudoku"]["puzzle"])
        self.assertEqual(self.call(self.b, "listing")["tables"], [])

    def test_owner_controls_late_joiner_spectates_and_tables_close(self):
        tid = self.call(self.a, "create", "competitive", "medium")["tid"]
        self.assertEqual(self.call(self.b, "listing")["tables"][0]["id"], tid)
        self.assertEqual(self.call(self.a, "act", tid, {"action": "sdStart"})["ok"], True)
        self.assertTrue(self.call(self.b, "enter", tid)["ok"])
        self.assertEqual(self.call(self.b, "act", tid, {"action": "sdEnd"})["error"], "Only the table owner can do that.")
        self.assertTrue(self.call(self.b, "act", tid, {"action": "sdWatch", "target": self.a["mid"]})["ok"])
        self.assertIn("solution", self.call(self.b, "state", tid)["sudoku"]["spectate"])
        self.call(self.a, "leave")
        self.assertTrue(self.call(self.b, "state", tid)["table"]["isOwner"])     # ownership passes on
        self.assertEqual(sudoku.load_history()[0]["result"], "unfinished")       # Ana's race got logged
        self.call(self.b, "leave")
        self.assertEqual(self.T.tables, {})

    def test_table_rules_and_owner_only_pause(self):
        tid = self.call(self.a, "create", "competitive", "easy", boards="each", lives=3, start=True)["tid"]
        self.assertTrue(self.call(self.b, "enter", tid)["ok"])
        g = self.call(self.a, "state", tid)["sudoku"]
        self.assertEqual((g["boards"], g["lives"]), ("each", 3))
        self.assertEqual(self.call(self.b, "act", tid, {"action": "sdPause"})["error"], "Only the table owner can do that.")
        self.assertTrue(self.call(self.a, "act", tid, {"action": "sdPause", "value": True})["ok"])
        self.assertTrue(self.call(self.b, "state", tid)["sudoku"]["paused"])
        self.assertTrue(self.call(self.a, "act", tid, {"action": "sdPause", "value": False})["ok"])
        self.assertFalse(self.call(self.b, "state", tid)["sudoku"]["paused"])

    def test_tokens_are_checked(self):
        self.assertEqual(self.T.listing(self.a["mid"], "nope")["error"], "who")
        tid = self.call(self.a, "create", "coop", "easy")["tid"]
        self.assertEqual(self.call(self.b, "act", tid, {"action": "sdStart"})["error"], "You're not at that table.")
        self.assertEqual(self.call(self.a, "act", tid, {"action": "kick"})["error"], "Unknown action.")
