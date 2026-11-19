"""Offline checks for resolver changes, auxiliary processes and private inputs."""
import contextlib
import importlib.util
import io
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import threading
from unittest import mock

from shell_testlib import REPO, ShellTestCase, section


class ShellSecurityTests(ShellTestCase):
    def test_warp_trace_timeout_bad_state_and_inactive_unit(self):
        self.run_shell(section("warpcheck", "firewall_sync") + r'''
curl(){
    [[ $1 == -q && " $* " == *" --noproxy * "* && " $* " == *" --proto =https "* &&
       " $* " == *" --max-time 5 "* && " $* " != *" -k "* ]] || return 7
    if [[ $3 == -4 ]]; then printf '%s\n' warp=off; else return 28; fi
}
public_ip(){ printf '%s\n' 192.0.2.4; }
systemctl(){
    printf '%s\n' "$*" >> calls
    [[ $1 != is-active || $3 == wg-quick@wgcf ]]
}
warpcheck
[[ $wgcfv4 == off && $wgcfv6 == unknown ]] || exit 1
result=$(warp_unrouted_ips node) && [[ $result == $'192.0.2.4\n-' ]] || exit 1
! grep -Eq '^(stop|start) warp-go|^enable ' calls || exit 1
grep -Fxq 'start wg-quick@wgcf' calls || exit 1
curl(){ printf '%s\n' 'warp=maybe'; }
warpcheck
[[ $wgcfv4 == unknown && $wgcfv6 == unknown ]]
''')

    def test_dns64_needs_explicit_consent_and_no_ipv4(self):
        self.run_shell(self.dns_script() + r'''
printf '%s\n' nameserver-original > resolver
ip(){ :; }; readp(){ answer=no; }
v4orv6 '' 2001:db8::4 && grep -Fxq nameserver-original resolver || exit 1
ip(){ echo '2: eth0 inet 10.0.0.1/24 scope global'; }; readp(){ answer=YES; }
v4orv6 '' 2001:db8::4 && grep -Fxq nameserver-original resolver || exit 1
ip(){ :; }
v4orv6 '' 2001:db8::4 && grep -Fq 'nameserver 2a00:' resolver &&
    grep -Fxq nameserver-original backups/resolver-backup.*/contents
''')

    def dns_script(self):
        script = section("v4orv6", "v6")
        return (script.replace("/etc/resolv.conf", "resolver").replace("/etc/s-box/", "backups/")
                .replace("/etc/.sbox-resolv", ".sbox-resolv") +
                "\nmkdir -p backups\nred(){ :; }; yellow(){ :; }; green(){ :; }\n")

    def test_dns64_preserves_symlink_target_and_can_restore_link(self):
        self.run_shell(self.dns_script() + r'''
printf '%s\n' 'nameserver 192.0.2.53' > managed
ln -s managed resolver && [[ -L resolver ]] || exit 1
ip(){ :; }; readp(){ answer=YES; }
v4orv6 '' 2001:db8::4 && [[ ! -L resolver ]] || exit 1
grep -Fxq 'nameserver 192.0.2.53' managed || exit 1
cp -a --remove-destination backups/resolver-backup.*/original resolver &&
   [[ $(readlink resolver) == managed ]] && cmp -s resolver backups/resolver-backup.*/contents
''', env={"MSYS": "winsymlinks:sys"} if os.name == "nt" else None)

    def test_latest_complete_quick_tunnel_and_exact_process_selection(self):
        script = section("quick_tunnel_hostname", "config_jq").replace("/etc/s-box", "box").replace("/proc/", "proc/")
        self.run_shell(script + r'''
mkdir -p box proc/{11,12,13}
printf '%s\n' '| https://old.trycloudflare.com |' 'noise' \
    '| https://new-one.trycloudflare.com |' 'https://suffix.trycloudflare.com.invalid' \
    'https://-bad.trycloudflare.com' 'https://bad.trycloudflare.com:9999' > box/argo.log
[[ $(quick_tunnel_hostname) == new-one.trycloudflare.com ]] || exit 1
pgrep(){ printf '%s\n' 11 12 13; }
readlink(){ if [[ $1 == proc/13/exe ]]; then echo /opt/another; else echo box/cloudflared; fi; }
for entry in '11 8000' '12 80001' '13 8000'; do
    set -- $entry
    printf '%s\0' box/cloudflared tunnel --url "http://127.0.0.1:$2" > "proc/$1/cmdline"
done
kill(){ [[ $1 != -0 ]] && printf '%s\n' "$2" >> selected; }
project_process temporary '' stop && exit 1
project_process temporary 8000 stop && [[ $(cat selected) == 11 ]] || exit 1
pgrep(){ return 2; }
if project_process temporary-all '' stop; then exit 1; fi
pgrep(){ return 1; }
project_process temporary-all '' stop
''')

    def test_socks_tls_probe_checks_status_and_ip(self):
        self.run_shell(section("warp_socks_probe", "inssbwpph") + r'''
curl(){ printf '%s\n' "$*">options; printf '%s\n' "$fixture_response"; }
for fixture_response in $'error page\n200' $'192.0.2.1\n500'; do
    warp_socks_probe 40000 && exit 1
done
fixture_response=$'2001:db8::2\n\n200'
warp_socks_probe 40000 && grep -Fq 'https://icanhazip.com' options &&
   grep -Fq -- '--proto =https' options
''')

    def test_signal_cleanup_for_gitlab_warp_and_serv00_staging(self):
        kp = (REPO / "kp.sh").read_text(encoding="utf-8")
        remote = kp.split("    provision='\n", 1)[1].split("\n    '\n", 1)[0]
        local = kp.split("    payload=$(mktemp -d)", 1)[1].split("    # operation", 1)[0]
        settings = section("gitlabsub", "gitlabsubgo").replace("/etc/s-box", "box")
        warp = section("changewg", "sbymfl").replace("/etc/s-box", "box")
        script = settings + warp + r'''
mkdir box || exit 1
signal_now(){
    [[ $test_signal == normal ]] || kill -s "$test_signal" "$BASHPID"
}
readp(){
    case "$2" in choice|menu) printf -v "$2" 1 ;; project) project='demo/private' ;; branch) branch=main ;; esac
}
red(){ :; }; green(){ :; }; yellow(){ :; }
gitlab_settings_valid(){ :; }; sbshare(){ :; }; gitlab_clear_legacy(){ :; }
gitlab_publish(){ grep -Fq 'fixture-secret' "$1" || return 1; signal_now; }
client_warp_register(){ printf '%s\n' '{"enabled":true,"private_key":"fixture-warp"}' > "$1"; signal_now; }
config_apply(){ :; }
jq(){ :; }
for test_signal in normal HUP INT TERM; do
    gitlabsub <<< 'fixture-secret'
    if [[ $test_signal == normal ]]; then [[ -s box/gitlab-publisher ]] || exit 1; fi
    changewg
    if compgen -G 'box/.gitlab-settings.*' || compgen -G 'box/.server-warp.*'; then exit 1; fi
    (
        payload=$(mktemp -d)''' + local + r'''
        printf '%s' fixture-secret > "$payload/input.json"
        printf '%s\n' "$payload" > stage
        signal_now
    )
    [[ -s stage && ! -e $(<stage) ]] || exit 1
    (
        tar(){
            printf '%s\n' "$d" > remote-stage
            printf '%s' fixture-secret > "$d/input.json"
        }
        bash(){ signal_now; }
''' + remote.replace('$HOME/.serv00-deploy.', './box/.serv00-deploy.') + r'''
    )
    [[ -s remote-stage && ! -e $(<remote-stage) ]] || exit 1
done
'''
        self.run_shell(script)

    def test_socks_cron_and_alpine_start_the_validated_argv(self):
        box = self.root / "box"
        box.mkdir()
        startup = self.root / "local.d"
        startup.mkdir()
        script = section("warp_socks_boot", "sbsm").replace("/etc/s-box", box.as_posix()).replace("/etc/local.d", startup.as_posix())
        self.run_shell(script + r'''
sbactive(){ :; }; yellow(){ :; }; green(){ :; }; red(){ :; }
readp(){ case $2 in choice) choice=2 ;; port) port=45678 ;; country) country=US ;; esac; }
v4v6(){ v4=192.0.2.5; }; release_matches(){ :; }; project_process(){ :; }
sleep(){ :; }; export -f sleep
ss(){ :; }; warp_socks_probe(){ :; }; config_apply(){ :; }
crontab(){ if [[ $1 == -l ]]; then cat cron 2>/dev/null; else cp "$1" cron; fi; }
rc-update(){ :; }
printf '%s\n' '#!/bin/bash' 'printf "%s\n" "$@" > recorded' > box/sbwpph
chmod 700 box/sbwpph || exit 1
inssbwpph || exit 1
# Run the cron command and the Alpine executable installed by the menu.
line=$(<cron); bash -c "${line#@reboot }" || exit 1
printf '%s\n' -b 127.0.0.1:45678 --cfon --country US -4 --endpoint 162.159.192.1:2408 > expected
cmp expected recorded || exit 1
rm recorded
apk(){ :; }
warp_socks_boot yes && bash local.d/alpinews5.start || exit 1
for counter in {1..60}; do [[ -s recorded ]] && break; command sleep .05; done
cmp expected recorded
''')

    def test_private_proxy_and_url_reach_curl_only_on_stdin(self):
        spec = importlib.util.spec_from_file_location("private_proxy_probe", REPO / "tools/network_probe.py")
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        captured = []
        class Proxy(BaseHTTPRequestHandler):
            def do_GET(self):
                captured.append((self.path, self.headers.get("Proxy-Authorization")))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Proxy) as server:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            url = "http://unused.invalid/demo-private-target"
            proxy = f"http://fake:proxy-credential@127.0.0.1:{server.server_port}"
            argv = ["--private-stdin", "--label", "fixture", "--count", "1", "--output", str(self.root / "proxy.csv")]
            try:
                with (mock.patch.object(sys, "stdin", io.StringIO(json.dumps({"url": url, "proxy": proxy}))),
                      mock.patch.object(probe.subprocess, "run", wraps=subprocess.run) as child,
                      contextlib.redirect_stdout(io.StringIO())):
                    self.assertEqual(probe.main(argv), 0)
                    args, kwargs = child.call_args
                    for secret in ("proxy-credential", "demo-private-target"):
                        self.assertIn(secret, kwargs["input"])
                        self.assertNotIn(secret, " ".join(argv + args[0]))
                self.assertEqual(captured[0][0], url)
                self.assertTrue(captured[0][1].startswith("Basic "))
            finally:
                server.shutdown()

    def test_network_probe_private_input_and_tls_redirect(self):
        spec = importlib.util.spec_from_file_location("private_probe", REPO / "tools/network_probe.py")
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        # Use a trusted, temporary fixture CA for this curl process only.
        git_bin = Path(r"C:\Program Files\Git\mingw64\bin")
        real_curl = (str(git_bin / "curl.exe") if os.name == "nt" and (git_bin / "curl.exe").exists()
                     else shutil.which("curl"))
        if not real_curl:
            self.skipTest("curl unavailable")
        self.run_shell("openssl req -new -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes "
                       "-days 1 -keyout key.pem -out ca.pem -subj /CN=localhost "
                       "-addext subjectAltName=IP:127.0.0.1")
        received = []

        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(self.path)
                self.send_response(302)
                self.send_header("Location", f"https://127.0.0.1:{self.server.plain_port}/other")
                self.end_headers()

            def log_message(self, *args):
                pass

        class Plain(Redirect):
            def do_GET(self):
                received.append(self.path)
                self.send_response(200)
                self.end_headers()

        with (ThreadingHTTPServer(("127.0.0.1", 0), Redirect) as server,
              ThreadingHTTPServer(("127.0.0.1", 0), Plain) as plain):
            server.plain_port = plain.server_port
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(self.root / "ca.pem", self.root / "key.pem")
            server.socket = ctx.wrap_socket(server.socket, server_side=True)
            plain.socket = ctx.wrap_socket(plain.socket, server_side=True)
            threading.Thread(target=plain.serve_forever, daemon=True).start()
            threading.Thread(target=server.serve_forever, daemon=True).start()
            secret = "fixture-subscription-path"
            target = f"https://127.0.0.1:{server.server_port}/{secret}"
            argv = ["--private-stdin", "--label", "fixture", "--count", "1",
                    "--output", str(self.root / "sample.csv")]
            run = subprocess.run

            def with_private_ca(command, **kwargs):
                command = command[:2] + ["--cacert", str(self.root / "ca.pem")] + command[2:]
                return run(command, **kwargs)

            try:
                with (mock.patch.object(probe.shutil, "which", return_value=real_curl),
                      mock.patch.dict(os.environ, {"CURL_CA_BUNDLE": str(self.root / "ca.pem")}),
                      mock.patch.object(sys, "stdin", io.StringIO(json.dumps({"url": target}))),
                      mock.patch.object(probe.subprocess, "run", side_effect=with_private_ca) as child,
                      contextlib.redirect_stdout(io.StringIO())):
                    self.assertEqual(probe.main(argv), 1)
                    args, kwargs = child.call_args
                    self.assertNotIn(secret, " ".join(argv + args[0]))
                    self.assertIn(secret, kwargs["input"])
                self.assertEqual(received, ["/" + secret], (self.root / "sample.csv").read_text())
            finally:
                server.shutdown()
                plain.shutdown()

    def test_probe_refuses_invalid_proxy_on_both_inputs(self):
        spec = importlib.util.spec_from_file_location("proxy_schemes", REPO / "tools/network_probe.py")
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        url = "https://example.test"
        common = ["--label", "fixture", "--output", str(self.root / "probe.csv")]
        for proxy in ("file:///local", "gopher://proxy.invalid", "http://", "host:1080",
                      "http://fixture:pw@proxy.example", "socks5h://fixture:pw@192.0.2.2"):
            with self.subTest(proxy=proxy), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    probe.arguments(common + ["--url", url, "--proxy", proxy])
                with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(dict(url=url, proxy=proxy)))):
                    with self.assertRaises(SystemExit):
                        probe.arguments(common + ["--private-stdin"])

    def test_health_worker_bounds_stream_and_cancels_oversize(self):
        script = """
import worker from './workers_keep.js';
const env = {HEALTH_URLS:'https://example.test/health'};
for (const chunks of [['o','k\\n'], ['ok\\n','excess'], ['x'.repeat(8192)]]) {
    let reads = 0, cancelled = false, failed = false;
    globalThis.fetch = async () => ({status:200, body:{getReader:() => ({
        read:async () => {
            if (reads >= chunks.length) return {done:true};
            return {value:new TextEncoder().encode(chunks[reads++])};
        },
        cancel:async () => {cancelled = true;}
    })}});
    try { await worker.scheduled({}, env); } catch { failed = true; }
    if (failed !== (chunks[0] !== 'o') || (chunks[0].length > 16 && !cancelled)) process.exit(1);
}
"""
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=REPO, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
