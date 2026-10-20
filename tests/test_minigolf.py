"""Mini golf engine. Run: python3 -m unittest discover -s tests."""
import copy
import math
from pathlib import Path
import random
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import minigolf as mg
import server

INITIAL = copy.deepcopy(server.state)


def straight_hole(obs=()):
    cells = mg._carve([(3, 10), (3, 0)], 2)
    return {"n": 1, "shape": "straight", "par": 2, "cells": sorted(cells), "walls": mg._walls(cells),
            "tee": [40.0, 110.0], "cup": [40.0, 10.0], "obs": list(obs)}


def aim(hole, ball):
    return math.atan2(hole["cup"][1] - ball[1], hole["cup"][0] - ball[0])


class CourseTests(unittest.TestCase):
    def test_every_generated_hole_is_playable_and_closed(self):
        for seed in range(6):
            cfg = dict(mg.DEFAULTS, holes=18, amount="lots", obsMode="surprise")
            name, holes = mg.make_course(cfg, seed=seed)
            self.assertEqual(len(holes), 18)
            self.assertTrue(name)
            for h in holes:
                idx = mg._index([tuple(w) for w in h["walls"]])
                cells = {tuple(c) for c in h["cells"]}
                self.assertIsNotNone(mg._path_len(cells, idx, h["obs"], h["tee"], h["cup"]), (seed, h["n"]))
                self.assertTrue(2 <= h["par"] <= 5)

    def test_only_chosen_obstacles_appear(self):
        cfg = dict(mg.DEFAULTS, holes=18, obstacles=["water", "windmill"], amount="lots")
        _, holes = mg.make_course(cfg, seed=3)
        kinds = {o["k"] for h in holes for o in h["obs"]}
        self.assertTrue(kinds <= {"water", "windmill"})
        self.assertTrue(kinds)
        _, holes = mg.make_course(dict(cfg, obstacles=[]), seed=3)
        self.assertFalse(any(h["obs"] for h in holes))

    def test_ball_never_leaves_the_course(self):
        rng = random.Random(9)
        _, holes = mg.make_course(dict(mg.DEFAULTS, holes=18, amount="lots", obsMode="surprise"), seed=11)
        for h in holes:
            cells = {tuple(c) for c in h["cells"]}
            ball = h["tee"]
            for _ in range(12):
                r = mg.simulate(h, ball, rng.uniform(-math.pi, math.pi), rng.uniform(.05, 1), rng.uniform(0, 50))
                x, y = r["end"]
                self.assertIn((int(x // 10), int(y // 10)), cells)
                ball = r["end"]
                if r["sunk"]:
                    break


class PhysicsTests(unittest.TestCase):
    def test_a_good_putt_sinks_and_a_rocket_lips_out(self):
        h = straight_hole()
        sunk = [mg.simulate(h, h["tee"], aim(h, h["tee"]), p, 0)["sunk"] for p in (.62, .66, .7, .74)]
        self.assertTrue(any(sunk))
        short = mg.simulate(h, [40, 20], aim(h, [40, 20]), 1.0, 0)
        self.assertFalse(short["sunk"])

    def test_water_costs_a_stroke_and_puts_ball_back(self):
        water = {"k": "water", "x": 31.2, "y": 51.2, "w": 17.6, "h": 7.6}
        h = straight_hole([water])
        r = mg.simulate(h, h["tee"], aim(h, h["tee"]), .6, 0)
        self.assertEqual(r["penalty"], 1)
        self.assertFalse(mg._in_rect(water, *r["end"]))
        self.assertGreater(r["end"][1], 58)          # back on the tee side of the pond
        self.assertTrue(any(e["k"] == "water" for e in r["events"]))

    def test_ball_on_a_rail_is_moved_a_clubhead(self):
        h = straight_hole()
        (x, y), moved = mg._settle(h, 30.9, 60, 0)
        self.assertTrue(moved)
        self.assertGreater(x - 30, 3.5)

    def test_portal_teleports(self):
        portal = {"k": "portal", "ax": 40, "ay": 90, "bx": 40, "by": 25, "r": 2.4}
        h = straight_hole([portal])
        r = mg.simulate(h, h["tee"], aim(h, h["tee"]), .25, 0)
        self.assertTrue(r["cuts"])
        self.assertLess(r["end"][1], 30)


class GameTests(unittest.TestCase):
    def setUp(self):
        self.players = {i: {"name": f"P{i}", "connected": True, "color": "#fff", "score": 0} for i in (1, 2, 3)}
        self.g = mg.fresh()

    def act(self, pid, action, **data):
        return mg.act(self.g, self.players, pid, action, data)

    def use_straight_holes(self):
        self.g["holes"] = [straight_hole() for _ in self.g["holes"]]
        for n, h in enumerate(self.g["holes"]):
            h["n"] = n + 1
        mg._begin_hole(self.g, 0)

    def wait(self):
        for s in self.g["sides"].values():
            s["until"] = 0
        mg.tick(self.g, self.players)

    def up(self):
        return mg._turn(self.g, self.players, self.g["groups"][0], time.time())

    def test_everyone_tees_off_before_second_strokes(self):
        self.act(None, "mgSet", key="holes", value=9)
        self.assertIsNone(self.act(None, "mgStart")[0])
        self.use_straight_holes()
        order = []
        for _ in range(3):
            sid = self.up()
            order.append(sid)
            pid = self.g["sides"][sid]["members"][0]
            other = next(p for p in (1, 2, 3) if f"p{p}" != sid)
            self.assertIn("Not your turn", self.act(other, "mgShot", angle=0, power=.1)[0])
            err, extra = self.act(pid, "mgShot", angle=-math.pi / 2, power=.15)
            self.assertIsNone(err)
            self.assertIn("frames", extra["shot"])
            self.assertIn("Wait", self.act(pid, "mgShot", angle=0, power=.1)[0])
            self.wait()
        self.assertEqual(sorted(order), ["p1", "p2", "p3"])
        self.assertEqual(order, self.g["teeOrder"])
        # now the farthest ball goes first
        cup = self.g["holes"][0]["cup"]
        far = max(order, key=lambda s: mg._dist(*self.g["sides"][s]["ball"], *cup))
        self.assertEqual(self.up(), far)

    def test_stroke_limit_picks_up_and_hole_advances(self):
        self.act(None, "mgSet", key="limit", value=6)
        self.act(None, "mgStart")
        self.use_straight_holes()
        for _ in range(40):
            sid = self.up()
            if sid in (None, "busy"):
                self.wait()
                continue
            pid = self.g["sides"][sid]["members"][0]
            self.act(pid, "mgShot", angle=math.pi / 2, power=.05)     # tiny taps backwards
            self.wait()
            if self.g["phase"] == "hole":
                break
        self.assertEqual(self.g["phase"], "hole")
        self.assertTrue(all(s["scores"] == [6] for s in self.g["sides"].values()))
        self.act(None, "mgNext")
        self.assertEqual((self.g["phase"], self.g["hole"]), ("playing", 1))

    def test_alternate_shot_teams_take_turns_hitting(self):
        self.players[4] = {"name": "P4", "connected": True, "color": "#000", "score": 0}
        self.act(None, "mgSet", key="format", value="alternate")
        self.act(None, "mgSet", key="pace", value="together")
        for pid, t in ((1, 0), (2, 0), (3, 1), (4, 1)):
            self.act(pid, "mgTeam", team=t)
        self.assertIsNone(self.act(None, "mgStart")[0])
        self.assertEqual(set(self.g["sides"]), {"t0", "t1"})
        side = self.g["sides"]["t0"]
        first = mg._hitter(self.g, self.players, side)
        second = ({1, 2} - {first}).pop()
        self.assertIn("shot for your team", self.act(second, "mgShot", angle=0, power=.1)[0])
        self.assertIsNone(self.act(first, "mgShot", angle=-1.5, power=.1)[0])
        self.wait()
        self.assertEqual(mg._hitter(self.g, self.players, side), second)

    def test_best_ball_team_scores_best_member(self):
        self.act(None, "mgSet", key="format", value="best")
        self.act(None, "mgSet", key="holes", value=9)
        self.act(1, "mgTeam", team=0); self.act(2, "mgTeam", team=0); self.act(3, "mgTeam", team=1)
        self.act(None, "mgStart")
        self.g["sides"]["p1"]["scores"] = [2]
        self.g["sides"]["p2"]["scores"] = [5]
        self.g["sides"]["p3"]["scores"] = [3]
        rows = mg.standings(self.g)
        self.assertEqual([(r["name"], r["total"]) for r in rows], [("Red Parrots", 2), ("Blue Sharks", 3)])

    def test_finish_ranks_lowest_and_awards_points(self):
        self.act(None, "mgStart")
        for sid, sc in (("p1", 30), ("p2", 25), ("p3", 40)):
            self.g["sides"][sid]["scores"] = [sc // 9] * 9
        self.g["phase"], self.g["hole"] = "hole", 8
        self.g["nextAt"] = 1
        mg.tick(self.g, self.players)
        self.assertEqual(self.g["phase"], "done")
        self.assertEqual(self.g["standings"][0]["name"], "P2")
        self.assertEqual(self.players[2]["score"], 300)

    def test_settings_obstacle_toggles_and_random(self):
        self.act(None, "mgSet", key="obs:kraken", value=False)
        self.assertNotIn("kraken", self.g["cfg"]["obstacles"])
        self.act(None, "mgSet", key="obs:kraken", value=True)
        self.assertIn("kraken", self.g["cfg"]["obstacles"])
        self.assertEqual(self.act(None, "mgSet", key="holes", value=7)[0], "Unknown setting.")
        self.act(None, "mgRandomObs")
        self.assertTrue(3 <= len(self.g["cfg"]["obstacles"]) <= 6)

    def test_late_joiner_plays_from_the_current_hole(self):
        self.act(None, "mgStart")
        self.g["hole"] = 2
        self.players[9] = {"name": "Late", "connected": True, "color": "#000", "score": 0}
        pub = mg.public(self.g, self.players, 9)
        self.assertIn("watching", pub["me"]["status"])
        self.assertIsNone(self.act(9, "mgJoin")[0])
        self.assertEqual(self.g["sides"]["p9"]["scores"], [6, 6])

    def test_public_hides_nothing_secret_and_keeps_payload_small(self):
        import json
        self.act(None, "mgStart")
        pub = mg.public(self.g, self.players, 1)
        self.assertLess(len(json.dumps(pub)), 12000)
        self.assertIn("hole", pub)


class ServerTests(unittest.TestCase):
    def setUp(self):
        server.state.clear()
        server.state.update(copy.deepcopy(INITIAL))

    def test_host_only_and_dispatch(self):
        self.assertEqual(server.do_select("minigolf")["ok"], True)
        server.state["players"][1] = {"name": "A", "color": "#fff", "score": 0, "token": "t", "connected": True}
        self.assertEqual(server.do_input({"pid": 1, "token": "t", "action": "mgStart"})["error"], "not_host")
        self.assertTrue(server.do_input({"action": "mgStart"}, is_host=True)["ok"])
        self.assertEqual(server.public_state(1)["minigolf"]["phase"], "playing")


if __name__ == "__main__":
    unittest.main()
