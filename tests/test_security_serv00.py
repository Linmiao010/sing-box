"""Offline Serv00 deployment, SSH identity and generation regressions."""
import base64
import contextlib
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import serv00_runtime as runtime


def shell_tools():
    bash = os.getenv("SERV00_TEST_BASH") or shutil.which("bash") or r"C:\Program Files\Git\bin\bash.exe"
    ssh = os.getenv("SERV00_TEST_SSH") or shutil.which("ssh")
    jq = os.getenv("SERV00_TEST_JQ") or shutil.which("jq")
    if os.name == "nt":
        if not os.getenv("SERV00_TEST_SSH") and Path(bash).is_file():
            # kp.sh runs in Bash. Select the OpenSSH implementation on its PATH;
            # native Windows OpenSSH uses a different null file / ACL convention.
            found = subprocess.run([bash, "-c", 'p=$(type -P ssh) && cygpath -aw "$p"'], capture_output=True, text=True)
            if found.returncode == 0:
                value = Path(found.stdout.strip())
                ssh = str(value if value.exists() else value.with_suffix(".exe"))
        jq = jq or REPO / ".test-tools/jq.exe"
    return bash, ssh, jq


class Serv00Security(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.dir = Path(self.work.name)

    def test_connector_uses_issued_origin_and_requires_dashboard_confirmation(self):
        state = {"backend": 23405, "argo_token": "example", "argo_domain": "t.example"}
        openssl = shutil.which("openssl") or r"C:\Program Files\Git\usr\bin\openssl.exe"
        with patch.object(runtime, "ROOT", self.dir), patch.dict(os.environ, PATH=str(Path(openssl).parent) + os.pathsep + os.environ["PATH"]), patch.object(sys.stdin, "isatty", return_value=False):
            with self.assertRaises(runtime.SetupError):
                runtime.prepare_origin(state, {})
            with patch.dict(os.environ, SERV00_FIXED_ORIGIN_CONFIRMED="https://127.0.0.1:23405"):
                runtime.prepare_origin(state, {})
            for token in ("example", ""):
                state["argo_token"] = token
                with patch.object(runtime, "pid_of", return_value=None), patch.object(runtime, "start") as start:
                    runtime.ensure_running(self.dir, state)
                    connector = start.call_args.args
                    self.assertEqual(connector[0], "server")
                    if token:
                        self.assertEqual(connector[1][-1], "run")
                        self.assertEqual(connector[3]["TUNNEL_TOKEN"], token)
                    else:
                        self.assertEqual(connector[1][-6:], ["--url", "https://127.0.0.1:23405", "--origin-server-name",
                                runtime.ORIGIN_NAME, "--origin-ca-pool", str(self.dir / "origin-tls" / "cert.pem")])
            for name in ("origin-tls", "wrong"):
                target = self.dir / name
                if name == "wrong":
                    target.mkdir()
                    runtime.certificate(target, None, {"sni":runtime.ORIGIN_NAME})
                else:
                    properties = runtime.run(["openssl", "x509", "-in", target / "cert.pem", "-text", "-noout"], text=True).stdout
                    for extension in ("CA:FALSE", "Digital Signature", "Key Encipherment", "TLS Web Server Authentication"):
                        self.assertIn(extension, properties)
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", 0))
                    sock.listen()
                    def serve():
                        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                        ctx.load_cert_chain(target / "cert.pem", target / "private.key")
                        with sock.accept()[0] as connection, contextlib.suppress(OSError):
                            ctx.wrap_socket(connection, server_side=True).close()
                    thread = threading.Thread(target=serve)
                    thread.start()
                    ctx = ssl.create_default_context(cafile=str(self.dir / "origin-tls" / "cert.pem"))
                    with socket.create_connection(sock.getsockname(), 3) as plain:
                        if name == "wrong":
                            with self.assertRaises(ssl.SSLCertVerificationError):
                                ctx.wrap_socket(plain, server_hostname=runtime.ORIGIN_NAME)
                        else:
                            ctx.wrap_socket(plain, server_hostname=runtime.ORIGIN_NAME).close()
                    thread.join(4)
                    self.assertFalse(thread.is_alive())

    def test_fixed_rotation_keeps_staged_anchor_and_requires_new_confirmation(self):
        openssl = shutil.which("openssl") or r"C:\Program Files\Git\usr\bin\openssl.exe"
        url = "https://127.0.0.1:20001"
        old = {"backend":20001, "argo_token":"fixed", "argo_domain":"fixed.example", "origin_https":True}
        first, second = self.dir / "candidate", self.dir / "retry"
        first.mkdir()
        second.mkdir()
        with patch.dict(os.environ, PATH=str(Path(openssl).parent) + os.pathsep + os.environ["PATH"],
                        SERV00_FIXED_ORIGIN_CONFIRMED=url), patch.object(runtime,"ROOT",self.dir), patch.object(
                        sys.stdin,"isatty",return_value=False):
            # A legacy CA in active use must remain intact until the operator changes the dashboard.
            legacy = self.dir / "origin-tls"
            legacy.mkdir()
            runtime.run(["openssl","req","-x509","-newkey","ec","-pkeyopt","ec_paramgen_curve:P-256","-nodes",
                         "-keyout",legacy/"private.key","-out",legacy/"cert.pem","-subj","/CN="+runtime.ORIGIN_NAME,
                         "-addext","subjectAltName=DNS:"+runtime.ORIGIN_NAME,"-addext","basicConstraints=critical,CA:TRUE"])
            legacy_key = (legacy / "private.key").read_bytes()
            candidate = dict(old)
            with self.assertRaises(runtime.SetupError):
                runtime.prepare_origin(candidate,old,first)
            fp = candidate["origin_cert_sha256"]
            anchor = self.dir / "origin-tls-v2" / (fp+".pem")
            self.assertEqual(anchor.read_bytes(), (first / "origin-tls" / "cert.pem").read_bytes())
            with patch.dict(os.environ,SERV00_FIXED_ORIGIN_CONFIRMED=url+"#sha256="+fp):
                runtime.prepare_origin(dict(old),old,second)
            self.assertEqual((second/"origin-tls"/"cert.pem").read_bytes(),anchor.read_bytes())
            self.assertEqual((legacy/"private.key").read_bytes(),legacy_key)
            # Missing active origin material also needs the replacement fingerprint.
            previous = dict(candidate)
            missing = self.dir / "missing"
            missing.mkdir()
            with patch.object(runtime,"ACTIVE",missing):
                shutil.rmtree(self.dir/"pending-origin-tls")
                retry = self.dir / "after-loss"
                retry.mkdir()
                with self.assertRaises(runtime.SetupError):
                    runtime.prepare_origin(dict(previous),previous,retry)

    def test_legacy_warp_requires_provenance(self):
        route = {"rules": [{"domain_suffix": ["test.invalid"], "outbound": "wg"}]}
        with patch.object(sys.stdin, "isatty", return_value=False), self.assertRaises(runtime.SetupError):
            runtime.legacy_warp_policy(route, None)
        with patch.object(sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="IMPORT-WARP"):
            self.assertEqual(runtime.legacy_warp_policy(route, None)["warp_rules"], route["rules"])
        backup = self.dir / "trusted.json"
        runtime.write_json(backup, {"route": {"rules": []}})
        backup.chmod(0o600)
        with contextlib.ExitStack() as stack:
            if os.name == "nt":
                stack.enter_context(patch.object(os, "getuid", return_value=0, create=True))
                original = Path.stat
                def posix_stat(path, *args, **kwargs):
                    result = original(path, *args, **kwargs)
                    if path == backup:
                        return os.stat_result([0o100600, *result[1:]])
                    return result
                stack.enter_context(patch.object(Path, "stat", posix_stat))
            self.assertEqual(runtime.legacy_warp_policy(route, backup)["warp_rules"], [])

    def test_legacy_warp_validates_strings_and_lists_even_after_review(self):
        rule = {"outbound": "wg", "rule_set": "gemini"}
        source = {"tag": "gemini", "type": "remote", "url": "https://raw.githubusercontent.com/example/data/sha/rule.srs"}
        for ref in ("gemini", ["gemini"]):
            route = {"rules": [dict(rule, rule_set=ref)], "rule_set": [source]}
            with patch.object(sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="IMPORT-WARP"):
                self.assertEqual(runtime.legacy_warp_policy(route, None)["warp_sets"], [source])
            for url in ("https://127.0.0.1/list", "https://[::1]/list", "https://example.com/list",
                        "https://raw.githubusercontent.com@untrusted.invalid/list"):
                with self.subTest(ref=ref,url=url), self.assertRaises(runtime.SetupError):
                    runtime.legacy_warp_policy({**route,"rule_set":[dict(source,url=url)]}, None)
        for override in ({"override_address":"target.invalid"}, {"type":"logical","rules":[{"domain":["nested.invalid"]}]}):
            state = {"origin_https":True,"warp_policy_reviewed":True,"warp_rules":[dict(rule, **override)], "warp_sets":[source]}
            with self.assertRaises(runtime.SetupError), patch.object(runtime,"start") as start:
                runtime.ensure_running(self.dir, state)
            start.assert_not_called()

    def test_unreviewed_or_plaintext_state_never_starts_a_daemon(self):
        for state in ({"origin_https": False}, {"origin_https": True, "warp_rules": [{"outbound":"wg"}]}):
            with patch.object(runtime, "stop") as stop, patch.object(runtime, "start") as start, self.assertRaises(runtime.SetupError):
                runtime.ensure_running(self.dir, state)
            stop.assert_called_once_with("server")
            start.assert_not_called()

    def test_restart_update_ports_and_rotation_switch_origin_before_connector(self):
        state = {"ip":"192.0.2.9", "addresses":["192.0.2.9"], "path":"/fixture", "sni":"test.invalid",
                 "uuid":"aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "origin_https":True,
                 "backend":21001,"vmess_port":21001,"vless_port":21002,"hy2_port":21003,"token":"a"*48}
        active = self.dir / "active"
        active.mkdir()
        (active / "reality.key").write_text("fixture")
        (active / "argo-domain").write_text("fixed.example")
        for fixed in (True,False):
            working = dict(state, argo_domain="fixed.example" if fixed else "", argo_token="example" if fixed else "")
            runtime.write_json(active / "state.json", working)
            for op in ("restart", "update", "ports", "rotate"):
                with self.subTest(fixed=fixed,op=op), contextlib.ExitStack() as stack:
                    events = []
                    stack.enter_context(patch.object(runtime, "ROOT", self.dir))
                    stack.enter_context(patch.object(runtime, "ACTIVE", active))
                    stack.enter_context(patch.object(runtime, "stop", side_effect=lambda name: events.append(("stop",name))))
                    stack.enter_context(patch.object(runtime, "start", side_effect=lambda name,*args:events.append(("start",name))))
                    stack.enter_context(patch.object(runtime, "pid_of", return_value=None))
                    for key in ("prepare_assets","install_release","emit_clients","publish","switch","retire_token","publish_tunnel_change","prepare_origin"):
                        stack.enter_context(patch.object(runtime,key))
                    stack.enter_context(patch.object(runtime, "run", return_value=Mock(stdout="PrivateKey: new-secret\nPublicKey: new-public\n")))
                    stack.enter_context(patch.object(runtime, "acquire_state", side_effect=lambda old,m:dict(old, backend=old["vmess_port"])))
                    stack.enter_context(patch.object(runtime, "server_config", return_value={}))
                    stack.enter_context(patch.object(runtime, "server_series", return_value="1.10.7"))
                    stack.enter_context(patch.object(runtime, "running_argo", return_value="fixed.example"))
                    stack.enter_context(patch.object(runtime, "certificate", side_effect=lambda stage,*args:(stage / "cert.pem").write_text("fixture")))
                    if op == "restart":
                        runtime.perform(op)
                    else:
                        runtime.configure(op,{"vmess_port":21011} if op == "ports" else None)
                    self.assertEqual(events,[("stop","server"),("stop","sb"),("start","sb"),("start","server")])

    def test_provider_port_and_rule_revision_state(self):
        old = {"uuid":"aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "path":"/fixture", "ip":"192.0.2.9", "sni":"test.invalid", "reality":"handshake.invalid",
               "addresses":["192.0.2.9"], "backend":49000, "vmess_port":21001, "vless_port":21002, "hy2_port":21003}
        def ports():
            return {"tcp":[21001,21002,21004], "udp":[21003]}
        with patch.object(runtime,"ROOT",self.dir), patch.object(runtime,"allocated_ports",side_effect=ports), patch.object(sys.stdin,"isatty",return_value=False):
            with self.assertRaises(runtime.SetupError):
                runtime.acquire_state(old, False)
            revision = "ab" * 20
            with patch.dict(os.environ,SERV00_LOOPBACK_PORT_CONFIRMED="1", SERV00_CLIENT_RULES_REVISION=revision):
                current = runtime.acquire_state(old, False)
            self.assertEqual(current["backend"],21001)
            self.assertEqual(runtime.acquire_state(current, False)["rules_revision"],revision)
            with patch.dict(os.environ,SERV00_ARGO_PORT="21004"):
                self.assertEqual(runtime.acquire_state(old, False)["backend"],21004)

    def test_server_kernel_series_and_generated_configs(self):
        openssl = shutil.which("openssl") or r"C:\Program Files\Git\usr\bin\openssl.exe"
        wg_key = base64.b64encode(bytes(range(32))).decode()
        state = {"uuid":"aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "path":"/fixture", "ip":"192.0.2.9",
                 "addresses":["192.0.2.9","2001:db8::9"], "backend":21001, "vmess_port":21001, "vless_port":21002,
                 "hy2_port":21003, "sni":"host.example", "reality":"handshake.example", "rules_revision":"ab"*20}
        with patch.object(runtime, "ROOT", self.dir), patch.object(runtime,"HOST_ID","s14"), patch.dict(os.environ, PATH=str(Path(openssl).parent) + os.pathsep + os.environ["PATH"]):
            runtime.prepare_origin(state,{})
            fp = runtime.certificate(self.dir, None, state)
            for variable in ("SING_BOX_110_BIN", "SING_BOX_BIN"):
                binary = os.getenv(variable)
                if not binary:
                    self.skipTest("Set SING_BOX_110_BIN and SING_BOX_BIN for native server check")
                version = runtime.run([binary,"version"],text=True).stdout
                with patch.object(runtime,"run",return_value=Mock(stdout=version)):
                    series = runtime.server_series()
                state["client_series"] = "1.10" if series.startswith("1.10.") else "modern"
                keys = runtime.run([binary,"generate","reality-keypair"],text=True).stdout
                private = re.search(r"PrivateKey:\s*(\S+)", keys)[1]
                state["public_key"] = re.search(r"PublicKey:\s*(\S+)",keys)[1]
                runtime.emit_clients(self.dir,state,"test.example",(self.dir/"cert.pem").read_text(),fp)
                profile = runtime.load(self.dir/"singbox.txt")
                result = subprocess.run([binary, "check", "-c", "singbox.txt"], cwd=self.dir, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                resolver = next(r for r in profile["dns"]["servers"] if r["tag"] == "dns-cn")
                self.assertTrue(resolver.get("type") == "https" or resolver.get("address", "").startswith("https://"))
                self.assertTrue(all("@"+state["rules_revision"]+"/" in r["url"] for r in profile["route"]["rule_set"]))
                self.assertNotIn("clash_api",profile["experimental"])
                for warp in (None, {"type":"wireguard", "tag":"wg", "private_key":wg_key, "peer_public_key":wg_key,
                                    "local_address":["172.16.0.2/32"],"server":"engage.cloudflareclient.com","server_port":10000}):
                    with self.subTest(series=series, warp=bool(warp)):
                        config = runtime.server_config(state,private,warp,series)
                        self.assertIn(runtime.SERVER_RULES_REVISION, config["route"]["rule_set"][0]["url"])
                        rules = config["route"]["rules"]
                        offset = int(series.startswith("1.14."))
                        denied = [ipaddress.ip_network(cidr) for cidr in rules[offset]["ip_cidr"]]
                        for target in ("64:ff9b::a9fe:a9fe", "64:ff9b:1::c0a8:1", "2002:7f00:1::1", "192.0.2.9", "2001:db8::9"):
                            self.assertTrue(any(ipaddress.ip_address(target) in network for network in denied))
                        if offset:
                            self.assertEqual(rules[3], {"network":"udp", "action":"route-options", "udp_connect":True})
                        else:
                            self.assertEqual(rules[2], {"network":"udp", "outbound":"block"})
                        runtime.write_json(self.dir/"server.json", config)
                        result = subprocess.run([binary,"check","-c","server.json"],cwd=self.dir,capture_output=True)
                        self.assertEqual(result.returncode,0,result.stderr.decode(errors="replace"))

    def test_new_old_and_custom_identity(self):
        old = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        for path in (old + "-vm", "/" + old + "-vm?ed=2048"):
            with self.assertRaises(runtime.SetupError):
                runtime.identity({"uuid": old, "path": path})
        for path, migrate in (("", False), (old + "-vm", True), ("/custom?q", True), ("/custom", True)):
            if path == "/custom?q":
                with self.assertRaises(runtime.SetupError):
                    runtime.identity({"uuid": old, "path": path}, migrate)
                continue
            user, ws = runtime.identity({"uuid": old, "path": path}, migrate)
            if migrate:
                self.assertNotEqual(user, old)
            self.assertNotIn(user, ws)
            if path == "/custom":
                self.assertEqual(ws, path)
            s = dict(uuid=user, path=ws, ip="127.0.0.1", reality="reality.example",
                     vless_port=23401, vmess_port=23402, hy2_port=23403, backend=23404,
                     public_key="test", sni="cert.example", trusted_cert=False)
            self.assertEqual(runtime.identity(s), (user, ws))
            server = runtime.server_config(s, "dummy", None)
            tls_ws, argo_ws = server["inbounds"][-2:]
            self.assertTrue(tls_ws["tls"]["enabled"])
            self.assertEqual((argo_ws["listen"], argo_ws["listen_port"]), ("127.0.0.1", 23404))
            self.assertTrue(argo_ws["tls"]["enabled"])
            self.assertEqual(argo_ws["transport"]["path"], ws)
            self.assertEqual(tls_ws["transport"]["path"], ws)
            runtime.emit_clients(self.dir, s, "argo.example", "test PEM", "c0ffee")
            sub = json.loads((self.dir / "singbox.txt").read_text())
            self.assertNotIn("clash_api", sub["experimental"])
            for out in [o for o in sub["outbounds"] if o["type"] == "vmess"]:
                self.assertEqual(out["transport"]["path"], ws)
                self.assertTrue(out["tls"]["enabled"])
            hy = next(o for o in sub["outbounds"] if o["type"] == "hysteria2")
            self.assertEqual(hy["tls"]["certificate"], "test PEM")
            mh = json.loads((self.dir / "clashmeta.txt").read_text())
            self.assertFalse(mh["allow-lan"])
            self.assertTrue(mh["dns"]["enable"])
            self.assertTrue(mh["dns"]["listen"].startswith("127.0.0.1:"))
            for key in ("nameserver","default-nameserver","proxy-server-nameserver"):
                self.assertTrue(all(x.startswith("https://") for x in mh["dns"][key]))
            self.assertTrue(all(x.endswith(",no-resolve") for x in mh["rules"] if x.startswith("GEOIP,")))
            uris = (self.dir / "v2sub.txt").read_text().splitlines()
            self.assertTrue(any("pinSHA256=c0ffee" in u for u in uris))
            for u in [u for u in uris if u.startswith("vmess://")]:
                vm = json.loads(base64.b64decode(u.split("://")[1]))
                self.assertEqual((vm["path"], vm["tls"]), (ws, "tls"))
            if migrate:
                self.assertNotIn(old, json.dumps(server))
                self.assertNotIn(old, json.dumps(sub))

    def test_public_index_hides_tokens_and_snapshot_is_outside_webroot(self):
        public = self.dir / "public_html"
        public.mkdir()
        inputs = self.dir / "input"
        inputs.mkdir()
        for name in (*runtime.PRODUCTS, "private.key"):
            (inputs / name).write_text("sample")
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(runtime, "PUBLIC", public))
            if os.name == "nt":
                stack.enter_context(patch.object(os, "getuid", return_value=0, create=True))
                stack.enter_context(patch.object(os, "chown", create=True))
            stage = runtime.new_public(inputs, {})
            htaccess = public / runtime.SUB_DIRECTORY / ".htaccess"
            for control in (htaccess, stage / ".htaccess"):
                self.assertEqual(control.read_text(), runtime.HTTPS_ACCESS)
                if os.name != "nt":
                    self.assertEqual(control.stat().st_mode & 0o777, 0o640)
            htaccess.write_text("Options -Indexes\n")
            upgraded = runtime.new_public(inputs, {})
            self.assertEqual((upgraded / ".htaccess").read_text(), runtime.HTTPS_ACCESS)
        self.assertNotIn(public, stage.parents)
        self.assertEqual({x.name for x in stage.iterdir()}, {*runtime.PRODUCTS, ".htaccess"})
        self.assertEqual((public / runtime.SUB_DIRECTORY / "index.html").read_text(), "")
        self.assertEqual(htaccess.read_text(), runtime.HTTPS_ACCESS)

    def test_missing_warp_key_blocks_preserved_routes(self):
        state = dict(uuid="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", path="/custom", ip="127.0.0.1",
                     reality="example.test", hy2_port=24401, vless_port=24402, vmess_port=24403,
                     backend=24404, warp_rules=[{"domain":["test.invalid"],"outbound":"wg"}], warp_sets=[])
        with patch.object(runtime, "ROOT", self.dir):
            config = runtime.server_config(state, "dummy", runtime.warp_outbound())
        self.assertEqual(config["route"]["rules"][3:], state["warp_rules"])
        self.assertIn("100.64.0.0/10", config["route"]["rules"][0]["ip_cidr"])
        self.assertEqual(next(o for o in config["outbounds"] if o["tag"] == "wg")["type"], "block")

    def test_checksum_blocks_execution_and_replacement(self):
        dest = self.dir / "core"
        dest.write_text("original")
        def fetched(_, path):
            path.write_bytes(b"replacement")
        for digest in ("", "0" * 64):
            with self.assertRaises(runtime.SetupError):
                runtime.install_asset(dest, "sb", digest, fetched)
            self.assertEqual(dest.read_text(), "original")
        runtime.install_asset(dest, "sb", hashlib.sha256(b"replacement").hexdigest(), fetched)
        self.assertEqual(dest.read_text(), "replacement")

    def test_invalid_candidate_preserves_active_and_never_restarts(self):
        previous = self.dir / "active"
        previous.mkdir()
        state = dict(uuid="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", path="/custom",
                     public_key="pub", ip="127.0.0.1", addresses=["127.0.0.1"])
        runtime.write_json(previous / "state.json", state)
        (previous / "reality.key").write_text("private")
        (previous / "config.json").write_text("old config")
        with contextlib.ExitStack() as stack:
            for key, value in dict(ROOT=self.dir, ACTIVE=previous).items():
                stack.enter_context(patch.object(runtime, key, value))
            for key, value in dict(acquire_state=state, warp_outbound=None, prepare_assets=None,
                                   certificate="fingerprint", server_config={}).items():
                stack.enter_context(patch.object(runtime, key, return_value=value))
            stack.enter_context(patch.object(runtime, "run", side_effect=subprocess.CalledProcessError(1, "check")))
            startup = stack.enter_context(patch.object(runtime, "ensure_running"))
            stop = stack.enter_context(patch.object(runtime, "stop"))
            with self.assertRaises(subprocess.CalledProcessError):
                runtime.configure("refresh")
            startup.assert_not_called()
            stop.assert_not_called()
        self.assertEqual((previous / "config.json").read_text(), "old config")

    def test_deployed_panel_only_health_and_no_operations(self):
        panel = self.dir / "public_nodejs"
        root = self.dir / "private"
        root.mkdir()
        with contextlib.ExitStack() as stack:
            for key, value in dict(ROOT=root, HOME=self.dir, PANEL=panel, SITE="test.serv00.net",
                                  HOST="s1.serv00.com", HOST_ID="s1").items():
                stack.enter_context(patch.object(runtime, key, value))
            devil = stack.enter_context(patch.object(runtime, "run", return_value=Mock(stdout="")))
            if os.name == "nt":
                # Windows hosts can lack the FreeBSD symlink privilege. The actual
                # installer still creates the private release, shortcut and Node app.
                stack.enter_context(patch.object(runtime, "switch"))
            runtime.install_release()
            self.assertIn(["devil", "www", "restart", "s1.test.serv00.net"], [c.args[0] for c in devil.call_args_list])
        self.assertEqual((panel / "app.js").read_bytes(), (REPO / "app.js").read_bytes())
        sentinel = "test-token-never-echo"
        (panel / "probe.cjs").write_text(
            "const cp=require('child_process');const fs=require('fs');"
            "for(const n of ['exec','execFile','spawn','execSync','spawnSync'])"
            "cp[n]=()=>{fs.appendFileSync('ops','called');throw Error('blocked')};")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        proc = subprocess.Popen(["node", "--require", "./probe.cjs", "-e", "require('./app.js')"], cwd=panel,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env={**os.environ, "PORT": str(port), "ARGO_AUTH": sentinel})
        try:
            base = f"http://127.0.0.1:{port}"
            for _ in range(80):
                try:
                    urllib.request.urlopen(base + "/health", timeout=1).close()
                    break
                except urllib.error.URLError:
                    time.sleep(.05)
            for method, path, status in [
                ("GET", "/health", 200), ("GET", "/up", 404), ("GET", "/jc", 404),
                ("GET", "/re", 404), ("GET", "/rp", 404), ("POST", "/rp", 404),
                ("GET", "/list/" + sentinel, 404), ("POST", "/health", 404)
            ]:
                req = urllib.request.Request(base + path, method=method)
                try:
                    response = urllib.request.urlopen(req, timeout=3)
                except urllib.error.HTTPError as err:
                    response = err
                with response:
                    self.assertEqual(response.status, status)
                    self.assertIn(response.read(), (b"ok\n", b"not found\n"))
            self.assertFalse((panel / "ops").exists())
        finally:
            proc.terminate()
            logs = proc.communicate(timeout=5)[0]
        self.assertEqual(logs, b"")

    def test_cert_embed_accepts_only_original_leaf_and_hostname(self):
        openssl = shutil.which("openssl") or r"C:\Program Files\Git\usr\bin\openssl.exe"
        if not Path(openssl).exists():
            self.skipTest("OpenSSL unavailable")
        with patch.dict(os.environ, PATH=str(Path(openssl).parent) + os.pathsep + os.environ["PATH"]):
            certificates = []
            for i in range(2):
                here = self.dir / str(i)
                here.mkdir()
                state = {"sni": "host.example"}
                fp = runtime.certificate(here, None, state)
                der = ssl.PEM_cert_to_DER_cert((here / "cert.pem").read_text())
                self.assertEqual(hashlib.sha256(der).hexdigest(), fp)
                certificates.append(here)
        for i, here in enumerate(certificates):
            server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server.load_cert_chain(here / "cert.pem", here / "private.key")
            client = ssl.create_default_context(cafile=str(certificates[0] / "cert.pem"))
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen()
                def serve():
                    sock, _ = listener.accept()
                    with sock, contextlib.suppress(OSError), server.wrap_socket(sock, server_side=True):
                        pass
                thread = threading.Thread(target=serve, daemon=True)
                thread.start()
                with socket.create_connection(listener.getsockname(), 2) as conn:
                    if i == 0:
                        with client.wrap_socket(conn, server_hostname="host.example"):
                            pass
                    else:
                        with self.assertRaises(ssl.SSLCertVerificationError):
                            client.wrap_socket(conn, server_hostname="host.example")
                thread.join(3)

    def test_external_certificate_key_san_system_trust_and_rotation(self):
        openssl = shutil.which("openssl") or r"C:\Program Files\Git\usr\bin\openssl.exe"
        source, first, next_gen = [self.dir / name for name in ("source", "first", "next")]
        for directory in (source, first, next_gen):
            directory.mkdir()
        state = {"sni": "host.example", "ip": "192.0.2.1", "uuid": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                 "public_key":"fixture", "reality":"reality.example", "path":"/custom", "vmess_port":10001,
                 "vless_port":10002, "hy2_port":10003}
        with patch.dict(os.environ, PATH=str(Path(openssl).parent) + os.pathsep + os.environ["PATH"]):
            runtime.issue_leaf(source, "host.example", 1)
            with patch.dict(os.environ, SERV00_TLS_CERT=str(source/"cert.pem"), SERV00_TLS_KEY=str(source/"private.key")):
                old_fp = runtime.certificate(first, None, state)
                self.assertFalse(state["trusted_cert"])
                pem = (first / "cert.pem").read_text().strip()
                runtime.emit_clients(first,state,"",pem,old_fp)
                self.assertIn("pinSHA256=" + old_fp,(first / "v2sub.txt").read_text())
                self.assertIn(old_fp, (first/"clashmeta.txt").read_text())
                self.assertTrue(any(o.get("tls",{}).get("certificate") == pem for o in runtime.load(first/"singbox.txt")["outbounds"]))
                with self.assertRaises(runtime.SetupError):
                    runtime.certificate(next_gen, first, state, True)
                with self.assertRaises(runtime.SetupError):
                    runtime.certificate(next_gen, None, dict(state,sni="wrong.example"))
                for offset in (-172800, 172800):
                    with patch.object(runtime.time,"time",return_value=time.time()+offset), self.assertRaises(runtime.SetupError):
                        runtime.validate_leaf(source, "host.example")
                runtime.issue_leaf(next_gen, "host.example", 1)
                with patch.dict(os.environ, SERV00_TLS_KEY=str(next_gen/"private.key")), self.assertRaises(runtime.SetupError):
                    runtime.certificate(first, None, state)
            # Restore the pair used by the previous generation.
            shutil.copyfile(source/"private.key", first/"private.key")
            new_fp = runtime.certificate(next_gen, first, state, True)
            self.assertNotEqual(old_fp, new_fp)
            self.assertNotEqual((next_gen/"private.key").read_bytes(), (first/"private.key").read_bytes())
            details = runtime.run(["openssl","x509","-in",next_gen/"cert.pem","-noout","-text"],text=True).stdout
            for extension in ("CA:FALSE", "Digital Signature", "Key Encipherment", "TLS Web Server Authentication"):
                self.assertIn(extension, details)
            runtime.run(["openssl","req","-x509","-newkey","ec","-pkeyopt","ec_paramgen_curve:P-256","-nodes",
                         "-subj","/CN=host.example","-keyout",source/"private.key","-out",source/"cert.pem",
                         "-addext","subjectAltName=DNS:host.example","-addext","basicConstraints=critical,CA:TRUE"])
            with patch.dict(os.environ, SERV00_TLS_CERT=str(source/"cert.pem"), SERV00_TLS_KEY=str(source/"private.key")), self.assertRaises(runtime.SetupError):
                runtime.certificate(first,None,state)

    def test_external_fullchain_requires_a_root_in_the_trust_store(self):
        openssl = shutil.which("openssl") or r"C:\Program Files\Git\usr\bin\openssl.exe"
        dest = self.dir / "candidate"
        dest.mkdir()
        state = {"sni":"host.example"}
        with patch.dict(os.environ,PATH=str(Path(openssl).parent)+os.pathsep+os.environ["PATH"]):
            def generate(*args):
                runtime.run(["openssl",*args],cwd=self.dir)
            for name in ("root","intermediate","leaf"):
                generate("req","-new","-nodes","-newkey","ec","-pkeyopt","ec_paramgen_curve:P-256","-keyout",
                         name+".key","-out",name+".csr","-subj","/CN="+("host.example" if name=="leaf" else name))
            (self.dir/"ca.ext").write_text("basicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n")
            (self.dir/"leaf.ext").write_text("basicConstraints=critical,CA:FALSE\nsubjectAltName=DNS:host.example\nextendedKeyUsage=serverAuth\n")
            generate("x509","-req","-in","root.csr","-signkey","root.key","-extfile","ca.ext","-out","root.pem")
            for child, parent, ext in (("intermediate","root","ca.ext"),("leaf","intermediate","leaf.ext")):
                generate("x509","-req","-in",child+".csr","-CA",parent+".pem","-CAkey",parent+".key","-CAcreateserial",
                         "-extfile",ext,"-out",child+".pem")
            bundle = self.dir / "fullchain.pem"
            bundle.write_bytes((self.dir/"leaf.pem").read_bytes() + (self.dir/"intermediate.pem").read_bytes())
            with patch.dict(os.environ,SERV00_TLS_CERT=str(bundle),SERV00_TLS_KEY=str(self.dir/"leaf.key")):
                runtime.certificate(dest,None,state)
                self.assertFalse(state["trusted_cert"])
                with patch.dict(os.environ,SSL_CERT_FILE=str(self.dir/"root.pem")):
                    runtime.certificate(dest,None,state)
                    self.assertTrue(state["trusted_cert"])
                    bundle.write_bytes((self.dir/"leaf.pem").read_bytes())
                    runtime.certificate(dest,None,state)
                    self.assertFalse(state["trusted_cert"])

    def test_temporary_origin_rotates_in_candidate_only(self):
        openssl = shutil.which("openssl") or r"C:\Program Files\Git\usr\bin\openssl.exe"
        first, next_gen, rotated = (self.dir / name for name in ("first","next","rotated"))
        for directory in (first,next_gen,rotated):
            directory.mkdir()
        with patch.object(runtime,"ROOT",self.dir), patch.object(runtime,"ACTIVE",first), patch.dict(
                os.environ, PATH=str(Path(openssl).parent) + os.pathsep + os.environ["PATH"]):
            state = {}
            runtime.prepare_origin(state, {}, first)
            old = (first / "origin-tls" / "private.key").read_bytes()
            runtime.prepare_origin(dict(state), state, next_gen)
            self.assertEqual((next_gen / "origin-tls" / "private.key").read_bytes(),old)
            runtime.prepare_origin(dict(state), state, rotated, True)
            self.assertNotEqual((rotated / "origin-tls" / "private.key").read_bytes(),old)
            self.assertEqual((first / "origin-tls" / "private.key").read_bytes(),old)

    def test_rotate_configuration_and_subscriptions_share_the_new_identity(self):
        binary = os.getenv("SING_BOX_BIN")
        if not binary:
            self.skipTest("Set SING_BOX_BIN to exercise real key generation and candidate check")
        openssl = shutil.which("openssl") or r"C:\Program Files\Git\usr\bin\openssl.exe"
        prev = self.dir / "active"
        prev.mkdir()
        old = {"ip":"192.0.2.1","addresses":["192.0.2.1"],"sni":"host.example","reality":"reality.example",
               "uuid":"aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee","path":"/fixture","token":"f"*48,
               "backend":20001,"vmess_port":20001,"hy2_port":20002,"vless_port":20003,"argo_domain":"","argo_token":""}
        actual_run = runtime.run
        def run(args, **kwargs):
            return actual_run([binary, *args[1:]] if args[0] == self.dir/"sb" else args, **kwargs)
        with contextlib.ExitStack() as stack:
            for target, value in (("ACTIVE",prev),("ROOT",self.dir),("HOST_ID","fixture")):
                stack.enter_context(patch.object(runtime,target,value))
            stack.enter_context(patch.dict(os.environ,PATH=str(Path(openssl).parent)+os.pathsep+os.environ["PATH"]))
            stack.enter_context(patch.object(runtime,"run",side_effect=run))
            stack.enter_context(patch.object(runtime,"acquire_state",side_effect=lambda state,m:dict(state)))
            stack.enter_context(patch.object(runtime,"running_argo",return_value="fixture.trycloudflare.com"))
            for name in ("prepare_assets","stop","ensure_running","publish","switch"):
                stack.enter_context(patch.object(runtime,name))
            retire = stack.enter_context(patch.object(runtime,"retire_token"))
            oldpair = actual_run([binary,"generate","reality-keypair"],text=True).stdout
            old["public_key"] = re.search(r"PublicKey:\s*(\S+)",oldpair)[1]
            (prev/"reality.key").write_text(re.search(r"PrivateKey:\s*(\S+)",oldpair)[1])
            oldfp = runtime.certificate(prev,None,old)
            runtime.prepare_origin(old,{},prev)
            runtime.emit_clients(prev,old,"",(prev/"cert.pem").read_text(),oldfp)
            runtime.write_json(prev/"state.json",old)
            runtime.configure("rotate")
        new = next(self.dir.glob("generation-*"))
        state = runtime.load(new/"state.json")
        retire.assert_called_once_with(old["token"])
        for key in ("uuid","public_key","token","path","origin_cert_sha256"):
            self.assertNotEqual(state[key],old[key])
        for key in ("reality.key","private.key","origin-tls/private.key"):
            self.assertNotEqual((new/key).read_bytes(),(prev/key).read_bytes())
        profile = (new/"singbox.txt").read_text()
        for key in ("uuid","public_key","path"):
            self.assertIn(state[key],profile)
            self.assertNotIn(old[key],profile)
        self.assertNotIn(oldfp,(new/"v2sub.txt").read_text())
        for inbound in runtime.load(new/"config.json")["inbounds"]:
            self.assertEqual(inbound["users"][0].get("uuid",inbound["users"][0].get("password")),state["uuid"])

    def test_account_file_consumed_before_waiting_for_operation(self):
        account = self.dir / "input.json"
        account.write_text('{"SUUID":"fixture"}')
        operation = self.dir / "private"
        entered, release = threading.Event(), threading.Event()
        def wait_for_lock(*args):
            entered.set()
            release.wait(5)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules,fcntl=Mock(flock=wait_for_lock)))
            stack.enter_context(patch.dict(os.environ))
            stack.enter_context(patch.object(sys,"argv",["sb","--install","--account-file",str(account)]))
            stack.enter_context(patch.object(runtime,"ROOT",operation))
            stack.enter_context(patch.object(os,"umask"))
            if os.name == "nt":
                stack.enter_context(patch.object(os,"getuid",return_value=0,create=True))
                original = Path.stat
                def stat(path, *args, **kwargs):
                    result = original(path, *args, **kwargs)
                    return os.stat_result([0o40700,*result[1:]]) if path == operation else result
                stack.enter_context(patch.object(Path,"stat",stat))
            perform = stack.enter_context(patch.object(runtime,"perform"))
            worker = threading.Thread(target=runtime.main)
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                self.assertFalse(account.exists())
                self.assertEqual(os.environ["SUUID"],"fixture")
                perform.assert_not_called()
            finally:
                release.set()
                worker.join(5)
            perform.assert_called_once_with("install")

    @unittest.skipUnless(os.name == "posix" and getattr(os, "geteuid", lambda: 1)() == 0,
                         "run as root in an isolated POSIX test container")
    def test_publisher_and_unrelated_account_permissions(self):
        import grp
        import pwd
        actors = [p for p in pwd.getpwall() if p.pw_uid != 0 and p.pw_gid != 0]
        reader = actors[0]
        stranger = next(p for p in actors if p.pw_gid != reader.pw_gid and p.pw_uid != reader.pw_uid)
        self.dir.chmod(0o711)
        public = self.dir / "public_html"
        public.mkdir(mode=0o711)
        private = self.dir / "private"
        private.mkdir(mode=0o700)
        for file in (*runtime.PRODUCTS, "private.key", "start.sh"):
            (private / file).write_text("test")
            (private / file).chmod(0o600)
        with patch.object(runtime, "PUBLIC", public), patch.dict(os.environ, SERV00_WEB_GROUP=grp.getgrgid(reader.pw_gid).gr_name):
            runtime.publish(private, {"token": "f"*48})
        token_dir = public / runtime.SUB_DIRECTORY / ("f"*48)
        self.assertNotEqual(token_dir.resolve().parent, public)
        self.assertEqual(list(public.iterdir()), [public / runtime.SUB_DIRECTORY])
        self.assertEqual((token_dir.parent / ".htaccess").read_text(), runtime.HTTPS_ACCESS)
        for p, is_reader in ((reader, True), (stranger, False)):
            def drop():
                os.setgroups([])
                os.setgid(p.pw_gid)
                os.setuid(p.pw_uid)
            for path, access, allowed in [(private/"private.key", "r", False), (private/"start.sh", "w", False)] + [
                (token_dir/f, "r", is_reader) for f in runtime.PRODUCTS]:
                r = subprocess.run([sys.executable, "-c", "import sys;open(sys.argv[1],sys.argv[2]).close()",
                                    str(path), access], preexec_fn=drop, stderr=subprocess.DEVNULL)
                self.assertEqual(r.returncode == 0, allowed)

    def test_ssh_install_stream_contains_only_reviewed_bundle_and_deploy_inputs(self):
        import tarfile
        bash, _, jq = shell_tools()
        if not all(p and Path(p).is_file() for p in (bash,jq)):
            self.skipTest("Bash or jq missing")
        fake = self.dir / "ssh"
        fake.write_text('#!/usr/bin/env bash\ncat > "$ARCHIVE"\nprintf "%s\\n" "$@" > "$ARGS"\n', newline="\n")
        fake.chmod(0o700)
        known = self.dir / "known"
        known.write_text("fixture")
        archive = self.dir / "archive"
        args = self.dir / "args"
        marker = "fixture-argo-token"
        settings = {"SERV00_ARGO_PORT": 12346, "SERV00_LOOPBACK_PORT_CONFIRMED": "1",
                    "SERV00_FIXED_ORIGIN_CONFIRMED": "https://127.0.0.1:12346",
                    "SERV00_CLIENT_RULES_REVISION": "a" * 40}
        env = dict(os.environ, ACCOUNTS=json.dumps([{"SSH_USER":"fake","HOST":"host.test","ARGO_AUTH":marker, **settings}]),
                   SSH_KNOWN_HOSTS_FILE=known.as_posix(), TEST_BIN=self.dir.as_posix(), JQ_BIN=Path(jq).parent.as_posix(),
                   SCRIPT=(REPO/"kp.sh").as_posix(), ARCHIVE=archive.as_posix(), ARGS=args.as_posix())
        code = 'if command -v cygpath >/dev/null; then TEST_BIN=$(cygpath -u "$TEST_BIN"); JQ_BIN=$(cygpath -u "$JQ_BIN"); fi; '
        r = subprocess.run([bash, "-c", code + 'export PATH="$TEST_BIN:$JQ_BIN:$PATH"; bash "$SCRIPT" --install'],
                           env=env, capture_output=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), b"account 1 ok")
        self.assertNotIn(marker, args.read_text())
        self.assertNotIn(marker.encode(), r.stderr)
        with tarfile.open(archive) as tf:
            self.assertEqual(set(tf.getnames()), set(runtime.BUNDLE) | {"input.json"})
            for name in runtime.BUNDLE:
                self.assertEqual(tf.extractfile(name).read(), (REPO/name).read_bytes())
            data = json.load(tf.extractfile("input.json"))
            self.assertEqual(data["ARGO_AUTH"], marker)
            self.assertEqual({key: data[key] for key in settings}, settings)
            self.assertNotIn("SSH_PASS", data)

    def test_native_clients_on_loopback(self):
        """Use actual emitted protocol blocks, with a loopback mixed inbound in place of TUN."""
        import http.server
        sb, mh = os.getenv("SERV00_TEST_SB"), os.getenv("SERV00_TEST_MH")
        if not sb or not mh:
            self.skipTest("set SERV00_TEST_SB and SERV00_TEST_MH to run native clients")
        openssl = shutil.which("openssl") or r"C:\Program Files\Git\usr\bin\openssl.exe"
        def free():
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                return sock.getsockname()[1]
        class Target(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"generated-config-proof")
            def log_message(self, *args):
                pass
        def start(binpath, *args):
            with (self.dir / "native.log").open("a") as log:
                return subprocess.Popen([binpath, *args], cwd=self.dir, stdout=log, stderr=subprocess.STDOUT)
        def finish(proc):
            proc.terminate()
            proc.wait(timeout=8)
        port, user = free(), "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        pair = subprocess.check_output([sb, "generate", "reality-keypair"], text=True)
        public_key = re.search(r"PublicKey:\s*(\S+)", pair).group(1)
        version = subprocess.check_output([sb, "version"], text=True).splitlines()[0]
        s = dict(ip="127.0.0.1", uuid=user, path="/fixture", reality="example.com", sni="host.example",
                 vmess_port=port, backend=free(), vless_port=free(), hy2_port=free(), public_key=public_key,
                 client_series="1.10" if "1.10." in version else "modern")
        certs = []
        for i in range(2):
            d = self.dir / str(i)
            d.mkdir()
            with patch.dict(os.environ, PATH=str(Path(openssl).parent) + os.pathsep + os.environ["PATH"]):
                fp = runtime.certificate(d, None, s)
            certs.append(((d / "cert.pem").read_text(), fp))
        for f in ("cert.pem", "private.key"):
            shutil.copyfile(self.dir / "0" / f, self.dir / f)
        runtime.emit_clients(self.dir, s, "argo.example.com", *certs[0])
        checked = subprocess.run([sb, "check", "-c", "singbox.txt"], cwd=self.dir, capture_output=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        data = Path(os.environ.get("MIHOMO_TEST_DATA", REPO / ".test-tools/protocol-validation/generated/mihomo-data"))
        if all((data / name).is_file() for name in ("GeoSite.dat","geoip.metadb")):
            for name in ("GeoSite.dat","geoip.metadb"):
                shutil.copyfile(data/name,self.dir/name)
            checked = subprocess.run([mh,"-d",str(self.dir),"-t","-f",str(self.dir/"clashmeta.txt")], capture_output=True)
            self.assertEqual(checked.returncode,0,checked.stdout + checked.stderr)
        incoming = runtime.server_config(s, "unused", None, version.split()[2])["inbounds"]
        runtime.write_json(self.dir / "server.json", {"inbounds": [i for i in incoming if i["tag"] in
            ("vmess-ws-in", "hysteria-in1")], "outbounds": [{"type":"direct","tag":"direct"}]})
        server = start(sb, "run", "-c", "server.json")
        self.addCleanup(finish, server)
        target = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Target)
        threading.Thread(target=target.serve_forever, daemon=True).start()
        self.addCleanup(target.server_close)
        self.addCleanup(target.shutdown)
        target_url = "http://127.0.0.1:" + str(target.server_port) + "/proof"
        for engine in ("singbox", "mihomo"):
            for tag in ("wss", "hy2"):
                for mode in ("valid", "wrong-cert", "revoked-user"):
                    with self.subTest(engine=engine, tag=tag, mode=mode):
                        state = dict(s)
                        if mode == "revoked-user":
                            state["uuid"] = "bbbbbbbb-aaaa-4ccc-8ddd-eeeeeeeeeeee"
                        runtime.emit_clients(self.dir, state, "", *certs[mode == "wrong-cert"])
                        proxy_port = free()
                        if engine == "singbox":
                            out = next(o for o in runtime.load(self.dir / "singbox.txt")["outbounds"] if o["tag"] == tag)
                            conf = {"inbounds": [{"type":"mixed","listen":"127.0.0.1","listen_port":proxy_port}],
                                    "outbounds": [out]}
                            runtime.write_json(self.dir / "client.json", conf)
                            client = start(sb, "run", "-c", "client.json")
                        else:
                            conf = runtime.load(self.dir / "clashmeta.txt")
                            conf.update({"mixed-port":proxy_port,"port":0,"proxies":[o for o in conf["proxies"] if o["name"] == tag],
                                         "proxy-groups":[],"rules":["MATCH," + tag]})
                            runtime.write_json(self.dir / "client.yaml", conf)
                            client = start(mh, "-d", str(self.dir), "-f", str(self.dir / "client.yaml"))
                        try:
                            deadline = time.monotonic() + 6
                            while time.monotonic() < deadline:
                                if client.poll() is not None:
                                    self.fail("native client exited before accepting connections")
                                try:
                                    sock = socket.create_connection(("127.0.0.1", proxy_port), .2)
                                    break
                                except OSError:
                                    time.sleep(.08)
                            else:
                                self.fail("native client did not start")
                            data = b""
                            with sock, contextlib.suppress(OSError):
                                sock.settimeout(2)
                                sock.sendall(f"GET {target_url} HTTP/1.0\r\nHost: 127.0.0.1:{target.server_port}\r\nConnection: close\r\n\r\n".encode())
                                while part := sock.recv(1024):
                                    data += part
                            self.assertEqual(b"generated-config-proof" in data, mode == "valid",
                                             (self.dir / "native.log").read_text(errors="replace")[-2200:])
                        finally:
                            finish(client)

    def test_known_hosts_with_real_ssh_client(self):
        try:
            import paramiko
        except ImportError:
            self.skipTest("install Paramiko for the isolated SSH server test")
        import logging
        transport_log = logging.getLogger("paramiko.transport")
        level = transport_log.level
        transport_log.setLevel(logging.CRITICAL)
        self.addCleanup(transport_log.setLevel, level)
        bash, ssh, jq = shell_tools()
        if not all(p and Path(p).is_file() for p in (bash, ssh, jq)):
            self.skipTest("Bash, OpenSSH or jq unavailable")
        host_key = paramiko.RSAKey.generate(2048)
        changed = paramiko.RSAKey.generate(2048)
        client_key = paramiko.RSAKey.generate(2048)
        key = self.dir / "client"
        client_key.write_private_key_file(str(key))
        ssh_exec = Path(ssh).as_posix()
        endpoints = []
        exec_requested = threading.Event()
        class Server(paramiko.ServerInterface):
            def check_auth_publickey(self, username, key):
                return paramiko.AUTH_SUCCESSFUL if key == client_key else paramiko.AUTH_FAILED
            def get_allowed_auths(self, _):
                return "publickey"
            def check_channel_request(self, kind, _):
                return paramiko.OPEN_SUCCEEDED
            def check_channel_exec_request(self, channel, command):
                endpoints.append(command)
                exec_requested.set()
                return True
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            num = listener.getsockname()[1]
            wrapper = self.dir / "ssh"
            wrapper.write_text(f'#!/usr/bin/env bash\nexec "{ssh_exec}" -i "{key.as_posix()}" -p {num} "$@"\n',
                               newline="\n")
            wrapper.chmod(0o700)
            known = self.dir / "known_hosts"
            for mode in ("match", "unknown", "changed"):
                exec_requested.clear()
                saved = changed if mode == "changed" else host_key
                alias = "elsewhere.example" if mode == "unknown" else "127.0.0.1"
                known.write_text(f"[{alias}]:{num} {saved.get_name()} {saved.get_base64()}\n")
                def serve():
                    conn, _ = listener.accept()
                    with conn, paramiko.Transport(conn) as transport:
                        transport.add_server_key(host_key)
                        with contextlib.suppress(EOFError, OSError, paramiko.SSHException):
                            transport.start_server(server=Server())
                            chan = transport.accept(8)
                            if chan and exec_requested.wait(8):
                                # ssh -T reads /dev/null. Its EOF lets the transport finish
                                # acknowledging the exec request before this side sends status.
                                chan.settimeout(8)
                                chan.recv(1)
                                chan.send_exit_status(0)
                                chan.close()
                                # Keep TCP alive until OpenSSH completes its disconnect handshake.
                                transport.join(8)
                t = threading.Thread(target=serve, daemon=True)
                t.start()
                env = dict(os.environ, ACCOUNTS='[{"SSH_USER":"fake-user","HOST":"127.0.0.1"}]',
                           SSH_KNOWN_HOSTS_FILE=known.as_posix(), TEST_BIN=self.dir.as_posix(),
                           JQ_BIN=Path(jq).parent.as_posix(), SCRIPT=(REPO / "kp.sh").as_posix())
                setup = 'if command -v cygpath >/dev/null; then TEST_BIN=$(cygpath -u "$TEST_BIN"); JQ_BIN=$(cygpath -u "$JQ_BIN"); fi; '
                r = subprocess.run([bash, "-c", setup + 'export PATH="$TEST_BIN:$JQ_BIN:$PATH"; bash "$SCRIPT" --login'],
                                   capture_output=True, timeout=20, env=env)
                self.assertEqual(r.returncode == 0, mode == "match", (r.stdout, r.stderr))
                self.assertNotIn(b"fake-user", r.stdout + r.stderr)
                self.assertIn(("account 1 " + ("ok" if mode == "match" else "failed")).encode(), r.stdout + r.stderr)
                t.join(4)
        self.assertEqual(endpoints, [b"exit 0"])


if __name__ == "__main__":
    unittest.main()
