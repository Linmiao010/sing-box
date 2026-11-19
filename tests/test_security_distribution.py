"""Publishing with two dummy identities against a loopback Git HTTP server."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import shlex
import shutil
import subprocess
import threading
import unittest
from urllib.parse import urlsplit

from shell_testlib import REPO, ShellTestCase, section


class DistributionTests(ShellTestCase):
    def setUp(self):
        super().setUp()
        self.box = self.root / "box"
        self.box.mkdir()
        self.products = ("sbox.json", "clmi.yaml", "jhsub.txt")
        for name in self.products:
            (self.box / name).write_text("fixture " + name + "\n", encoding="utf-8")

    def test_publisher_and_reader_permissions(self):
        repo = self.root / "group/project.git"
        repo.parent.mkdir()
        subprocess.run(["git", "init", "-q", "--bare", str(repo)], check=True)
        # The test server authorizes fetch separately from receive-pack.
        write, read = "write-credential-example", "read-credential-example"
        storage = self.root
        statuses = []
        requests = []
        visibility = [404]
        class GitHTTP(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle_git(self):
                path = urlsplit(self.path)
                requests.append(path.path)
                if path.path == "/api/v4/projects/group%2Fproject":
                    self.send_response(visibility[0])
                    self.end_headers()
                    return
                auth = self.headers.get("Authorization", "")
                creds = base64.b64decode(auth[6:]).decode() if auth.startswith("Basic ") else ""
                identity = creds.partition(":")[2]
                pushing = "git-receive-pack" in self.path
                if identity not in (read, write) or (pushing and identity != write):
                    status = 403 if identity in (read, write) else 401
                    self.send_response(status)
                    self.send_header("WWW-Authenticate", 'Basic realm="fixture"')
                    self.end_headers()
                    statuses.append(status)
                    return
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                env = dict(os.environ, GIT_PROJECT_ROOT=str(storage), GIT_HTTP_EXPORT_ALL="1",
                           PATH_INFO=path.path, REQUEST_METHOD=self.command,
                           QUERY_STRING=path.query, CONTENT_TYPE=self.headers.get("Content-Type", ""),
                           CONTENT_LENGTH=str(len(body)), REMOTE_USER="fixture",
                           SERVER_PROTOCOL="HTTP/1.1")
                output = subprocess.run(["git", "http-backend"], env=env, input=body,
                                        capture_output=True, check=True).stdout
                headers, data = output.split(b"\r\n\r\n", 1)
                self.send_response(200)
                for h in headers.decode().split("\r\n"):
                    k, v = h.split(":", 1)
                    self.send_header(k, v.strip())
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = handle_git

        server = ThreadingHTTPServer(("127.0.0.1", 0), GitHTTP)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        global_git = self.root / "global.gitconfig"
        global_git.write_text(f'[url "http://127.0.0.1:9/"]\n\tinsteadOf = {base}/\n', newline="\n")
        settings = self.box / "gitlab-publisher"
        functions = section("gitlab_settings_valid", "gitlab_clear_legacy").replace(
            "/etc/s-box", self.box.as_posix()).replace("https://gitlab.com/", base + "/")
        def publish(secret, verify=False):
            settings.write_text("group/project\nmain\n" + secret + "\n", encoding="utf-8", newline="\n")
            test_source = functions.replace(") >/dev/null 2>&1", ")")
            if os.name == "nt":
                # shell_testlib disables MSYS argument translation; curl.exe needs the Windows null device.
                test_source = "flock(){ :; }\n" + test_source.replace("--output /dev/null", "--output NUL")
            result = self.run_shell(test_source + "\ngitlab_publish " + shlex.quote(settings.as_posix())
                                    + (" verify-write" if verify else ""),
                                    check=False, env={"NO_PROXY": "127.0.0.1",
                                                      "GIT_CONFIG_GLOBAL": global_git.as_posix()})
            self.assertNotIn(secret, result.stdout + result.stderr)
            self.git_status = f"{result.stderr} {requests} {statuses}"
            return result.returncode
        visibility[0] = 200
        self.assertNotEqual(publish(write), 0, "must reject anonymous project access")
        visibility[0] = 404
        self.assertEqual(publish(write, verify=True), 0, self.git_status)
        self.assertNotEqual(publish(read, verify=True), 0, "read-only must fail even with unchanged files")
        (self.box / "clmi.yaml").write_text("updated\n", encoding="utf-8")
        self.assertNotEqual(publish(read), 0)
        self.assertIn(403, statuses)
        self.assertEqual(publish(write), 0)
        self.assertEqual(publish(write), 0)
        def git(*args):
            return subprocess.check_output(["git", "--git-dir", str(repo), *args]).decode().strip()
        self.assertEqual(set(git("ls-tree", "--name-only", "main").splitlines()), set(self.products))
        for name in self.products:
            self.assertEqual(git("show", "main:" + name), (self.box / name).read_text().strip())
        self.assertEqual(git("rev-list", "--count", "main"), "2")
        self.assertFalse(list(self.box.glob(".git-publish.*")))

    def test_old_token_removed_from_git_and_notification_and_qr(self):
        write, read = "old-push-token-demo", "separate-caddy-path"
        legacy_names = ("sing_box_gitlab.txt", "clash_meta_gitlab.txt", "jh_sub_gitlab.txt",
                        "gitpush.sh", "gitlabtoken.txt", "gitlab_ml_ml")
        for name in legacy_names:
            (self.box / name).write_text(write, encoding="utf-8")
        (self.box / "subdomain.log").write_text("sub.example.test", encoding="utf-8")
        (self.box / "subtoken.log").write_text(read, encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(self.box)], check=True)
        subprocess.run(["git", "-C", str(self.box), "remote", "add", "origin",
                        f"https://{write}@gitlab.com/group/project.git"], check=True)
        tg = self.box / "sbtg.sh"
        tg.write_text('#!/bin/bash\nm9=$(cat ' + self.box.as_posix()
                      + '/sing_box_gitlab.txt 2>/dev/null)\nprintf "%s" "$m9"\n', encoding="utf-8")
        funcs = (section("sub_migrate_tg", "tgnotice") + section("sub_valid_domain", "sub_service")
                 + section("sub_show_urls", "ipsub") + section("gitlab_clear_legacy", "gitlabsub"))
        script = funcs.replace("/etc/s-box", self.box.as_posix()) + """
blue(){ echo "$1"; }; qrencode(){ cat; echo; }; red(){ echo "$1" >&2; }
printf '%s\\n' '12345:fake' '98765' > box/telegram.credentials
sub_migrate_tg && sub_show_urls && gitlab_clear_legacy
"""
        result = self.run_shell(script)
        self.assertNotIn(write, result.stdout + result.stderr)
        self.assertNotIn("gitlab", tg.read_text())
        for name in self.products:
            self.assertGreaterEqual(result.stdout.count("https://sub.example.test/" + read + "/" + name), 2)
        self.assertTrue(all(not (self.box / f).exists() for f in legacy_names))
        self.assertNotIn(write, (self.box / ".git/config").read_text())

    def test_restart_schedule_preserves_other_applications_jobs(self):
        unrelated = "0 1 * * * /opt/sing-box-monitor --url http://localhost/metrics\n"
        owned = '@reboot sleep 10 && nohup /etc/s-box/cloudflared tunnel --url http://localhost:9001\n'
        cron = self.root / "cron"
        cron.write_bytes((unrelated + owned).encode())
        functions = section("cronsb", "lnsb") + '\ncrontab(){ if [[ $1 == -l ]]; then cat cron; else cp "$1" cron; fi; }\n'
        self.run_shell(functions + "cronsb && cronsb")
        self.assertIn(owned, cron.read_text())
        self.assertEqual(cron.read_text().count("/usr/bin/sing_box --maintenance"), 1)
        self.run_shell(functions + "uncronsb")
        self.assertEqual(cron.read_text(), unrelated)

    def test_generated_http_listener_always_binds_loopback_and_drops_privileges(self):
        fn = section("sub_valid_domain", "sub_service") + section("sub_httpd_write", "sub_httpd_start")
        fn = fn.replace("/etc/s-box", self.box.as_posix())
        for domain in ("", "sub.example.test"):
            self.run_shell(fn + f"\nsub_httpd_write {shlex.quote(domain)} 18765")
            self.assertRegex((self.box / "sub-httpd.sh").read_text(),
                             r"httpd -f -p 127\.0\.0\.1:18765 -u sboxsub:sboxsub -h ")

    def test_legacy_listener_survives_failed_probe_and_closes_after_success(self):
        legacy_root = self.root / "oldweb"
        old = "old-demo-token-12345"
        new = "new-demo-token-12345"
        (legacy_root / old).mkdir(parents=True)
        scripts = section("sub_valid_domain", "vmesscfadd")
        paths = {"/etc/s-box": self.box, "/root/websbox": legacy_root,
                 "/etc/caddy": self.root / "caddy", "/etc/local.d": self.root / "local",
                 "/etc/init.d": self.root / "init", "/etc/systemd/system": self.root / "systemd",
                 "/var/lib/sbox-subscription": self.root / "stage"}
        for src, dst in paths.items():
            scripts = scripts.replace(src, dst.as_posix())
        for value, name in ((old, "subtoken.log"), ("18080", "subport.log")):
            (self.box / name).write_bytes((value + "\n").encode())
        scripts += """
red(){ :; }; sub_service(){ return 0; }; sub_caddy_prepare(){ echo managed; }
sub_caddy_apply(){ :; }; sub_caddy_reload(){ :; }; sub_prepare_reader(){ :; }
crontab(){ :; }; sub_httpd_write(){ :; }; sub_httpd_start(){ :; }; sub_httpd_boot(){ :; }
sub_links(){ :; }; sub_unlink(){ :; }; sbshare(){ echo regenerated > bad; }
sub_httpd_running(){ [[ $1 == *oldweb ]]; }
sub_httpd_stop(){ if [[ $1 == *oldweb ]]; then echo stop >> events; fi; }
sub_https_ready(){ echo probe >> events; [[ $can_verify == yes ]]; }
"""
        call = f"\nsub_apply_locked sub.example.test {new} 18081"
        self.assertNotEqual(self.run_shell(scripts + "\ncan_verify=no" + call, check=False).returncode, 0)
        self.assertEqual((self.root / "events").read_text().splitlines(), ["probe"])
        self.assertTrue((legacy_root / old).exists())
        self.assertEqual((self.box / "subtoken.log").read_text().strip(), old)
        self.run_shell(scripts + "\ncan_verify=yes" + call)
        self.assertEqual((self.root / "events").read_text().splitlines(), ["probe", "probe", "stop"])
        self.assertFalse((legacy_root / old).exists())
        self.assertFalse((self.root / "bad").exists())
        self.assertEqual((self.box / "subtoken.log").read_text().strip(), new)

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0,
                         "requires root in a disposable Linux environment")
    def test_linux_reader_can_only_read_three_published_files(self):
        import socket
        import time
        import urllib.request
        for program in ("setpriv", "busybox"):
            if not shutil.which(program):
                self.skipTest("requires Linux " + program)
        # These numeric identities live only in the subprocess credentials.
        reader = 63572
        other = 63573
        root = self.root / "published"
        web = root / "www"
        web.mkdir(parents=True)
        self.root.chmod(0o711)
        self.box.chmod(0o700)
        (self.box / "private.key").write_text("test only")
        for directory in (root, web):
            os.chown(directory, 0, reader)
            directory.chmod(0o750)
        fn = (section("sub_valid_domain", "sub_service") + section("sub_links", "sub_publish_current")
              + section("sub_httpd_write", "sub_httpd_start"))
        fn = fn.replace("/etc/s-box", self.box.as_posix()).replace("/var/lib/sbox-subscription", root.as_posix())
        fn = fn.replace("sboxsub", str(reader))
        token = "private-demo-token"
        self.run_shell(fn + "\nsub_links " + token)
        def as_identity(uid, *args):
            return subprocess.run(["setpriv", "--reuid", str(uid), "--regid", str(uid), "--clear-groups",
                                   *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        for name in self.products:
            path = str(web / token / name)
            self.assertEqual(as_identity(reader, "cat", path), 0)
            self.assertNotEqual(as_identity(other, "cat", path), 0)
        for uid in (reader, other):
            self.assertNotEqual(as_identity(uid, "cat", str(self.box / "private.key")), 0)
            self.assertNotEqual(as_identity(uid, "touch", str(web / token / "replaced")), 0)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.run_shell(fn + f"\nsub_httpd_write '' {port}")
        httpd = subprocess.Popen(["sh", str(self.box / "sub-httpd.sh")])
        self.addCleanup(httpd.wait, 5)
        self.addCleanup(httpd.terminate)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(30):
            try:
                with opener.open(f"http://127.0.0.1:{port}/{token}/sbox.json", timeout=1) as reply:
                    self.assertEqual(reply.read(), (self.box / "sbox.json").read_bytes())
                break
            except OSError:
                if httpd.poll() is not None:
                    self.fail("BusyBox failed to start")
                time.sleep(0.1)
        else:
            self.fail("BusyBox did not answer on loopback")
        with open("/proc/net/tcp") as tcp:
            listeners = [line.split()[1] for line in tcp if len(line.split()) > 3
                         and line.split()[3] == "0A" and line.split()[1].endswith(f":{port:04X}")]
        self.assertEqual(listeners, [f"0100007F:{port:04X}"])

    def test_corrupt_third_subscription_fails_https_probe(self):
        fn = section("sub_https_ready", "sub_prepare_reader").replace("/etc/s-box", self.box.as_posix())
        script = fn + """
sleep(){ :; }; red(){ :; }
curl(){
    local config name
    [[ "$*" != *https://* ]] || return 1
    IFS= read -r config || return 1
    name=${config##*/}
    name=${name%?}
    cp "box/$name" "${@: -1}" || return 1
    if [[ $name == jhsub.txt && $damage == yes ]]; then echo broken > "${@: -1}"; fi
}
damage=yes
sub_https_ready sub.example.test test probe && exit 71
damage=no
sub_https_ready sub.example.test test probe
"""
        self.run_shell(script)
