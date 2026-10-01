"""Private-hand BS engine. Call under the server state lock."""
import random
import time

RANKS = ['Ace', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'Jack', 'Queen', 'King']
SUITS = ['♠', '♥', '♦', '♣']


def fresh(decks=1):
    return dict(phase='lobby', decks=decks, order=[], hands={}, pile=[], turn=0,
                rank=0, last=None, reveal=None, deadline=0, winner=None, message='', revision=random.randrange(1, 2**48))


def card(value):
    return {'id': value, 'rank': RANKS[value % 13], 'suit': SUITS[(value % 52) // 13]}


def advance(g):
    g['turn'] = (g['turn'] + 1) % len(g['order'])
    g['rank'] = (g['rank'] + 1) % 13
    g.update(phase='playing', deadline=0, last=None)
    g['revision'] += 1


def expire(g, now=None):
    now = time.time() if now is None else now
    if g['phase'] not in ('challenge', 'reveal') or now < g['deadline']:
        return False
    actor = g['last']['pid']
    if not g['hands'][actor]:
        g.update(phase='gameover', winner=actor, deadline=0)
    else:
        advance(g)
    return True


def act(g, players, pid, action, data):
    expire(g)
    if action == 'bsSet':
        if g['phase'] != 'lobby' or data.get('value') not in (1, 2):
            return 'Choose one or two decks in the lobby.'
        g['decks'] = int(data['value'])
    elif action == 'bsLobby':
        decks = g['decks']; g.clear(); g.update(fresh(decks))
    elif action == 'bsStart':
        if g['phase'] not in ('lobby', 'gameover'):
            return 'Finish this game first.'
        order = sorted(p for p, v in players.items() if v.get('connected', True))
        if not 2 <= len(order) <= 10:
            return 'BS needs 2–10 connected players.'
        decks = g['decks']; g.clear(); g.update(fresh(decks))
        deck = list(range(52 * decks)); random.shuffle(deck)
        g['order'] = order
        g['hands'] = {p: sorted(deck[i::len(order)]) for i, p in enumerate(order)}
        g['turn'] = next(i for i, p in enumerate(order) if 0 in g['hands'][p])
        g['phase'] = 'playing'
    elif action == 'bsPlay':
        if g['phase'] != 'playing' or not g['order'] or pid != g['order'][g['turn']]:
            return 'Wait for your turn.'
        if data.get('revision') != g['revision']:
            return 'The turn changed. Select your cards again.'
        cards = data.get('cards')
        if not isinstance(cards, list) or not 1 <= len(cards) <= 4 * g['decks'] or any(type(c) is not int for c in cards) or len(set(cards)) != len(cards) or any(c not in g['hands'][pid] for c in cards):
            return 'Select valid cards from your hand.'
        for c in cards:
            g['hands'][pid].remove(c)
        g['pile'].extend(cards)
        g.update(last={'pid': pid, 'cards': cards[:], 'rank': g['rank']}, reveal=None,
                 phase='challenge', deadline=time.time() + 8)
    elif action == 'bsCall':
        if g['phase'] != 'challenge' or pid not in g['hands'] or pid == g['last']['pid']:
            return 'You cannot challenge that play.'
        if data.get('revision') != g['revision']:
            return 'That challenge window has closed.'
        last = g['last']; liar = any(c % 13 != last['rank'] for c in last['cards'])
        loser = last['pid'] if liar else pid
        g['reveal'] = dict(caller=pid, liar=liar, loser=loser, count=len(g['pile']), cards=[card(c) for c in last['cards']])
        g['hands'][loser].extend(g['pile']); g['hands'][loser].sort()
        g.update(pile=[], phase='reveal', deadline=time.time() + 5)
    else:
        return 'Unknown BS action.'
    return None


def public(g, players, pid=None):
    out = {k: g[k] for k in ('phase', 'decks', 'deadline', 'winner', 'message', 'revision', 'reveal')}
    out.update(rank=RANKS[g['rank']], pileCount=len(g['pile']),
               turnPid=g['order'][g['turn']] if g['order'] else None,
               roster=[dict(pid=p, name=players.get(p, {}).get('name', 'Player'), count=len(g['hands'][p]), connected=players.get(p, {}).get('connected', False)) for p in g['order']],
               hand=[card(c) for c in sorted(g['hands'].get(pid, []), key=lambda c: (c % 13, c))],
               inGame=pid in g['hands'], last=None)
    if g['last']:
        last = g['last']
        out['last'] = dict(pid=last['pid'], count=len(last['cards']), rank=RANKS[last['rank']])
    return out
