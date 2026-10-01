"""Phone-only room regressions. Run: python3 -m unittest discover -s tests."""
import copy
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

INITIAL = copy.deepcopy(server.state)


class PhoneRoomTests(unittest.TestCase):
    def setUp(self):
        server.state.clear()
        server.state.update(copy.deepcopy(INITIAL))
        server._next_pid[0] = 1
        server._kicked.clear()
        server._join_hits.clear()
        self.players = [server.do_join(name, server.ROOM_CODE, 'test') for name in ['Host', 'Blue', 'Red', 'Guest', 'Moderator']]
        self.assertTrue(all(p.get('pid') for p in self.players))
        self.host('hostMode', mode='phone')

    def host(self, action, **data):
        return server.do_input({'pid': 1, 'action': action, **data}, True)

    def player(self, player_id, action, **data):
        return server.do_input({'pid': player_id, 'action': action, **data})

    def test_removed_game_and_actions(self):
        self.assertNotIn('tap-race', server.PLAYABLE)
        self.assertFalse(server.do_select('tap-race')['ok'])
        for action in ['tap', 'start', 'reset']:
            self.assertEqual(self.host(action)['error'], 'unknown_action')
        self.assertNotIn('taps', server.public_state(1)['players'][0])
        self.assertNotIn('goal', server.public_state(1))

    def test_host_cookie_required_for_mode_and_settings(self):
        for action, extra in [('hostMode', {'mode': 'screen'}), ('whStart', {}), ('kick', {'pid': 2}), ('scatSet', {'key': 'time', 'value': 30})]:
            self.assertEqual(self.player(2, action, **extra)['error'], 'not_host')
        self.assertTrue(server.state['phoneMode'])

    def test_mode_switch_preserves_active_game(self):
        server.do_select('word-hunt')
        self.host('whStart')
        grid = server.state['wordhunt']['grid'][:]
        self.assertEqual(self.host('hostMode', mode='screen')['error'], 'finish_round')
        self.assertTrue(self.host('hostMode', mode='phone')['ok'])
        self.assertEqual(server.state['wordhunt']['grid'], grid)
        self.host('whEndRound')
        self.assertTrue(self.host('hostMode', mode='screen')['ok'])
        self.assertFalse(server.state['phoneMode'])

    def test_scattergories_can_complete_review_without_display(self):
        server.do_select('scattergories')
        self.host('scatSet', key='time', value=45)
        self.host('scatStart')
        self.assertEqual(server.public_state(1)['scat']['time'], 45)
        self.player(1, 'scatAnswers', answers={'0': 'Answer'})
        self.host('scatEndRound')
        for _ in range(len(server.state['scat']['categories'])):
            self.host('scatNext')
        self.assertEqual(server.public_state(1)['scat']['phase'], 'done')
        self.assertEqual(len(server.public_state(1)['scat']['roundScores']), 5)

    def test_taboo_giver_rotates_and_guessers_cannot_receive_or_score_cards(self):
        server.do_select('taboo')
        self.assertTrue(self.host('tabooStart')['ok'])
        self.host('tabooBeginTurn')
        tb = server.state['taboo']
        first = tb['clueGiver']
        guesser = next(p for p, row in server.state['players'].items() if row['team'] == tb['activeTeam'] and p != first)
        watcher = next(p for p, row in server.state['players'].items() if row['team'] != tb['activeTeam'])
        self.assertIsNotNone(server.public_state(first)['taboo']['card'])
        self.assertIsNone(server.public_state(guesser)['taboo']['card'])
        self.assertIsNone(server.public_state()['taboo']['card'])
        self.assertIsNotNone(server.public_state(watcher)['taboo']['card'])
        seq = tb['card']['seq']
        self.assertFalse(self.player(guesser, 'tabooGot', seq=seq)['ok'])
        self.assertTrue(self.player(first, 'tabooGot', seq=seq)['ok'])
        self.assertEqual(tb['scores'][str(tb['activeTeam'])], 1)
        for _ in range(2):
            self.host('tabooEndTurn'); self.host('tabooNextTurn'); self.host('tabooBeginTurn')
        self.assertNotEqual(tb['clueGiver'], first)

    def test_taboo_requires_a_guesser_per_team(self):
        server.do_select('taboo')
        for p in server.state['players'].values(): p['team'] = 1
        server.state['players'][2]['team'] = 2
        self.assertEqual(self.host('tabooStart')['error'], 'taboo_teams')

    def test_codenames_uses_private_phone_boards(self):
        server.do_select('codenames')
        self.assertEqual(self.host('cnMode', mode='party')['error'], 'phone_mode')
        self.player(1, 'cnSpymaster'); self.player(2, 'cnSpymaster')
        self.assertTrue(self.host('cnStart')['ok'])
        self.assertIn('key', server.public_state(1)['codenames'])
        self.assertNotIn('key', server.public_state(3)['codenames'])
        self.assertNotIn('key', server.public_state()['codenames'])

    def test_blackbox_host_can_play_with_a_private_hand(self):
        server.do_select('blackbox'); self.host('blackboxStart')
        bb = server.state['blackbox']
        self.assertIn('hand', server.public_state(1)['blackbox'])
        self.assertNotIn('hand', server.public_state()['blackbox'])
        for pid in list(server.state['players']):
            if pid != bb['czar']: self.player(pid, 'blackboxPlay', cards=list(range(bb['black']['pick'])))
        self.assertEqual(bb['phase'], 'judge')
        for _ in bb['order']: self.host('blackboxFlip')
        self.host('blackboxPick', choice=0)
        self.assertEqual(bb['phase'], 'reveal')
        self.host('blackboxNext')
        self.assertEqual(bb['phase'], 'select')

    def test_imposter_host_does_not_need_to_know_word_to_judge(self):
        server.do_select('imposter'); self.host('impStart')
        im = server.state['imposter']
        imp = next(pid for pid, role in im['roles'].items() if role == 'imposter')
        while im['phase'] == 'clue': self.host('impSkip')
        self.assertIsNone(server.public_state(imp)['imposter']['yourWord'])
        self.player(imp, 'impGuess')
        self.player(imp, 'impGuessSubmit', text='  ' + im['word'].upper() + '  ')
        self.assertEqual(im['phase'], 'gameover')
        self.assertEqual(im['winner'], 'imposters')

    def test_wrong_imposter_guess_eliminates_guesser(self):
        server.do_select('imposter'); self.host('impStart')
        im = server.state['imposter']; imp = next(p for p,r in im['roles'].items() if r == 'imposter')
        while im['phase'] == 'clue': self.host('impSkip')
        self.player(imp, 'impGuess'); self.player(imp, 'impGuessSubmit', text='definitely not the secret word')
        self.assertEqual(im['out'][imp], 'guessed_wrong')
        self.assertEqual(im['winner'], 'crew')

    def test_mafia_host_can_play_without_moderator_secrets(self):
        server.do_select('mafia'); self.host('mafiaSetModerator', pid=5); self.host('mafiaStart')
        own = server.public_state(1)['mafia']; mod = server.public_state(5)['mafia']
        self.assertTrue(own['youInGame']); self.assertFalse(own['youModerator'])
        self.assertTrue(mod['youModerator']); self.assertFalse(mod['youInGame'])
        self.assertTrue(all('role' not in row for row in own['roster']))
        self.assertTrue(all('role' in row for row in mod['roster'] if row['inGame']))
        self.host('mafiaBeginNight'); self.player(5, 'mafiaNight', target=0)
        self.assertEqual(server.state['mafia']['phase'], 'day')

    def test_ludo_full_board_and_moves_exist_on_player_state(self):
        server.do_select('ludo'); self.host('ludoStart')
        lu = server.public_state(1)['ludo']
        self.assertTrue(lu['youSeated']); self.assertEqual(len(lu['seats']), 4)
        self.player(1, 'ludoRoll')
        self.assertIn(server.state['ludo']['phase'], ('rolling', 'moving'))

    def test_wordhunt_expires_without_host_browser_and_rejects_late_words(self):
        server.do_select('word-hunt'); self.host('whStart')
        wh = server.state['wordhunt']; wh['grid'] = list('CATXXXXXXXXXXXXX'); wh['endsAt'] = time.time() - 1
        self.player(1, 'whFound', path=[0, 1, 2])
        self.assertEqual(wh['phase'], 'review'); self.assertEqual(wh['roundScores'], {})
        self.host('whStart'); wh['endsAt'] = time.time() - 1
        self.assertTrue(server._wh_expire())

    def test_touch_targets_need_no_motion_and_only_score_own_targets(self):
        server.do_select('wii-sandbox'); self.host('wiiSelect', item='targets')
        tg = server.state['wii']['targets']
        self.assertEqual(len(tg['pids']), 5)
        self.player(1, 'wiiTouch', id=tg['byPid'][2][0]['id'])
        self.assertFalse(tg['byPid'][2][0]['hit'])
        for target in tg['byPid'][1]: self.player(1, 'wiiTouch', id=target['id'])
        self.assertEqual(tg['doneOrder'], [1])
        self.assertEqual(self.host('wiiSelect', item='paint')['error'], 'phone_mode')

    def test_http_player_identity_cannot_be_swapped(self):
        httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
        base = f'http://127.0.0.1:{httpd.server_port}'
        try:
            def read(pid, token):
                with urlopen(Request(base + f'/state?pid={pid}', headers={'X-Player-Token': token})) as r: return json.load(r)
            self.assertNotIn('locked', read(1, self.players[0]['token']))
            self.assertTrue(read(2, self.players[0]['token'])['locked'])
            payload = json.dumps({'pid': 2, 'token': self.players[0]['token'], 'action': 'mafiaClaimModerator'}).encode()
            with self.assertRaises(HTTPError) as err: urlopen(Request(base + '/input', data=payload, headers={'Content-Type':'application/json'}))
            self.assertEqual(err.exception.code, 403)
        finally:
            httpd.shutdown(); httpd.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
