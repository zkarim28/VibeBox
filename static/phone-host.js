/* Host controls alongside the normal, private player view. No host-display iframe. */
globalThis.PhoneHost = (() => {
  const enabled = /^\/host\/play\/?$/.test(location.pathname);
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let getPid = () => null, latest = null, games = [], formKey = '', busy = false;
  const messages = {
    not_host: 'Unlock host controls again to continue.', need_players: 'More players need to join first.',
    need_3: 'BlackBox needs at least 3 players.', need_teams: 'Codenames needs at least 2 players on each team.',
    need_spy_red: 'Someone on Red needs to volunteer as spymaster in Play.', need_spy_blue: 'Someone on Blue needs to volunteer as spymaster in Play.',
    taboo_teams: 'Taboo needs at least 2 players per team: one clue-giver and one guesser.',
    need_moderator: 'Choose a Mafia moderator first. They use their phone and do not receive a card.',
    bad_counts: 'The role counts exceed the number of card-holders.', too_many_mafia: 'The town must outnumber the Mafia.',
    finish_round: 'Finish the current round (or return to its lobby) on the shared-screen host before changing room mode.',
    phone_mode: 'This option needs a shared display. Use the phone-compatible option in this room.',
    not_allowed: 'That control is not available for your role.', bad_password: 'That password is not correct.',
    rate_limited: 'Too many attempts. Wait a minute and try again.'
  };
  async function request(path, body) {
    const r = await fetch(path, { method: body ? 'POST' : 'GET', cache: 'no-store',
      headers: body ? {'Content-Type':'application/json'} : {}, body: body ? JSON.stringify(body) : undefined });
    const d = await r.json();
    if (!r.ok || d.ok === false || d.error) throw new Error(messages[d.error] || `Could not complete that action (${d.error || r.status}).`);
    return d;
  }
  const action = (name, extra = {}) => request('/input', { pid: getPid(), action: name, ...extra });
  function tab(host) {
    $('ph-panel').hidden = !host;
    $('ph-play').setAttribute('aria-pressed', String(!host));
    $('ph-host').setAttribute('aria-pressed', String(host));
    if (host) $('ph-title').focus({preventScroll:true});
  }
  function button(action, label, extra = {}, danger = false) {
    return `<button type="button" data-command="${esc(JSON.stringify({action, ...extra}))}"${danger ? ' data-confirm="true" class="ph-danger"' : ''}>${esc(label)}</button>`;
  }
  function number(label, action, key, value, min, max) {
    return `<form class="ph-setting" data-setting="${action}" data-key="${key}"><label>${esc(label)}<input name="value" type="number" value="${value}" min="${min}" max="${max}" required inputmode="numeric"></label><button>Save</button></form>`;
  }
  function toggle(label, action, key, value) {
    return `<label class="ph-toggle"><input type="checkbox" data-toggle="${action}" data-key="${key}"${value ? ' checked' : ''}>${esc(label)}</label>`;
  }
  function textSetting(label, key, value) {
    return `<form class="ph-setting" data-setting="tabooSet" data-key="${key}"><label>${label}<input name="value" value="${esc(value)}" maxlength="16" required></label><button>Save</button></form>`;
  }
  function gameData(s) {
    return s[{'scattergories':'scat', 'wii-sandbox':'wii', 'word-hunt':'wordhunt'}[s.game] || s.game] || {};
  }
  function settings(s) {
    const g = gameData(s);
    if (!s.game) return '<p>Choose a game to configure it.</p>';
    if (g.phase !== 'lobby' && s.game !== 'wii-sandbox') return '<p>Settings are available in the game lobby.</p>';
    switch (s.game) {
      case 'scattergories': return number('Round length (seconds)', 'scatSet', 'time', g.time, 15, 600) + toggle('Show answer authors', 'scatSet', 'showNames', g.showNames) + toggle('Alliteration bonus', 'scatSet', 'alliterationBonus', g.alliterationBonus);
      case 'taboo': return number('Turn length (seconds)', 'tabooSet', 'turnSeconds', g.turnSeconds, 15, 300) + toggle('Award a point for a correct buzz', 'tabooSet', 'buzzScoresPoint', g.buzzScoresPoint) + textSetting('Team 1 name', 'team1Name', g.teamNames['1']) + textSetting('Team 2 name', 'team2Name', g.teamNames['2']) + '<p>Clue-givers rotate each turn. Their teammates guess without seeing the card. Switch teams in Play.</p>';
      case 'blackbox': return number('Points to win', 'blackboxSet', 'target', g.target, 3, 15) + number('Card selection time (seconds)', 'blackboxSet', 'selectSeconds', g.selectSeconds, 20, 180);
      case 'codenames': return '<p>Everyone plays on a phone. Choose teams and volunteer as spymaster in Play.</p>' + button('cnAutoTeam', 'Balance teams');
      case 'imposter': return number('Number of imposters', 'impSet', 'imposterCount', g.imposterCount, 1, 5) + '<p>Typed guesses are checked automatically, so the host can also be an imposter.</p>';
      case 'mafia': return ['mafia', 'sheriff', 'doctor'].map(k => number(k[0].toUpperCase() + k.slice(1) + ' count', 'mafiaSet', k, g.counts[k], k === 'mafia' ? 1 : 0, 8)).join('') + '<p>One player moderates using their phone instead of receiving a role. Choose someone else if you want to play.</p>';
      case 'word-hunt': return number('Round length (seconds)', 'whSet', 'seconds', g.seconds, 30, 300);
      case 'wii-sandbox': return '<p>Phone mode uses touch Target Practice. Everyone races to tap their own six targets; no motion sensors or shared display needed.</p>';
      case 'bs': return g.phase === 'lobby' ? number('Decks (1 or 2)', 'bsSet', 'decks', g.decks, 1, 2) : '<p>Finish the deal to change decks.</p>';
      case 'ludo': return '<p>The first four players get seats. Everyone sees the board on their phone.</p>';
      default: return '<p>Choose a game to configure it.</p>';
    }
  }
  function controls(s) {
    const g = gameData(s), ph = g.phase; let out = '';
    switch (s.game) {
      case 'scattergories':
        if (ph === 'lobby') out += button('scatStart','Start round') + button('scatResetScores','Reset scores',{},true);
        if (ph === 'playing') out += button('scatEndRound','End answers & review',{},true);
        if (ph === 'review') out += `<p>Category ${g.reviewIndex + 1} of ${g.reviewTotal} · ${g.votersDone || 0}/${g.votersNeeded || 0} voters finished</p>` + button('scatNext', 'Score & next category');
        if (ph === 'done') out += button('scatLobby','Next round settings');
        break;
      case 'taboo':
        if (ph === 'lobby') out += button('tabooStart','Start game');
        if (ph === 'ready') out += button('tabooBeginTurn','Start turn now');
        if (ph === 'turn') out += button('tabooEndTurn','End turn',{},true);
        if (ph === 'turnend') out += button('tabooNextTurn','Next turn');
        if (['turn','turnend'].includes(ph)) out += button('tabooEndGame','End game',{},true);
        if (ph === 'gameover') out += button('tabooNewGame','New game settings');
        break;
      case 'blackbox':
        if (ph === 'lobby') out += button('blackboxStart','Deal & start');
        if (['select','judge'].includes(ph)) out += button('blackboxSkip','Skip prompt',{},true);
        if (ph === 'judge') {
          out += button('blackboxFlip','Reveal next card');
          out += (g.plays || []).filter(p => p.slot < g.flipped).map(p => button('blackboxPick', `Choose: ${p.filled || "Submission " + (p.slot + 1)}`, {choice:p.slot})).join('');
          out += '<p>The Card Czar reviews and chooses submissions in Play.</p>';
        }
        if (ph === 'reveal') out += button('blackboxNext','Next round');
        if (['select','judge','reveal'].includes(ph)) out += button('blackboxEndGame','End game',{},true);
        if (ph === 'gameover') out += button('blackboxNewGame','New game settings');
        break;
      case 'codenames':
        if (ph === 'lobby') out += button('cnStart','Start game');
        if (ph === 'gameover') out += button('cnNewGame','Play again');
        if (ph !== 'lobby') out += button('cnLobby','Return to lobby',{},ph !== 'gameover');
        break;
      case 'imposter':
        if (['lobby','gameover'].includes(ph)) out += button('impStart','Start game');
        if (ph === 'clue') out += button('impSkip','Skip current clue',{},true);
        if (ph === 'review') out += button('impNextRound','Another clue round') + button('impVoteStart','Call a vote');
        if (ph === 'vote') out += button('impVoteResolve','Resolve vote now',{},true);
        if (!['lobby','gameover'].includes(ph)) out += button('impEndGame','Reveal & end game',{},true);
        if (ph === 'gameover') out += button('impLobby','Back to settings');
        if (ph === 'guess') out += '<p>Waiting for the imposter to submit their guess in Play.</p>';
        break;
      case 'mafia':
        if (['lobby','gameover'].includes(ph)) out += button('mafiaStart','Deal roles & start');
        if (ph === 'reveal') out += button('mafiaBeginNight','Begin night');
        if (ph === 'night') {
          out += '<p>The moderator handles secret role checks in Play. Record the night result here if needed.</p>';
          out += button('mafiaNight','No night death',{target:0});
          out += (g.roster || []).filter(p=>p.alive).map(p=>button('mafiaNight', `Night victim: ${p.name}`,{target:p.pid},true)).join('');
        }
        if (ph === 'day' && !g.voteResult) out += button('mafiaVoteResolve','Resolve votes') + button('mafiaNoLynch','No elimination') + (g.roster || []).filter(p=>p.alive).map(p=>button('mafiaLynch',`Eliminate ${p.name}`,{target:p.pid},true)).join('');
        if (ph === 'day' && g.voteResult) out += button('mafiaNextNight','Next night');
        if (['reveal','night','day'].includes(ph)) out += button('mafiaEndGame','End game',{},true);
        if (ph === 'gameover') out += button('mafiaLobby','Back to settings');
        break;
      case 'bs':
        if (['lobby','gameover'].includes(ph)) out += button('bsStart','Deal & start');
        if (ph !== 'lobby') out += button('bsLobby','Return to lobby',{},true);
        break;
      case 'ludo':
        if (ph === 'lobby') out += button('ludoStart','Start game');
        else if (ph === 'gameover') out += button('ludoNewGame','Play again') + button('ludoLobby','Back to lobby');
        else out += button('ludoEndGame','End game',{},true);
        break;
      case 'word-hunt':
        if (['lobby','review'].includes(ph)) out += button('whStart','Start round') + button('whResetScores','Reset scores',{},true);
        if (ph === 'review') out += button('whLobby','Round settings');
        if (ph === 'playing') out += button('whEndRound','End round',{},true);
        break;
      case 'wii-sandbox': out += button('wiiSelect',g.targets ? 'New targets' : 'Start Target Practice',{item:'targets'}) + button('wiiOpen','Back to lobby',{},!!g.targets); break;
    }
    return out;
  }
  function update(s) {
    if (!enabled || !$('ph-panel')) return;
    latest = s;
    const g = gameData(s), title = games.find(g=>g.id===s.game)?.name || 'Choose a game';
    $('ph-current').textContent = `${title}${g.phase ? ' · ' + g.phase : ''}`;
    $('ph-count').textContent = `${s.players.length} players joined${getPid() ? '' : ' · Join in Play to take your seat'}`;
    const key = `${s.game}:${g.phase}:${s.phoneMode}`;
    if (formKey !== key) { $('ph-settings').innerHTML = settings(s); formKey = key; }
    const html = controls(s);
    if ($('ph-actions').innerHTML !== html) $('ph-actions').innerHTML = html;
    const roster = s.players.map(p=>`<div class="ph-player"><span>${esc(p.name)}${p.pid===getPid() ? ' (you)' : ''}${p.connected===false ? ' · reconnecting' : ''}</span>${s.game==='mafia' && g.phase==='lobby' ? button('mafiaSetModerator',g.moderatorPid===p.pid ? 'Moderator ✓' : 'Make moderator',{pid:p.pid}) : ''}${p.pid!==getPid() ? button('kick','Remove',{pid:p.pid},true) : ''}</div>`).join('');
    if ($('ph-roster').innerHTML !== roster) $('ph-roster').innerHTML = roster;
    if (document.activeElement !== $('ph-game')) $('ph-game').value = s.game || '';
  }
  async function run(work) {
    if (busy) return;
    busy = true; $('ph-error').textContent = '';
    $('ph-panel').setAttribute('aria-busy', 'true');
    $('ph-panel').querySelectorAll('button, input, select').forEach(el => el.disabled = true);
    try { await work(); await refresh(); }
    catch(e) { $('ph-error').textContent = e.message || 'Connection lost. Try again.'; }
    finally { busy = false; $('ph-panel').removeAttribute('aria-busy'); $('ph-panel').querySelectorAll('button, input, select').forEach(el => el.disabled = false); }
  }
  async function refresh() {
    const id = getPid();
    update(await request('/state' + (id ? '?pid=' + id : '')));
  }
  async function init(pidGetter) {
    if (!enabled) return null;
    getPid = pidGetter;
    document.body.classList.add('has-phone-host');
    const shell = document.createElement('div'); shell.className = 'phone-host-shell';
    shell.innerHTML = `<div id="ph-auth" class="ph-auth"><form id="ph-unlock"><h1>Host & play</h1><p>Unlock with the host password, then join as a player.</p><input id="ph-password" type="password" autocomplete="current-password" aria-label="Host password" placeholder="Host password"><button>Unlock</button><p id="ph-auth-error" role="alert"></p><a href="/host">Open shared-screen host</a></form></div>
      <nav class="ph-tabs" aria-label="Phone mode"><button id="ph-play" type="button" aria-pressed="true">Play</button><button id="ph-host" type="button" aria-pressed="false">Host controls</button></nav>
      <section id="ph-panel" class="ph-panel" aria-labelledby="ph-title" hidden><div class="ph-content">
        <h2 id="ph-title" tabindex="-1">Your game night</h2><p id="ph-count"></p>
        <p id="ph-error" role="alert"></p>
        <details><summary>Invite players</summary><p>Share this player link:</p><input id="ph-link" readonly aria-label="Player join link"><button type="button" id="ph-copy">Copy player link</button><p>Room <strong id="ph-code"></strong></p><img id="ph-qr" alt="Scan to join as a player" width="180" height="180"></details>
        <label class="ph-game-label">Game<select id="ph-game"><option value="">Game menu</option></select></label>
        <h3 id="ph-current"></h3><div id="ph-actions" class="ph-actions"></div>
        <details open><summary>Game settings</summary><div id="ph-settings"></div></details>
        <details><summary>Players & moderator</summary><div id="ph-roster"></div></details>
        <a href="/host" class="ph-screen-link">Open shared-screen view</a>
      </div></section>`;
    document.body.prepend(shell);
    $('ph-play').onclick=()=>tab(false); $('ph-host').onclick=()=>tab(true);
    let auth = await request('/amihost');
    if (!auth.host) {
      await new Promise(resolve=>{
        $('ph-unlock').onsubmit=async e=>{ e.preventDefault(); try { await request('/unlock',{password:$('ph-password').value}); $('ph-password').value=''; resolve(); } catch(err) { $('ph-auth-error').textContent=err.message; } };
      });
      auth=await request('/amihost');
    }
    // A mode change during an active round is explicit; existing play is never reset.
    while (true) {
      try { await action('hostMode',{mode:'phone'}); break; }
      catch(e) {
        $('ph-auth-error').textContent=e.message;
        $('ph-password').hidden=true; $('ph-unlock').querySelector('button').textContent='Try enabling phone mode again';
        await new Promise(resolve=>{ $('ph-unlock').onsubmit=e=>{e.preventDefault();resolve()}; });
      }
    }
    const clean = new URL(location.href); clean.searchParams.delete('host'); clean.searchParams.set('code',auth.code); history.replaceState(null,'',clean.pathname+clean.search);
    $('ph-auth').hidden=true;
    games=(await request('/games')).games.filter(g=>g.status==='ready');
    $('ph-game').insertAdjacentHTML('beforeend',games.map(g=>`<option value="${g.id}">${esc(g.id==='wii-sandbox' ? 'Target Practice (touch)' : g.name)}</option>`).join(''));
    const share = async()=>{try{const w=await request('/whoami');$('ph-link').value=w.playUrl;$('ph-code').textContent=w.code;const image=$('ph-qr');if(image.dataset.url!==w.playUrl){image.src='/phoneQR.png?v='+Date.now();image.dataset.url=w.playUrl}}catch(e){$('ph-error').textContent=e.message}};
    await share(); setInterval(share,10000);
    $('ph-copy').onclick=()=>run(async()=>{try{await navigator.clipboard.writeText($('ph-link').value);$('ph-copy').textContent='Copied!'}catch(e){$('ph-link').focus();$('ph-link').select();$('ph-error').textContent='Select and copy the player link above.'}});
    $('ph-game').onchange=()=>run(async()=>{const game=$('ph-game').value||null;if(latest?.game && game!==latest.game && !confirm('Changing games ends the current game. Continue?')){$('ph-game').value=latest.game;return}await request('/select',{game});formKey=''});
    $('ph-panel').addEventListener('click',e=>{const b=e.target.closest('[data-command]');if(!b)return;if(b.dataset.confirm && !confirm(b.textContent+'?'))return;const {action:name,...extra}=JSON.parse(b.dataset.command);run(()=>action(name,extra))});
    $('ph-panel').addEventListener('submit',e=>{const f=e.target.closest('[data-setting]');if(!f)return;e.preventDefault();const input=f.elements.value;run(async()=>{await action(f.dataset.setting,{key:f.dataset.key,value:input.type==='number'?Number(input.value):input.value});formKey=''})});
    $('ph-panel').addEventListener('change',e=>{const input=e.target.closest('[data-toggle]');if(input)run(()=>action(input.dataset.toggle,{key:input.dataset.key,value:input.checked}))});
    await refresh();
    setInterval(()=>{if(!getPid())refresh().catch(()=>{})},1000);
    return auth.code;
  }
  async function boot(pidGetter) {
    try { return await init(pidGetter); }
    catch (e) {
      if (!enabled) return null;
      const gate = $('ph-auth');
      if (gate) {
        gate.hidden = false;
        $('ph-auth-error').textContent = 'Could not connect to the game. Check your connection and retry.';
        $('ph-password').hidden = true;
        $('ph-unlock').querySelector('button').textContent = 'Retry connection';
        $('ph-unlock').onsubmit = event => { event.preventDefault(); location.reload(); };
      }
      // Keep player setup pending until this host page can authenticate.
      return new Promise(() => {});
    }
  }
  return {init: boot,update};
})();
