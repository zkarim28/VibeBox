/* Mini golf: top-down 2D course on a canvas, for phones and the shared screen.
   The server simulates every shot; this file only draws the course, animates
   the returned path, and turns a drag (or the keyboard) into angle + power. */
globalThis.GolfView = (() => {
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const FORMAT = {solo: 'Solo (every player for themselves)', best: 'Teams: best ball', alternate: 'Teams: alternate shot'};
  const FORMAT_SHORT = {solo: 'Solo', best: 'Best ball', alternate: 'Alternate shot'};
  const PACE = {turns: 'Take turns (official order)', together: 'Everyone at once (fast)'};
  const ORDER = {far: 'Farthest from the cup goes next', near: 'Closest to the cup goes next'};
  const AMOUNT = {few: 'A few', some: 'Some', lots: 'Lots'};
  const OBS_MODE = {chosen: 'Only the ones ticked below', surprise: 'Surprise me (random every hole)'};
  const MAX_DRAG = 170;      // px of pull for a full-power putt
  const THEME_LABEL = {random: '🎲 Random world', pirate: '🏴‍☠️ Pirate Cove', neon: '🌈 Neon Nights', candy: '🍭 Sugar Rush',
    jungle: '🌴 Lost Jungle', space: '🚀 Lunar Links', mix: '🌍 Grand Tour (a new world every hole)'};
  // Look of each world. pattern: stripes | checker; deco: what's drawn in the "sea".
  const THEME = {
    pirate: {sea: ['#0f3d57', '#0a2638'], deco: 'waves', grass: ['#3fae54', '#4bbd5f'], pattern: 'stripes', tee: '#7fd98a', rail: ['#6b4423', '#a8743f'], cup: '#1b2b1e'},
    neon: {sea: ['#140a2a', '#05030d'], deco: 'grid', grass: ['#3b2a9e', '#c2257f', '#1f8fb8', '#e0a325'], pattern: 'checker', tee: '#f0f', rail: ['#ff3ea5', '#ffe0f2'], cup: '#000', glow: '#ff3ea5'},
    candy: {sea: ['#ffd6e8', '#ffadd0'], deco: 'sprinkles', grass: ['#fff0f6', '#ffd3e6'], pattern: 'stripes', tee: '#ffe066', rail: ['#ffffff', '#e64980'], cup: '#5c2a3a', cane: true},
    jungle: {sea: ['#0f4020', '#06210f'], deco: 'leaves', grass: ['#5a8f29', '#69a332'], pattern: 'stripes', tee: '#a3d977', rail: ['#4e342e', '#8d6e63'], cup: '#1b1b0f'},
    space: {sea: ['#0b0b2b', '#000010'], deco: 'stars', grass: ['#2d2a5a', '#38356e'], pattern: 'stripes', tee: '#6c63ff', rail: ['#00bcd4', '#b2f5ff'], cup: '#000', glow: '#00e5ff'},
  };
  const CUP_COLORS = ['#ff5d73', '#4cc9f0', '#ffc94d', '#5be38c'];
  const TRIVIA_EVERY = {0: 'Off', 1: 'After every hole', 2: 'After every 2nd hole', 3: 'After every 3rd hole'};
  const TOAST_KINDS = new Set(['answer', 'in', 'water', 'sand', 'kraken', 'oob', 'picked', 'lip', 'nudge', 'portal', 'tramp', 'spin']);

  let root = null, s = null, g = null, pid = null, send = null, host = false;
  let built = null, offset = 0, raf = 0, canvas = null, ctx = null, k = 4, dpr = 1, vb = {x: 0, y: 0, w: 80, h: 120};
  let staticKey = '', staticCanvas = null, drag = null, aim = null, kbAim = null, err = '', busy = false;
  let shownEvents = new Set(), toastTimer = 0, lastShotSeen = {}, popups = [];

  const now = () => (Date.now() + offset) / 1000;

  function skeleton() {
    root.innerHTML = `<div class="mg-head"><h1>⛳ Mini Golf</h1><p class="mg-sub"></p></div>
      <div class="mg-qbar" hidden></div>
      <div class="mg-main">
        <div class="mg-stage" hidden><canvas class="mg-canvas" aria-label="Mini golf hole, top-down view"></canvas>
          <div class="mg-toast" role="status" hidden></div><div class="mg-power" hidden><span></span></div></div>
        <aside class="mg-side"></aside>
      </div>
      <p class="mg-status" role="status"></p>
      <p class="mg-error" role="alert"></p>
      <div class="mg-info"></div>`;
    built = root;
    canvas = root.querySelector('.mg-canvas');
    ctx = canvas.getContext('2d');
    canvas.addEventListener('pointerdown', onDown);
    canvas.addEventListener('pointermove', onMove);
    canvas.addEventListener('pointerup', onUp);
    canvas.addEventListener('pointercancel', () => { drag = null; aim = null; });
    root.addEventListener('click', onClick);
    root.addEventListener('change', onChange);
  }

  // Only touch the DOM when our own markup changes (comparing against
  // innerHTML never matches once the browser re-serialises it, which used to
  // rebuild panels every poll and snap dropdowns shut). Never rebuild under a
  // field that's being used, and keep <details> open/closed as the user left them.
  function setHTML(node, html) {
    if (node._mgHTML === html) return;
    const ae = document.activeElement;
    if (ae && node.contains(ae) && ae.matches('select, textarea, input:not([type=checkbox])')) return;
    const open = new Set([...node.querySelectorAll('details')].filter(d => d.open).map(d => d.className));
    const shut = new Set([...node.querySelectorAll('details')].filter(d => !d.open).map(d => d.className));
    node.innerHTML = html; node._mgHTML = html;
    node.querySelectorAll('details').forEach(d => { if (open.has(d.className)) d.open = true; else if (shut.has(d.className)) d.open = false; });
  }

  // isHost = the shared laptop/TV display (draws big, shows host controls)
  function render(el, state, myPid, sender, isHost = false) {
    const gs = state.minigolf; if (!gs || !el) return;
    root = el; s = state; g = gs; pid = myPid; send = sender; host = isHost;
    if (built !== root) skeleton();
    offset = g.serverNow * 1000 - Date.now();
    root.classList.toggle('mg-host', host);
    const playing = !!g.hole && g.phase !== 'lobby';
    // between holes, phones swap the course for the trivia card + leaderboards
    root.querySelector('.mg-stage').hidden = !playing || (g.phase === 'hole' && !host);
    const qb = root.querySelector('.mg-qbar');
    qb.hidden = !g.trivia;
    setHTML(qb, g.trivia ? qbar() : '');
    setHTML(root.querySelector('.mg-sub'), sub());
    setHTML(root.querySelector('.mg-side'), playing ? side() : '');
    root.querySelector('.mg-status').textContent = playing && !host ? (g.me && g.me.status) || '' : '';
    root.querySelector('.mg-error').textContent = err;
    setHTML(root.querySelector('.mg-info'), info());
    for (const sd of g.sides || []) if (sd.shot && sd.shot.id !== lastShotSeen[sd.id]) lastShotSeen[sd.id] = sd.shot.id;
    if (playing) { size(); loop(); }
  }

  function sub() {
    if (g.phase === 'lobby') return `${FORMAT_SHORT[g.cfg.format]} · ${g.cfg.holes} holes · stroke limit ${g.cfg.limit}`;
    if (!g.hole) return '';
    const h = g.hole;
    const left = g.nextAt ? Math.max(0, Math.ceil(g.nextAt - now())) : 0;
    if (g.phase === 'done') return `${esc(g.course)} · final scores`;
    if (g.phase === 'trivia') return `🧠 Bonus trivia hole · ${Math.max(0, Math.ceil(g.trivia.endsAt - now()))}s left`;
    if (g.phase === 'treveal') return `🧠 Trivia answer · next hole in ${left}s`;
    return `${esc(g.course)} · Hole <b>${h.n}</b> of ${g.holeCount} · Par ${h.par}${g.phase === 'hole' ? ` · next hole in ${left}s` : ''}`;
  }

  // ------------------------------------------------------------- drawing ---
  // The view is cropped to the hole's fairway (plus a little sea) so narrow
  // holes fill the screen instead of floating in water.
  function viewBox() {
    const C = g.consts, xs = g.hole.cells.map(c => c[0]), ys = g.hole.cells.map(c => c[1]), pad = 5;
    const x0 = Math.max(0, Math.min(...xs) * C.C - pad), y0 = Math.max(0, Math.min(...ys) * C.C - pad);
    const x1 = Math.min(C.W, (Math.max(...xs) + 1) * C.C + pad), y1 = Math.min(C.H, (Math.max(...ys) + 1) * C.C + pad);
    return {x: x0, y: y0, w: x1 - x0, h: y1 - y0};
  }

  function size() {
    const stage = root.querySelector('.mg-stage');
    vb = viewBox();
    const maxW = stage.clientWidth || 360;
    const qb = root.querySelector('.mg-qbar');
    const above = qb && !qb.hidden ? qb.offsetHeight + 12 : 0;      // leave room for a trivia question
    const maxH = host ? window.innerHeight * 0.78 - above : Math.max(280, window.innerHeight - 190 - above);
    const scale = Math.min(maxW / vb.w, maxH / vb.h, host ? 14 : 9);
    dpr = window.devicePixelRatio || 1;
    const w = Math.floor(vb.w * scale), h = Math.floor(vb.h * scale);
    if (canvas.width !== Math.floor(w * dpr) || canvas.height !== Math.floor(h * dpr)) {
      canvas.width = Math.floor(w * dpr); canvas.height = Math.floor(h * dpr);
      canvas.style.width = w + 'px'; canvas.style.height = h + 'px';
      staticKey = '';
    }
    k = scale * dpr;
  }

  function loop() {
    if (raf) return;
    const step = () => {
      raf = 0;
      if (!root || !g || root.offsetParent === null || !g.hole || g.phase === 'lobby' || root.querySelector('.mg-stage').hidden) return;
      draw();
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
  }

  function drawStatic() {
    const h = g.hole, C = g.consts;
    const key = `${g.course}:${h.key || h.n}:${canvas.width}x${canvas.height}:${g.epoch}:${vb.x},${vb.y}`;
    if (key === staticKey && staticCanvas) return staticCanvas;
    staticKey = key;
    staticCanvas = staticCanvas || document.createElement('canvas');
    staticCanvas.width = canvas.width; staticCanvas.height = canvas.height;
    const c = staticCanvas.getContext('2d');
    c.setTransform(k, 0, 0, k, -vb.x * k, -vb.y * k);
    const th = THEME[h.theme] || THEME.pirate;
    // the "sea" around the course
    const sea = c.createLinearGradient(0, 0, 0, C.H);
    sea.addColorStop(0, th.sea[0]); sea.addColorStop(1, th.sea[1]);
    c.fillStyle = sea; c.fillRect(0, 0, C.W, C.H);
    let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    if (th.deco === 'waves') {
      c.strokeStyle = 'rgba(255,255,255,.06)'; c.lineWidth = .4;
      for (let y = 4; y < C.H; y += 7) for (let x = (y % 14 ? 0 : 3.5); x < C.W; x += 7) { c.beginPath(); c.arc(x, y, 1.6, Math.PI * 1.1, Math.PI * 1.9); c.stroke(); }
    } else if (th.deco === 'grid') {
      c.strokeStyle = 'rgba(255,62,165,.16)'; c.lineWidth = .25;
      for (let x = 0; x <= C.W; x += 5) { c.beginPath(); c.moveTo(x, 0); c.lineTo(x, C.H); c.stroke(); }
      for (let y = 0; y <= C.H; y += 5) { c.beginPath(); c.moveTo(0, y); c.lineTo(C.W, y); c.stroke(); }
    } else if (th.deco === 'sprinkles') {
      const cols = ['#ff6b6b', '#4dabf7', '#ffd43b', '#69db7c', '#b197fc', '#fff'];
      for (let i = 0; i < 260; i++) { c.save(); c.translate(rnd() * C.W, rnd() * C.H); c.rotate(rnd() * 3.14); c.fillStyle = cols[i % cols.length]; c.fillRect(-.7, -.2, 1.4, .4); c.restore(); }
    } else if (th.deco === 'leaves') {
      for (let i = 0; i < 90; i++) { c.save(); c.translate(rnd() * C.W, rnd() * C.H); c.rotate(rnd() * 6.28); c.fillStyle = `rgba(${40 + rnd() * 40 | 0},${110 + rnd() * 60 | 0},40,.55)`; c.beginPath(); c.ellipse(0, 0, 2.6, 1, 0, 0, Math.PI * 2); c.fill(); c.restore(); }
    } else if (th.deco === 'stars') {
      for (let i = 0; i < 220; i++) { c.fillStyle = `rgba(255,255,255,${.25 + rnd() * .7})`; const r = rnd() * .35 + .08; c.beginPath(); c.arc(rnd() * C.W, rnd() * C.H, r, 0, Math.PI * 2); c.fill(); }
    }
    // fairway
    for (const [x, y] of h.cells) {
      if (th.pattern === 'checker') {
        for (let i = 0; i < 2; i++) for (let j = 0; j < 2; j++) { c.fillStyle = th.grass[(x * 2 + i + (y * 2 + j) * 3) % th.grass.length]; c.fillRect(x * C.C + i * C.C / 2, y * C.C + j * C.C / 2, C.C / 2, C.C / 2); }
        c.fillStyle = 'rgba(0,0,0,.18)'; c.fillRect(x * C.C, y * C.C, C.C, C.C);
      } else {
        c.fillStyle = th.grass[0]; c.fillRect(x * C.C, y * C.C, C.C, C.C);
        c.fillStyle = th.grass[1]; c.fillRect(x * C.C, y * C.C, C.C, C.C / 2);
      }
    }
    // tee mat
    c.fillStyle = th.tee; roundRect(c, h.tee[0] - 4, h.tee[1] - 2.5, 8, 5, 1.2); c.fill();
    for (const o of h.obs) drawObstacle(c, o, 0, true);
    // rails
    c.lineCap = 'round';
    if (th.glow) { c.shadowColor = th.glow; c.shadowBlur = 2.5 * k; }
    c.strokeStyle = th.rail[0]; c.lineWidth = 1.6;
    for (const [x0, y0, x1, y1] of h.walls) { c.beginPath(); c.moveTo(x0, y0); c.lineTo(x1, y1); c.stroke(); }
    c.shadowBlur = 0;
    c.strokeStyle = th.rail[1]; c.lineWidth = th.cane ? 1.6 : .6;
    if (th.cane) c.setLineDash([1.4, 1.4]);
    for (const [x0, y0, x1, y1] of h.walls) { c.beginPath(); c.moveTo(x0, y0); c.lineTo(x1, y1); c.stroke(); }
    c.setLineDash([]);
    // cup
    for (const [cx, cy] of h.cups || [h.cup]) { c.fillStyle = th.cup; c.beginPath(); c.arc(cx, cy, C.cupR, 0, Math.PI * 2); c.fill(); }
    if (h.cups && g.trivia) {      // answer text under each cup
      h.cups.forEach(([cx, cy], i) => {
        c.font = '700 2.3px system-ui, sans-serif'; c.textAlign = 'center'; c.textBaseline = 'top';
        const words = g.trivia.options[i] || '';
        const lines = []; let line = '';
        for (const w of words.split(' ')) { if ((line + ' ' + w).trim().length > 12 && line) { lines.push(line); line = w; } else line = (line + ' ' + w).trim(); }
        lines.push(line);
        const shown = lines.slice(0, 3), wpx = Math.max(...shown.map(l => c.measureText(l).width)) + 1.6;
        c.fillStyle = 'rgba(10,8,25,.78)'; roundRect(c, cx - wpx / 2, cy + 2.9, wpx, shown.length * 2.6 + 1, .8); c.fill();
        shown.forEach((ln, j) => { c.fillStyle = 'rgba(0,0,0,.55)'; c.fillText(ln, cx + .15, cy + 3.4 + j * 2.6 + .15); c.fillStyle = '#fff'; c.fillText(ln, cx, cy + 3.4 + j * 2.6); });
      });
    }
    c.strokeStyle = '#e8f5e9'; c.lineWidth = .35; c.stroke();
    return staticCanvas;
  }

  function roundRect(c, x, y, w, hh, r) {
    c.beginPath(); c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + hh, r); c.arcTo(x + w, y + hh, x, y + hh, r);
    c.arcTo(x, y + hh, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath();
  }

  function arrow(c, x, y, a, len, color) {
    c.save(); c.translate(x, y); c.rotate(a); c.strokeStyle = color; c.lineWidth = .7; c.lineCap = 'round';
    c.beginPath(); c.moveTo(-len / 2, 0); c.lineTo(len / 2, 0); c.moveTo(len / 2 - 1.6, -1.4); c.lineTo(len / 2, 0); c.lineTo(len / 2 - 1.6, 1.4); c.stroke();
    c.restore();
  }

  function emoji(c, ch, x, y, size) {
    c.font = `${size}px system-ui, "Apple Color Emoji", "Noto Color Emoji", sans-serif`;
    c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText(ch, x, y);
  }

  function drawObstacle(c, o, T, isStatic) {
    switch (o.k) {
      case 'water':
        if (!isStatic) return;
        c.fillStyle = '#1f78b4'; roundRect(c, o.x, o.y, o.w, o.h, 2); c.fill();
        c.strokeStyle = 'rgba(255,255,255,.35)'; c.lineWidth = .35;
        for (let y = o.y + 2; y < o.y + o.h - 1; y += 2.5) { c.beginPath(); for (let x = o.x + 1; x < o.x + o.w - 1; x += .5) c.lineTo(x, y + Math.sin(x * 1.3) * .35); c.stroke(); }
        emoji(c, '🦈', o.x + o.w / 2, o.y + o.h / 2, 3.6); return;
      case 'sand':
        if (!isStatic) return;
        c.fillStyle = '#e9cf8f'; roundRect(c, o.x, o.y, o.w, o.h, 3); c.fill();
        c.fillStyle = 'rgba(150,110,50,.45)';
        for (let i = 0; i < o.w * o.h / 6; i++) { const px = o.x + 1 + ((i * 37) % 97) / 97 * (o.w - 2), py = o.y + 1 + ((i * 61) % 89) / 89 * (o.h - 2); c.fillRect(px, py, .35, .35); }
        return;
      case 'ice':
        if (!isStatic) return;
        c.fillStyle = 'rgba(200,240,255,.75)'; c.fillRect(o.x, o.y, o.w, o.h);
        c.strokeStyle = 'rgba(255,255,255,.9)'; c.lineWidth = .3;
        for (let i = 0; i < 5; i++) { c.beginPath(); c.moveTo(o.x + 2 + i * 4, o.y + 2); c.lineTo(o.x + 5 + i * 4, o.y + 7); c.stroke(); }
        return;
      case 'slope':
        if (!isStatic) return;
        c.fillStyle = 'rgba(20,60,25,.28)'; c.fillRect(o.x, o.y, o.w, o.h);
        for (let i = 0; i < 2; i++) for (let j = 0; j < 2; j++) arrow(c, o.x + 5 + i * 10, o.y + 5 + j * 10, o.a, 5, 'rgba(255,255,255,.55)');
        return;
      case 'boost':
        if (!isStatic) return;
        c.fillStyle = '#ff9f1c'; roundRect(c, o.x, o.y, o.w, o.h, 1); c.fill();
        arrow(c, o.x + o.w / 2 - Math.cos(o.a) * 1.2, o.y + o.h / 2 - Math.sin(o.a) * 1.2, o.a, 3, '#fff');
        arrow(c, o.x + o.w / 2 + Math.cos(o.a) * 1.2, o.y + o.h / 2 + Math.sin(o.a) * 1.2, o.a, 3, '#fff');
        return;
      case 'bumpers':
        if (!isStatic) return;
        for (const [x, y, r] of o.b) {
          c.fillStyle = '#8b4a2b'; c.beginPath(); c.arc(x, y, r, 0, Math.PI * 2); c.fill();
          c.strokeStyle = '#d9a066'; c.lineWidth = .5; c.beginPath(); c.arc(x, y, r * .7, 0, Math.PI * 2); c.stroke();
        }
        return;
      case 'portal':
        if (!isStatic) return;
        for (const [x, y, col] of [[o.ax, o.ay, '#b388ff'], [o.bx, o.by, '#ffd54f']]) {
          c.fillStyle = '#20123a'; c.beginPath(); c.arc(x, y, o.r, 0, Math.PI * 2); c.fill();
          c.strokeStyle = col; c.lineWidth = .7; c.stroke();
        }
        c.setLineDash([.8, 1.2]); c.strokeStyle = 'rgba(255,255,255,.25)'; c.lineWidth = .3;
        c.beginPath(); c.moveTo(o.ax, o.ay); c.lineTo(o.bx, o.by); c.stroke(); c.setLineDash([]);
        return;
      case 'kraken': {
        if (isStatic) {
          const gr = c.createRadialGradient(o.x, o.y, 1, o.x, o.y, o.r);
          gr.addColorStop(0, '#071b2a'); gr.addColorStop(1, 'rgba(31,120,180,.25)');
          c.fillStyle = gr; c.beginPath(); c.arc(o.x, o.y, o.r, 0, Math.PI * 2); c.fill();
          return;
        }
        c.strokeStyle = 'rgba(180,230,255,.55)'; c.lineWidth = .35;
        for (let arm = 0; arm < 3; arm++) {
          c.beginPath();
          for (let t = 0; t < 1; t += .04) { const a = arm * 2.09 + t * 5 + T * 2.5, r = 1.5 + t * (o.r - 1.5); c.lineTo(o.x + Math.cos(a) * r, o.y + Math.sin(a) * r); }
          c.stroke();
        }
        emoji(c, '🐙', o.x, o.y, 3.4); return;
      }
      case 'windmill': {
        if (isStatic) {
          c.strokeStyle = 'rgba(0,0,0,.18)'; c.lineWidth = .4; c.beginPath(); c.arc(o.x, o.y, o.L, 0, Math.PI * 2); c.stroke();
          return;
        }
        const th = o.ph + o.w * T;
        c.lineCap = 'round';
        for (const a of [th, th + Math.PI / 2]) {
          const ux = Math.cos(a) * o.L, uy = Math.sin(a) * o.L;
          c.strokeStyle = '#5d4037'; c.lineWidth = 1.5; c.beginPath(); c.moveTo(o.x - ux, o.y - uy); c.lineTo(o.x + ux, o.y + uy); c.stroke();
          c.strokeStyle = '#f5f5f5'; c.lineWidth = .7; c.beginPath(); c.moveTo(o.x - ux * .95, o.y - uy * .95); c.lineTo(o.x + ux * .95, o.y + uy * .95); c.stroke();
        }
        c.fillStyle = '#c62828'; c.beginPath(); c.arc(o.x, o.y, 1.6, 0, Math.PI * 2); c.fill();
        return;
      }
      case 'tramp':
        if (!isStatic) return;
        c.fillStyle = '#263238'; c.beginPath(); c.arc(o.x, o.y, o.r + .6, 0, Math.PI * 2); c.fill();
        c.fillStyle = '#29b6f6'; c.beginPath(); c.arc(o.x, o.y, o.r, 0, Math.PI * 2); c.fill();
        c.strokeStyle = 'rgba(255,255,255,.7)'; c.lineWidth = .25;
        for (let a = 0; a < 12; a++) { c.beginPath(); c.moveTo(o.x + Math.cos(a / 12 * 6.28) * (o.r - .2), o.y + Math.sin(a / 12 * 6.28) * (o.r - .2)); c.lineTo(o.x + Math.cos(a / 12 * 6.28) * (o.r + .5), o.y + Math.sin(a / 12 * 6.28) * (o.r + .5)); c.stroke(); }
        c.fillStyle = '#fff'; c.font = '700 2.2px system-ui, sans-serif'; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText('BOING', o.x, o.y);
        return;
      case 'spin': {
        if (isStatic) {
          c.fillStyle = '#3e2723'; c.beginPath(); c.arc(o.x, o.y, o.r + .7, 0, Math.PI * 2); c.fill();
          return;
        }
        const a0 = o.ph + o.w * T, segs = 12;
        for (let i = 0; i < segs; i++) {
          c.fillStyle = i === 0 ? '#2e7d32' : i % 2 ? '#c62828' : '#212121';
          c.beginPath(); c.moveTo(o.x, o.y); c.arc(o.x, o.y, o.r, a0 + i / segs * 6.283, a0 + (i + 1) / segs * 6.283); c.fill();
        }
        c.fillStyle = '#ffd54f'; c.beginPath(); c.arc(o.x, o.y, 1.1, 0, Math.PI * 2); c.fill();
        arrow(c, o.x + Math.cos(a0) * o.r * .45, o.y + Math.sin(a0) * o.r * .45, a0, o.r * .9, '#fff');
        return;
      }
      case 'gate': {
        if (isStatic) {
          c.fillStyle = 'rgba(0,0,0,.15)';
          const half = o.amp + o.len / 2;
          if (o.ax) c.fillRect(o.x - half, o.y - .5, half * 2, 1); else c.fillRect(o.x - .5, o.y - half, 1, half * 2);
          return;
        }
        const sx = o.ax ? 1 : 0, sy = o.ax ? 0 : 1, off = o.amp * Math.sin(o.w * T + o.ph);
        const cx = o.x + sx * off, cy = o.y + sy * off, hl = o.len / 2;
        c.lineCap = 'round'; c.strokeStyle = '#3e2723'; c.lineWidth = 1.8;
        c.beginPath(); c.moveTo(cx - sx * hl, cy - sy * hl); c.lineTo(cx + sx * hl, cy + sy * hl); c.stroke();
        c.strokeStyle = '#8d6e63'; c.lineWidth = .6; c.stroke();
        return;
      }
    }
  }

  function ballAt(sd, T) {
    const sh = sd.shot;
    if (!sh || !sh.frames || T >= sh.t0 + sh.dur) return sd.ball;
    const fr = sh.frames, f = Math.max(0, (T - sh.t0) * g.consts.fps), i = Math.floor(f);
    if (i >= fr.length - 1) return fr[fr.length - 1];
    if ((sh.cuts || []).includes(i + 1)) return f - i < .5 ? fr[i] : fr[i + 1];
    const a = fr[i], b = fr[i + 1], t = f - i;
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, (a[2] || 0) + ((b[2] || 0) - (a[2] || 0)) * t];
  }

  function draw() {
    const C = g.consts, h = g.hole, T = now(), clock = T - g.epoch;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.drawImage(drawStatic(), 0, 0);
    ctx.setTransform(k, 0, 0, k, -vb.x * k, -vb.y * k);
    for (const o of h.obs) drawObstacle(ctx, o, clock, false);
    // coins (a coin vanishes the moment a ball rolls over it on screen)
    const taken = g.coinsTaken || {};
    (h.coins || []).forEach(([x, y], i) => {
      if (taken[i] != null && T >= taken[i]) return;
      const sq = Math.abs(Math.cos(T * 2.6 + i));
      ctx.fillStyle = '#b8860b'; ctx.beginPath(); ctx.ellipse(x + .15, y + .2, 1.5 * Math.max(.18, sq), 1.5, 0, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = '#ffd54f'; ctx.beginPath(); ctx.ellipse(x, y, 1.4 * Math.max(.15, sq), 1.4, 0, 0, Math.PI * 2); ctx.fill();
      if (sq > .5) { ctx.fillStyle = '#b8860b'; ctx.font = '800 1.6px system-ui, sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText('$', x, y + .05); }
    });
    // flag (lifted out while a ball is close, like a caddie would)
    if (h.cups) {                  // trivia: a lettered flag per answer; the right one glows at the reveal
      h.cups.forEach(([cx, cy], i) => {
        if (g.trivia && g.trivia.correct === i) {
          ctx.strokeStyle = `rgba(105,240,174,${.5 + Math.sin(T * 6) * .4})`; ctx.lineWidth = 1;
          ctx.beginPath(); ctx.arc(cx, cy, C.cupR + 1.6, 0, Math.PI * 2); ctx.stroke();
        }
        ctx.strokeStyle = '#eee'; ctx.lineWidth = .35; ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx, cy - 7); ctx.stroke();
        ctx.fillStyle = CUP_COLORS[i % 4]; ctx.beginPath(); ctx.moveTo(cx, cy - 7); ctx.lineTo(cx + 5, cy - 5.6); ctx.lineTo(cx, cy - 4.2); ctx.fill();
        ctx.fillStyle = '#111'; ctx.font = '800 2px system-ui, sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText('ABCD'[i], cx + 1.8, cy - 5.6);
      });
    }
    const near = h.cups || (g.sides || []).some(sd => { const b = ballAt(sd, T); return Math.hypot(b[0] - h.cup[0], b[1] - h.cup[1]) < 8 && sd.state !== 'in'; });
    if (!near) {
      ctx.strokeStyle = '#eee'; ctx.lineWidth = .35; ctx.beginPath(); ctx.moveTo(h.cup[0], h.cup[1]); ctx.lineTo(h.cup[0], h.cup[1] - 9); ctx.stroke();
      ctx.fillStyle = '#e53935'; ctx.beginPath(); ctx.moveTo(h.cup[0], h.cup[1] - 9);
      ctx.lineTo(h.cup[0] + 5 + Math.sin(T * 3) * .4, h.cup[1] - 7.6); ctx.lineTo(h.cup[0], h.cup[1] - 6.2); ctx.fill();
    }
    const mine = g.me && g.me.side;
    for (const sd of g.sides || []) {
      const moving = sd.shot && sd.shot.frames && T < sd.shot.t0 + sd.shot.dur;
      if (sd.state === 'in' && !moving) continue;
      if (sd.state === 'picked' && !moving) continue;
      const [x, y, z = 0] = ballAt(sd, T);
      const grow = 1 + z * 0.09;            // higher = closer to you = bigger
      if (sd.id === mine && g.me.canShoot) {
        ctx.strokeStyle = 'rgba(255,255,255,.8)'; ctx.lineWidth = .4;
        ctx.beginPath(); ctx.arc(x, y, C.R * 2.2 + Math.sin(T * 5) * .5, 0, Math.PI * 2); ctx.stroke();
      }
      ctx.fillStyle = `rgba(0,0,0,${Math.max(.08, .25 - z * .012)})`;
      ctx.beginPath(); ctx.arc(x + .3 + z * .35, y + .4 + z * .5, C.R * 1.25 * Math.max(.6, 1 - z * .02), 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(x, y, C.R * 1.25 * grow, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = sd.color; ctx.lineWidth = .6 * grow; ctx.stroke();
      if (z > .5) { ctx.fillStyle = 'rgba(255,255,255,.6)'; ctx.beginPath(); ctx.arc(x - .4 * grow, y - .4 * grow, .45 * grow, 0, Math.PI * 2); ctx.fill(); }
      if (host || sd.id !== mine) {
        ctx.font = '600 2.6px system-ui, sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
        ctx.fillStyle = 'rgba(0,0,0,.55)'; ctx.fillText(sd.name.slice(0, 10), x + .15, y - 1.8 + .15);
        ctx.fillStyle = sd.color; ctx.fillText(sd.name.slice(0, 10), x, y - 1.8);
      }
      if (moving) eventsFor(sd, T);
    }
    // floating pinball scores
    popups = popups.filter(p => T - p.born < 1.4);
    for (const p of popups) {
      const age = T - p.born;
      ctx.globalAlpha = Math.max(0, 1 - age / 1.4);
      ctx.font = `800 ${p.big ? 4 : 3}px system-ui, sans-serif`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillStyle = 'rgba(0,0,0,.6)'; ctx.fillText(p.text, p.x + .2, p.y - age * 7 + .2);
      ctx.fillStyle = p.color; ctx.fillText(p.text, p.x, p.y - age * 7);
      ctx.globalAlpha = 1;
    }
    // aim
    const a = aim || kbAim;
    const meSide = mine && (g.sides || []).find(sd => sd.id === mine);
    if (a && meSide && g.me.canShoot) {
      const [x, y] = meSide.ball, len = 5 + a.power * 32;
      ctx.setLineDash([1.2, 1]); ctx.strokeStyle = 'rgba(255,255,255,.9)'; ctx.lineWidth = .5;
      ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + Math.cos(a.angle) * len, y + Math.sin(a.angle) * len); ctx.stroke(); ctx.setLineDash([]);
      const hue = 120 - 120 * a.power;
      ctx.fillStyle = `hsl(${hue} 90% 55%)`;
      ctx.beginPath(); ctx.arc(x + Math.cos(a.angle) * len, y + Math.sin(a.angle) * len, .8, 0, Math.PI * 2); ctx.fill();
    }
    const pw = root.querySelector('.mg-power');
    pw.hidden = !a || !g.me || !g.me.canShoot;
    if (!pw.hidden) { const sp = pw.firstChild; sp.style.width = Math.round(a.power * 100) + '%'; sp.style.background = `hsl(${120 - 120 * a.power} 85% 50%)`; }
    if (['hole', 'trivia', 'treveal'].includes(g.phase)) setHTML(root.querySelector('.mg-sub'), sub());
  }

  function eventsFor(sd, T) {
    for (const [n, e] of (sd.shot.events || []).entries()) {
      const key = sd.shot.id + ':' + n;
      if (shownEvents.has(key) || T < sd.shot.t0 + e.t) continue;
      shownEvents.add(key);
      if (e.pts) popups.push({x: e.x ?? sd.ball[0], y: (e.y ?? sd.ball[1]) - 2, born: T, big: e.k === 'in' || e.mult >= 3,
        color: e.k === 'coin' ? '#ffd54f' : e.k === 'in' ? '#69f0ae' : '#ff80ab', text: `+${e.pts}${e.mult > 1 ? ' ×' + e.mult : ''}`});
      if (e.text && TOAST_KINDS.has(e.k)) toast(`${sd.name}: ${e.text}`, e.k === 'in' ? 'good' : ['water', 'sand', 'kraken', 'oob', 'picked'].includes(e.k) ? 'bad' : '');
    }
    if (shownEvents.size > 300) shownEvents = new Set([...shownEvents].slice(-100));
  }

  function toast(text, kind) {
    const t = root.querySelector('.mg-toast');
    t.textContent = text; t.className = 'mg-toast' + (kind ? ' mg-' + kind : ''); t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 2600);
  }

  // --------------------------------------------------------------- input ---
  function canAim() { return !host && g && (g.phase === 'playing' || g.phase === 'trivia') && g.me && g.me.canShoot && !busy; }

  function onDown(e) {
    if (!canAim()) return;
    canvas.setPointerCapture(e.pointerId);
    drag = {x: e.clientX, y: e.clientY};
    aim = null;
    e.preventDefault();
  }
  function onMove(e) {
    if (!drag) return;
    const dx = drag.x - e.clientX, dy = drag.y - e.clientY, d = Math.hypot(dx, dy);
    aim = d < 6 ? null : {angle: Math.atan2(dy, dx), power: Math.min(1, d / MAX_DRAG)};
  }
  function onUp(e) {
    if (!drag) return;
    onMove(e);
    const a = aim;
    drag = null; aim = null;
    if (a && a.power > 0.04) shoot(a);
  }

  async function shoot(a) {
    if (busy) return;
    busy = true; kbAim = null;
    try {
      const r = await send('mgShot', {angle: a.angle, power: a.power});
      if (!r || r.ok === false) err = (r && r.error) || 'Connection lost. Try again.';
      else {
        err = '';
        const sd = (g.sides || []).find(x => x.id === r.side);
        if (sd && r.shot) { sd.shot = r.shot; sd.ball = r.shot.frames[r.shot.frames.length - 1]; sd.moving = true; }
        if (g.me) { g.me.canShoot = false; g.me.status = 'Rolling…'; }
      }
    } catch { err = 'Connection lost. Try again.'; }
    finally { busy = false; rerender(); }
  }

  function rerender() { if (s) render(root, s, pid, send, host); }

  document.addEventListener('keydown', e => {
    if (!root || root.offsetParent === null || !canAim()) return;
    if (e.target.closest && e.target.closest('input, textarea, select')) return;
    const meSide = (g.sides || []).find(sd => sd.id === g.me.side);
    if (!meSide) return;
    if (!kbAim) kbAim = {angle: Math.atan2(g.hole.cup[1] - meSide.ball[1], g.hole.cup[0] - meSide.ball[0]), power: .4};
    const step = e.shiftKey ? .01 : .05;
    if (e.key === 'ArrowLeft') kbAim.angle -= step;
    else if (e.key === 'ArrowRight') kbAim.angle += step;
    else if (e.key === 'ArrowUp') kbAim.power = Math.min(1, kbAim.power + .04);
    else if (e.key === 'ArrowDown') kbAim.power = Math.max(.04, kbAim.power - .04);
    else if (e.key === ' ' || e.key === 'Enter') shoot(kbAim);
    else return;
    e.preventDefault();
  });

  // ---------------------------------------------------------------- side ---
  function scoreClass(score, par) {
    if (score == null) return '';
    return score === 1 ? 'mg-ace' : score < par ? 'mg-under' : score > par ? 'mg-over' : 'mg-par';
  }

  function side() {
    const rows = (g.sides || []).map(sd => {
      const st = sd.state === 'in' ? '⛳ in' : sd.state === 'picked' ? 'picked up' : sd.moving ? 'rolling…' : sd.state === 'tee' ? 'on the tee' : `${sd.strokes} stroke${sd.strokes === 1 ? '' : 's'}`;
      const up = sd.up ? ' mg-up' : '';
      const hitter = sd.hitter != null && sd.members.length > 1 ? ` · ${esc((sd.members.find(m => m.pid === sd.hitter) || {}).name || '')} hits` : '';
      const you = g.me && g.me.side === sd.id ? ' <small>(you)</small>' : '';
      return `<li class="${up}${sd.present ? '' : ' mg-gone'}"><span class="mg-dot" style="background:${esc(sd.color)}"></span><b>${esc(sd.name)}${you}</b><span>${st}${hitter}</span><em>${sd.total}<small>${sd.points} pts</small></em></li>`;
    }).join('');
    const log = (g.log || []).slice(-4).reverse().map(t => `<li>${esc(t)}</li>`).join('');
    let ctl = '';
    if (host) {
      if (g.phase === 'playing') ctl = `<button type="button" data-act="mgSkip" class="mg-ghost">Pick up for the player who's up</button><button type="button" data-act="mgEnd" class="mg-danger">End game</button>`;
      if (g.phase === 'hole' || g.phase === 'treveal') ctl = `<button type="button" data-act="mgNext">Next hole now</button><button type="button" data-act="mgEnd" class="mg-danger">End game</button>`;
      if (g.phase === 'trivia') ctl = `<button type="button" data-act="mgNext">Close trivia &amp; reveal</button><button type="button" data-act="mgEnd" class="mg-danger">End game</button>`;
    }
    const join = !host && g.phase !== 'done' && g.me && !g.me.side ? `<button type="button" data-act="mgJoin">Join in from here</button>` : '';
    return `<ol class="mg-board">${rows}</ol>${join}${ctl ? `<div class="mg-controls">${ctl}</div>` : ''}${log ? `<ul class="mg-log">${log}</ul>` : ''}`;
  }

  function scorecard() {
    const n = g.holeCount, pars = g.pars || [];
    const cur = g.holeIndex;
    const head = `<tr><th>Hole</th>${pars.map((_, i) => `<th class="${i === cur && g.phase !== 'done' ? 'mg-cur' : ''}">${i + 1}</th>`).join('')}<th>Tot</th></tr>
      <tr class="mg-parrow"><th>Par</th>${pars.map(p => `<td>${p}</td>`).join('')}<td>${pars.reduce((a, b) => a + b, 0)}</td></tr>`;
    const body = (g.sides || []).map(sd => `<tr><th><span class="mg-dot" style="background:${esc(sd.color)}"></span>${esc(sd.name)}</th>${Array.from({length: n}, (_, i) => {
      const v = sd.scores[i];
      return `<td class="${scoreClass(v, pars[i])}${i < sd.missed ? ' mg-missed' : ''}">${v ?? (i === cur && g.phase === 'playing' && sd.strokes ? `<small>${sd.strokes}</small>` : '')}</td>`;
    }).join('')}<td><b>${sd.total}</b></td></tr>`).join('');
    let teams = '';
    if (g.cfg.format === 'best' && g.teamTotals) teams = Object.entries(g.teamTotals).map(([t, v]) => `<span class="mg-chip" style="--c:${esc(g.teamColors[t])}">${esc(g.teamNames[t])}: <b>${v}</b></span>`).join(' ');
    return `<div class="mg-card"><table>${head}${body}</table></div>${teams ? `<p class="mg-teamtot">Best-ball team totals: ${teams}</p>` : ''}`;
  }

  function standingsHTML() {
    const member = r => r.members && r.members.length ? `<small>${r.members.map(esc).join(', ')}</small>` : '';
    const strokes = (g.standings || []).map(r => {
      const tp = r.toPar === 0 ? 'E' : (r.toPar > 0 ? '+' : '') + r.toPar;
      return `<li><span class="mg-place">${r.place === 1 ? '🏆' : '#' + r.place}</span><span class="mg-dot" style="background:${esc(r.color)}"></span><b>${esc(r.name)}${member(r)}</b><span>${r.total} <small>(${tp})</small></span></li>`;
    }).join('');
    const points = (g.pointsStandings || []).map(r =>
      `<li><span class="mg-place">${r.place === 1 && r.points ? '🏅' : '#' + r.place}</span><span class="mg-dot" style="background:${esc(r.color)}"></span><b>${esc(r.name)}${member(r)}</b><span>${r.points} <small>pts</small></span></li>`).join('');
    return `<div class="mg-boards"><section><h3>⛳ Fewest strokes</h3><ol class="mg-stand">${strokes}</ol></section>
      <section><h3>🪙 Most points</h3><ol class="mg-stand">${points}</ol></section></div>`;
  }

  // The question and answers, shown above the course on a trivia hole.
  function qbar() {
    const tv = g.trivia, reveal = tv.correct != null;
    const picks = {};
    for (const r of tv.results) if (r.choice != null) (picks[r.choice] = picks[r.choice] || []).push(r);
    const opts = tv.options.map((o, i) => {
      const cls = reveal ? (i === tv.correct ? ' mg-right' : picks[i] ? ' mg-wrong' : '') : '';
      const who = reveal && picks[i] ? `<small>${picks[i].map(r => esc(r.name) + (r.pts ? ' +' + r.pts : '')).join(', ')}</small>` : '';
      return `<li class="${cls.trim()}" style="--c:${CUP_COLORS[i % 4]}"><b>${'ABCD'[i]}</b><span>${esc(o)}${who}</span></li>`;
    }).join('');
    const locked = tv.results.filter(r => r.locked).length;
    const foot = reveal
      ? (tv.results.some(r => r.pts) ? 'Correct answers score bonus points!' : 'Nobody sank the right answer this time.')
      : `Sink the ball in the cup with the right answer: ${tv.points.join(' / ')} points in 1 / 2 / 3 putts. ${locked} locked in.`;
    return `<p class="mg-tq">🧠 ${esc(tv.q)}</p><ol class="mg-opts">${opts}</ol><p class="mg-tfoot">${foot}</p>`;
  }

  function rulesHTML() {
    const c = g.cfg;
    return `<details class="mg-how"><summary>How to play</summary>
      <p>Get the ball in the cup in as few strokes as you can. Lowest total after ${c.holes} holes wins.</p>
      <p><b>Putting:</b> when it's your shot, drag back from anywhere on the course (like a slingshot) and let go. The farther you pull, the harder the putt. On a keyboard: ← → aim, ↑ ↓ power, Space to putt.</p>
      <p><b>Order:</b> everyone tees off first; after that ${c.order === 'far' ? 'the ball farthest from the cup' : 'the ball closest to the cup'} goes next. On later holes, the best score on the last hole tees off first. ${c.pace === 'together' ? 'This game is set to <b>everyone at once</b>, so you can putt whenever your ball has stopped.' : `Groups hold at most ${c.groupSize} balls; groups play side by side.`}</p>
      <p><b>Stroke limit:</b> ${c.limit}. Reach it without sinking and your ball is picked up with a ${c.limit}.</p>
      <p><b>Hazards:</b> water, sand traps, the kraken's mouth or out of bounds cost <b>+1 stroke</b>, and the ball goes back to where it left the course. A ball resting against a rail (or in a windmill's or drawbridge's path) is moved one clubhead away, no penalty.</p>
      ${c.format === 'best' ? '<p><b>Best ball:</b> everyone plays their own ball; each hole your team scores its best ball.</p>' : ''}
      ${c.format === 'alternate' ? '<p><b>Alternate shot:</b> your team shares one ball and teammates take turns hitting it.</p>' : ''}
      <p><b>Trampolines</b> launch the ball into the air: it flies over hazards and can even sail over a rail (and land out of bounds, +1). <b>Spin Cycle</b> flings the ball the way its arrow points when it reaches the middle.</p>
      <p><b>Points</b> (a second leaderboard, most wins): coins +${g.scoreTable ? g.scoreTable.coin : 50}, barrels +25, tunnels +30, trampolines +40, Spin Cycle +30, turbo arrows +10. Every trick in one shot raises your combo (×2, ×3… up to ×5), and sinking it scores a bonus times your combo: hole in one 500, eagle 300, birdie 150, par 75, otherwise 25.</p>
      ${+c.trivia ? `<p><b>🧠 Trivia holes</b> (${esc(TRIVIA_EVERY[c.trivia].toLowerCase())}): a question pops up and every answer is its own cup. Everyone putts at once and gets 3 putts to sink into the cup they think is right. Right answer: 250 points in one putt, 200 in two, 150 in three. Trivia holes don't count toward your strokes.</p>` : ''}
      <p>Balls don't knock each other. Moving obstacles keep moving while you aim, so time it.</p></details>`;
  }

  function info() {
    let out = '';
    const c = g.cfg;
    if (g.phase === 'lobby' || (g.phase === 'done' && !g.hole)) {
      if (host) {
        out += `<div class="mg-controls mg-settings">
          ${sel({label: 'Format', key: 'format'}, c.format, FORMAT)}
          ${sel({label: 'Holes', key: 'holes'}, c.holes, {9: '9 holes', 18: '18 holes'})}
          ${sel({label: 'Stroke limit', key: 'limit'}, c.limit, {6: '6 (mercy rule)', 8: '8', 10: '10'})}
          ${sel({label: 'Pace', key: 'pace'}, c.pace, PACE)}
          ${sel({label: 'After the tee', key: 'order'}, c.order, ORDER)}
          ${c.format !== 'solo' ? sel({label: 'Teams', key: 'teams'}, c.teams, {2: '2 teams', 3: '3 teams', 4: '4 teams'}) : ''}
          ${c.pace === 'turns' ? sel({label: 'Group size', key: 'groupSize'}, c.groupSize, {4: 'Up to 4 per group', 5: 'Up to 5 per group'}) : ''}
          ${sel({label: 'World', key: 'theme'}, c.theme, THEME_LABEL)}
          ${sel({label: 'Trivia holes (bonus points)', key: 'trivia'}, +c.trivia, TRIVIA_EVERY)}
          ${sel({label: 'Obstacles', key: 'obsMode'}, c.obsMode, OBS_MODE)}
          ${sel({label: 'How many per hole', key: 'amount'}, c.amount, AMOUNT)}
        </div>
        <div class="mg-obs-head"><h2>Obstacles</h2>${c.obsMode === 'chosen' ? `<span><button type="button" data-act="mgRandomObs">🎲 Random pick</button><button type="button" data-obsall="1" class="mg-ghost">All</button><button type="button" data-obsall="0" class="mg-ghost">None</button></span>` : ''}</div>`;
        out += obstacleList(true);
        out += teamPicker();
        out += `<div class="mg-controls"><button type="button" data-act="mgStart" class="mg-go">Tee off ⛳</button></div>`;
      } else {
        out += `<p class="mg-help">${esc(FORMAT[c.format])} · ${c.holes} holes · stroke limit ${c.limit} · ${esc(PACE[c.pace])}. Waiting for the host to tee off…</p>`;
        out += teamPicker();
        out += `<h2 class="mg-h2">On this course</h2>` + (c.obsMode === 'chosen' && !c.obstacles.length ? '<p class="mg-help">No obstacles: just you, the rails and the cup.</p>' : obstacleList(false));
      }
      out += rulesHTML();
      return out;
    }
    if (g.phase === 'hole') out += `<h2 class="mg-h2">Hole ${g.hole.n} done</h2>${standingsHTML()}`;
    if (g.phase === 'treveal') out += `<h2 class="mg-h2">Standings</h2>${standingsHTML()}`;
    if (g.phase === 'done') {
      out += `<h2 class="mg-h2">Final scores</h2>${standingsHTML()}`;
      if (host) out += `<div class="mg-controls"><button type="button" data-act="mgStart" class="mg-go">Play a new course</button><button type="button" data-act="mgLobby" class="mg-ghost">Change settings</button></div>`;
    }
    if (g.hole) out += `<details class="mg-sc"${host || g.phase !== 'playing' ? ' open' : ''}><summary>Scorecard</summary>${scorecard()}</details>`;
    if (g.phase === 'playing' && !host) {
      const kinds = [...new Set(g.hole.obs.map(o => o.k))];
      if (kinds.length) out += `<p class="mg-legend">This hole: ${kinds.map(id => { const o = g.obstacles.find(x => x.id === id); return o ? `${o.emoji} ${esc(o.name)}` : ''; }).join(' · ')}</p>`;
    }
    out += rulesHTML();
    return out;
  }

  // ------------------------------------------------------------- actions ---
  async function run(action, extra = {}) {
    try {
      const r = await send(action, extra);
      err = !r || r.ok === false ? (r && r.error) || 'Connection lost. Try again.' : '';
    } catch { err = 'Connection lost. Try again.'; }
    rerender();
  }

  function onClick(e) {
    const a = e.target.closest('[data-act]');
    if (a) {
      if (a.dataset.act === 'mgEnd' && !confirm('End the game now? Only holes everyone finished will count.')) return;
      run(a.dataset.act); return;
    }
    const t = e.target.closest('[data-team]');
    if (t) { run('mgTeam', {team: +t.dataset.team}); return; }
    const all = e.target.closest('[data-obsall]');
    if (all) { (async () => { for (const o of g.obstacles) await send('mgSet', {key: 'obs:' + o.id, value: all.dataset.obsall === '1'}); rerender(); })(); }
  }

  function onChange(e) {
    const s1 = e.target.closest('[data-set]');
    if (s1) { s1.blur(); run('mgSet', {key: s1.dataset.set, value: s1.value}); return; }
    const ob = e.target.closest('[data-obs]');
    if (ob) run('mgSet', {key: 'obs:' + ob.dataset.obs, value: ob.checked});
  }

  window.addEventListener('resize', () => { if (root && g && g.hole) { size(); } });

  return {render};
})();
