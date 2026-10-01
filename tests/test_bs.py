import copy
import unittest
import bs
import server


class BSTests(unittest.TestCase):
    def setUp(self):
        self.players = {i: {'name': f'P{i}', 'connected': True, 'color': '#fff', 'score': 0} for i in range(1, 5)}
        self.g = bs.fresh()
        self.assertIsNone(bs.act(self.g, self.players, None, 'bsStart', {}))

    def play(self, cards):
        return bs.act(self.g, self.players, self.g['order'][self.g['turn']], 'bsPlay', {'cards': cards, 'revision': self.g['revision']})

    def rig(self, hand):
        self.g.update(turn=0, rank=0, revision=0, phase='playing', hands={1: hand[:], 2: [8], 3: [9], 4: [10]}, pile=[20, 21])

    def test_deal_and_starter_for_both_decks(self):
        for decks in (1, 2):
            for count in (2, 6, 10):
                g = bs.fresh(decks)
                players = {i: {'name': str(i)} for i in range(count)}
                self.assertIsNone(bs.act(g, players, None, 'bsStart', {}))
                cards = [c for hand in g['hands'].values() for c in hand]
                self.assertEqual(sorted(cards), list(range(52 * decks)))
                sizes = [len(h) for h in g['hands'].values()]
                self.assertLessEqual(max(sizes) - min(sizes), 1)
                self.assertIn(0, g['hands'][g['order'][g['turn']]])

    def test_private_hand_and_facedown_pile(self):
        self.rig([0, 1]); self.play([1])
        for pid in (None, 1, 2, 99):
            out = bs.public(self.g, self.players, pid)
            self.assertEqual([c['id'] for c in out['hand']], self.g['hands'].get(pid, []))
            self.assertNotIn('cards', out['last'])
            self.assertNotIn('hands', out)
            self.assertNotIn('pile', out)

    def test_caught_bluff_takes_entire_pile_and_no_win(self):
        self.rig([1]); self.play([1])
        self.assertIsNone(bs.act(self.g, self.players, 2, 'bsCall', {'revision':0}))
        self.assertEqual(sorted(self.g['hands'][1]), [1, 20, 21])
        self.assertTrue(self.g['reveal']['liar'])
        bs.expire(self.g, self.g['deadline'])
        self.assertEqual(self.g['phase'], 'playing')
        self.assertEqual(self.g['turn'], 1)
        self.assertEqual(self.g['rank'], 1)

    def test_truthful_last_card_wins_after_reveal(self):
        self.rig([0]); self.play([0])
        self.assertNotEqual(self.g['phase'], 'gameover')
        bs.act(self.g, self.players, 2, 'bsCall', {'revision':0})
        self.assertFalse(self.g['reveal']['liar'])
        self.assertEqual(sorted(self.g['hands'][2]), [0, 8, 20, 21])
        bs.expire(self.g, self.g['deadline'])
        self.assertEqual(self.g['winner'], 1)

    def test_unchallenged_last_bluff_wins_only_after_deadline(self):
        self.rig([1]); self.play([1])
        self.assertFalse(bs.expire(self.g, self.g['deadline'] - .01))
        self.assertTrue(bs.expire(self.g, self.g['deadline']))
        self.assertEqual(self.g['winner'], 1)

    def test_validation_and_duplicate_requests(self):
        self.rig([0, 1])
        for cards in ([], [0, 0], [52], ['0'], [True], None):
            self.assertIsNotNone(self.play(cards))
        self.assertIsNotNone(bs.act(self.g, self.players, 2, 'bsPlay', {'cards':[8], 'revision':0}))
        self.assertIsNone(self.play([0]))
        self.assertIsNotNone(self.play([1]))
        self.assertIsNotNone(bs.act(self.g, self.players, 1, 'bsCall', {'revision':0}))
        self.assertIsNotNone(bs.act(self.g, self.players, 99, 'bsCall', {'revision':0}))
        self.assertIsNotNone(bs.act(self.g, self.players, 2, 'bsCall', {'revision':-1}))
        self.assertIsNone(bs.act(self.g, self.players, 2, 'bsCall', {'revision':0}))
        self.assertIsNotNone(bs.act(self.g, self.players, 3, 'bsCall', {'revision':0}))

    def test_rank_wrap_and_late_challenge(self):
        self.rig([0, 1]); self.g['rank'] = 12; self.play([0])
        self.g['deadline'] = 1
        self.assertIsNotNone(bs.act(self.g, self.players, 2, 'bsCall', {'revision':0}))
        self.assertEqual(self.g['rank'], 0)
        self.assertEqual(self.g['revision'], 1)

    def test_host_authorization_and_game_routing(self):
        original = copy.deepcopy(server.state)
        self.addCleanup(lambda: (server.state.clear(), server.state.update(original)))
        server.do_select('bs')
        for action in ('bsStart', 'bsSet', 'bsLobby'):
            self.assertEqual(server.do_input({'action':action})['error'], 'not_host')
        server.state['players'] = self.players
        self.assertTrue(server.do_input({'action':'bsStart'}, True)['ok'])
        self.assertEqual(server.public_state()['bs']['hand'], [])
        server.do_select('ludo')
        self.assertFalse(server.do_input({'action':'bsPlay', 'pid':1, 'cards':[0]})['ok'])


if __name__ == '__main__':
    unittest.main()
