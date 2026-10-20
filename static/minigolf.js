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

  let root = null, s = null, g = null, pid = null, send = null, host = false;
  let built = null, offset = 0, raf = 0, canvas = null, ctx = null, k = 4, dpr = 1, vb = {x: 0, y: 0, w: 80, h: 120};
  let staticKey = '', staticCanvas = null, drag = null, aim = null, kbAim = null, err = '', busy = false;
  let shownEvents = new Set(), toastTimer = 0, lastShotSeen = {};

  const now = () => (Date.now() + offset) / 1000;

  function skeleton() {
    root.innerHTML = `<div class="mg-head"><h1>⛳ Mini Golf</h1><p class="mg-sub"></p></div>
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

  function setHTML(node, html) { if (node.innerHTML !== html) node.innerHTML = html; }

  // isHost = the shared laptop/TV display (draws big, shows host controls)
  function render(el, state, myPid, sender, isHost = false) {
    const gs = state.minigolf; if (!gs || !el) return;
    root = el; s = state; g = gs; pid = myPid; send = sender; host = isHost;
    if (built !== root) skeleton();
    offset = g.serverNow * 1000 - Date.now();
    root.classList.toggle('mg-host', host);
    const playing = !!g.hole && g.phase !== 'lobby';
    root.querySelector('.mg-stage').hidden = !playing;
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
    const left = g.phase === 'hole' && g.nextAt ? Math.max(0, Math.ceil(g.nextAt - now())) : 0;
    if (g.phase === 'done') return `${esc(g.course)} · final scores`;
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
    const maxH = host ? window.innerHeight * 0.78 : Math.max(320, window.innerHeight - 190);
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
      if (!root || !g || root.offsetParent === null || !g.hole || g.phase === 'lobby') return;
      draw();
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
  }

  function drawStatic() {
    const h = g.hole, C = g.consts;
    const key = `${g.course}:${h.n}:${canvas.width}x${canvas.height}:${g.epoch}:${vb.x},${vb.y}`;
    if (key === staticKey && staticCanvas) return staticCanvas;
    staticKey = key;
    staticCanvas = staticCanvas || document.createElement('canvas');
    staticCanvas.width = canvas.width; staticCanvas.height = canvas.height;
    const c = staticCanvas.getContext('2d');
    c.setTransform(k, 0, 0, k, -vb.x * k, -vb.y * k);
    // the sea around the course
    const sea = c.createLinearGradient(0, 0, 0, C.H);
    sea.addColorStop(0, '#0f3d57'); sea.addColorStop(1, '#0a2638');
    c.fillStyle = sea; c.fillRect(0, 0, C.W, C.H);
    c.strokeStyle = 'rgba(255,255,255,.06)'; c.lineWidth = .4;
    for (let y = 4; y < C.H; y += 7) for (let x = (y % 14 ? 0 : 3.5); x < C.W; x += 7) {
      c.beginPath(); c.arc(x, y, 1.6, Math.PI * 1.1, Math.PI * 1.9); c.stroke();
    }
    // fairway: mowed stripes
    for (const [x, y] of h.cells) {
      c.fillStyle = '#3fae54'; c.fillRect(x * C.C, y * C.C, C.C, C.C);
      c.fillStyle = '#4bbd5f'; c.fillRect(x * C.C, y * C.C, C.C, C.C / 2);
    }
    // tee mat
    c.fillStyle = '#7fd98a'; roundRect(c, h.tee[0] - 4, h.tee[1] - 2.5, 8, 5, 1.2); c.fill();
    for (const o of h.obs) drawObstacle(c, o, 0, true);
    // rails
    c.lineCap = 'round';
    c.strokeStyle = '#6b4423'; c.lineWidth = 1.6;
    for (const [x0, y0, x1, y1] of h.walls) { c.beginPath(); c.moveTo(x0, y0); c.lineTo(x1, y1); c.stroke(); }
    c.strokeStyle = '#a8743f'; c.lineWidth = .6;
    for (const [x0, y0, x1, y1] of h.walls) { c.beginPath(); c.moveTo(x0, y0); c.lineTo(x1, y1); c.stroke(); }
    // cup
    c.fillStyle = '#1b2b1e'; c.beginPath(); c.arc(h.cup[0], h.cup[1], C.cupR, 0, Math.PI * 2); c.fill();
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
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
  }

  function draw() {
    const C = g.consts, h = g.hole, T = now(), clock = T - g.epoch;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.drawImage(drawStatic(), 0, 0);
    ctx.setTransform(k, 0, 0, k, -vb.x * k, -vb.y * k);
    for (const o of h.obs) drawObstacle(ctx, o, clock, false);
    // flag (lifted out while a ball is close, like a caddie would)
    const near = (g.sides || []).some(sd => { const b = ballAt(sd, T); return Math.hypot(b[0] - h.cup[0], b[1] - h.cup[1]) < 8 && sd.state !== 'in'; });
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
      const [x, y] = ballAt(sd, T);
      if (sd.id === mine && g.me.canShoot) {
        ctx.strokeStyle = 'rgba(255,255,255,.8)'; ctx.lineWidth = .4;
        ctx.beginPath(); ctx.arc(x, y, C.R * 2.2 + Math.sin(T * 5) * .5, 0, Math.PI * 2); ctx.stroke();
      }
      ctx.fillStyle = 'rgba(0,0,0,.25)'; ctx.beginPath(); ctx.arc(x + .3, y + .4, C.R * 1.25, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(x, y, C.R * 1.25, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = sd.color; ctx.lineWidth = .6; ctx.stroke();
      if (host || sd.id !== mine) {
        ctx.font = '600 2.6px system-ui, sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
        ctx.fillStyle = 'rgba(0,0,0,.55)'; ctx.fillText(sd.name.slice(0, 10), x + .15, y - 1.8 + .15);
        ctx.fillStyle = sd.color; ctx.fillText(sd.name.slice(0, 10), x, y - 1.8);
      }
      if (moving) eventsFor(sd, T);
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
    if (g.phase === 'hole') { const el = root.querySelector('.mg-sub'); const html = sub(); if (el.innerHTML !== html) el.innerHTML = html; }
  }

  function eventsFor(sd, T) {
    for (const [n, e] of (sd.shot.events || []).entries()) {
      const key = sd.shot.id + ':' + n;
      if (shownEvents.has(key) || T < sd.shot.t0 + e.t) continue;
      shownEvents.add(key);
      toast(`${sd.name}: ${e.text}`, e.k === 'in' ? 'good' : ['water', 'sand', 'kraken', 'oob', 'picked'].includes(e.k) ? 'bad' : '');
    }
    if (shownEvents.size > 300) shownEvents = new Set([...shownEvents].slice(-100));
  }

  function toast(text, kind) {
    const t = root.querySelector('.mg-toast');
    t.textContent = text; t.className = 'mg-toast' + (kind ? ' mg-' + kind : ''); t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 2600);
  }

  // --------------------------------------------------------------- input ---
  function canAim() { return !host && g && g.phase === 'playing' && g.me && g.me.canShoot && !busy; }

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
      return `<li class="${up}${sd.present ? '' : ' mg-gone'}"><span class="mg-dot" style="background:${esc(sd.color)}"></span><b>${esc(sd.name)}${you}</b><span>${st}${hitter}</span><em>${sd.total}</em></li>`;
    }).join('');
    const log = (g.log || []).slice(-4).reverse().map(t => `<li>${esc(t)}</li>`).join('');
    let ctl = '';
    if (host) {
      if (g.phase === 'playing') ctl = `<button type="button" data-act="mgSkip" class="mg-ghost">Pick up for the player who's up</button><button type="button" data-act="mgEnd" class="mg-danger">End game</button>`;
      if (g.phase === 'hole') ctl = `<button type="button" data-act="mgNext">Next hole now</button><button type="button" data-act="mgEnd" class="mg-danger">End game</button>`;
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
    const rows = (g.standings || []).map(r => {
      const tp = r.toPar === 0 ? 'E' : (r.toPar > 0 ? '+' : '') + r.toPar;
      const members = r.members && r.members.length ? `<small>${r.members.map(esc).join(', ')}</small>` : '';
      return `<li><span class="mg-place">${r.place === 1 ? '🏆' : '#' + r.place}</span><span class="mg-dot" style="background:${esc(r.color)}"></span><b>${esc(r.name)}${members}</b><span>${r.total} <small>(${tp})</small></span></li>`;
    }).join('');
    return `<ol class="mg-stand">${rows}</ol>`;
  }

  // ---------------------------------------------------------------- info ---
  function sel(key, value, options) {
    return `<label>${esc(key.label)}<select data-set="${key.key}">${Object.entries(options).map(([v, t]) => `<option value="${esc(v)}"${String(v) === String(value) ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select></label>`;
  }

  function obstacleList(editable) {
    const on = new Set(g.cfg.obstacles);
    const surprise = g.cfg.obsMode === 'surprise';
    return `<ul class="mg-obs">${g.obstacles.map(o => {
      const enabled = surprise || on.has(o.id);
      if (!editable && !enabled) return '';
      return `<li class="${enabled ? '' : 'mg-off'}">${editable && !surprise ? `<label><input type="checkbox" data-obs="${o.id}"${on.has(o.id) ? ' checked' : ''}>` : '<label>'}<span class="mg-emo">${o.emoji}</span><span><b>${esc(o.name)}</b><small>${esc(o.blurb)}</small></span></label></li>`;
    }).join('')}</ul>`;
  }

  function teamPicker() {
    if (g.cfg.format === 'solo' || !g.teams) return '';
    const cols = g.teams.map((members, t) => `<div class="mg-team" style="--c:${esc(g.teamColors[t])}"><h3>${esc(g.teamNames[t])}</h3>
      <ul>${members.map(m => `<li>${esc(m.name)}${m.pid === pid ? ' <small>(you)</small>' : ''}</li>`).join('') || '<li class="mg-empty">nobody yet</li>'}</ul>
      ${!host && pid != null && g.myTeam !== t ? `<button type="button" data-team="${t}">Join ${esc(g.teamNames[t].split(' ')[1] || g.teamNames[t])}</button>` : ''}</div>`).join('');
    return `<div class="mg-teams">${cols}</div>${host ? '<button type="button" data-act="mgShuffle" class="mg-ghost">🔀 Shuffle teams</button>' : '<p class="mg-help">Pick a team, or leave it and you\'ll be balanced in at the start.</p>'}`;
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
    if (s1) { run('mgSet', {key: s1.dataset.set, value: s1.value}); return; }
    const ob = e.target.closest('[data-obs]');
    if (ob) run('mgSet', {key: 'obs:' + ob.dataset.obs, value: ob.checked});
  }

  window.addEventListener('resize', () => { if (root && g && g.hole) { size(); } });

  return {render};
})();
