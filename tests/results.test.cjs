// Run with: node --test tests/results.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
require('../static/results.js');
const results = globalThis.VibeResults.fromState;
const players = [{ pid: 1, name: 'Alex', team: 1 }, { pid: 2, name: 'Blair', team: 2 }, { pid: 3, name: 'Casey', team: 1 }];

test('Scattergories ranks the completed round and separately labels cumulative totals', () => {
  const r = results({ game: 'scattergories', scat: { phase: 'done', roundScores: [
    { ...players[0], points: 2, total: 30 }, { ...players[1], points: 5, total: 10 },
  ] } }, 1);
  assert.equal(r.rows[0].name, 'Blair');
  assert.equal(r.rows[0].detail, '10 total');
  assert.equal(r.rows[1].score, 2);
  assert.equal(results({ game: 'scattergories', scat: { phase: 'review' } }, 1), null);
});

test('Taboo shows team scores at both turn and game end, including tied teams', () => {
  for (const phase of ['turnend', 'gameover']) {
    const r = results({ game: 'taboo', players, taboo: { phase, teamNames: { 1: 'Orchids', 2: 'Mint' }, scores: { 1: 5, 2: 5 }, turnPoints: { 1: 2, 2: 0 } } }, 1);
    assert.deepEqual(r.rows.map(p => p.rank), [1, 1]);
    assert.equal(r.rows.find(p => p.you).name, 'Orchids');
    assert.equal(r.rows.find(p => p.you).detail, '+2 this turn');
  }
});

test('BlackBox ranks game points independently of the latest round winner', () => {
  for (const phase of ['reveal', 'gameover']) {
    const r = results({ game: 'blackbox', blackbox: { phase, winnerPid: 1, players: players.map((p, i) => ({ ...p, score: [1, 3, 0][i] })) } }, 1);
    assert.equal(r.rows[0].name, 'Blair');
    assert.equal(r.rows[1].detail, 'Round winner');
  }
});

test('Word Hunt ranks round points, includes zero scorers and distinguishes total points', () => {
  const s = { game: 'word-hunt', wordhunt: { phase: 'review', leaderboard: players.map((p, i) => ({ ...p, roundScore: [400, 800, 0][i], total: [2000, 800, 0][i], wordCount: [1, 2, 0][i] })) } };
  const r = results(s, 3);
  assert.deepEqual(r.rows.map(p => p.score), [800, 400, 0]);
  assert.equal(r.rows[1].detail, '1 word · 2000 total');
  assert.equal(r.rows[2].you, true);
  assert.equal(results({ ...s, wordhunt: { ...s.wordhunt, phase: 'playing' } }, 3), null);
});

test('Codenames respects the winner even when the losing team found more agents', () => {
  const r = results({ game: 'codenames', codenames: { phase: 'gameover', winner: 'blue', youTeam: 'blue', target: { red: 9, blue: 8 }, left: { red: 1, blue: 5 } } }, 1);
  assert.equal(r.rows[0].name, 'Blue team');
  assert.equal(r.rows[0].score, 3);
  assert.equal(r.rows[0].you, true);
});

test('Target Practice respects finish order and keeps unfinished players unranked', () => {
  const targetPlayers = players.map((p, i) => ({ ...p, targets: Array.from({ length: 3 }, (_, j) => ({ hit: i !== 2 || j === 0 })) }));
  const s = { game: 'wii-sandbox', wii: { selection: { item: 'targets' }, targets: { players: targetPlayers, doneOrder: [players[1], players[0]] } } };
  const r = results(s, 3);
  assert.deepEqual(r.rows.map(p => [p.name, p.rank, p.score]), [['Blair', 1, 3], ['Alex', 2, 3], ['Casey', null, 1]]);
  assert.equal(r.rows[2].detail, 'Still playing');
  assert.equal(results({ ...s, wii: { ...s.wii, selection: null } }, 3), null);
});

test('non-scoring games and the menu do not display stale results', () => {
  for (const game of [null, 'mafia', 'imposter', 'ludo']) assert.equal(results({ game, players }, 1), null);
});
