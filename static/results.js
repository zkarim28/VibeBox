/* Public score data only; shared by every controller result screen. */
globalThis.VibeResults = (() => {
  function ranked(rows) {
    rows.sort((a, b) => b.score - a.score || a.name.localeCompare(b.name));
    let rank = 0;
    return rows.map((row, i) => {
      if (!i || row.score !== rows[i - 1].score) rank = i + 1;
      return { ...row, rank };
    });
  }
  function fromState(s, pid) {
    const me = (s.players || []).find(p => p.pid === pid);
    const player = (p, score, detail = '') => ({ name: p.name, score, detail, you: p.pid === pid });
    if (s.game === 'scattergories' && s.scat?.phase === 'done') {
      return { title: 'Round leaderboard', unit: 'Points', rows: ranked((s.scat.roundScores || []).map(p => player(p, p.points, `${p.total} total`))) };
    }
    if (s.game === 'taboo' && ['turnend', 'gameover'].includes(s.taboo?.phase)) {
      const t = s.taboo;
      return { title: t.phase === 'gameover' ? 'Final team standings' : 'Team standings', unit: 'Points',
        rows: ranked([1, 2].map(team => ({ name: t.teamNames[String(team)], score: t.scores[String(team)],
          detail: `+${t.turnPoints[String(team)] || 0} this turn`, you: me?.team === team }))) };
    }
    if (s.game === 'blackbox' && ['reveal', 'gameover'].includes(s.blackbox?.phase)) {
      return { title: s.blackbox.phase === 'gameover' ? 'Final standings' : 'Round standings', unit: 'Points',
        rows: ranked((s.blackbox.players || []).map(p => player(p, p.score, p.pid === s.blackbox.winnerPid ? 'Round winner' : ''))) };
    }
    if (s.game === 'word-hunt' && s.wordhunt?.phase === 'review') {
      return { title: 'Round leaderboard', unit: 'Points', rows: ranked((s.wordhunt.leaderboard || []).map(p =>
        player(p, p.roundScore, `${p.wordCount} ${p.wordCount === 1 ? 'word' : 'words'} · ${p.total} total`))) };
    }
    if (s.game === 'codenames' && s.codenames?.phase === 'gameover') {
      const c = s.codenames;
      return { title: 'Team results', unit: 'Agents found', rows: ['red', 'blue']
        .sort((a, b) => Number(b === c.winner) - Number(a === c.winner))
        .map((team, i) => ({ name: team === 'red' ? 'Red team' : 'Blue team', rank: i + 1,
          score: (c.target?.[team] || 0) - c.left[team], detail: team === c.winner ? 'Winner' : 'Runner-up', you: c.youTeam === team })) };
    }
    if (s.game === 'wii-sandbox' && s.wii?.selection?.item === 'targets' && s.wii.targets?.doneOrder?.length) {
      const t = s.wii.targets;
      const finishers = new Map(t.doneOrder.map((p, i) => [p.pid, i + 1]));
      const rows = t.players.map(p => ({ ...player(p, p.targets.filter(t => t.hit).length,
        finishers.has(p.pid) ? 'Finished' : 'Still playing'), rank: finishers.get(p.pid) || null }));
      rows.sort((a, b) => (a.rank || Infinity) - (b.rank || Infinity) || b.score - a.score || a.name.localeCompare(b.name));
      return { title: rows.every(p => p.rank) ? 'Target Practice results' : 'Target Practice standings', unit: 'Targets hit', rows };
    }
    return null;
  }
  return { fromState };
})();
