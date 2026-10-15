/* Sudoku: phone board (own or shared) and the shared-screen view. */
globalThis.SudokuView = (() => {
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const LABEL = {easy:'Easy', medium:'Medium', advanced:'Advanced', hard:'Hard', expert:'Expert'};
  const MODE = {competitive:'Competitive', coop:'Co-op', zen:'Zen'};
  const MODE_HELP = {
    competitive: 'Everyone races the same board. Wrong digits are rejected and add a 30-second penalty. Fastest adjusted time wins.',
    coop: 'One shared board. Everyone fills it in together; mistakes count against the team.',
    zen: 'No clock, no ranking. Pick any difficulty and play at your own pace. Nothing is checked until the board is full.'
  };
  const SKINS = [
    ['paper-moon', 'Paper Moon', 'the classic lavender'],
    ['midnight-snack', 'Midnight Snack', 'mint on navy, soft & round'],
    ['grandmas-crossword', "Grandma's Crossword", 'newsprint & blue biro'],
    ['bubblegum-riot', 'Bubblegum Riot', 'pink, loud, comic sans'],
    ['swamp-thing', 'Swamp Thing', 'mossy & mysterious'],
    ['terminal-velocity', 'Terminal Velocity', 'green phosphor hacker'],
  ];
  const getSkin = () => { try { const k = localStorage.getItem('sd-skin'); return SKINS.some(x => x[0] === k) ? k : 'paper-moon'; } catch { return 'paper-moon'; } };
  const row = i => Math.floor(i / 9), col = i => i % 9, box = i => Math.floor(row(i) / 3) * 3 + Math.floor(col(i) / 3);
  const peers = (a, b) => a !== b && (row(a) === row(b) || col(a) === col(b) || box(a) === box(b));
  const clock = s => { s = Math.max(0, Math.floor(s)); const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, x = s % 60;
    return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(x).padStart(2, '0'); };

  let root = null, s = null, pid = null, send = null, host = false, ctrl = false, hint = null;
  let built = null, key = '', sel = -1, notesMode = false, notes = {}, pending = {}, flash = {}, error = '', busy = false, offset = 0;

  function loadNotes() {
    try { notes = JSON.parse(localStorage.getItem('sd:' + key) || '{}'); } catch { notes = {}; }
  }
  function saveNotes() {
    try { localStorage.setItem('sd:' + key, JSON.stringify(notes)); } catch {}
  }

  function skeleton() {
    root.innerHTML = `<div class="sd-head"></div>
      <div class="sd-play" hidden>
        <div class="sd-bar"><span class="sd-timer"></span><span class="sd-mist"></span></div>
        <div class="sd-specbar" hidden></div>
        <div class="sd-grid" role="grid" aria-label="Sudoku board">${Array.from({length: 81}, (_, i) =>
          `<button type="button" class="sd-cell" data-i="${i}" role="gridcell" aria-label="Row ${row(i) + 1}, column ${col(i) + 1}"></button>`).join('')}</div>
        <div class="sd-pad" hidden>${[1,2,3,4,5,6,7,8,9].map(d => `<button type="button" data-d="${d}"><b>${d}</b><small></small></button>`).join('')}
          <button type="button" data-notes class="sd-tool" aria-pressed="false">✎ Notes</button>
          <button type="button" data-d="0" class="sd-tool">⌫ Erase</button>
          <button type="button" data-hint class="sd-tool">💡 Hint</button></div>
        <div class="sd-hintbox" role="status" hidden><p></p><div><button type="button" data-hintfill>Fill it in</button><button type="button" data-hintclose aria-label="Close hint">✕</button></div></div>
        <details class="sd-skins"><summary>🎨 Board style</summary><div class="sd-skin-list">${SKINS.map(([k, name, blurb]) =>
          `<button type="button" data-skin="${k}" data-skin-pick="${k}" aria-pressed="false"><span class="sd-swatch" aria-hidden="true"><b>5</b><b>3</b><b>7</b><b>1</b></span><span>${esc(name)}<small>${esc(blurb)}</small></span></button>`).join('')}</div></details>
      </div>
      <p role="alert" class="sd-error"></p>
      <div class="sd-info"></div>
      <details class="sd-chat" open><summary>💬 Chat <span class="sd-chatcount"></span></summary>
        <ol class="sd-chatlog" aria-live="polite"></ol><p class="sd-chatnote"></p>
        <form class="sd-chatform"><input maxlength="200" autocomplete="off" placeholder="Say something…" aria-label="Chat message"><button>Send</button></form></details>`;
    built = root;
    root.addEventListener('click', onClick);
    root.addEventListener('change', onChange);
    root.querySelector('.sd-chatform').addEventListener('submit', async e => {
      e.preventDefault();
      const input = e.target.querySelector('input'), text = input.value.trim();
      if (!text) return;
      input.value = '';
      const r = await run('sdChat', {text});
      if (r && r.ok === false) input.value = text;
    });
  }

  function boardOf(g) {
    if (g.spectate) return {puzzle: g.spectate.puzzle, cells: g.spectate.cells, solution: g.spectate.solution,
      by: g.mode === 'coop' ? g.by : null, own: false};
    if (!g.puzzle) return null;
    return {puzzle: g.puzzle, cells: g.cells, by: g.by || null, own: !!g.seatRid};
  }

  // isHost = the shared laptop/TV display. opts.controls = show round controls
  // on a phone (the owner of an open table on the /sudoku page).
  function render(el, state, myPid, sender, isHost = false, opts = {}) {
    const g = state.sudoku; if (!g || !el) return;
    root = el; s = state; pid = myPid; send = sender; host = isHost; ctrl = isHost || !!opts.controls;
    if (built !== root) skeleton();
    offset = g.serverNow * 1000 - Date.now();
    const b = boardOf(g);
    const nextKey = b ? (g.spectate ? 'spec:' + g.spectate.rid : g.seatRid || g.rid + ':' + g.mode) : '';
    if (nextKey !== key) { key = nextKey; sel = -1; pending = {}; flash = {}; error = ''; hint = null; notes = {}; if (key && !g.spectate) loadNotes(); }
    // drop optimistic digits the server has now confirmed
    if (b) for (const i in pending) if (b.cells[i] !== '0') delete pending[i];
    if (hint && b && hint.kind !== 'wrong' && b.cells[hint.i] !== '0') hint = null;

    const showBoard = !!b && (!host || g.mode === 'coop');
    root.querySelector('.sd-play').hidden = !showBoard;
    const canPlay = showBoard && !host && !g.spectate && g.phase === 'playing' && g.inGame && !(g.me && g.me.finished);
    if (!canPlay) hint = null;
    const spec = root.querySelector('.sd-specbar');
    spec.hidden = !g.spectate;
    setHTML(spec, g.spectate ? `👀 Watching <b>${esc(g.spectate.name)}</b>${g.spectate.finished ? ' (finished)' : ''} · faint digits are the solution <button type="button" data-watch="">Stop watching</button>` : '');
    const hb = root.querySelector('.sd-hintbox');
    hb.hidden = !hint;
    if (hint) {
      hb.querySelector('p').textContent = '💡 ' + hint.text;
      hb.querySelector('[data-hintfill]').hidden = hint.kind === 'wrong' || !hint.d;
      hb.querySelector('[data-hintfill]').textContent = hint.kind === 'wrong' ? '' : `Fill in the ${hint.d}`;
    }
    root.querySelector('[data-hint]').disabled = !canPlay;
    paintChat(g);
    root.querySelector('.sd-pad').hidden = !canPlay;
    root.querySelector('.sd-grid').classList.toggle('sd-readonly', !canPlay);
    root.querySelector('.sd-grid').classList.toggle('sd-big', host);
    root.querySelector('.sd-grid').classList.toggle('sd-noting', canPlay && notesMode);
    const skin = getSkin();
    if (root.dataset.skin !== skin) root.dataset.skin = skin;
    root.querySelectorAll('[data-skin-pick]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.skinPick === skin)));
    if (showBoard) paintBoard(g, b, canPlay);
    paintTimer();
    setHTML(root.querySelector('.sd-head'), head(g));
    setHTML(root.querySelector('.sd-info'), info(g));
    root.querySelector('.sd-error').textContent = error || (g.message && g.phase === 'playing' ? g.message : '');
  }

  function setHTML(node, html) { if (node.innerHTML !== html) node.innerHTML = html; }

  function value(b, i) { return pending[i] ? String(pending[i]) : b.cells[i]; }

  function paintBoard(g, b, canPlay) {
    const vals = Array.from({length: 81}, (_, i) => value(b, i));
    const selVal = sel >= 0 ? vals[sel] : '0';
    const counts = {}; vals.forEach(v => { if (v !== '0') counts[v] = (counts[v] || 0) + 1; });
    const clash = new Set();
    if (g.mode === 'zen') for (let i = 0; i < 81; i++) if (vals[i] !== '0')
      for (let j = i + 1; j < 81; j++) if (vals[j] === vals[i] && peers(i, j)) { clash.add(i); clash.add(j); }
    const colorOf = Object.fromEntries((g.roster || []).map(r => [r.pid, r.color]));
    const now = Date.now();
    root.querySelectorAll('.sd-cell').forEach(cell => {
      const i = +cell.dataset.i, v = vals[i], given = b.puzzle[i] !== '0';
      const cls = ['sd-cell'];
      if (given) cls.push('sd-given'); else if (v !== '0') cls.push('sd-filled');
      if (pending[i]) cls.push('sd-pending');
      if (i === sel) cls.push('sd-sel');
      else if (sel >= 0 && peers(i, sel)) cls.push('sd-peer');
      if (selVal !== '0' && v === selVal) cls.push('sd-same');
      if (clash.has(i)) cls.push('sd-clash');
      if (flash[i] && now - flash[i] < 900) cls.push('sd-wrong');
      if (hint && hint.i === i) cls.push('sd-hint');
      if (col(i) % 3 === 2 && col(i) < 8) cls.push('sd-r');
      if (row(i) % 3 === 2 && row(i) < 8) cls.push('sd-b');
      const name = cls.join(' ');
      if (cell.className !== name) cell.className = name;
      const owner = b.by && b.by[i];
      const tint = owner && colorOf[owner] ? `--sd-owner:${colorOf[owner]}` : '';
      if ((cell.getAttribute('style') || '') !== tint) cell.setAttribute('style', tint);
      let html;
      if (v !== '0') html = v;
      else if (b.solution) html = `<span class="sd-ghost">${b.solution[i]}</span>`;
      else if (notes[i] && notes[i].length) html = `<span class="sd-notes">${[1,2,3,4,5,6,7,8,9].map(d => `<i>${notes[i].includes(d) ? d : ''}</i>`).join('')}</span>`;
      else html = '';
      if (cell.innerHTML !== html) cell.innerHTML = html;
      cell.disabled = !canPlay;
      cell.setAttribute('aria-selected', String(i === sel));
    });
    root.querySelectorAll('.sd-pad [data-d]').forEach(btn => {
      const d = btn.dataset.d;
      if (d === '0') { btn.disabled = !canPlay; return; }
      const left = 9 - (counts[d] || 0);
      btn.querySelector('small').textContent = left > 0 ? left : '';
      btn.disabled = !notesMode && left <= 0;
      btn.classList.toggle('sd-hot', selVal === d);
    });
    const nb = root.querySelector('[data-notes]');
    nb.setAttribute('aria-pressed', String(notesMode));
    const m = root.querySelector('.sd-mist');
    const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
    if (g.spectate) m.textContent = LABEL[g.spectate.level] || '';
    else if (g.mode === 'coop') m.textContent = `${plural(g.teamMistakes || 0, 'team mistake')} · ${plural(g.teamHints || 0, 'hint')}`;
    else if (g.mode === 'competitive' && g.me) m.textContent = `${plural(g.me.mistakes, 'mistake')} · ${plural(g.me.hints, 'hint')} (+${g.me.mistakes * g.penalty + g.me.hints * g.hintPenalty}s)`;
    else m.textContent = (g.level ? LABEL[g.level] : '') + (g.me && g.me.hints ? ` · ${plural(g.me.hints, 'hint')}` : '');
  }

  function paintChat(g) {
    const box = root.querySelector('.sd-chat');
    box.hidden = host;
    if (host) return;
    const log = root.querySelector('.sd-chatlog');
    const msgs = g.chat || [];
    const html = msgs.map(m => `<li><b style="color:${esc(m.color)}">${esc(m.name)}</b>${m.spoiler ? ' <span class="sd-spoil" title="Sent by someone who can see the solution">👀</span>' : ''} ${esc(m.text)}</li>`).join('');
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 30;
    if (log.innerHTML !== html) { log.innerHTML = html; if (atBottom) log.scrollTop = log.scrollHeight; }
    root.querySelector('.sd-chatcount').textContent = msgs.length ? `(${msgs.length})` : '';
    let note = '';
    if (g.spoilerHidden) note = 'Messages from spectators are hidden until you finish, so nobody can give away answers.';
    else if (g.peeked && g.phase === 'playing') note = 'You can see the solution, so players still solving won\'t see your messages until they finish.';
    root.querySelector('.sd-chatnote').textContent = note;
  }

  function elapsed() {
    const g = s && s.sudoku; if (!g || g.phase === 'lobby') return null;
    const end = g.phase === 'done' && g.endedAt ? g.endedAt * 1000 : Date.now() + offset;
    return (end - g.startedAt * 1000) / 1000;
  }

  function paintTimer() {
    const t = root && root.querySelector('.sd-timer'); if (!t) return;
    const g = s.sudoku, e = elapsed();
    t.textContent = g.mode === 'zen' ? '🌿 Zen' : e == null ? '' : '⏱ ' + clock(e);
  }

  function head(g) {
    const lvl = LABEL[g.difficulty];
    let sub = `${MODE[g.mode]} · ${lvl}`;
    if (g.phase === 'lobby') sub = host ? 'Choose a mode and difficulty, then start.' : `Up next: ${MODE[g.mode]} · ${lvl}. Waiting for the host…`;
    if (g.phase === 'playing' && g.mode === 'zen') sub = 'Zen mode · play at your own pace';
    if (g.phase === 'playing' && g.mode !== 'zen' && g.level) sub = `${MODE[g.mode]} · ${LABEL[g.level]} · board ${esc(g.boardId)}`;
    if (g.phase === 'done') sub = g.mode === 'zen' ? 'Zen session over' : 'Round over';
    return `<h1>🔢 Sudoku</h1><p class="sd-sub">${sub}</p>`;
  }

  function progress(r) {
    const pct = r.blanks ? Math.round(100 * r.filled / r.blanks) : 0;
    return `<div class="sd-prog" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100" aria-label="${esc(r.name)} progress"><span style="width:${pct}%"></span></div>`;
  }

  function roster(g) {
    const rows = (g.roster || []).map(r => {
      const you = r.pid === pid ? ' <small>(you)</small>' : '';
      let right;
      const hints = r.hints ? ` · ${r.hints} hint${r.hints === 1 ? '' : 's'}` : '';
      if (g.mode === 'coop') right = `${r.placed} placed · ${r.mistakes} wrong${hints}`;
      else if (r.seconds != null) right = g.mode === 'competitive' ? `✓ ${clock(r.adjusted)}${r.mistakes ? ` (${clock(r.seconds)} + ${r.mistakes}×${g.penalty}s)` : ''}` : `✓ solved in ${clock(r.seconds)}`;
      else right = g.mode === 'competitive' ? `${r.filled ?? 0}/${r.blanks ?? 0} · ${r.mistakes} wrong${hints}` : `${LABEL[r.level] || ''} · ${r.filled ?? 0}/${r.blanks ?? 0}${hints}`;
      if (r.seconds != null && hints) right += hints;
      const watching = g.spectate && g.spectate.pid === r.pid;
      const watch = !host && g.phase === 'playing' && r.watchable && r.here
        ? ` <button type="button" class="sd-watch" data-watch="${watching ? '' : r.pid}" aria-pressed="${watching}">${watching ? 'Watching' : '👀 Watch'}</button>` : '';
      right += watch;
      return `<li class="${r.here ? '' : 'sd-gone'}"><span class="sd-dot" style="background:${esc(r.color)}"></span><b>${r.place ? '#' + r.place + ' ' : ''}${esc(r.name)}${you}</b><span>${right}${r.here ? '' : ' · left'}</span>${g.mode === 'coop' ? '' : progress(r)}</li>`;
    }).join('');
    return rows ? `<ul class="sd-roster">${rows}</ul>` : '';
  }

  function pickers(label) {
    return `<div class="sd-pick"><p>${label}</p>${Object.keys(LABEL).map(l => `<button type="button" data-zen="${l}">${LABEL[l]}</button>`).join('')}</div>`;
  }

  function info(g) {
    let out = '';
    if (g.phase === 'lobby') {
      if (ctrl) out += `<div class="sd-controls">
          <label>Mode <select data-set="mode">${g.modes.map(m => `<option value="${m}"${m === g.mode ? ' selected' : ''}>${MODE[m]}</option>`).join('')}</select></label>
          <label>Difficulty <select data-set="difficulty">${g.levels.map(l => `<option value="${l}"${l === g.difficulty ? ' selected' : ''}>${LABEL[l]}</option>`).join('')}</select></label>
          <button type="button" data-act="sdStart">Start ${MODE[g.mode]}</button></div>`;
      out += `<p class="sd-help">${MODE_HELP[g.mode]}</p>`;
      if (g.bank) out += `<div class="sd-bank"><h2>Boards nobody here has played</h2><ul>${g.levels.map(l =>
        `<li class="${l === g.difficulty ? 'sd-on' : ''}"><b>${LABEL[l]}</b><span>${g.bank[l].fresh} of ${g.bank[l].total}</span></li>`).join('')}</ul></div>`;
      if (host && g.recent && g.recent.length) out += `<details class="sd-recent"><summary>Recently played</summary><ul>${g.recent.map(r =>
        `<li>${esc(r.date)} · <b>${esc(r.player)}</b> · ${esc(r.board)} (${LABEL[r.level] || esc(r.level)}, ${MODE[r.mode] || esc(r.mode)}) · ${r.result === 'solved' ? '✓ ' + clock(r.seconds || 0) : 'unfinished'}</li>`).join('')}</ul></details>`;
    }
    if (g.phase === 'playing') {
      if (g.mode === 'zen' && !host && (!g.inGame || (g.me && g.me.finished)))
        out += pickers(g.me && g.me.finished ? '🎉 Solved! Another one?' : 'Pick a difficulty to start your board.');
      if (g.mode === 'coop' && !host && !g.inGame) out += g.peeked
        ? `<p>You've seen this board's solution, so you're spectating this round.</p>`
        : `<p>A board is in progress. Help fill it in, or tap 👀 Watch on a player to spectate with the solution ghosted in (then you can't join this round).</p><button type="button" class="sd-join" data-act="sdJoin">Join in and help</button>`;
      if (g.mode === 'competitive' && !host && !g.inGame) out += `<p>This race started before you joined. Tap 👀 Watch on anyone to spectate with the solution ghosted in. You'll be in the next race.</p>`;
      if (g.mode === 'zen' && !host && !g.inGame) out += `<p>Or tap 👀 Watch to see how someone else is doing.</p>`;
      if (g.mode === 'competitive' && g.me && g.me.finished) out += `<p class="sd-win">✓ Done! Waiting for the others…</p>`;
      out += roster(g);
      if (g.mode === 'zen' && !host && g.inGame && !(g.me && g.me.finished)) out += `<details class="sd-new"><summary>Start a different board</summary>${pickers('Your current board will be logged as unfinished.')}</details>`;
      if (ctrl) out += `<div class="sd-controls"><button type="button" data-act="sdEnd" class="sd-danger">${g.mode === 'zen' ? 'End Zen session' : 'End round now'}</button></div>`;
    }
    if (g.phase === 'done') {
      if (g.mode === 'coop') {
        const solved = (g.roster || []).length && g.cells && !g.cells.includes('0');
        out += `<p class="sd-win">${solved ? `🎉 Solved together in ${clock(elapsed())} with ${g.teamMistakes} mistake${g.teamMistakes === 1 ? '' : 's'}.` : 'The board was left unfinished.'}</p>`;
      } else if (g.mode === 'competitive') {
        const w = (g.roster || []).find(r => r.place === 1);
        out += `<p class="sd-win">${w ? `🏆 ${esc(w.name)} wins with ${clock(w.adjusted)}` : 'Nobody finished this one.'}</p>`;
      }
      out += roster(g);
      if (ctrl) out += `<div class="sd-controls"><button type="button" data-act="sdStart">Play again</button><button type="button" data-act="sdLobby">Change mode / difficulty</button></div>`;
    }
    out += `<details class="sd-how"><summary>How to play</summary><p>Fill every row, column and 3×3 box with 1–9, no repeats. Tap a cell, then a number. Turn on ✎ Notes to pencil in candidates: the board greys out and your notes turn white while you're noting (notes stay on your phone). Pick a look under 🎨 Board style; it's saved on this device. On a keyboard: arrows move, 1–9 fill, Backspace erases, N toggles notes, H asks for a hint.</p><p><b>💡 Hints</b> point at the next cell you can work out logically and say why; you choose whether to fill it in. In Competitive each hint adds ${s && s.sudoku ? s.sudoku.hintPenalty : 45} seconds. Co-op counts them for the team. In Zen they're free.</p><p><b>Spectating:</b> people who arrive mid-round can 👀 Watch any player and see the solution faintly behind the board. Their chat messages stay hidden from anyone still solving until the round ends.</p><p><b>Difficulty</b> is set by the hardest technique a board needs: Easy and Medium need only singles; Advanced adds pointing/claiming; Hard adds pairs and triples; Expert adds X-wings, swordfish and XY-wings. Each round deals a board nobody in the room has played yet.</p></details>`;
    return out;
  }

  function rerender() { if (s) render(root, s, pid, send, host, {controls: ctrl}); }

  async function askHint() {
    const r = await run('sdHint');
    if (r && r.hint) { hint = r.hint; sel = r.hint.i; }
    rerender();
  }

  async function run(action, extra = {}) {
    if (busy) return null;
    busy = true;
    try {
      const r = await send(action, extra);
      if (!r || r.ok === false) error = (r && r.error) || 'Connection lost. Please retry.';
      else error = '';
      return r;
    } catch { error = 'Connection lost. Please retry.'; return null; }
    finally { busy = false; rerender(); }
  }

  async function enter(d) {
    const g = s.sudoku, b = boardOf(g);
    if (!b || sel < 0 || b.puzzle[sel] !== '0') return;
    const cur = value(b, sel);
    if (notesMode && d) {
      if (cur !== '0') return;
      const n = notes[sel] || [];
      notes[sel] = n.includes(d) ? n.filter(x => x !== d) : [...n, d].sort();
      saveNotes(); rerender(); return;
    }
    if (d === 0) {
      if (notes[sel]) { delete notes[sel]; saveNotes(); }
      if (g.mode === 'zen' && cur !== '0') { const i = sel; pending[i] = 0; await place(i, 0); }
      else rerender();
      return;
    }
    if (cur !== '0' && g.mode !== 'zen') return;
    const i = sel;
    pending[i] = d;
    rerender();
    await place(i, d);
  }

  async function place(i, d) {
    const r = await send('sdPlace', {i, d}).catch(() => null);
    if (!r || r.ok === false) { delete pending[i]; error = (r && r.error) || 'Connection lost. Please retry.'; }
    else if (r.correct === false) { delete pending[i]; flash[i] = Date.now(); error = ''; setTimeout(() => s && rerender(), 950); }
    else {
      error = '';
      if (d) { for (const j in notes) if (peers(+j, i) && notes[j].includes(d)) notes[j] = notes[j].filter(x => x !== d); delete notes[i]; saveNotes(); }
      if (d === 0) { const b = boardOf(s.sudoku); if (b) { b.cells = b.cells.slice(0, i) + '0' + b.cells.slice(i + 1); s.sudoku.cells = b.cells; } delete pending[i]; }
    }
    rerender();
  }

  function onClick(e) {
    const cell = e.target.closest('.sd-cell');
    if (cell && !cell.disabled) { sel = +cell.dataset.i; rerender(); return; }
    const d = e.target.closest('[data-d]');
    if (d && !d.disabled) { enter(+d.dataset.d); return; }
    if (e.target.closest('[data-notes]')) { notesMode = !notesMode; rerender(); return; }
    if (e.target.closest('[data-hint]')) { askHint(); return; }
    if (e.target.closest('[data-hintclose]')) { hint = null; rerender(); return; }
    if (e.target.closest('[data-hintfill]') && hint && hint.d) { const h = hint; hint = null; sel = h.i; notesMode = false; enter(h.d); return; }
    const w = e.target.closest('[data-watch]');
    if (w) {
      const target = w.dataset.watch ? +w.dataset.watch : null;
      if (target && s.sudoku.mode === 'coop' && !s.sudoku.peeked && !confirm('Spectators see the solution, so you won\'t be able to join this board afterwards. Watch anyway?')) return;
      run('sdWatch', {target}); return;
    }
    const sk = e.target.closest('[data-skin-pick]');
    if (sk) { try { localStorage.setItem('sd-skin', sk.dataset.skinPick); } catch {} rerender(); return; }
    const z = e.target.closest('[data-zen]');
    if (z) { run('sdZenNew', {difficulty: z.dataset.zen}); return; }
    const a = e.target.closest('[data-act]');
    if (a && !a.disabled) {
      if (a.dataset.act === 'sdEnd' && !confirm('End this round for everyone?')) return;
      run(a.dataset.act);
    }
  }

  function onChange(e) {
    const sel = e.target.closest('[data-set]');
    if (sel) run('sdSet', {key: sel.dataset.set, value: sel.value});
  }

  document.addEventListener('keydown', e => {
    if (!root || !s || !s.sudoku || root.offsetParent === null || host) return;
    if (e.target.closest && e.target.closest('input, textarea, select')) return;
    const pad = root.querySelector('.sd-pad'); if (pad.hidden) return;
    const k = e.key;
    if (/^[1-9]$/.test(k)) { enter(+k); e.preventDefault(); }
    else if (k === 'Backspace' || k === 'Delete' || k === '0') { enter(0); e.preventDefault(); }
    else if (k === 'n' || k === 'N') { notesMode = !notesMode; rerender(); }
    else if (k === 'h' || k === 'H') { askHint(); }
    else if (k.startsWith('Arrow')) {
      if (sel < 0) sel = 0;
      else sel = {ArrowUp: (sel + 72) % 81, ArrowDown: (sel + 9) % 81, ArrowLeft: row(sel) * 9 + (col(sel) + 8) % 9, ArrowRight: row(sel) * 9 + (col(sel) + 1) % 9}[k];
      rerender(); e.preventDefault();
    }
  });
  setInterval(() => { if (root && s && s.sudoku && root.offsetParent !== null) paintTimer(); }, 500);

  return {render};
})();
