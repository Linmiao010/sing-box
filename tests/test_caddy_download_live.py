"""Verify the Caddy low-speed fallback with real curl and a loopback server."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import tarfile
import threading
import time

from shell_testlib import REPO, ShellTestCase, section


class CaddyLiveDownloadTests(ShellTestCase):
    def test_stalled_primary_uses_release_and_installs_within_thirty_seconds(self):
        box = self.root / "s-box"
        box.mkdir()
        payload = b"#!/bin/bash\nprintf 'v2.11.7-live-test\\n'\n"
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
            entry = tarfile.TarInfo("caddy")
            entry.size = len(payload)
            entry.mode = 0o755
            bundle.addfile(entry, io.BytesIO(payload))
        archive_bytes = archive.getvalue()
        release_url = (
            "https://github.com/caddyserver/caddy/releases/download/v2.11.7/"
            "caddy_2.11.7_linux_amd64.tar.gz"
        )
        metadata = json.dumps({
            "tag_name": "v2.11.7",
            "assets": [{
                "name": "caddy_2.11.7_linux_amd64.tar.gz",
                "browser_download_url": release_url,
                "digest": "sha256:" + hashlib.sha256(archive_bytes).hexdigest(),
            }],
        }).encode()
        release_stall = threading.Event()
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, time.monotonic()))
                if self.path == "/primary":
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.flush()
                    # The safety limit exceeds the 30-second test deadline, so
                    # curl must detect low speed before server EOF can help it.
                    release_stall.wait(35)
                    return
                bodies = {"/release": metadata, "/archive": archive_bytes}
                body = bodies.get(self.path)
                if body is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        functions = (section("sub_caddy_site", "sub_caddy_reload")
                     .replace("/etc/s-box", "s-box")
                     .replace("/etc/caddy", "system-caddy"))
        stubs = r'''
red(){ printf '%s\n' "$*"; }
blue(){ printf '%s\n' "$*"; }
yellow(){ printf '%s\n' "$*"; }
sub_service(){ return 1; }
ss(){ :; }
uname(){ printf 'x86_64\n'; }
curl(){
    local arg
    local -a args=()
    for arg in "$@"; do
        case "$arg" in
            'https://caddyserver.com/api/download?os=linux&arch=amd64')
                arg="$TEST_HTTP_ORIGIN/primary" ;;
            'https://api.github.com/repos/caddyserver/caddy/releases/latest')
                arg="$TEST_HTTP_ORIGIN/release" ;;
            'https://github.com/caddyserver/caddy/releases/download/v2.11.7/caddy_2.11.7_linux_amd64.tar.gz')
                arg="$TEST_HTTP_ORIGIN/archive" ;;
            http://*|https://*)
                printf 'Unexpected download URL: %s\n' "$arg" >&2
                return 22 ;;
        esac
        args+=("$arg")
    done
    command curl --noproxy '*' "${args[@]}"
}
'''
        script = functions + stubs + '\nmode=$(sub_caddy_prepare) || exit $?\nprintf "%s\\n" "$mode"\n'
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = False
        server_thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.05}
        )
        server_thread.start()
        started = time.monotonic()
        try:
            result = self.run_shell(script, check=False, env={
                "TEST_HTTP_ORIGIN": f"http://127.0.0.1:{server.server_port}",
                "PATH": str(REPO / ".test-tools") + os.pathsep + os.environ["PATH"],
            })
            elapsed = time.monotonic() - started
        finally:
            release_stall.set()
            server.shutdown()
            server.server_close()
            server_thread.join()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "managed\n")
        self.assertEqual([path for path, _ in requests], ["/primary", "/release", "/archive"])
        self.assertGreaterEqual(requests[1][1] - requests[0][1], 18)
        self.assertLess(elapsed, 30)
        self.assertEqual((box / "caddy").read_bytes(), payload)
