#!/usr/bin/env python3
"""Deploy the verified, pinned Colformer Atlas snapshot on Alibaba Cloud Linux 3."""

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile


VERSION = 1
COMMIT = "b8e549dd12ffa555455f3a4b095690c768df46a6"
MANIFEST_SHA256 = "fb258c30265672149134118e1d26fc97a01fca11dd088e1b5722dae24db05833"
ARCHIVE_URL = "https://codeload.github.com/YIYIFAN0000/collagendb/zip/" + COMMIT
RELEASES = Path("/www/wwwroot/collagendb-releases")
RELEASE = RELEASES / COMMIT
WEBROOT = Path("/www/wwwroot/collagendb.com")
STATE = Path("/var/lib/collagendb-deploy/state.json")
MANAGED_BY = "collagendb-deploy_aliyun.py"
BT_BINARY = Path("/www/server/nginx/sbin/nginx")
OS_BINARY = Path("/usr/sbin/nginx")


class DeployError(Exception):
    pass


def fail(message):
    raise DeployError(message)


def run(args, required=True, timeout=60):
    try:
        result = subprocess.run(args, universal_newlines=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        if required:
            fail("Command could not finish: {} ({})".format(" ".join(args), exc))
        return None
    if required and result.returncode:
        fail("Command failed: {}\n{}".format(" ".join(args), result.stdout.strip()))
    return result


def active_service(name):
    result = run(["systemctl", "is-active", name], required=False)
    return bool(result and result.returncode == 0)


def listeners():
    if not shutil.which("ss"):
        fail("The ss command is required to check existing listeners; no services were stopped.")
    result = run(["ss", "-H", "-ltnp"])
    return [line for line in result.stdout.splitlines()
            if len(line.split()) > 3 and re.search(r":(?:80|443)$", line.split()[3])]


def process_binary(pid):
    try:
        target = os.readlink("/proc/{}/exe".format(pid))
        if target.endswith(" (deleted)"):
            target = target[:-10]
        return Path(target)
    except OSError:
        return None


def running_master(binary):
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or process_binary(entry.name) != binary:
            continue
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ")
        except OSError:
            continue
        if b"nginx: master process" in command:
            return True
    return False


def inspect():
    print("Read-only inspection; no installation, downloads, or configuration changes.")
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith(("PRETTY_NAME=", "ID=", "VERSION_ID=")):
                print(line)
    except OSError:
        pass
    for path in [BT_BINARY, OS_BINARY, WEBROOT, RELEASE,
                 Path("/www/server/panel/vhost/nginx/collagendb.com.conf"),
                 Path("/etc/nginx/conf.d/collagendb.com.conf")]:
        print("{}: {}{}".format(path, "present" if os.path.lexists(path) else "absent",
                                " -> " + os.readlink(path) if path.is_symlink() else ""))
    for command in [["rpm", "-q", "nginx"],
                    ["systemctl", "is-active", "nginx", "httpd", "apache2", "openresty"],
                    ["firewall-cmd", "--state"]]:
        result = run(command, required=False)
        print("$ " + " ".join(command))
        print(result.stdout.strip() if result else "unavailable")
    try:
        print("Listeners on TCP 80/443:")
        print("\n".join(listeners()) or "none")
    except DeployError as exc:
        print(str(exc))


def check_root_path():
    if os.path.lexists(WEBROOT):
        if not WEBROOT.is_symlink() or os.readlink(WEBROOT) != str(RELEASE):
            fail("Refusing to replace existing {}. It must be absent or a symlink to {}. "
                 "Inspect and back up any existing site yourself first.".format(WEBROOT, RELEASE))
    for path in [Path("/www"), RELEASES.parent, RELEASES, RELEASE, STATE.parent]:
        if os.path.lexists(path) and (path.is_symlink() or not path.is_dir()):
            fail("Refusing unexpected directory or symlink: " + str(path))
    if os.path.lexists(STATE):
        if STATE.is_symlink() or not STATE.is_file():
            fail("Refusing unexpected deployment state path: " + str(STATE))
        try:
            previous = json.loads(STATE.read_text())
        except (OSError, ValueError):
            fail("Unrecognized existing deployment state: " + str(STATE))
        if STATE.is_symlink() or previous.get("managed_by") != MANAGED_BY:
            fail("Refusing to overwrite unrelated deployment state: " + str(STATE))


def config_text(logdir):
    return """# Managed by collagendb-deploy_aliyun.py v{version}; snapshot {commit}
server {{
    listen 80;
    server_name collagendb.com www.collagendb.com;
    root /www/wwwroot/collagendb.com;
    index index.html;
    autoindex off;
    charset utf-8;
    etag on;
    add_header Cache-Control "public, no-cache";
    access_log {logdir}/collagendb.com.access.log;
    error_log {logdir}/collagendb.com.error.log;

    gzip on;
    gzip_vary on;
    gzip_comp_level 5;
    gzip_min_length 1024;
    gzip_types text/plain text/css application/javascript text/javascript application/json application/xml;

    location ^~ /.well-known/acme-challenge/ {{
        try_files $uri =404;
    }}
    location ~ /\\.(?!well-known(?:/|$)) {{
        deny all;
    }}
    location / {{
        try_files $uri $uri/ =404;
    }}
}}
""".format(version=VERSION, commit=COMMIT, logdir=logdir)


def check_config_path(config, desired):
    if os.path.lexists(config):
        if config.is_symlink() or not config.is_file() or config.read_bytes() != desired:
            fail("Refusing to replace existing site configuration: {}. "
                 "Only an identical configuration from this script may be reused.".format(config))


def choose_nginx():
    if BT_BINARY.is_file() and os.access(BT_BINARY, os.X_OK):
        binary = BT_BINARY
        config = Path("/www/server/panel/vhost/nginx/collagendb.com.conf")
        kind, logdir = "baota", "/www/wwwlogs"
        if not Path("/etc/init.d/nginx").is_file():
            fail("BaoTa Nginx found, but /etc/init.d/nginx is missing. Inspect the server first.")
    else:
        binary = OS_BINARY
        config = Path("/etc/nginx/conf.d/collagendb.com.conf")
        kind, logdir = "os", "/var/log/nginx"
        for service in ["httpd", "apache2", "apache", "openresty"]:
            if active_service(service):
                fail("Existing {} service is active; refusing to install or replace its web server.".format(service))
        known = [Path("/usr/local/nginx/sbin/nginx"),
                 Path("/usr/local/openresty/nginx/sbin/nginx")]
        found = shutil.which("nginx")
        if found:
            known.append(Path(found))
        for candidate in known:
            if candidate.exists() and candidate.resolve() != OS_BINARY.resolve():
                fail("Another Nginx installation exists: {}. Inspect it before deployment.".format(candidate))
        if active_service("nginx") and not binary.is_file():
            fail("Nginx service is active but /usr/sbin/nginx is absent. Inspect its unit and binary.")
    for line in listeners():
        pids = re.findall(r"pid=(\d+)", line)
        if not pids or any(process_binary(pid) != binary for pid in pids):
            fail("Existing listener is not the selected Nginx; no service was stopped:\n" + line)
    return kind, binary, config, Path(logdir)


def ensure_nginx(kind, binary):
    if kind == "os":
        if not binary.is_file():
            if not shutil.which("dnf"):
                fail("No supported Nginx installation or dnf command. Inspect the operating system first.")
            print("Installing distro Nginx from the server's existing repositories...")
            run(["dnf", "-y", "install", "nginx"], timeout=600)
        owner = run(["rpm", "-qf", str(binary)], required=False)
        if not owner or owner.returncode or not owner.stdout.strip().startswith("nginx"):
            fail("/usr/sbin/nginx is not owned by a recognized nginx RPM; refusing service changes.")
        unit = run(["systemctl", "show", "nginx", "-p", "ExecStart", "--value"])
        if "path=/usr/sbin/nginx " not in unit.stdout:
            fail("Nginx systemd unit does not directly launch /usr/sbin/nginx. Inspect the unit first.")
    if not binary.is_file() or not os.access(binary, os.X_OK):
        fail("Selected Nginx executable is unavailable: " + str(binary))
    run([str(binary), "-t"])


def check_domain_conflicts(binary, config):
    expanded = run([str(binary), "-T"]).stdout
    sections = re.split(r"(?m)^# configuration file ([^\n]+):\s*$", expanded)
    for offset in range(1, len(sections), 2):
        filename, content = sections[offset:offset + 2]
        if filename == str(config):
            continue
        for names in re.findall(r"(?m)^\s*server_name\s+([^;]+);", content):
            if set(names.split()) & {"collagendb.com", "www.collagendb.com",
                                      ".collagendb.com", "*.collagendb.com"}:
                fail("Domain is already configured by another Nginx file: " + filename)


def hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_release(directory, manifest):
    expected = set(manifest) | {"FILES_SHA256.json"}
    actual = set()
    for path in directory.rglob("*"):
        if path.is_symlink():
            fail("Release contains an unexpected symlink: " + str(path))
        if path.is_file():
            actual.add(path.relative_to(directory).as_posix())
    if actual != expected:
        fail("Release file list differs from the verified manifest: " + str(directory))
    if hash_file(directory / "FILES_SHA256.json") != MANIFEST_SHA256:
        fail("Release manifest SHA256 mismatch.")
    for name, expected_hash in manifest.items():
        if hash_file(directory / name) != expected_hash:
            fail("Release SHA256 mismatch: " + name)


def prepare_release():
    print("Downloading pinned website snapshot " + COMMIT + "...")
    archive_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="collagendb-", suffix=".zip", delete=False) as stream:
            archive_path = Path(stream.name)
            request = urllib.request.Request(ARCHIVE_URL, headers={"User-Agent": MANAGED_BY})
            with urllib.request.urlopen(request, timeout=60) as response:
                total = 0
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > 100 * 1024 * 1024:
                        fail("Download exceeded the expected archive size limit.")
                    stream.write(block)
        with zipfile.ZipFile(archive_path) as archive:
            prefix = "collagendb-" + COMMIT + "/"
            entries = {}
            for info in archive.infolist():
                if not info.filename.startswith(prefix) or "\\" in info.filename:
                    fail("Unexpected archive path.")
                name = info.filename[len(prefix):]
                relative = PurePosixPath(name)
                if relative.is_absolute() or ".." in relative.parts or stat.S_ISLNK(info.external_attr >> 16):
                    fail("Unsafe archive entry.")
                if info.is_dir():
                    continue
                if name in entries:
                    fail("Duplicate archive entry.")
                entries[name] = info
            raw_manifest = archive.read(entries["FILES_SHA256.json"])
            if hashlib.sha256(raw_manifest).hexdigest() != MANIFEST_SHA256:
                fail("Pinned archive manifest does not match the original verified package.")
            manifest = json.loads(raw_manifest.decode("utf-8"))
            if len(manifest) != 323:
                fail("Expected exactly 323 original files in the manifest.")
            for name, digest in manifest.items():
                relative = PurePosixPath(name)
                if (name not in entries or relative.is_absolute() or ".." in relative.parts
                        or "\\" in name or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                    fail("Invalid or missing manifest entry: " + name)
            selected = list(manifest) + ["FILES_SHA256.json"]
            if sum(entries[name].file_size for name in selected) > 200 * 1024 * 1024:
                fail("Expanded website exceeds the expected size limit.")
            if RELEASE.exists():
                verify_release(RELEASE, manifest)
                print("Reusing existing verified release.")
                return manifest
            for directory in [Path("/www"), RELEASES.parent, RELEASES]:
                if not directory.exists():
                    directory.mkdir()
                    directory.chmod(0o755)
            staging = Path(tempfile.mkdtemp(prefix=".staging-" + COMMIT[:12] + "-", dir=str(RELEASES)))
            print("Extracting only original package files into " + str(staging))
            for name in selected:
                destination = staging / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entries[name]) as source, destination.open("xb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                destination.chmod(0o644)
            for directory in staging.rglob("*"):
                if directory.is_dir():
                    directory.chmod(0o755)
            staging.chmod(0o755)
            verify_release(staging, manifest)
            if os.path.lexists(RELEASE):
                fail("Release destination appeared during deployment; staging retained at " + str(staging))
            staging.rename(RELEASE)
            print("Verified all 323 original files and the manifest.")
            return manifest
    finally:
        if archive_path and archive_path.is_file():
            archive_path.unlink()


def configure_site(binary, config, logdir, desired):
    check_config_path(config, desired)
    if not config.parent.is_dir():
        fail("Expected Nginx configuration directory is absent: " + str(config.parent))
    logdir.mkdir(parents=True, exist_ok=True)
    created_root = False
    created_config = False
    try:
        if not os.path.lexists(WEBROOT):
            WEBROOT.symlink_to(RELEASE, target_is_directory=True)
            created_root = True
        if not os.path.lexists(config):
            with config.open("xb") as stream:
                stream.write(desired)
            config.chmod(0o644)
            created_config = True
        run([str(binary), "-t"])
        expanded = run([str(binary), "-T"])
        if "# configuration file {}:".format(config) not in expanded.stdout:
            fail("Selected Nginx does not include this site's configuration directory. Inspect nginx.conf first.")
    except Exception:
        # Only remove files or symlinks created by this invocation; never touch existing sites.
        if created_config and config.is_file() and not config.is_symlink() and config.read_bytes() == desired:
            config.unlink()
        if created_root and WEBROOT.is_symlink() and os.readlink(WEBROOT) == str(RELEASE):
            WEBROOT.unlink()
        raise
    return created_root, created_config


def rollback_own_site(binary, config, desired, created):
    created_root, created_config = created
    if created_config and config.is_file() and not config.is_symlink() and config.read_bytes() == desired:
        config.unlink()
    if created_root and WEBROOT.is_symlink() and os.readlink(WEBROOT) == str(RELEASE):
        WEBROOT.unlink()
    if created_config and running_master(binary):
        # Reload the remaining valid configuration; no service is stopped.
        run([str(binary), "-t"])
        run([str(binary), "-s", "reload"])


def start_site(kind, binary):
    if kind == "baota":
        action = "reload" if running_master(binary) else "start"
        run(["/etc/init.d/nginx", action])
    else:
        was_active = active_service("nginx")
        run(["systemctl", "enable", "--now", "nginx"])
        if was_active:
            run(["systemctl", "reload", "nginx"])


def verify_http(manifest):
    for host in ["collagendb.com", "www.collagendb.com"]:
        connection = http.client.HTTPConnection("127.0.0.1", 80, timeout=15)
        try:
            connection.request("GET", "/", headers={"Host": host, "Accept-Encoding": "identity"})
            response = connection.getresponse()
            content = response.read(1024 * 1024)
            if response.status != 200 or hashlib.sha256(content).hexdigest() != manifest["index.html"]:
                fail("Local HTTP verification failed for {} (HTTP {}). Configuration retained for inspection."
                     .format(host, response.status))
        finally:
            connection.close()


def open_host_firewall():
    status = run(["firewall-cmd", "--state"], required=False)
    if status and status.returncode == 0:
        for service in ["http", "https"]:
            run(["firewall-cmd", "--permanent", "--add-service=" + service])
            run(["firewall-cmd", "--add-service=" + service])
        print("Added HTTP/HTTPS to the running host firewalld; other rules were preserved.")
    else:
        print("Host firewalld is not running; no firewall settings changed.")


def write_state(kind, binary, config):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    state = {"managed_by": MANAGED_BY, "version": VERSION, "commit": COMMIT,
             "manifest_sha256": MANIFEST_SHA256, "webroot": str(WEBROOT),
             "release": str(RELEASE), "nginx_kind": kind, "nginx_binary": str(binary),
             "nginx_config": str(config), "http_verified": True,
             "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=str(STATE.parent),
                                     prefix=".state-", delete=False) as stream:
        json.dump(state, stream, indent=2)
        stream.write("\n")
        state_temp = Path(stream.name)
    state_temp.chmod(0o644)
    os.replace(state_temp, STATE)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", action="store_true", help="read-only environment inspection")
    arguments = parser.parse_args()
    if sys.platform != "linux":
        fail("Run this script on the Alibaba Cloud Linux server, not your local computer.")
    if arguments.inspect:
        inspect()
        return
    if os.geteuid() != 0:
        fail("Run this deployment from the existing root Shell, or with sudo python3.")
    check_root_path()
    kind, binary, config, logdir = choose_nginx()
    desired = config_text(logdir).encode("utf-8")
    check_config_path(config, desired)
    ensure_nginx(kind, binary)
    check_domain_conflicts(binary, config)
    manifest = prepare_release()
    check_root_path()
    created = configure_site(binary, config, logdir, desired)
    try:
        start_site(kind, binary)
        verify_http(manifest)
    except Exception:
        try:
            rollback_own_site(binary, config, desired, created)
        except Exception as rollback_error:
            print("Rollback needs inspection: " + str(rollback_error), file=sys.stderr)
        raise
    write_state(kind, binary, config)
    open_host_firewall()
    print("\nSUCCESS: verified website is serving on local HTTP for both domain names.")
    print("Release: " + str(RELEASE))
    print("Deployment state: " + str(STATE))
    print("Check: curl -I -H 'Host: collagendb.com' http://127.0.0.1/")
    print("Check gzip: curl -I -H 'Host: collagendb.com' -H 'Accept-Encoding: gzip' http://127.0.0.1/data/index.js")
    print("Next: allow inbound TCP 80/443 in this ECS instance's Aliyun security group.")
    print("Aliyun DNS: @ A 8.218.195.119; www A 8.218.195.119; default line; TTL 10 minutes.")
    print("Then verify public HTTP and issue an HTTPS certificate for both domains.")
    print("This script did not change cloud security groups or issue certificates.")


if __name__ == "__main__":
    try:
        main()
    except (DeployError, OSError, ValueError, KeyError, zipfile.BadZipFile, http.client.HTTPException) as exc:
        print("\nSTOPPED: " + str(exc), file=sys.stderr)
        print("Existing sites and services were not deleted or stopped. "
              "Run with --inspect and review the output before retrying.", file=sys.stderr)
        sys.exit(1)
