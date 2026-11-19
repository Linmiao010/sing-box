"""VPS identity, TLS trust and fail-closed WARP. All endpoints are local."""
import base64
import contextlib
import hashlib
import http.client
import http.server
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import ssl
import struct
import subprocess
import threading
import time

import yaml

from shell_testlib import REPO, ShellTestCase, section


OLD = "11111111-1111-4111-a111-111111111111"
NEW = "22222222-2222-4222-b222-222222222222"


class SecurityIdentityTests(ShellTestCase):
    def setUp(self):
        super().setUp()
        self.box = self.root / "box"
        self.box.mkdir()
        jq = shutil.which("jq") or str(REPO / ".test-tools/jq.exe")
        if not Path(jq).exists():
            self.skipTest("Install jq to run generator and transaction checks")
        self.env = {"PATH": str(Path(jq).parent) + os.pathsep + os.environ["PATH"]}
        self.box.joinpath("public.key").write_text(base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("="))
        self.box.joinpath("server_ip.log").write_text("127.0.0.1")
        self.box.joinpath("server_ipcl.log").write_text("127.0.0.1")
        self.prologue = f"""
uuid={OLD}; sbnh=1.14; hostname=test; ipv=prefer_ipv4
port_vm_ws=18443; port_vl_re=18444; port_tu=18445; port_hy2=18446; port_an=18447
tlsyn=true; ym_vm_ws=www.bing.com; ym_vl_re=reality.example.test; short_id=abcd
private_key=AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8
for proto in vmess_ws hy2 tuic an; do
    printf -v "certificatec_$proto" '%s' box/cert.pem
    printf -v "certificatep_$proto" '%s' box/private.key
done
red() {{ printf '%s\n' "$*" >&2; }}
blue() {{ :; }}; green() {{ :; }}; white() {{ :; }}; yellow() {{ :; }}
qrencode() {{ :; }}; ps() {{ :; }}; firewall_hops() {{ :; }}
ip_priority_restart() {{ :; }}
"""
        self.run_code([], """
for pair in 'private.key cert.pem' 'other.key other.pem'; do
    set -- $pair
    openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -x509 -nodes -days 2 \
        -keyout "box/$1" -out "box/$2" -subj /CN=www.bing.com \
        -addext subjectAltName=DNS:www.bing.com -addext basicConstraints=CA:FALSE || exit 1
done
""")

    def run_code(self, sections, code, check=True):
        script = "\n".join(section(*s) for s in sections)
        script = script.replace("/etc/s-box", "box").replace("/root/ygkkkca", "absent-acme")
        return self.run_shell(script + self.prologue + "\n" + code, env=self.env, check=check)

    def generate(self, version="1.14"):
        self.run_code([("inssbjsonser", "snell_supported"), ("extra_inbound", "extra_port_available")],
                      f"sbnh={version}\ninssbjsonser || exit 1\n" +
                      "extra_inbound trojan 18448 \"$uuid\" box/cert.pem box/private.key www.bing.com | "
                      "jq '{inbounds:[.]}' > box/sb_extra.json")

    def render(self, version="1.14"):
        self.run_code([("extra_client_data", "extra_menu"), ("result_vl_vm_hy_tu", "cfargo_ym")],
                      f"sbnh={version}\nresult_vl_vm_hy_tu && resvmess && reshy2 && restu5 && "
                      "extra_share && sb_client")
        return (json.loads(self.box.joinpath("sbox.json").read_text()),
                yaml.safe_load(self.box.joinpath("clmi.yaml").read_text(encoding="utf-8")))

    def test_new_and_legacy_identity_transaction(self):
        self.generate()
        shutil.copyfile(self.box / "cert.pem", self.root / "trusted-public-chain.pem")
        self.env["SSL_CERT_FILE"] = str(self.root / "trusted-public-chain.pem")
        path = next(x for x in self.read_nodes() if x["type"] == "vmess")["transport"]["path"]
        self.assertRegex(path, r"^/[0-9a-f]{48}$")
        self.generate()
        self.assertEqual(next(x for x in self.read_nodes() if x["type"] == "vmess")["transport"]["path"], path)
        for legacy in (OLD + "-vm", "/" + OLD + "-vm", "/safe/custom"):
            with self.subTest(legacy=legacy):
                self.generate()
                files = [self.box / n for n in ("sb.json", "sb10.json", "sb11.json")]
                for file in files:
                    cfg = json.loads(file.read_text())
                    node = next(x for x in cfg["inbounds"] if x["type"] == "vmess")
                    node["transport"]["path"] = legacy
                    node["tls"]["certificate_path"] = "trusted-public-chain.pem"
                    file.write_text(json.dumps(cfg))
                extra = self.box / "sb_extra.json"
                original = {p: p.read_bytes() for p in files + [extra]}
                core = self.box / "sing-box"
                for accept in (False, True):
                    core.write_text("#!/bin/bash\nexit " + ("0" if accept else "1") + "\n")
                    core.chmod(0o700)
                    result = self.run_code([("config_jq", "inssbjsonser")],
                                           f'config_apply config_patch_identity {NEW} /separate', check=False)
                    self.assertEqual(result.returncode == 0, accept, result.stderr)
                    if not accept:
                        self.assertTrue(all(p.read_bytes() == original[p] for p in original))
                for p in files:
                    self.assertNotIn(OLD, p.read_text())
                    vm = next(x for x in json.loads(p.read_text())["inbounds"] if x["type"] == "vmess")
                    self.assertEqual(vm["transport"]["path"], "/separate" if OLD in legacy else legacy)
                self.assertEqual(json.loads(extra.read_text())["inbounds"][0]["users"][0]["password"], NEW)
                sb, mh = self.render()
                expected_path = "/separate" if OLD in legacy else legacy
                self.assertEqual(next(x for x in sb["outbounds"] if x["type"] == "vmess")["transport"]["path"], expected_path)
                self.assertEqual(next(x for x in mh["proxies"] if x["type"] == "vmess")["ws-opts"]["path"], expected_path)
                link = json.loads(base64.b64decode((self.box / "vm_ws_tls.txt").read_text().split("://")[1]))
                self.assertEqual((link["path"], link["id"]), (expected_path, NEW))

    def read_nodes(self):
        return json.loads(self.box.joinpath("sb.json").read_text())["inbounds"]

    def test_external_leaf_is_pinned_for_each_protocol_and_mismatched_key_fails(self):
        self.generate()
        external = self.root / "absent-acme"
        external.mkdir()
        shutil.copyfile(self.box / "other.pem", external / "cert.crt")
        shutil.copyfile(self.box / "other.key", external / "private.key")
        (external / "private.key").chmod(0o600)
        (external / "ca.log").write_text("www.bing.com\n")
        for file in ("sb.json", "sb_extra.json"):
            cfg = json.loads((self.box / file).read_text())
            for node in cfg["inbounds"]:
                if node["type"] in ("trojan", "hysteria2", "vmess"):
                    node["tls"].update(certificate_path="absent-acme/cert.crt", key_path="absent-acme/private.key")
            (self.box / file).write_text(json.dumps(cfg))
        sb, mh = self.render()
        for kind in ("trojan", "hysteria2", "vmess", "tuic", "anytls"):
            pem = (external / "cert.crt" if kind in ("trojan", "hysteria2", "vmess")
                   else self.box / "cert.pem").read_text().strip()
            node = next(n for n in sb["outbounds"] if n["type"] == kind)
            self.assertEqual(node["tls"]["certificate"].strip(), pem)
            pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
            self.assertEqual(next(n for n in mh["proxies"] if n["type"] == kind)["fingerprint"], pin)
        shutil.copyfile(self.box / "private.key", external / "private.key")
        result = self.run_code([("result_vl_vm_hy_tu", "cfargo_ym")], "result_vl_vm_hy_tu", check=False)
        self.assertNotEqual(result.returncode, 0)

    def test_acme_domain_and_subscription_commit_or_restore_together(self):
        self.generate()
        domain = "renewed.example.test"
        old_store = self.root / "absent-acme"
        old_store.mkdir()
        shutil.copyfile(self.box / "cert.pem", old_store / "cert.crt")
        shutil.copyfile(self.box / "private.key", old_store / "private.key")
        (old_store / "ca.log").write_text("www.bing.com\n")
        for name in ("sb.json", "sb10.json", "sb11.json", "sb_extra.json"):
            cfg = json.loads((self.box / name).read_text())
            for node in cfg["inbounds"]:
                if "certificate_path" in node.get("tls", {}):
                    node["tls"].update(certificate_path="absent-acme/cert.crt", key_path="absent-acme/private.key")
            (self.box / name).write_text(json.dumps(cfg))
        self.run_code([], f"""
mkdir -p lineage/{domain}
openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -x509 -nodes -days 2 \\
    -keyout lineage/{domain}/privkey.pem -out lineage/{domain}/fullchain.pem \\
    -subj /CN={domain} -addext subjectAltName=DNS:{domain} -addext basicConstraints=CA:FALSE
printf '#!/bin/bash\\nexit 0\\n' > box/sing-box && chmod +x box/sing-box
""")
        functions = section("acme_activate_locked", "cfwarp").replace("/etc/letsencrypt/live", "lineage")
        functions = functions.replace("/etc/letsencrypt/renewal-hooks/deploy", "renewal-hooks/deploy")
        functions = functions.replace("/etc/s-box", "box").replace("/root/ygkkkca", "absent-acme")
        sections = [("extra_client_data", "extra_menu"), ("result_vl_vm_hy_tu", "cfargo_ym")]
        runtime = """
config_lock_held=1
certbot(){ [[ $1 == certonly && $SBOX_ACME_INTERACTIVE == 1 ]]; }
readp(){ case $2 in domain) domain=renewed.example.test ;; method) method=1 ;; *) return 1 ;; esac; }
ip_priority_restart(){ printf 'restart\\n' >> restarted; }
config_refresh(){ result_vl_vm_hy_tu && sb_client; }
"""
        start = "acme"
        files = [self.box / name for name in ("sb.json", "sb10.json", "sb11.json", "sb_extra.json")]
        certs = [old_store / name for name in ("ca.log", "cert.crt", "private.key")]
        original = {p: p.read_bytes() for p in files + certs}
        failed = self.run_code(sections, functions + runtime + "\nconfig_refresh(){ return 1; }\n" + start, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertTrue(all(p.read_bytes() == data for p, data in original.items()))
        interrupted = self.run_code(sections, functions + runtime + """
config_refresh(){
    result_vl_vm_hy_tu && sb_client || return 1
    if [[ ! -e signal-sent ]]; then touch signal-sent; kill -TERM "$BASHPID"; fi
}
acme
""", check=False)
        self.assertNotEqual(interrupted.returncode, 0)
        self.assertTrue((self.root / "signal-sent").exists())
        self.assertTrue(all(p.read_bytes() == data for p, data in original.items()))
        old = json.loads((self.box / "sbox.json").read_text())
        self.assertEqual(next(n["tls"]["server_name"] for n in old["outbounds"] if n["type"] == "trojan"), "www.bing.com")
        self.run_code(sections, functions + runtime + start)
        for path in files:
            for node in json.loads(path.read_text())["inbounds"]:
                if node.get("tls", {}).get("certificate_path") == "absent-acme/cert.crt":
                    self.assertEqual(node["tls"]["server_name"], domain)
        tls = next(n for n in json.loads((self.box / "sbox.json").read_text())["outbounds"] if n["type"] == "trojan")["tls"]
        self.assertEqual((tls["server_name"], tls["certificate"].strip()), (domain, certs[1].read_text().strip()))
        self.assertEqual(certs[0].read_text().strip(), domain)
        self.assertTrue((self.root / "restarted").is_file())
        self.assertIn('--acme-renew "$RENEWED_LINEAGE"', (self.root / "renewal-hooks/deploy/51-sing-box-certificate").read_text())

    def test_refresh_and_push_keep_credentials_out_of_program_arguments(self):
        self.generate()
        profile = dict(enabled=True, server="192.0.2.5", port=2408, ip="172.16.0.4",
                       private_key=base64.b64encode(b"W" * 32).decode(),
                       public_key=base64.b64encode(b"P" * 32).decode())
        (self.box / "client_warp.json").write_text(json.dumps(profile))
        (self.box / "subdomain.log").write_bytes(b"sub.example.test\n")
        (self.box / "subtoken.log").write_bytes(b"sample_path_token\n")
        (self.box / "cfvmadd_local.txt").write_bytes(b"203.0.113.4\n")
        (self.box / "cfymjx.txt").write_bytes(b"cdn.example.test\n")
        cfg = json.loads((self.box / "sb.json").read_text())
        (self.root / "acme").mkdir()
        shutil.copyfile(self.box / "cert.pem", self.root / "acme/cert.crt")
        self.env["SSL_CERT_FILE"] = str(self.root / "acme/cert.crt")
        next(n for n in cfg["inbounds"] if n["tag"] == "vmess-sb")["tls"]["certificate_path"] = "acme/cert.crt"
        (self.box / "sb.json").write_text(json.dumps(cfg))
        wrappers = r'''
export ARG_LOG="$PWD/argv-fixture"
export QR_IN="$PWD/qr-fixture"
export PUSH_BODY="$PWD/push-fixture"
for tool in jq sed awk cat openssl base64 grep tr cp mv install mktemp; do
    eval "$tool"'(){ printf "%s\n" "$@" >> "$ARG_LOG"; command '"$tool"' "$@"; }'
done
qrencode(){ printf '%s\n' "$@" >> "$ARG_LOG"; command cat >> "$QR_IN"; }
curl(){ printf '%s\n' "$@" >> "$ARG_LOG"; command cat >> "$PUSH_BODY"; command cat <&3 >/dev/null; echo '{"ok":true}'; }
export -f curl jq
flock(){ :; }
config_locked(){ "$@"; }
sub_publish_current(){ local dir=$1 file; shift; for file in "$@"; do cp "$dir/$file" "box/$file" || return 1; done; }
'''
        sources = [("extra_client_data", "extra_menu"), ("result_vl_vm_hy_tu", "cfargo_ym"),
                   ("sbshare", "clash_sb_share"), ("tgsbshow", "changeserv"), ("sub_show_urls", "ipsub")]
        if os.name == "nt":
            wrappers += '\njq(){ printf "%s\\n" "$@" >> "$ARG_LOG"; command jq -b "$@"; }\nexport -f jq\n'
        for enabled in (True, False):
            if enabled:
                (self.box / "telegram.credentials").write_bytes(b"1234:example_bot\n@fixture_room\n")
            else:
                self.run_code(sources, "readp(){ menu=2; }; tgsbshow")
            code = wrappers + "\nsbshare >/dev/null && sub_show_urls >/dev/null"
            if enabled:
                code += " && tgnotice >/dev/null"
            self.run_code(sources, code)
            arguments = self.root.joinpath("argv-fixture").read_text(encoding="utf8")
            for value in (OLD, "vless://", "vmess://", "hysteria2://", "sample_path_token",
                          profile["private_key"], "example_bot", "@fixture_room"):
                self.assertNotIn(value, arguments)
            qr = self.root.joinpath("qr-fixture").read_text()
            self.assertIn("sample_path_token", qr)
            self.assertIn("vless://", qr)
            mh = yaml.safe_load((self.box / "clmi.yaml").read_text(encoding="utf8"))
            vm = next(n for n in mh["proxies"] if n["type"] == "vmess")
            self.assertEqual((vm["server"], vm["servername"]), ("203.0.113.4", "cdn.example.test"))
            vm = next(n for n in json.loads((self.box / "sbox.json").read_text())["outbounds"] if n["type"] == "vmess")
            self.assertEqual(vm["tls"]["server_name"], "cdn.example.test")
            uri = self.box.joinpath("vm_ws_tls.txt").read_text().strip()
            self.assertEqual(json.loads(base64.b64decode(uri.removeprefix("vmess://")))["host"], "cdn.example.test")
        self.box.joinpath("cfymjx.txt").write_text("invalid value")
        self.assertNotEqual(self.run_code(sources, wrappers + "\nsbshare >/dev/null", check=False).returncode, 0)

    def test_legacy_client_accepts_complete_export(self):
        cli = os.environ.get("SING_BOX_110_BIN") or next(
            REPO.glob(".test-tools/serv00-compat/sing-box-1.10.*/sing-box.exe"), None)
        if not cli:
            self.skipTest("Set SING_BOX_110_BIN for the 1.10 client compatibility check")
        self.generate("1.10")
        self.render("1.10")
        result = subprocess.run([str(cli), "check", "-c", str(self.box / "sbox.json")],
                                cwd=self.root, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_trust_publication_and_listeners_both_branches(self):
        pem = self.box.joinpath("cert.pem").read_text().strip()
        fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
        for version in ("1.10", "1.14"):
            revision = "sing"
            if version == "1.14":
                revision = "ab" * 20
                (self.box / "client-rules-revision").write_text(revision + "\n")
            self.generate(version)
            sb, mh = self.render(version)
            sets = sb["route"]["rule_set"]
            self.assertEqual({item["tag"] for item in sets}, {"geosite-cn", "geoip-cn"})
            for item in sets:
                self.assertIn(f"meta-rules-dat@{revision}/geo/", item["url"])
                self.assertEqual(item["update_interval"], "1d")
            self.assertNotIn("clash_api", sb["experimental"])
            self.assertFalse(mh["allow-lan"])
            self.assertEqual(mh["dns"]["listen"], "127.0.0.1:1053")
            for kind in ("hysteria2", "tuic", "trojan") + (("anytls",) if version != "1.10" else ()):
                tls = next(x for x in sb["outbounds"] if x["type"] == kind)["tls"]
                self.assertFalse(tls["insecure"])
                self.assertEqual((tls["server_name"], tls["certificate"].strip()), ("www.bing.com", pem))
                node = next(x for x in mh["proxies"] if x["type"] == kind)
                self.assertFalse(node["skip-cert-verify"])
                self.assertEqual(node["fingerprint"], fingerprint)
            self.assertIn("pinSHA256=" + fingerprint, self.box.joinpath("hy2.txt").read_text())
            self.assertFalse(any(self.box.joinpath(n).exists() for n in ("trojan.txt", "tuic5.txt", "an.txt")))
        self.run_code([("inssbjsonser", "snell_supported")], "tlsyn=false; inssbjsonser")
        vm = next(x for x in self.read_nodes() if x["type"] == "vmess")
        self.assertEqual(vm["listen"], "127.0.0.1")
        sb, mh = self.render()
        self.assertFalse(any(x["type"] == "vmess" for x in sb["outbounds"] + mh["proxies"]))

    def test_native_clients_accept_only_the_issued_certificate(self):
        sb = os.environ.get("SING_BOX_BIN") or shutil.which("sing-box")
        mh = os.environ.get("MIHOMO_BIN") or shutil.which("mihomo")
        if not sb:
            sb = next(REPO.glob(".test-tools/protocol-validation/sing-box/*/sing-box.exe"), None)
        if not mh:
            mh = next(REPO.glob(".test-tools/protocol-validation/mihomo/mihomo*.exe"), None)
        if not sb or not mh:
            self.skipTest("Set SING_BOX_BIN and MIHOMO_BIN for local protocol handshakes")
        self.generate()
        core = self.box / "sing-box"
        core.write_text("#!/bin/bash\nexec " + shlex.quote(Path(sb).resolve().as_posix()) + ' "$@"\n')
        core.chmod(0o700)
        self.run_code([("config_jq", "inssbjsonser")], f"config_apply config_patch_identity {NEW} /test-rotated")
        clients, clash = self.render()
        legacy_cli = os.environ.get("SING_BOX_110_BIN") or next(
            REPO.glob(".test-tools/serv00-compat/sing-box-1.10.*/sing-box.exe"), None)
        legacy_clients = self.render("1.10")[0] if legacy_cli else {}
        protocols = ("trojan", "anytls", "tuic", "hysteria2", "vmess")
        servers = self.read_nodes() + json.loads((self.box / "sb_extra.json").read_text())["inbounds"]
        inbounds = [n for n in servers if n["type"] in protocols]
        for node in inbounds:
            with socket.socket(type=socket.SOCK_DGRAM if node["type"] in ("tuic", "hysteria2") else socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                node.update(listen="127.0.0.1", listen_port=s.getsockname()[1])
        ports = {n["type"]:n["listen_port"] for n in inbounds}
        response_marker = b"local-test-response-4d80c3"

        class Target(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(response_marker)))
                self.end_headers()
                self.wfile.write(response_marker)

            def log_message(self, *_args):
                pass

        @contextlib.contextmanager
        def process(exe, args, config, ready):
            path = self.root / "native.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with (self.root / "native.log").open("a+b") as log:
                offset = log.tell()
                proc = subprocess.Popen([str(exe), *args, str(path)], cwd=self.root,
                                        stdout=log, stderr=log)
                try:
                    limit = time.monotonic() + 6
                    while time.monotonic() < limit and proc.poll() is None:
                        try:
                            socket.create_connection(("127.0.0.1", ready), timeout=0.1).close()
                            break
                        except OSError:
                            time.sleep(0.05)
                    else:
                        log.seek(offset)
                        self.fail(log.read().decode(errors="replace"))
                    yield
                finally:
                    proc.terminate()
                    proc.wait(timeout=5)

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Target)
        self.addCleanup(httpd.server_close)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        for scenario in ("valid", "same-name-other-cert", "retired-credential"):
            alternate = scenario == "same-name-other-cert"
            for node in inbounds:
                node["tls"].update(certificate_path=str(self.box / ("other.pem" if alternate else "cert.pem")),
                                   key_path=str(self.box / ("other.key" if alternate else "private.key")))
            with process(sb, ["run", "-c"], {"inbounds":inbounds, "outbounds":[{"type":"direct"}]}, ports["trojan"]):
                for kind in protocols:
                    engines = ["sing-box", "mihomo"]
                    if legacy_cli and kind != "anytls":
                        engines.append("sing-box 1.10")
                    for engine in engines:
                        with self.subTest(kind=kind, engine=engine, scenario=scenario):
                            with socket.socket() as s:
                                s.bind(("127.0.0.1", 0))
                                mixed = s.getsockname()[1]
                            if engine.startswith("sing-box"):
                                exported = legacy_clients if engine == "sing-box 1.10" else clients
                                node = dict(next(n for n in exported["outbounds"] if n["type"] == kind))
                                node.update(server="127.0.0.1", server_port=ports[kind])
                                config = {"inbounds":[{"type":"mixed","listen":"127.0.0.1","listen_port":mixed}],
                                          "outbounds":[node]}
                                exe, args = (legacy_cli if engine == "sing-box 1.10" else sb), ["run", "-c"]
                            else:
                                node = dict(next(n for n in clash["proxies"] if n["type"] == kind))
                                node.update(server="127.0.0.1", port=ports[kind])
                                config = {"mixed-port":mixed,"allow-lan":False,"proxies":[node],
                                          "rules":["MATCH," + node["name"]], "geo-auto-update":False}
                                exe, args = mh, ["-d", str(self.root), "-f"]
                            if scenario == "retired-credential":
                                for field in ("uuid", "password"):
                                    if field in node:
                                        node[field] = OLD
                            with process(exe, args, config, mixed):
                                with socket.create_connection(("127.0.0.1", mixed), timeout=4) as sock:
                                    with sock.makefile("rb") as reader:
                                        try:
                                            sock.sendall(b"\x05\x01\x00")
                                            self.assertEqual(reader.read(2), b"\x05\x00")
                                            sock.sendall(b"\x05\x01\x00\x01" + socket.inet_aton("127.0.0.1") +
                                                         struct.pack("!H", httpd.server_port))
                                            head = reader.read(4)
                                            if len(head) != 4 or head[1] != 0:
                                                raise ConnectionError("SOCKS CONNECT failed")
                                            size = {1:4, 4:16}.get(head[3])
                                            if size is None:
                                                size = reader.read(1)[0]
                                            reader.read(size + 2)
                                            sock.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
                                            response = http.client.HTTPResponse(sock)
                                            response.begin()
                                            body = response.read()
                                        except (OSError, http.client.HTTPException):
                                            body = b""
                                self.assertEqual(body == response_marker, scenario == "valid",
                                    (self.root / "native.log").read_text(encoding="utf-8", errors="replace")[-2500:])

    def test_warp_unavailable_rejects_and_reactivation_preserves_domains(self):
        self.run_code([("warpwg", "changewg")], "client_warp_register(){ return 1; }; warpwg")
        self.generate()
        files = [self.box / x for x in ("sb10.json", "sb11.json")]
        for file, version in zip(files, ("1.10", "1.14")):
            cfg = json.loads(file.read_text())
            if version == "1.10":
                self.assertEqual(next(x for x in cfg["outbounds"] if x["tag"]=="wireguard-out")["type"], "block")
            else:
                self.assertEqual(cfg["endpoints"], [])
                self.assertTrue(any(x.get("method") == "drop" for x in cfg["route"]["rules"]))
            profile = dict(enabled=True, server="192.0.2.1", port=2408, ip="172.16.0.2",
                           ipv6="2001:db8::1", private_key=base64.b64encode(bytes(range(32))).decode(),
                           public_key=base64.b64encode(bytes(reversed(range(32)))).decode(), mtu=1280, reserved=[1,2,3])
            self.box.joinpath("profile.json").write_text(json.dumps(profile))
            self.run_code([("config_jq", "inssbjsonser")], f"""
config_patch_route box/{file.name} box/rule.json {version} warp6 domain_suffix '["wanted.test"]' &&
config_patch_warp_profile box/rule.json box/enabled.json {version} box/profile.json &&
config_rule_get box/enabled.json {version} warp6 domain_suffix | grep -Fx wanted.test
""")
            self.assertIn(profile["private_key"], self.box.joinpath("enabled.json").read_text())
            cfg = json.loads(self.box.joinpath("enabled.json").read_text())
            key = "outbounds" if version == "1.10" else "endpoints"
            tag = "wireguard-out" if version == "1.10" else "warp-out"
            node = next(x for x in cfg[key] if x["tag"] == tag)
            addr = "local_address" if version == "1.10" else "address"
            node[addr][1] = "2001:db8::1/64"
            node["routing_mark"] = 42
            if version != "1.10":
                node["peers"][0]["allowed_ips"] = ["2001:db8::/32"]
            self.box.joinpath("enabled.json").write_text(json.dumps(cfg))
            profile["ipv6"] = "2001:db8::2"
            self.box.joinpath("profile.json").write_text(json.dumps(profile))
            self.run_code([("config_jq", "inssbjsonser")],
                          f"config_patch_warp_profile box/enabled.json box/replaced.json {version} box/profile.json")
            replaced = json.loads(self.box.joinpath("replaced.json").read_text())
            node = next(x for x in replaced[key] if x["tag"] == tag)
            self.assertEqual(node["routing_mark"], 42)
            self.assertEqual(node[addr][1], "2001:db8::2/64")
            self.assertEqual(replaced["route"], cfg["route"])
            if version != "1.10":
                self.assertEqual(node["peers"][0]["allowed_ips"], ["2001:db8::/32"])

    def test_mihomo_proxy_and_dns_listeners_and_shared_acl(self):
        cli = os.environ.get("MIHOMO_BIN") or shutil.which("mihomo") or next(
            REPO.glob(".test-tools/protocol-validation/mihomo/mihomo*.exe"), None)
        if not cli:
            self.skipTest("Set MIHOMO_BIN for native listener checks")
        self.generate()
        _, rendered = self.render()
        # Isolate rule/DoH providers. Preserve the exported allow-lan and DNS binding.
        dns = {"enable":rendered["dns"]["enable"], "listen":rendered["dns"]["listen"],
               "nameserver":["127.0.0.1:9"]}
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            proxy_port = s.getsockname()[1]
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            dns_port = s.getsockname()[1]
        dns["listen"] = dns["listen"].rsplit(":", 1)[0] + f":{dns_port}"
        for shared in (False, True):
            cfg = {"port":proxy_port, "dns":dns, "allow-lan":rendered["allow-lan"], "rules":["MATCH,REJECT"]}
            if shared:
                cfg.update({"allow-lan":True, "bind-address":"127.0.0.1",
                            "lan-allowed-ips":["127.0.0.1/32"], "authentication":["fixture:fixture"],
                            "skip-auth-prefixes":[]})
            path = self.root / "listener.json"
            path.write_text(json.dumps(cfg), encoding="utf-8")
            proc = subprocess.Popen([str(cli), "-d", str(self.root), "-f", str(path)],
                                    cwd=self.root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                limit = time.monotonic() + 5
                while time.monotonic() < limit:
                    try:
                        socket.create_connection(("127.0.0.1", dns_port), timeout=0.2).close()
                        socket.create_connection(("127.0.0.1", proxy_port), timeout=0.2).close()
                        break
                    except OSError:
                        time.sleep(0.1)
                else:
                    self.fail("Mihomo loopback listeners did not start")
                for port in (dns_port, proxy_port):
                    with self.assertRaises(OSError):
                        socket.create_connection(("127.0.0.2", port), timeout=0.5).close()
                if shared:
                    def query(source, auth):
                        try:
                            with socket.create_connection(("127.0.0.1", proxy_port), timeout=2,
                                                          source_address=(source, 0)) as conn:
                                header = b"Proxy-Authorization: Basic Zml4dHVyZTpmaXh0dXJl\r\n" if auth else b""
                                conn.sendall(b"GET http://127.0.0.1:9/ HTTP/1.1\r\nHost: 127.0.0.1\r\n" + header + b"\r\n")
                                return conn.recv(500)
                        except OSError:
                            return b""
                    self.assertIn(b"407", query("127.0.0.1", False))
                    allowed = query("127.0.0.1", True)
                    self.assertTrue(allowed.startswith(b"HTTP/") and b"407" not in allowed)
                    self.assertEqual(query("127.0.0.2", True), b"")
            finally:
                proc.terminate()
                proc.wait(timeout=5)
