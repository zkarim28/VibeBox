"""Startup notifications for VibeBox (ntfy push, optional SMTP/email-to-SMS).

Set NOTIFY_WEBHOOK=https://ntfy.sh/<your-random-topic> in the repo's .env.
Copy that setting to each computer running VibeBox to use the same topic.
Optional NOTIFY_TOKEN authenticates to an account-protected ntfy topic.
Run `python3 notify.py --test-push` to test only ntfy with clearly labelled
example links. `--test` also tests any configured SMTP destinations.
"""

import os
import socket
import time
import smtplib
import ssl
import sys
import threading
import urllib.request
import urllib.error
from urllib.parse import urlsplit, urlunsplit
from email.message import EmailMessage

DEFAULT_SMS = "6313741134"
DEFAULT_SMS_GATEWAY = "tmomail.net"     # T-Mobile email-to-SMS
_HERE = os.path.dirname(os.path.abspath(__file__))


def load_env_file(path=None):
    """Pull KEY=VALUE lines from .env into os.environ (does not overwrite
    anything already set). Called on import so `python3 server.py` alone works."""
    path = path or os.path.join(_HERE, ".env")
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


load_env_file()


def _env(name, default=""):
    return os.environ.get(name, "").strip() or default


def _smtp_ready():
    return bool(_env("SMTP_HOST") and _env("SMTP_USER") and _env("SMTP_PASS"))


def _ca_candidates():
    paths = []
    try:
        import certifi
        paths.append(certifi.where())
    except Exception:
        pass
    paths += [
        "/etc/ssl/cert.pem",                     # macOS system OpenSSL
        "/etc/pki/tls/certs/ca-bundle.crt",      # Fedora / RHEL
        "/etc/ssl/certs/ca-certificates.crt",    # Debian / Ubuntu / Arch
        "/opt/homebrew/etc/openssl@3/cert.pem",  # homebrew (Apple silicon)
        "/usr/local/etc/openssl@3/cert.pem",     # homebrew (Intel)
    ]
    dvp = ssl.get_default_verify_paths()
    if dvp.cafile:
        paths.append(dvp.cafile)
    return paths


def _ssl_context(allow_insecure=True):
    """A verifying context that still works on the python.org macOS build,
    whose trust store is empty until 'Install Certificates.command' is run."""
    ctx = ssl.create_default_context()
    if allow_insecure and _env("SMTP_INSECURE").lower() in ("1", "true", "yes"):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    if ctx.cert_store_stats().get("x509_ca", 0) > 0:
        return ctx
    for cand in _ca_candidates():
        try:
            ctx.load_verify_locations(cafile=cand)
        except (OSError, ssl.SSLError):
            continue
        if ctx.cert_store_stats().get("x509_ca", 0) > 0:
            return ctx
    return ctx     # still empty — the caller will surface a clear verify error


def _send_email(to_addrs, subject, body):
    host = _env("SMTP_HOST")
    port = int(_env("SMTP_PORT", "587") or "587")
    user = _env("SMTP_USER")
    pw = _env("SMTP_PASS")
    sender = _env("SMTP_FROM", user)

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(to_addrs)
    msg["Subject"] = subject
    msg.set_content(body)

    ctx = _ssl_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=20) as s:
            s.login(user, pw)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.ehlo()
            s.starttls(context=ctx)
            s.login(user, pw)
            s.send_message(msg)


def _send_webhook(url, text, click=None, play=None):
    headers = {"Content-Type": "text/plain; charset=utf-8",
               "Title": "VibeBox is live", "Tags": "video_game"}
    if click:
        headers["Click"] = click
    if click and play:
        headers["Actions"] = f"view, Host and play, {click}; view, Join game, {play}"
    if _env("NOTIFY_TOKEN"):
        headers["Authorization"] = "Bearer " + _env("NOTIFY_TOKEN")
    req = urllib.request.Request(url, data=text.encode("utf-8"), headers=headers)
    # Reuse the macOS/Linux CA discovery, but never disable push TLS checks
    # merely because an SMTP troubleshooting flag was set.
    context = _ssl_context(allow_insecure=False)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15, context=context) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code != 429 and exc.code < 500:
                raise
            if attempt == 2:
                raise
        except (urllib.error.URLError, OSError):
            if attempt == 2:
                raise
        time.sleep(1 + attempt * 2)


def _push_message(links):
    """Push notifications never carry a host unlock token or password."""
    parts = urlsplit(links.get("url", ""))
    url = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
    code = links.get("code", "")
    host = f"{url}/host/play"
    play = f"{url}/{code}" if code else f"{url}/play"
    public = links.get("public", True)
    mode = "Public game" if public else "Local game — join on the same WiFi"
    computer = links.get("source") or socket.gethostname()
    text = (f"VibeBox is ready on {computer}.\n{mode}\n\n"
            f"Host & play (host password required):\n{host}\n\n"
            f"Players join:\n{play}\n\nRoom code: {code}")
    return text, host, play


def _sms_addr():
    number = "".join(ch for ch in _env("NOTIFY_SMS", DEFAULT_SMS) if ch.isdigit())
    gateway = _env("SMS_GATEWAY", DEFAULT_SMS_GATEWAY)
    if not number or gateway.lower() in ("off", "none", "no"):
        return None
    return f"{number}@{gateway}"


def _compose(links):
    """(sms_text, email_text)."""
    url = links.get("url", "").rstrip("/")
    code = links.get("code", "")
    host_you = links.get("host_you") or (url + "/host")
    host_other = links.get("host_other") or (url + "/host")
    play = links.get("play") or (url + "/play")
    src = links.get("source", "")

    # keep the SMS to ONE link + the code — carriers filter link-heavy texts
    sms_text = (f"VibeBox is live: {url}  code {code}  "
                f"(open {url}/host and enter the host password)")

    email_text = (
        f"VibeBox / Party Games is LIVE ({'public' if links.get('public', True) else 'local WiFi'})"
        f"{f' ({src})' if src else ''}.\n\n"
        f"HOST (your browser, one-click):\n    {host_you}\n\n"
        f"HOST (any other machine, then the password):\n    {host_other}\n\n"
        f"PLAYERS join at:\n    {play}\n\n"
        f"Room code: {code}\nTunnel:    {url}\n"
    )
    return sms_text, email_text


def notify_server_started(links):
    """Notify once startup has picked its final public or LAN URL, off-thread."""
    if not _smtp_ready() and not _env("NOTIFY_WEBHOOK"):
        return None
    thread = threading.Thread(target=_run, args=(dict(links),), daemon=True)
    thread.start()
    return thread


# Retain the old entry point for callers outside server.py.
notify_public_link = notify_server_started


def _run(links):
    sms_text, email_text = _compose(links)
    sent, failed = [], []

    if _smtp_ready():
        addr = _sms_addr()
        if addr:
            try:
                _send_email([addr], "VibeBox", sms_text)
                sent.append(f"text -> {addr}")
            except Exception as exc:           # report, never crash startup
                failed.append(f"text ({exc})")

        inbox = _env("NOTIFY_EMAIL")
        if inbox and inbox.lower() not in ("off", "none", "no"):
            try:
                _send_email([inbox], "VibeBox is live (public link)", email_text)
                sent.append(f"email -> {inbox}")
            except Exception as exc:
                failed.append(f"email ({exc})")
    elif not _env("NOTIFY_WEBHOOK"):
        print("  (no SMTP set — copy .env.example to .env to get the link "
              "texted to you; run `python3 notify.py --test` to check it)")

    hook = _env("NOTIFY_WEBHOOK")
    if hook:
        try:
            push_text, host, play = _push_message(links)
            _send_webhook(hook, push_text, click=host, play=play)
            sent.append("push")
        except Exception as exc:
            # Do not log a private topic URL or authorization token.
            failed.append(f"push ({type(exc).__name__}{': HTTP ' + str(exc.code) if isinstance(exc, urllib.error.HTTPError) else ''})")

    if sent:
        print(f"  ✉  sent: {', '.join(sent)}")
    if failed:
        print(f"  ✉  FAILED: {', '.join(failed)}")
    return sent, failed


def _selftest(push_only=False):
    print("VibeBox notify self-test\n" + "-" * 40)
    if not _smtp_ready() and not _env("NOTIFY_WEBHOOK"):
        print("Not configured. Create .env (see .env.example) with NOTIFY_WEBHOOK "
              "or SMTP_HOST, SMTP_USER, SMTP_PASS — then run this again.")
        return 1
    if _smtp_ready() and not push_only:
        print(f"SMTP:     {_env('SMTP_USER')} via {_env('SMTP_HOST')}:"
              f"{_env('SMTP_PORT', '587')}")
        print(f"Texting:  {_sms_addr() or '(disabled)'}")
        if _env("NOTIFY_EMAIL"):
            print(f"Emailing: {_env('NOTIFY_EMAIL')}")
    if _env("NOTIFY_WEBHOOK"):
        print(f"Push:     {_env('NOTIFY_WEBHOOK')}")
    print("-" * 40)
    links = {
        "url": "https://example-test.trycloudflare.com",
        "code": "TEST", "source": "self-test",
        "host_you": "https://example-test.trycloudflare.com/host?host=xxx",
        "host_other": "https://example-test.trycloudflare.com/host",
        "play": "https://example-test.trycloudflare.com/play?code=TEST",
    }
    if push_only:
        hook = _env("NOTIFY_WEBHOOK")
        if not hook:
            print("Set NOTIFY_WEBHOOK in .env first.")
            return 1
        try:
            text, host, play = _push_message(links)
            _send_webhook(hook, "SETUP TEST — example links only, no game is running.\n\n" + text)
        except Exception as exc:
            print(f"Push test failed ({type(exc).__name__}). Check network and ntfy settings.")
            return 1
        print("Test accepted by ntfy. Subscribe to this topic on your phone.")
        return 0
    sent, failed = _run(links)
    print("-" * 40)
    if failed:
        print("Something failed above. Common fixes:")
        print("  - Gmail: SMTP_PASS must be a 16-char App Password, 2FA on.")
        print("  - 'Username and Password not accepted' -> wrong/again App Password.")
        print("  - 'CERTIFICATE_VERIFY_FAILED' on a Mac -> run once:")
        print("      /Applications/Python*/Install\\ Certificates.command")
        print("      (or add SMTP_INSECURE=1 to .env as a last resort)")
        return 1
    print("Handed off OK. Check your phone within ~1 minute.")
    print("If the text never arrives, T-Mobile filtered it (links from email "
          "gateways get blocked sometimes) — set NOTIFY_WEBHOOK to an ntfy.sh "
          "topic instead, then verify delivery in the ntfy app.")
    return 0


if __name__ == "__main__":
    if "--test-push" in sys.argv:
        sys.exit(_selftest(push_only=True))
    if "--test" in sys.argv or "-t" in sys.argv:
        sys.exit(_selftest())
    print(__doc__)
