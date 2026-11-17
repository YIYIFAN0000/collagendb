#!/usr/bin/env python3
"""Enable HTTPS for the verified Colformer Atlas deployment; run in a root TTY."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time

STATE = Path("/var/lib/collagendb-deploy/state.json")
CONFIG = Path("/etc/nginx/conf.d/collagendb.com.conf")
VENV = Path("/opt/collagendb-certbot")
MARKER = VENV / ".collagendb-managed"
MARKER_TEXT = "collagendb-enable_https.py v1\n"
CERTBOT = str(VENV / "bin/certbot")
PYTHON = "/usr/bin/python3.11"
DOMAINS = ("collagendb.com", "www.collagendb.com")
UNIT = "collagendb-certbot-renew"
SERVICE = """# Managed by collagendb-enable_https.py v1
[Unit]
Description=Renew collagendb.com HTTPS certificate
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
TimeoutStartSec=600
ExecStart=/opt/collagendb-certbot/bin/certbot renew --config /dev/null --quiet --cert-name collagendb.com
"""
TIMER = """# Managed by collagendb-enable_https.py v1
[Unit]
Description=Check collagendb.com certificate renewal twice daily
[Timer]
OnCalendar=*-*-* 00,12:00:00
RandomizedDelaySec=3600
Persistent=true
[Install]
WantedBy=timers.target
"""


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(args, env=None):
    subprocess.run(args, check=True, env=env)


def guarded_unit(path, expected):
    if os.path.lexists(path):
        require(not path.is_symlink() and path.is_file() and path.read_text() == expected,
                "Refusing to overwrite unrelated systemd unit: " + str(path))


def verify_https(index_digest):
    context = ssl.create_default_context()
    for host in DOMAINS:
        with socket.create_connection(("127.0.0.1", 443), timeout=20) as connection:
            with context.wrap_socket(connection, server_hostname=host) as tls:
                request = "GET / HTTP/1.1\r\nHost: {}\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n".format(host)
                tls.sendall(request.encode("ascii"))
                response = http.client.HTTPResponse(tls)
                response.begin()
                body = response.read(1024 * 1024)
                require(response.status == 200 and hashlib.sha256(body).hexdigest() == index_digest,
                        "HTTPS content check failed for " + host)


def main():
    require(sys.platform == "linux" and os.geteuid() == 0, "Run on the ECS server as root.")
    require(sys.stdin.isatty(), "An interactive root terminal is required for your email and terms choices.")
    require(STATE.is_file() and not STATE.is_symlink(), "Verified website deployment state is missing.")
    state = json.loads(STATE.read_text())
    require(state.get("managed_by") == "collagendb-deploy_aliyun.py" and state.get("http_verified")
            and state.get("nginx_kind") == "os" and state.get("nginx_binary") == "/usr/sbin/nginx"
            and state.get("nginx_config") == str(CONFIG), "Unsupported or unrecognized website deployment.")
    require(CONFIG.is_file() and not CONFIG.is_symlink()
            and CONFIG.read_text().startswith("# Managed by collagendb-deploy_aliyun.py v1;"),
            "The website's managed Nginx configuration is missing or replaced.")
    webroot, release = Path(state["webroot"]), Path(state["release"])
    require(webroot == Path("/www/wwwroot/collagendb.com") and webroot.is_symlink()
            and os.readlink(webroot) == str(release), "Website root no longer matches its verified release.")
    manifest_bytes = (release / "FILES_SHA256.json").read_bytes()
    require(hashlib.sha256(manifest_bytes).hexdigest() == state.get("manifest_sha256"),
            "Website manifest verification failed.")
    index_digest = json.loads(manifest_bytes.decode("utf-8"))["index.html"]
    require(hashlib.sha256((release / "index.html").read_bytes()).hexdigest() == index_digest,
            "Website index verification failed.")
    service_path = Path("/etc/systemd/system/" + UNIT + ".service")
    timer_path = Path("/etc/systemd/system/" + UNIT + ".timer")
    for path, content in [(service_path, SERVICE), (timer_path, TIMER)]:
        guarded_unit(path, content)
    if os.path.lexists(VENV):
        require(VENV.is_dir() and not VENV.is_symlink() and MARKER.is_file()
                and not MARKER.is_symlink() and MARKER.read_text() == MARKER_TEXT,
                "Refusing to replace an unrelated /opt/collagendb-certbot directory.")
    run(["/usr/sbin/nginx", "-t"])
    run(["systemctl", "is-active", "nginx"])
    expanded = subprocess.check_output(["/usr/sbin/nginx", "-T"], stderr=subprocess.STDOUT).decode("utf-8", "replace")
    sections = re.split(r"(?m)^# configuration file ([^\n]+):\s*$", expanded)
    for offset in range(1, len(sections), 2):
        filename, content = sections[offset:offset + 2]
        if filename != str(CONFIG):
            for names in re.findall(r"(?m)^\s*server_name\s+([^;]+);", content):
                require(not set(names.split()) & set(DOMAINS + (".collagendb.com", "*.collagendb.com")),
                        "Domain is also bound by another Nginx configuration: " + filename)
    if not Path(PYTHON).is_file():
        run(["dnf", "-y", "install", "python3.11"])
    run([PYTHON, "-c", "import sys; assert sys.version_info[:2] == (3, 11)"])
    if not VENV.exists():
        VENV.mkdir(mode=0o700)
        with MARKER.open("x") as marker:
            marker.write(MARKER_TEXT)
    run([PYTHON, "-m", "venv", str(VENV)])
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PIP_")}
    environment["PIP_CONFIG_FILE"] = os.devnull
    environment["PYTHONNOUSERSITE"] = "1"
    pip = [str(VENV / "bin/python"), "-m", "pip", "--disable-pip-version-check"]
    run(pip + ["install", "--upgrade", "--index-url", "https://pypi.org/simple", "pip"], environment)
    run(pip + ["install", "--upgrade", "--index-url", "https://pypi.org/simple", "certbot", "certbot-nginx"], environment)
    run(pip + ["check"], environment)
    run([CERTBOT, "--version"], environment)
    backups = STATE.parent / "https-backups"
    require(not backups.is_symlink(), "Unexpected HTTPS backup directory symlink.")
    backups.mkdir(mode=0o700, exist_ok=True)
    backups.chmod(0o700)
    backup = backups / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + str(os.getpid()))
    backup.mkdir(mode=0o700)
    shutil.copy2(str(CONFIG), str(backup / CONFIG.name))
    print("Nginx configuration backup: " + str(backup), flush=True)
    print("Certbot will ask YOU for an email and agreement to its certificate authority's terms.", flush=True)
    print("No email or agreement is supplied by this script. Default system Python is unchanged.", flush=True)
    run([CERTBOT, "--config", "/dev/null", "--nginx", "--redirect", "--cert-name", DOMAINS[0],
         "-d", DOMAINS[0], "-d", DOMAINS[1]], environment)
    certificate = Path("/etc/letsencrypt/live/collagendb.com")
    require((certificate / "fullchain.pem").is_file() and (certificate / "privkey.pem").is_file(),
            "Certbot returned successfully but expected certificate files are absent.")
    run(["/usr/sbin/nginx", "-t"])
    verify_https(index_digest)
    print("HTTPS certificate and website content verified locally for both domains.", flush=True)
    try:
        run([CERTBOT, "renew", "--config", "/dev/null", "--cert-name", DOMAINS[0], "--dry-run"], environment)
    except subprocess.CalledProcessError:
        print("HTTPS is working. Automatic renewal validation FAILED; the working certificate was retained.")
        print("No new renewal timer was enabled. Review the error and rerun this script to finish renewal setup.")
        print("Manual renewal: " + CERTBOT + " renew --cert-name collagendb.com")
        return 2
    for path, content in [(service_path, SERVICE), (timer_path, TIMER)]:
        guarded_unit(path, content)
        if not path.exists():
            with path.open("x") as stream:
                stream.write(content)
            path.chmod(0o644)
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", UNIT + ".timer"])
    run(["systemctl", "--no-pager", "list-timers", UNIT + ".timer"])
    print("SUCCESS: https://collagendb.com and https://www.collagendb.com are verified.")
    print("Renewal is scheduled twice daily with a random delay; only this site's certificate is managed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.CalledProcessError,
            http.client.HTTPException) as error:
        print("STOPPED: " + str(error), file=sys.stderr)
        print("No other websites or default Python were deliberately changed. Nginx backups are in "
              "/var/lib/collagendb-deploy/https-backups. Review Certbot's output before retrying; "
              "no configuration tree is automatically replaced.", file=sys.stderr)
        sys.exit(1)
