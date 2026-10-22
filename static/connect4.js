/* Connect 4: the shared screen (isHost) and every phone. Server: connect4.py */
globalThis.C4View = (() => {
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const FORMAT = {duels: '⚔️ Duels: everyone paired up at once', teams: '🔴🟡 Teams: Red vs Yellow', bot: '🤖 Everyone vs the computer'};
  const BOT = {easy: 'Easy', medium: 'Medium', hard: 'Hard'};
  const DOT = {red: '🔴', yellow: '🟡'};
  let busy = false, error = '', keysBound = false, current = null;

  // Only touch the DOM when our own markup changed (innerHTML re-serialises,
  // so never compare against it). Skip while a select inside has focus.
  function setHTML(node, html) {
    if (!node || node._c4HTML === html) return;
    const ae = document.activeElement;
    if (ae && node.contains(ae) && ae.matches('select')) return;
    node.innerHTML = html; node._c4HTML = html;
  }

  const sideLabel = sd => `${DOT[sd.color]} ${esc(sd.name)}`;

  function status(m, g, pid) {
    const cur = m.sides[m.turn];
    if (m.result === 'win') {
      const w = m.sides[m.winner];
      if (m.yourSide != null) return m.winner === m.yourSide ? '🏆 You win!' : `${sideLabel(w)} wins`;
      return `🏆 ${sideLabel(w)} wins!`;
    }
    if (m.result === 'draw') return '🤝 Draw. The board is full.';
    if (m.result === 'ended') return 'Game ended';
    if (m.yourTurn) return `Your turn! Drop a ${DOT[cur.color]}`;
    if (m.thinking) return `🤖 The computer is thinking…`;
    if (cur.kind === 'team') {
      const who = g._names[cur.dropper] || 'someone';
      return `${sideLabel(cur)}: <b>${esc(who)}</b> drops${m.yourSide === m.turn ? ' (your team)' : ''}`;
    }
    return `${sideLabel(cur)} to move`;
  }

  function clock(m) {
    if (!m.endsAt || m.result) return '';
    const left = Math.max(0, Math.ceil(m.endsAt - (Date.now() / 1000 - current.g.skew)));
    return `<span class="c4-clock${left <= 5 ? ' c4-hurry' : ''}">⏱ ${left}s</span>`;
  }

  function board(m) {
    const win = new Set((m.line || []).map(([c, r]) => c + ':' + r));
    const [lc, lr] = m.last || [-1, -1];
    const color = m.sides[m.turn].color;
    let cols = '';
    for (let c = 0; c < m.cols; c++) {
      const full = m.board[c][m.rows - 1] !== 0;
      let cells = '';
      for (let r = m.rows - 1; r >= 0; r--) {
        const v = m.board[c][r];
        const cls = ['c4-cell'];
        if (v) cls.push(v === 1 ? 'c4-red' : 'c4-yellow');
        if (win.has(c + ':' + r)) cls.push('c4-win');
        if (c === lc && r === lr) cls.push('c4-drop');
        cells += `<i class="${cls.join(' ')}"${c === lc && r === lr ? ` style="--fall:${m.rows - r}"` : ''}></i>`;
      }
      cols += m.yourTurn
        ? `<button class="c4-col" data-col="${c}" ${full ? 'disabled' : ''} aria-label="Drop in column ${c + 1}"><span class="c4-ghost c4-${color}"></span>${cells}</button>`
        : `<div class="c4-col">${cells}</div>`;
    }
    return `<div class="c4-board${m.yourTurn ? ' c4-live' : ''}${m.result ? ' c4-over' : ''}" style="--cols:${m.cols};--rows:${m.rows}">${cols}</div>`;
  }

  function versus(m) {
    return `<div class="c4-vs">${m.sides.map((sd, i) => `<span class="c4-pill c4-pill-${sd.color}${!m.result && m.turn === i ? ' c4-up' : ''}${m.result === 'win' && m.winner === i ? ' c4-won' : ''}">${sideLabel(sd)}${sd.kind === 'team' ? `<small>${sd.members.map(esc).join(', ') || 'no one yet'}</small>` : ''}</span>`).join('<em>vs</em>')}</div>`;
  }

  function lobby(g, s, pid, isHost) {
    const c = g.cfg;
    let out = `<div class="c4-card"><h2>${FORMAT[c.format]}</h2><p>Connect <b>${c.connect}</b> in a row on a <b>${c.size.replace('x', '×')}</b> board${c.timer ? ` · ${c.timer}s per move` : ''} · computer: <b>${BOT[c.bot]}</b></p>`;
    if (c.format === 'duels') out += '<p>Everyone gets paired up and plays at the same time. Odd one out plays the computer. Rematches mix up the pairs.</p>';
    if (c.format === 'bot') out += '<p>Everyone plays their own game against the computer. Beat Hard for 200 points!</p>';
    if (c.format === 'teams') {
      out += '<p>Teammates take turns dropping for their team. An empty team is played by the computer.</p><div class="c4-teams">'
        + [1, 2].map(t => `<div class="c4-team c4-team-${t}"><h3>${t === 1 ? '🔴 Team Red' : '🟡 Team Yellow'}</h3><p>${(g.teams[t] || []).map(p => esc(p.name)).join(', ') || '<span class="c4-muted">no one yet</span>'}</p>${pid && g.myTeam !== t ? `<button data-c4="c4Team" data-team="${t}">Join ${t === 1 ? 'Red' : 'Yellow'}</button>` : ''}${pid && g.myTeam === t ? '<p class="c4-muted">You’re on this team</p>' : ''}</div>`).join('')
        + '</div>';
    }
    out += '</div>';
    if (isHost) {
      const sel = (key, label, opts) => `<label>${label}<select data-set="${key}">${Object.entries(opts).map(([v, t]) => `<option value="${esc(v)}"${String(c[key]) === v ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select></label>`;
      out += `<div class="c4-controls">${sel('format', 'Format', FORMAT)}${sel('size', 'Board', {'6x5': '6×5 (quick)', '7x6': '7×6 (classic)', '8x7': '8×7', '9x7': '9×7 (big)'})}${sel('connect', 'Win with', {4: '4 in a row', 5: '5 in a row'})}${sel('bot', 'Computer', BOT)}${sel('timer', 'Move clock', {0: 'Off', 15: '15 s', 30: '30 s', 60: '60 s'})}
        ${c.format === 'teams' ? '<button data-c4="c4Shuffle">🔀 Shuffle teams</button>' : ''}
        <button class="c4-go" data-c4="c4Start">Start ▶</button></div>`;
    } else out += '<p class="c4-muted c4-center">Waiting for the host to start…</p>';
    return out;
  }

  function table(g) {
    if (!g.standings.length) return '';
    return `<details class="c4-table" open><summary>Standings (3 for a win, 1 for a draw)</summary><table><tr><th></th><th>Player</th><th>W</th><th>D</th><th>L</th><th>Pts</th></tr>${g.standings.map(r => `<tr><td>${r.place}</td><td>${esc(r.name)}</td><td>${r.w}</td><td>${r.d}</td><td>${r.l}</td><td><b>${r.pts}</b></td></tr>`).join('')}</table></details>`;
  }

  function render(root, s, pid, send, isHost = false) {
    const g = s.connect4; if (!g || !root) return;
    g._names = Object.fromEntries((s.players || []).map(p => [p.pid, p.name]));
    g.skew = Date.now() / 1000 - (g.now || Date.now() / 1000);
    current = {g, pid, send, isHost, root};
    if (!root._c4) {
      root.innerHTML = '<header class="c4-head"><h1>🔴 Connect 4 🟡</h1><p class="c4-sub"></p></header><div class="c4-main"></div><p role="alert" class="c4-error"></p><div class="c4-foot"></div>';
      root._c4 = true;
      root.addEventListener('click', onClick);
      root.addEventListener('change', onChange);
    }
    const sub = g.phase === 'lobby' ? 'Line up four before they do.' : g.phase === 'done' ? `Round ${g.round} is over` : `Round ${g.round}`;
    setHTML(root.querySelector('.c4-sub'), esc(sub));
    const main = root.querySelector('.c4-main');

    if (g.phase === 'lobby') {
      main._c4ids = null;
      setHTML(main, lobby(g, s, pid, isHost));
    } else {
      // one wrapper per match, each updated on its own so a move on one board
      // doesn't restart the drop animation on the others
      const mine = pid != null && g.me;
      const shown = mine && !isHost ? g.matches.filter(m => m.id === g.me.match) : g.matches;
      const ids = shown.map(m => m.id).join(',') + (isHost ? ':h' : ':p');
      if (main._c4ids !== ids) {
        main._c4HTML = null;
        main.innerHTML = `<div class="c4-boards c4-n${Math.min(shown.length, 4)}${isHost ? ' c4-screen' : ''}">${shown.map(m => `<section class="c4-match" data-m="${m.id}"><div class="c4-status"></div><div class="c4-vsw"></div><div class="c4-bw"></div></section>`).join('')}</div>`
          + (mine && !isHost && g.matches.length > 1 ? '<details class="c4-others"><summary>Other games</summary><div class="c4-mini"></div></details>' : '')
          + (!mine && !isHost && g.phase === 'playing' ? '<p class="c4-muted c4-center">You’re watching this round. You’ll be in the next one.</p>' : '');
        main._c4ids = ids;
      }
      for (const m of shown) {
        const sec = main.querySelector(`[data-m="${m.id}"]`); if (!sec) continue;
        sec.classList.toggle('c4-myturn', !!m.yourTurn);
        setHTML(sec.querySelector('.c4-status'), `<span>${status(m, g, pid)}</span>${clock(m)}${m.auto ? `<small>${esc(m.auto)}</small>` : ''}`);
        setHTML(sec.querySelector('.c4-vsw'), versus(m));
        setHTML(sec.querySelector('.c4-bw'), board(m));
      }
      const mini = main.querySelector('.c4-mini');
      if (mini) setHTML(mini, g.matches.filter(m => m.id !== g.me.match).map(m => `<div class="c4-minim">${versus(m)}<p>${status(m, g, null)}</p>${board({...m, yourTurn: false})}</div>`).join(''));
    }

    let foot = '';
    if (g.phase !== 'lobby') foot += table(g);
    if (isHost) {
      if (g.phase === 'playing') foot += '<div class="c4-controls"><button data-c4="c4End" class="c4-danger">End round</button></div>';
      if (g.phase === 'done') foot += '<div class="c4-controls"><button class="c4-go" data-c4="c4Start">Rematch ▶</button><button data-c4="c4Lobby">Change settings</button>' + (g.standings.length ? '<button data-c4="c4Reset">Reset standings</button>' : '') + '</div>';
    } else if (g.phase === 'done') foot += '<p class="c4-muted c4-center">Waiting for the host to start a rematch…</p>';
    foot += '<details class="c4-how"><summary>How to play</summary><p>Take turns dropping a disc into a column; it falls to the lowest empty spot. First to line up four (or five, if the host picks it) across, down or diagonally wins. Red always goes first, and who gets red is random. Tap a column to drop. On a keyboard, press 1–9.</p><p>Room points: 100 for a win, 40 for a draw; beating the computer is worth 50 / 100 / 200 on Easy / Medium / Hard. If you leave mid-game, the computer takes your seat.</p></details>';
    setHTML(root.querySelector('.c4-foot'), foot);
    root.querySelector('.c4-error').textContent = error;

    if (!keysBound) {
      keysBound = true;
      document.addEventListener('keydown', e => {
        const c = current; if (!c || c.isHost || c.root.hidden || !c.root.isConnected) return;
        if (e.target.matches?.('input, textarea, select')) return;
        const n = parseInt(e.key, 10); if (!(n >= 1 && n <= 9)) return;
        const m = (c.g.matches || []).find(x => x.yourTurn);
        if (m && n <= m.cols) drop(n - 1);
      });
    }
  }

  async function run(action, extra = {}) {
    if (busy) return;
    busy = true; error = '';
    try { const r = await current.send(action, extra); if (r && r.ok === false) error = r.error || 'Something went wrong.'; }
    catch (e) { error = 'Connection lost. Please retry.'; }
    finally { busy = false; if (current) current.root.querySelector('.c4-error').textContent = error; }
  }
  function drop(col) { run('c4Drop', {col}); }

  function onClick(e) {
    const col = e.target.closest('[data-col]');
    if (col && !col.disabled) { drop(+col.dataset.col); return; }
    const b = e.target.closest('[data-c4]'); if (!b) return;
    const a = b.dataset.c4;
    if (a === 'c4End' && !confirm('End this round for everyone?')) return;
    if (a === 'c4Reset' && !confirm('Reset the standings?')) return;
    run(a, b.dataset.team ? {team: +b.dataset.team} : {});
  }
  function onChange(e) {
    const sel = e.target.closest('[data-set]'); if (!sel) return;
    run('c4Set', {key: sel.dataset.set, value: sel.value});
    sel.blur();
  }
  return {render};
})();
