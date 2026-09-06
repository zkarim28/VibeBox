#!/usr/bin/env python3
"""
VibeBox Launcher — a tiny always-on control panel so you can spin up public
party-game servers from your phone, with no SSH.

It listens on localhost only; you expose it to your phone with Tailscale Funnel
(see setup-launcher.sh). Everything is password-gated.

  Run by hand:   python3 launcher.py
  On boot:       ./setup-launcher.sh   (installs a systemd --user service)

Zero dependencies — Python 3 stdlib + this repo's qr.py.
"""

import hashlib
import html
import http.cookies
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import qr  # local, dependency-free QR-code generator

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_PY = os.path.join(HERE, "server.py")
STATE_DIR = os.path.expanduser("~/.vibebox-launcher")
LOG_DIR = os.path.join(STATE_DIR, "logs")
STATE_FILE = os.path.join(STATE_DIR, "servers.json")
PW_FILE = os.path.join(STATE_DIR, "password")


def _load_env(path=os.path.join(HERE, ".env")):
    """Pull KEY=value lines from the repo's .env so `python3 launcher.py` works
    the same as the systemd unit (which uses EnvironmentFile)."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                if k and k not in os.environ:
                    os.environ[k] = v.strip().strip('"').strip("'")
    except OSError:
        pass


_load_env()

LAUNCHER_PORT = int(os.environ.get("LAUNCHER_PORT", "8790"))
BIND = os.environ.get("LAUNCHER_BIND", "127.0.0.1")
MAX_SERVERS = int(os.environ.get("LAUNCHER_MAX", "4"))
PORT_RANGE = range(int(os.environ.get("LAUNCHER_PORT_BASE", "8000")),
                   int(os.environ.get("LAUNCHER_PORT_BASE", "8000")) + 20)
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # no 0/O/1/I/L


def _password():
    """LAUNCHER_PASSWORD wins; otherwise generate one, save it, and print it."""
    env = os.environ.get("LAUNCHER_PASSWORD", "").strip()
    if env:
        return env
    os.makedirs(STATE_DIR, exist_ok=True)
    try:
        saved = open(PW_FILE).read().strip()
        if saved:
            return saved
    except OSError:
        pass
    pw = secrets.token_urlsafe(9)
    with open(PW_FILE, "w") as f:
        f.write(pw + "\n")
    os.chmod(PW_FILE, 0o600)
    print(f"\n  ***************************************************************\n"
          f"  ***  Launcher password (auto-generated):  {pw}\n"
          f"  ***  saved to {PW_FILE}\n"
          f"  ***  set LAUNCHER_PASSWORD in .env to pick your own\n"
          f"  ***************************************************************\n",
          flush=True)
    return pw


PASSWORD = _password()
# stable cookie value derived from the password: survives launcher restarts,
# and changing the password invalidates every existing session.
SESSION = hashlib.sha256(("vblaunch\0" + PASSWORD).encode()).hexdigest()

# ---------------------------------------------------------------- server table --
_lock = threading.Lock()
servers = {}          # id -> {id, pid, pgid, port, code, token, url, started, log}
_login_fails = []     # timestamps of recent bad passwords


def _persist_keys(s):
    return {k: s[k] for k in
            ("id", "pid", "pgid", "port", "code", "token", "url", "started", "log")}


def _save():
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"servers": [_persist_keys(s) for s in servers.values()]}, f)
    os.replace(tmp, STATE_FILE)


def _alive(pid):
    """True if pid is running AND still one of our game servers (guards against
    the OS recycling the pid)."""
    try:
        os.kill(pid, 0)
    except (OSError, TypeError):
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return b"server.py" in f.read()
    except OSError:
        return False


def _load():
    """Re-attach to game servers that outlived a launcher restart."""
    try:
        data = json.load(open(STATE_FILE))
    except (OSError, ValueError):
        return
    changed = False
    for s in data.get("servers", []):
        if _alive(s.get("pid")):
            if not s.get("url"):
                s["url"] = _scrape_url(s.get("log", ""), timeout=0)
            servers[s["id"]] = s
        else:
            changed = True
    if changed or servers:
        _save()


_TCF_RE = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")


def _scrape_url(logfile, timeout=90):
    """Read a spawned server's log until its public tunnel URL shows up."""
    deadline = time.time() + timeout
    while True:
        try:
            txt = open(logfile, errors="replace").read()
        except OSError:
            txt = ""
        m = _TCF_RE.search(txt)
        if m:
            return m.group(0)
        if "running LOCAL instead" in txt or "couldn't get a tunnel URL" in txt:
            return None
        if time.time() >= deadline:
            return None
        time.sleep(1.5)


def _free_port():
    used = {s["port"] for s in servers.values()}
    for p in PORT_RANGE:
        if p in used:
            continue
        with socket.socket() as sock:
            try:
                sock.bind((BIND, p))
                return p
            except OSError:
                continue
    return None


def start_server():
    with _lock:
        live = [s for s in servers.values() if _alive(s["pid"])]
        if len(live) >= MAX_SERVERS:
            return {"ok": False, "error": f"already running {MAX_SERVERS} servers"}
        port = _free_port()
        if not port:
            return {"ok": False, "error": "no free port in range"}
        sid = secrets.token_hex(4)
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(4))
        token = secrets.token_urlsafe(12)
        os.makedirs(LOG_DIR, exist_ok=True)
        log = os.path.join(LOG_DIR, sid + ".log")
        env = dict(os.environ, MODE="public", GUI="0", NO_HTTPS="1",
                   PORT=str(port), ROOM_CODE=code, HOST_TOKEN=token)
        env.pop("PUBLIC_URL", None)   # force a fresh Cloudflare quick tunnel
        proc = subprocess.Popen(
            [sys.executable, SERVER_PY], cwd=HERE, env=env,
            stdout=open(log, "ab"), stderr=subprocess.STDOUT,
            start_new_session=True)
        # start_new_session=True makes the child a group leader, so pgid == pid;
        # killpg(pid) later takes down server.py *and* the cloudflared it spawns.
        rec = {"id": sid, "pid": proc.pid, "pgid": proc.pid,
               "port": port, "code": code, "token": token, "url": None,
               "started": time.time(), "log": log}
        servers[sid] = rec
        _save()

    def _fill():
        url = _scrape_url(log)
        with _lock:
            if sid in servers:
                servers[sid]["url"] = url
                _save()

    threading.Thread(target=_fill, daemon=True).start()
    return {"ok": True, "id": sid}


def stop_server(sid):
    with _lock:
        s = servers.get(sid)
    if not s:
        return {"ok": False, "error": "unknown server"}
    try:
        os.killpg(s["pgid"], signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass

    def _reap():
        for _ in range(24):
            if not _alive(s["pid"]):
                break
            time.sleep(0.5)
        else:
            try:
                os.killpg(s["pgid"], signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        with _lock:
            servers.pop(sid, None)
            _save()

    threading.Thread(target=_reap, daemon=True).start()
    return {"ok": True}


def _players(port, token):
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/state",
            headers={"Cookie": f"hosttoken={token}"})
        with urllib.request.urlopen(req, timeout=2) as r:
            return len(json.load(r).get("players", []))
    except Exception:
        return None


def status():
    with _lock:
        snap = [dict(s) for s in servers.values()]
    rows, dead = [], []
    for s in snap:
        if not _alive(s["pid"]):
            dead.append(s["id"])
            continue
        u = s["url"]
        rows.append({
            "id": s["id"], "port": s["port"], "code": s["code"],
            "url": u, "started": s["started"],
            "players": _players(s["port"], s["token"]) if u else None,
            "join": f"{u}/{s['code']}" if u else None,
            "host": f"{u}/?host={s['token']}" if u else None,
        })
    if dead:
        with _lock:
            for sid in dead:
                servers.pop(sid, None)
            _save()
    rows.sort(key=lambda r: r["started"])
    return rows


def _login_ok(pw):
    now = time.time()
    _login_fails[:] = [t for t in _login_fails if now - t < 300]
    if len(_login_fails) >= 10:
        return None   # locked out
    if secrets.compare_digest(str(pw or ""), PASSWORD):
        return True
    _login_fails.append(now)
    return False


# --------------------------------------------------------------------- the page --
PAGE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VibeBox Launcher</title>
<style>
:root{color-scheme:dark}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
 background:radial-gradient(1000px 700px at 50% -10%,#1e293b,#0b1120);color:#e2e8f0;
 min-height:100vh;padding:1.25rem;line-height:1.5}
.wrap{max-width:520px;margin:0 auto}
h1{font-size:1.6rem;letter-spacing:-.02em}h1 span{color:#38bdf8}
.muted{color:#94a3b8}
button{font:inherit;cursor:pointer;border:0;border-radius:.7rem}
.big{width:100%;padding:1rem;font-size:1.15rem;font-weight:800;background:#38bdf8;color:#04121c;margin-top:1rem}
.big[disabled]{filter:grayscale(.5) brightness(.6)}
input{font:inherit;font-size:1.05rem;width:100%;padding:.8rem 1rem;border-radius:.7rem;
 border:1px solid #334155;background:#111827;color:#e2e8f0}
.card{background:#0f172a;border:1px solid #1e293b;border-radius:.9rem;padding:1rem;margin-top:1rem}
.row{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap}
.pill{background:#111827;border:1px solid #334155;border-radius:.5rem;padding:.35rem .6rem;
 font-family:ui-monospace,Menlo,monospace;font-size:.9rem;word-break:break-all;flex:1;min-width:0}
.qr{width:132px;height:132px;background:#fff;border-radius:.5rem;padding:.4rem;image-rendering:pixelated}
.linkbtn{background:#1e293b;color:#e2e8f0;padding:.5rem .8rem;font-weight:700;font-size:.9rem}
.stop{background:#7f1d1d;color:#fecaca;padding:.5rem .8rem;font-weight:800;font-size:.85rem}
.stop:hover{background:#b91c1c;color:#fff}
.tag{font-size:.8rem;font-weight:800;text-transform:uppercase;letter-spacing:.05em}
.tag.live{color:#4ade80}.tag.wait{color:#fbbf24}
.err{color:#fca5a5;min-height:1.2em;margin-top:.5rem}
a{color:#7dd3fc}
.foot{margin-top:2rem;font-size:.8rem}
</style></head><body><div class="wrap">
<h1>Vibe<span>Box</span> Launcher</h1>

<div id="login" hidden>
 <p class="muted" style="margin:.5rem 0 1rem">Enter the launcher password.</p>
 <input id="pw" type="password" autocomplete="current-password" placeholder="Password" enterkeyhint="go">
 <button class="big" id="login-btn" type="button">Unlock</button>
 <p class="err" id="login-err"></p>
</div>

<div id="panel" hidden>
 <button class="big" id="start" type="button">▶  Start a public game</button>
 <p class="err" id="start-err"></p>
 <div id="list"></div>
 <p class="foot muted"><a href="#" id="logout">log out</a> · servers keep running if you close this page</p>
</div>

<script>
const $=id=>document.getElementById(id);
const api=(p,b)=>fetch(p,{method:b?"POST":"GET",headers:b?{"Content-Type":"application/json"}:{},
  body:b?JSON.stringify(b):undefined});
function ago(t){const s=Math.max(0,Date.now()/1000-t);
  if(s<60)return Math.round(s)+"s";if(s<3600)return Math.round(s/60)+"m";return (s/3600).toFixed(1)+"h";}

async function refresh(){
  const r=await api("/api/status");
  if(r.status===401){show("login");return;}
  show("panel");
  const {servers}=await r.json();
  $("list").innerHTML = servers.length? "" : '<p class="muted card">No servers running. Tap Start.</p>';
  for(const s of servers){
    const el=document.createElement("div"); el.className="card";
    const live=!!s.url;
    el.innerHTML=`
      <div class="row" style="justify-content:space-between">
        <span class="tag ${live?'live':'wait'}">${live?'live':'starting…'}</span>
        <span class="muted">up ${ago(s.started)} · ${s.players==null?'—':s.players+' player'+(s.players===1?'':'s')} · code <b>${s.code}</b></span>
      </div>`;
    if(live){
      el.innerHTML+=`
      <div class="row" style="margin-top:.7rem">
        <img class="qr" src="/qr?u=${encodeURIComponent(s.join)}" alt="join QR">
        <div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:.5rem">
          <div class="pill">${s.join.replace(/^https:\\/\\//,'')}</div>
          <div class="row">
            <button class="linkbtn" data-copy="${s.join}">Copy join link</button>
            <a class="linkbtn" href="${s.host}" target="_blank" rel="noopener">Open host screen</a>
          </div>
        </div>
      </div>`;
    }
    el.innerHTML+=`<div class="row" style="justify-content:flex-end;margin-top:.7rem">
        <button class="stop" data-stop="${s.id}">Stop server</button></div>`;
    $("list").appendChild(el);
  }
}
function show(which){
  $("login").hidden=which!=="login"; $("panel").hidden=which!=="panel";
}
$("list").addEventListener("click",async e=>{
  const c=e.target.closest("[data-copy]"), s=e.target.closest("[data-stop]");
  if(c){navigator.clipboard?.writeText(c.dataset.copy); c.textContent="Copied ✓"; setTimeout(()=>c.textContent="Copy join link",1200);}
  if(s && confirm("Stop this game server?")){ s.disabled=true; await api("/api/stop",{id:s.dataset.stop}); refresh(); }
});
$("start").addEventListener("click",async()=>{
  $("start").disabled=true; $("start-err").textContent="";
  const d=await (await api("/api/start",{})).json();
  if(!d.ok)$("start-err").textContent=d.error||"could not start";
  setTimeout(()=>{$("start").disabled=false;refresh();},600);
});
$("login-btn").addEventListener("click",login);
$("pw").addEventListener("keydown",e=>{if(e.key==="Enter")login();});
async function login(){
  $("login-err").textContent="";
  const r=await api("/api/login",{password:$("pw").value});
  if(r.ok){$("pw").value="";refresh();}
  else $("login-err").textContent=r.status===429?"Too many tries — wait a few minutes.":"Wrong password.";
}
$("logout").addEventListener("click",async e=>{e.preventDefault();await api("/api/logout",{});show("login");});

refresh(); setInterval(()=>{ if(!$("panel").hidden) refresh(); },3000);
</script></div></body></html>
"""


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _authed(self):
        raw = self.headers.get("Cookie", "")
        try:
            jar = http.cookies.SimpleCookie(raw)
        except http.cookies.CookieError:
            return False
        c = jar.get("vblauncher")
        return bool(c) and secrets.compare_digest(c.value, SESSION)

    def _json(self, obj, code=200, cookie=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _read(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            return json.loads(self.rfile.read(n) or b"{}") if n else {}
        except ValueError:
            return {}

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            b = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)
        elif path == "/api/status":
            if not self._authed():
                return self._json({"error": "auth"}, 401)
            self._json({"servers": status()})
        elif path == "/qr":
            if not self._authed():
                return self._json({"error": "auth"}, 401)
            u = parse_qs(urlparse(self.path).query).get("u", [""])[0]
            if not u:
                return self._json({"error": "no url"}, 400)
            png = qr.png(qr.encode(u, "M"), scale=6, border=3)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(png)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        data = self._read()
        if path == "/api/login":
            ok = _login_ok(data.get("password"))
            if ok is None:
                return self._json({"error": "locked"}, 429)
            if not ok:
                return self._json({"error": "bad"}, 403)
            self._json({"ok": True}, cookie=(
                f"vblauncher={SESSION}; Path=/; Max-Age=2592000; "
                f"HttpOnly; SameSite=Lax"))
            return
        if path == "/api/logout":
            return self._json({"ok": True}, cookie=(
                "vblauncher=x; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"))
        if not self._authed():
            return self._json({"error": "auth"}, 401)
        if path == "/api/start":
            self._json(start_server())
        elif path == "/api/stop":
            self._json(stop_server(data.get("id")))
        else:
            self._json({"error": "not found"}, 404)


def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    os.makedirs(LOG_DIR, exist_ok=True)
    with _lock:
        _load()
    srv = ThreadingHTTPServer((BIND, LAUNCHER_PORT), H)
    print(f"  VibeBox Launcher on http://{BIND}:{LAUNCHER_PORT}  "
          f"({len(servers)} server(s) re-attached)", flush=True)

    def _die(*_):
        raise KeyboardInterrupt   # SIGINT already does this; make SIGTERM too
    signal.signal(signal.SIGTERM, _die)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        # leave running game servers alone — a launcher restart shouldn't kill
        # a game in progress; _load() re-attaches on the next start.
        print("  launcher stopped (game servers left running)", flush=True)


if __name__ == "__main__":
    main()
