/* Shared public table and private phone hand for BS. */
globalThis.BSView = (() => {
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const selected = new Set(); let key = '', busy = false, error = '';
  const face = c => `<span>${esc(c.rank)}</span><strong>${c.suit}</strong>`;
  function render(root, s, pid, send, host = false) {
    const g = s.bs; if (!g || !root) return;
    const nextKey = `${g.phase}:${g.revision}:${g.turnPid}:${g.hand.map(c=>c.id).join(',')}`;
    if (key !== nextKey) { selected.clear(); key = nextKey; error = ''; }
    const name = id => esc(s.players.find(p=>p.pid===id)?.name || 'Player');
    const mine = g.phase === 'playing' && g.turnPid === pid;
    let status = g.phase === 'lobby' ? 'Ready to bluff?' : `${name(g.turnPid)}’s turn · Claim ${esc(g.rank)}s`;
    if (g.phase === 'challenge') status = `${name(g.last.pid)} claims ${g.last.count} ${esc(g.last.rank)}${g.last.count===1?'':'s'}. Believe it?`;
    if (g.phase === 'reveal') status = g.reveal.liar ? 'Caught bluffing!' : 'They told the truth!';
    if (g.phase === 'gameover') status = g.winner ? `🏆 ${name(g.winner)} wins!` : esc(g.message || 'Game ended');
    const seconds = Math.max(0, Math.ceil((g.deadline*1000-Date.now())/1000));
    const html = `<h1>🃏 BS</h1><p class="bs-kicker">TRUST NO HAND.</p><div class="bs-table"><h2>${status}</h2><p>${g.pileCount} cards in the pile${g.deadline ? ` · ${seconds}s` : ''}</p>
      ${g.reveal && ['reveal','gameover'].includes(g.phase) ? `<div class="bs-cards">${g.reveal.cards.map(c=>`<div class="bs-card ${['♥','♦'].includes(c.suit)?'bs-red':''}">${face(c)}</div>`).join('')}</div><p>${name(g.reveal.loser)} picks up ${g.reveal.count} card${g.reveal.count===1?'':'s'}.</p>` : ''}
      ${g.phase==='challenge' && g.inGame && g.last.pid!==pid ? '<button data-bs="bsCall" class="bs-call">Call BS!</button>' : ''}
      ${g.phase==='challenge' ? '<p>The next turn starts after everyone has had a chance to challenge.</p>' : ''}</div>
      <div class="bs-roster">${g.roster.map(p=>`<div class="${p.pid===g.turnPid?'bs-active':''}"><b>${name(p.pid)}${p.pid===pid?' (you)':''}</b><span>${p.count} cards${p.connected?'':' · reconnecting'}</span></div>`).join('')}</div>
      ${host ? `<div class="bs-controls">${['lobby','gameover'].includes(g.phase)?'<button data-bs="bsStart">Deal & start</button>':''}${g.phase==='lobby'?`<label>Decks <select aria-label="Number of decks" id="bs-decks"><option value="1" ${g.decks===1?'selected':''}>1 · 52 cards</option><option value="2" ${g.decks===2?'selected':''}>2 · 104 cards</option></select></label>`:'<button data-bs="bsLobby">Return to lobby</button>'}</div>` : ''}
      ${g.inGame && g.phase!=='gameover' ? `<h2>Your hand · ${g.hand.length}</h2><p>${mine?`Choose 1–${4*g.decks} cards. You will claim they are ${esc(g.rank)}s.`:'Your cards are private. Select cards when it is your turn.'}</p><div class="bs-cards">${g.hand.map(c=>`<button class="bs-card ${['♥','♦'].includes(c.suit)?'bs-red':''}" data-card="${c.id}" aria-label="${esc(c.rank)} of ${c.suit}" aria-pressed="${selected.has(c.id)}" ${mine?'':'disabled'}>${face(c)}</button>`).join('')}</div>${mine?`<button data-bs="bsPlay" ${selected.size?'':'disabled'}>Play ${selected.size || ''} face-down · Claim ${esc(g.rank)}s</button>`:''}` : !host && g.phase!=='lobby' && !g.inGame ? '<p>You’re watching this deal. Join the next game.</p>' : ''}
      <p role="alert" class="bs-error">${esc(error)}</p><details><summary>How to play</summary><p>2–10 players. Empty your hand to win. Play face-down in turn: Aces, Twos, through Kings, then repeat. You may lie! Any other player has 8 seconds to call BS. A liar takes the whole pile; a mistaken challenger takes it instead. Only the challenged cards are revealed. The next player continues with the next rank.</p><p>The Ace of Spades starts. The host chooses one or two decks (two decks suit larger groups). New arrivals watch until the next deal. Even your last card can be challenged.</p></details>`;
    if (root.innerHTML !== html) root.innerHTML = html;
    root.onclick = async e => {
      const card = e.target.closest('[data-card]');
      if (card && mine && !busy) { const id=+card.dataset.card; if(selected.has(id))selected.delete(id);else if(selected.size<4*g.decks)selected.add(id);render(root,s,pid,send,host);return; }
      const b = e.target.closest('[data-bs]'); if(!b || busy || b.disabled)return;
      if(b.dataset.bs==='bsLobby' && !confirm('End this deal and return to the lobby?'))return;
      await run(b.dataset.bs, {cards:[...selected],revision:g.revision});
    };
    const decks = root.querySelector('#bs-decks'); if(decks)decks.onchange=()=>run('bsSet',{value:+decks.value});
    async function run(action, extra) {
      busy=true;
      try { const r=await send(action,extra); if(!r?.ok)error=r?.error || 'Connection lost. Please retry.'; }
      catch(e){error='Connection lost. Please retry.';}
      finally{busy=false;render(root,s,pid,send,host);}
    }
  }
  return {render};
})();
