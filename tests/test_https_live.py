"""Real loopback HTTPS routing; no public certificates or system trust changes."""
from functools import partial
from http.client import HTTPResponse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
import shlex
import shutil
import socket
import ssl
import subprocess
import threading
import time

from shell_testlib import REPO, ShellTestCase, section


class QuietFileHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


class HTTPSLiveTests(ShellTestCase):
    def setUp(self):
        super().setUp()
        local_caddy = REPO / ".test-tools" / ("caddy.exe" if os.name == "nt" else "caddy")
        self.caddy = str(local_caddy) if local_caddy.is_file() else shutil.which("caddy")
        if not self.caddy:
            self.skipTest("Real HTTPS tests require caddy on PATH or in .test-tools")

        self.token = "test_token_12345678"
        self.next_token = "rotated_token_87654321"
        self.webroot = self.root / "backend"
        self.files = {
            "sbox.json": b'{"outbounds":[{"type":"direct","tag":"test"}]}\n',
            "clmi.yaml": b"proxies: []\nproxy-groups: []\n",
            "jhsub.txt": b"vless://11111111-1111-1111-1111-111111111111@example.test:443\n",
        }
        for token in (self.token, self.next_token, "wrong-token"):
            directory = self.webroot / token
            directory.mkdir(parents=True)
            for name, content in self.files.items():
                (directory / name).write_bytes(content)
            (directory / "sb.json").write_bytes(b"private server configuration\n")

        handler = partial(QuietFileHandler, directory=str(self.webroot))
        self.backend = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.backend_thread = threading.Thread(
            target=self.backend.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True,
        )
        self.backend_thread.start()
        self.addCleanup(self.stop_backend)

        # Reserve both ports together so the ephemeral allocator cannot reuse one.
        reservations = [socket.socket(), socket.socket()]
        try:
            for reservation in reservations:
                reservation.bind(("127.0.0.1", 0))
            self.https_port, self.admin_port = [item.getsockname()[1] for item in reservations]
        finally:
            for reservation in reservations:
                reservation.close()

        self.data_home = self.root / "xdg-data"
        self.config_home = self.root / "xdg-config"
        self.environment = os.environ.copy()
        self.environment.update({
            "XDG_DATA_HOME": str(self.data_home),
            "XDG_CONFIG_HOME": str(self.config_home),
            "APPDATA": str(self.root / "appdata"),
            "LOCALAPPDATA": str(self.root / "local-appdata"),
        })
        self.config = self.root / "Caddyfile"
        self.write_config(self.token)
        self.log_path = self.root / "caddy.log"
        self.log_file = self.log_path.open("w", encoding="utf-8")
        self.caddy_process = None
        self.addCleanup(self.stop_caddy)
        self.caddy_process = subprocess.Popen(
            [self.caddy, "run", "--config", str(self.config), "--adapter", "caddyfile"],
            cwd=self.root, env=self.environment, stdout=self.log_file, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.wait_for_https()

    def stop_backend(self):
        self.backend.shutdown()
        self.backend.server_close()
        self.backend_thread.join(timeout=3)

    def stop_caddy(self):
        if self.caddy_process is not None and self.caddy_process.poll() is None:
            self.caddy_process.terminate()
            try:
                self.caddy_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.caddy_process.kill()
                self.caddy_process.wait(timeout=5)
        self.log_file.close()

    def write_config(self, token):
        generated = self.run_shell(
            section("sub_caddy_site", "sub_caddy_prepare")
            + f"\nsub_caddy_site localhost:{self.https_port} {shlex.quote(token)} {self.backend.server_port}\n"
        ).stdout
        # Only the local listener and certificate issuer differ from production.
        generated = generated.replace(" {\n", " {\n    bind 127.0.0.1\n    tls internal\n", 1)
        global_options = (
            "{\n"
            f"    admin 127.0.0.1:{self.admin_port}\n"
            "    auto_https disable_redirects\n"
            "    skip_install_trust\n"
            "}\n"
        )
        self.config.write_text(global_options + generated, encoding="utf-8", newline="\n")

    def wait_for_https(self):
        root_certificate = self.data_home / "caddy" / "pki" / "authorities" / "local" / "root.crt"
        deadline = time.monotonic() + 15
        last_error = None
        while time.monotonic() < deadline:
            if self.caddy_process.poll() is not None:
                self.fail("Caddy stopped during startup:\n" + self.log_path.read_text(encoding="utf-8"))
            if root_certificate.is_file():
                try:
                    # Trust this test's CA in this client only; retain hostname verification.
                    self.tls_context = ssl.create_default_context(cafile=str(root_certificate))
                    if self.get(f"/{self.token}/sbox.json")[0] == 200:
                        return
                except (OSError, ssl.SSLError) as error:
                    last_error = error
            time.sleep(0.1)
        self.fail(f"Local HTTPS did not become ready: {last_error}\n"
                  + self.log_path.read_text(encoding="utf-8"))

    def get(self, path):
        # Connect to IPv4 directly while validating the certificate for localhost.
        with socket.create_connection(("127.0.0.1", self.https_port), timeout=3) as transport:
            with self.tls_context.wrap_socket(transport, server_hostname="localhost") as connection:
                request = f"GET {path} HTTP/1.1\r\nHost: localhost:{self.https_port}\r\nConnection: close\r\n\r\n"
                connection.sendall(request.encode("ascii"))
                with HTTPResponse(connection) as response:
                    response.begin()
                    return response.status, response.read(), response.getheader("Cache-Control")

    def assert_subscription(self, token, name, expected):
        status, content, cache_control = self.get(f"/{token}/{name}")
        self.assertEqual(status, 200)
        self.assertEqual(content, expected)
        self.assertEqual(cache_control, "no-store")

    def test_https_serves_only_the_three_token_paths(self):
        for name, content in self.files.items():
            with self.subTest(name=name):
                self.assert_subscription(self.token, name, content)
        for path in (
            "/", f"/{self.token}", f"/{self.token}/", "/wrong-token/sbox.json",
            f"/{self.next_token}/sbox.json", f"/{self.token}/sb.json",
            f"/{self.token}/sbox.json/extra", "/sbox.json",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.get(path)[0], 404)

    def test_https_reads_new_content_after_atomic_file_replacement(self):
        self.assert_subscription(self.token, "sbox.json", self.files["sbox.json"])
        directory = self.webroot / self.token
        replacement = directory / ".sbox.json.next"
        content = b'{"updated":true,"padding":"' + b"x" * 65536 + b'"}\n'
        replacement.write_bytes(content)
        os.replace(replacement, directory / "sbox.json")
        self.assert_subscription(self.token, "sbox.json", content)

    def test_reload_rejects_old_token_and_serves_new_token(self):
        self.assert_subscription(self.token, "sbox.json", self.files["sbox.json"])
        self.write_config(self.next_token)
        result = subprocess.run(
            [self.caddy, "reload", "--config", str(self.config), "--adapter", "caddyfile",
             "--address", f"127.0.0.1:{self.admin_port}"],
            cwd=self.root, env=self.environment, capture_output=True, text=True,
            encoding="utf-8", timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # The old backend files still exist, so rejection must come from Caddy.
        for name, content in self.files.items():
            with self.subTest(name=name):
                self.assertEqual(self.get(f"/{self.token}/{name}")[0], 404)
                self.assert_subscription(self.next_token, name, content)
