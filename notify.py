"""
Text the owner the public links whenever a new Cloudflare tunnel opens.

There is no "just send an SMS" API in the Python stdlib, so the text goes out
as a short e-mail to your carrier's email-to-SMS gateway. That still needs an
outgoing mail account (SMTP) to hand the message to — a free Gmail "App
Password" is the easiest. Nothing is sent unless you configure it.

  REQUIRED for the text (put these in .env):
      SMTP_HOST      smtp.gmail.com
      SMTP_PORT      587           (587 = STARTTLS, 465 = SSL)
      SMTP_USER      your.address@gmail.com
      SMTP_PASS      16-char Google App Password  (NOT your login password)

  OPTIONAL:
      NOTIFY_SMS     phone number, digits only    (default: 6313741134)
      SMS_GATEWAY    carrier gateway host         (default: tmomail.net = T-Mobile)
                       Verizon vzwpix.com / vtext.com   AT&T txt.att.net
      NOTIFY_EMAIL   also e-mail the full links here   (off by default; set an
                     address to turn it on)
      NOTIFY_WEBHOOK ALSO push via ntfy.sh etc. — a URL the text is POSTed to.
                     Use this if the carrier filters the SMS (see --test).
      SMTP_INSECURE  "1" to skip TLS cert verification (last resort if a Mac
                     can't verify certs and you can't run Install Certificates).

Test it any time WITHOUT opening a tunnel:

      python3 notify.py --test

That sends a real message with dummy links and prints exactly what happened.
"""

import os
import smtplib
import ssl
import sys
import threading
import urllib.request
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


def _ssl_context():
    """A verifying context that still works on the python.org macOS build,
    whose trust store is empty until 'Install Certificates.command' is run."""
    ctx = ssl.create_default_context()
    if _env("SMTP_INSECURE").lower() in ("1", "true", "yes"):
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


def _send_webhook(url, text):
    req = urllib.request.Request(
        url, data=text.encode("utf-8"),
        headers={"Content-Type": "text/plain; charset=utf-8",
                 "Title": "VibeBox is live", "Tags": "video_game"})
    urllib.request.urlopen(req, timeout=15).read()


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
        f"VibeBox / Party Games is LIVE on a public link"
        f"{f' ({src})' if src else ''}.\n\n"
        f"HOST (your browser, one-click):\n    {host_you}\n\n"
        f"HOST (any other machine, then the password):\n    {host_other}\n\n"
        f"PLAYERS join at:\n    {play}\n\n"
        f"Room code: {code}\nTunnel:    {url}\n"
    )
    return sms_text, email_text


def notify_public_link(links):
    """Fire every configured channel on a daemon thread. Returns immediately."""
    threading.Thread(target=_run, args=(links,), daemon=True).start()


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
            _send_webhook(hook, sms_text)
            sent.append("push")
        except Exception as exc:
            failed.append(f"push ({exc})")

    if sent:
        print(f"  ✉  sent: {', '.join(sent)}")
    if failed:
        print(f"  ✉  FAILED: {', '.join(failed)}")
    return sent, failed


def _selftest():
    print("VibeBox notify self-test\n" + "-" * 40)
    if not _smtp_ready() and not _env("NOTIFY_WEBHOOK"):
        print("Not configured. Create .env (see .env.example) with SMTP_HOST, "
              "SMTP_USER, SMTP_PASS — then run this again.")
        return 1
    if _smtp_ready():
        print(f"SMTP:     {_env('SMTP_USER')} via {_env('SMTP_HOST')}:"
              f"{_env('SMTP_PORT', '587')}")
        print(f"Texting:  {_sms_addr() or '(disabled)'}")
        if _env("NOTIFY_EMAIL"):
            print(f"Emailing: {_env('NOTIFY_EMAIL')}")
    if _env("NOTIFY_WEBHOOK"):
        print(f"Push:     {_env('NOTIFY_WEBHOOK')}")
    print("-" * 40)
    sent, failed = _run({
        "url": "https://example-test.trycloudflare.com",
        "code": "TEST", "source": "self-test",
        "host_you": "https://example-test.trycloudflare.com/host?host=xxx",
        "host_other": "https://example-test.trycloudflare.com/host",
        "play": "https://example-test.trycloudflare.com/play?code=TEST",
    })
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
          "topic instead; that always lands.")
    return 0


if __name__ == "__main__":
    if "--test" in sys.argv or "-t" in sys.argv:
        sys.exit(_selftest())
    print(__doc__)
