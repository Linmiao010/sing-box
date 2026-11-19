"""Exercise post-remediation policy using test DNS records and a recording SOCKS sink."""
import contextlib
import ipaddress
import http.client
import json
import os
from pathlib import Path
import queue
import secrets
import shutil
import socket
import socketserver
import struct
import subprocess
import threading
import tempfile
import time
import unittest

import test_security_identity as identity
from shell_testlib import REPO


class DNS(socketserver.BaseRequestHandler):
    def handle(self):
        packet, udp = self.request
        pos, labels = 12, []
        while packet[pos]:
            length = packet[pos]
            labels.append(packet[pos + 1:pos + 1 + length].decode())
            pos += length + 1
        end = pos + 5
        kind = int.from_bytes(packet[pos + 1:pos + 3], "big")
        label = labels[0]
        allowed = {1: "198.51.100.24", 28: "2001:db8::24"}
        denied = {1: "100.64.0.7", 28: "fd00::7"}
        expected = allowed if label == "allowed" else denied
        if label == "dns64":
            expected = {1: denied[1], 28: "64:ff9b::a9fe:a9fe"}
        if label == "mixed":
            expected = {1: allowed[1], 28: denied[28]}
        answers = b""
        if kind in expected:
            addr = ipaddress.ip_address(expected[kind]).packed
            answers = b"\xc0\x0c" + struct.pack("!HHIH", kind, 1, 30, len(addr)) + addr
        udp.sendto(packet[:2] + struct.pack("!HHHHH", 0x8180, 1, int(bool(answers)), 0, 0)
                   + packet[12:end] + answers, self.client_address)


class Recorder(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(4)
        if self.rfile.read(1) != b"\x05":
            return
        self.rfile.read(self.rfile.read(1)[0])
        self.wfile.write(b"\x05\x00")
        header = self.rfile.read(4)
        kind = header[3]
        if kind == 3:
            dst = self.rfile.read(self.rfile.read(1)[0]).decode()
        else:
            dst = str(ipaddress.ip_address(self.rfile.read({1: 4, 4: 16}[kind])))
        self.rfile.read(2)
        self.server.targets.put(dst)
        self.wfile.write(b"\x05\x00\x00\x01" + bytes(6))
        with contextlib.suppress(OSError):
            if self.rfile.read(1) == b"X":
                self.wfile.write(b"Y")


class BootstrapDNS(socketserver.StreamRequestHandler):
    def handle(self):
        length = int.from_bytes(self.rfile.read(2), "big")
        message = self.rfile.read(length)
        if len(message) < 17:
            return
        pos, labels = 12, []
        while message[pos]:
            size = message[pos]
            labels.append(message[pos + 1:pos + 1 + size].decode("ascii"))
            pos += size + 1
        self.server.names.put(".".join(labels))
        kind = int.from_bytes(message[pos + 1:pos + 3], "big")
        answer = b""
        if kind == 1:
            answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 30, 4) + socket.inet_aton("127.0.0.1")
        reply = (message[:2] + struct.pack("!HHHHH", 0x8180, 1, bool(answer), 0, 0) +
                 message[12:pos + 5] + answer)
        self.wfile.write(len(reply).to_bytes(2, "big") + reply)


class ServerRoutePolicy(unittest.TestCase):
    def fixture(self):
        fixture = identity.SecurityIdentityTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_generated_policies_and_repeat_migration(self):
        fixture = self.fixture()
        fixture.prologue += """
ip(){
    [[ $* == '-o addr show scope global' ]] || return 1
    printf '%s\n' '2: ens3 inet 203.0.113.29/24 scope global ens3' '2: ens3 inet6 2001:db8::29/64 scope global'
}
"""
        fixture.box.joinpath("server_ipcl.log").write_text("198.51.100.30")
        fixture.generate()
        for series, name, deny, udp in [
                ("1.10", "sb10.json", {"outbound": "block"}, {"network": "udp", "outbound": "block"}),
                ("1.14", "sb11.json", {"action": "reject"},
                 {"network": "udp", "action": "route-options", "udp_connect": True})]:
            with self.subTest(series=series):
                original = json.loads((fixture.box / name).read_text())
                rules = original["route"]["rules"]
                guard = next(rule for rule in rules if "127.0.0.0/8" in rule.get("ip_cidr", []))
                for cidr in ("203.0.113.29/32", "2001:db8::29/128", "198.51.100.30/32",
                             "64:ff9b::/96", "64:ff9b:1::/48", "::ffff:0:0:0/96",
                             "2002::/16", "100::/64", "192.0.0.0/24", "198.18.0.0/15"):
                    self.assertIn(cidr, guard["ip_cidr"])
                start = rules.index(guard)
                self.assertEqual(rules[start + 1], dict(deny, ip_cidr=["0.0.0.0/0", "::/0"], invert=True))
                self.assertEqual(rules[start + 2], udp)
                self.assertFalse(any("outbound" in rule for rule in rules[:start]))
                fixture.run_code([("inssbjsonser", "snell_supported")],
                                 f"secure_server_routes box/{name} box/migrated.json {series}")
                self.assertEqual(json.loads((fixture.box / "migrated.json").read_text()), original)
        failure = fixture.run_code([("inssbjsonser", "snell_supported")],
                                   "ip(){ return 1; }; secure_server_routes box/sb11.json box/failed.json 1.14",
                                   check=False)
        self.assertNotEqual(failure.returncode, 0)
        self.assertFalse((fixture.box / "failed.json").exists())

    def test_nat_addresses_survive_fresh_migration_and_failed_family_probes(self):
        fixture = self.fixture()
        fixture.box.joinpath("server_ipcl.log").unlink()
        fixture.prologue += r"""
warpcheck(){ wgcfv4=${status4:-off}; wgcfv6=${status6:-off}; }
systemctl(){ [[ $* == 'is-active sing-box' ]] && echo active; }
rc-service(){ [[ $* == 'sing-box status' ]] && echo started; }
public_ip(){ if [[ $1 == 4 ]]; then echo "${node4:-198.51.100.24}"; else echo "${node6:-2001:db8::24}"; fi; }
egress_public_ip(){ echo unexpected-bound-probe >> unexpected; return 1; }
ip(){ [[ $* == '-o addr show scope global' ]] && echo '2: eth0 inet 10.0.8.2/24 scope global eth0'; }
config_apply(){
    echo apply >> changes
    [[ $1 == secure_server_routes && ${deny_apply:-no} == no ]] || return 1
    "$1" box/sb.json box/candidate 1.14 && mv -- box/candidate box/sb.json
}
"""
        sources = [("warpcheck", "firewall_sync"), ("inssbjsonser", "snell_supported"),
                   ("ipuuid", "result_vl_vm_hy_tu")]
        state = fixture.box / "node-addresses"
        advertised = fixture.box / "server_ipcl.log"
        changes = fixture.root / "changes"
        custom = fixture.box / "restricted-cidrs.json"
        custom.write_text('["192.0.2.43/32","203.0.113.0/24"]')
        fixture.run_code(sources, "v6 && inssbjsonser")
        self.assertEqual(state.read_text().splitlines(), ["198.51.100.24", "2001:db8::24"])
        self.assertFalse((fixture.root / "unexpected").exists())
        advertised.write_text("198.51.100.24")

        def guard(name):
            rules = json.loads((fixture.box / name).read_text())["route"]["rules"]
            return next(rule["ip_cidr"] for rule in rules if "127.0.0.0/8" in rule.get("ip_cidr", []))

        custom.write_text('["203.0.113.0/24"]')
        fixture.run_code(sources, "v4=198.51.100.99; v6=2001:db8::99; secure_server_routes box/sb.json box/migrated 1.14")
        self.assertIn("10.0.8.2/32", guard("migrated"))
        self.assertIn("198.51.100.24/32", guard("migrated"))
        self.assertIn("2001:db8::24/128", guard("migrated"))
        for stale in ("192.0.2.43/32", "198.51.100.99/32", "2001:db8::99/128"):
            self.assertNotIn(stale, guard("migrated"))

        # WARP-on IPv4 is ignored; the verified IPv6 replaces its own family even if
        # wgcfgo cannot refresh the advertised IPv4. Existing readers keep the old advertisement.
        result = fixture.run_code(sources, "status4=on; node6=2001:db8::25; wgcfgo", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state.read_text().splitlines(), ["198.51.100.24", "2001:db8::25"])
        self.assertIn("2001:db8::25/128", guard("sb.json"))
        self.assertNotIn("2001:db8::24/128", guard("sb.json"))
        self.assertEqual(advertised.read_text(), "198.51.100.24")
        self.assertEqual(changes.read_text().splitlines(), ["apply"])

        # A failed candidate can be retried from the saved observation before publishing it.
        fixture.run_code(sources, "status6=unknown; node4=198.51.100.25; deny_apply=yes; ! wgcfgo")
        self.assertEqual(advertised.read_text(), "198.51.100.24")
        self.assertNotIn("198.51.100.25/32", guard("sb.json"))
        fixture.run_code(sources, "status6=unknown; node4=198.51.100.25; wgcfgo")
        self.assertEqual(advertised.read_text().strip(), "198.51.100.25")
        self.assertIn("198.51.100.25/32", guard("sb.json"))
        self.assertEqual(state.read_text().splitlines(), ["198.51.100.25", "2001:db8::25"])
        self.assertEqual(len(changes.read_text().splitlines()), 3)

        # Selection of a node already covered by a larger administrator CIDR needs no restart.
        fixture.run_code(sources, r"""
pgrep(){ return 1; }; readp(){ menu=1; }
v4=203.0.113.42; v6=; status6=unknown
ipuuid select
""")
        self.assertEqual(advertised.read_text().strip(), "203.0.113.42")
        self.assertEqual(state.read_text().splitlines(), ["203.0.113.42", "2001:db8::25"])
        self.assertEqual(len(changes.read_text().splitlines()), 3)

    def test_client_dns_and_warp_profiles(self):
        fixture = self.fixture()
        fixture.box.joinpath("client_warp.json").write_text(json.dumps(dict(
            enabled=True, server="192.0.2.5", port=2408, ip="172.16.0.4",
            private_key="W" * 43 + "=", public_key="P" * 43 + "=")))
        data = Path(os.getenv("MIHOMO_DATA_DIR") or REPO / ".test-tools/protocol-validation/generated/mihomo-data")
        mihomo_bin = os.getenv("MIHOMO_BIN")
        if mihomo_bin and (data / "GeoSite.dat").is_file():
            for name in ("GeoSite.dat", "geoip.metadb", "Country.mmdb", "GeoIP.dat"):
                if (data / name).is_file():
                    shutil.copyfile(data / name, fixture.root / name)
        for series, variable in (("1.10", "SING_BOX_110_BIN"), ("1.14", "SING_BOX_BIN")):
            with self.subTest(series=series):
                fixture.generate(series)
                cfg, mihomo = fixture.render(series)
                warp = next(n for n in mihomo["proxies"] if n["type"] == "wireguard")
                self.assertTrue(warp["remote-dns-resolve"])
                self.assertEqual(warp["dns"], ["1.1.1.1", "8.8.8.8"])
                self.assertEqual(mihomo["dns"]["default-nameserver"],
                                 ["https://223.5.5.5/dns-query", "https://1.1.1.1/dns-query"])
                resolver = next(d for d in cfg["dns"]["servers"] if d["tag"] == "dns-cn")
                rules = cfg["route"]["rules"]
                private = next(i for i, r in enumerate(rules) if r.get("ip_is_private"))
                dns = [r for r in rules[:private] if r.get("inbound") == ["tun-in"] and
                       (r.get("action") == "hijack-dns" or r.get("outbound") == "dns-out")]
                self.assertEqual(len(dns), 2)
                self.assertTrue(any(r.get("port") == 53 for r in dns))
                self.assertTrue(any(r.get("protocol") == ["dns"] for r in dns))
                if series == "1.14":
                    self.assertEqual(rules[0], {"inbound": ["tun-in"], "action": "sniff"})
                    self.assertEqual((resolver["type"], resolver["server"]), ("https", "223.5.5.5"))
                    self.assertEqual(cfg["route"]["default_domain_resolver"], "dns-cn")
                else:
                    self.assertTrue(cfg["inbounds"][0]["sniff"])
                    self.assertFalse(any(r.get("action") == "sniff" for r in rules))
                    self.assertEqual(resolver["address"], "https://223.5.5.5/dns-query")
                    self.assertEqual(cfg["dns"]["rules"][0], {"outbound": "any", "server": "dns-cn"})
                if os.getenv(variable):
                    checked = subprocess.run([os.environ[variable], "check", "-c", fixture.box / "sbox.json"],
                                             cwd=fixture.root, capture_output=True, timeout=15)
                    self.assertEqual(checked.returncode, 0, checked.stderr)
                if mihomo_bin and (fixture.root / "GeoSite.dat").is_file() and any(
                        (fixture.root / name).is_file() for name in ("geoip.metadb", "Country.mmdb", "GeoIP.dat")):
                    checked = subprocess.run([mihomo_bin, "-t", "-d", fixture.root, "-f", fixture.box / "clmi.yaml"],
                                             cwd=fixture.root, capture_output=True, timeout=20)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    @staticmethod
    def free_port(kind):
        with socket.socket(socket.AF_INET, kind) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    @contextlib.contextmanager
    def run_core(self, binary, fixture, cfg):
        ready = self.free_port(socket.SOCK_STREAM)
        cfg["inbounds"].append(dict(type="mixed", tag="readiness", listen="127.0.0.1", listen_port=ready))
        config = fixture.root / "isolated.json"
        config.write_text(json.dumps(cfg), encoding="utf8")
        with (fixture.root / "core.log").open("w+") as log:
            proc = subprocess.Popen([binary, "run", "-c", config], cwd=fixture.root, stdout=log, stderr=log)
            try:
                for _ in range(80):
                    if proc.poll() is not None:
                        log.seek(0)
                        self.fail(log.read())
                    try:
                        socket.create_connection(("127.0.0.1", ready), .1).close()
                        break
                    except OSError:
                        time.sleep(.05)
                else:
                    self.fail("Local sing-box listener did not start")
                yield
            finally:
                proc.terminate()
                proc.wait(timeout=5)

    def test_native_client_node_uses_bootstrap_resolver(self):
        binaries = [os.getenv(key) for key in ("SING_BOX_110_BIN", "SING_BOX_BIN")]
        if not all(binaries):
            self.skipTest("Set both sing-box binaries for the native node resolver check")
        fixture = self.fixture()
        for series, binary in zip(("1.10", "1.14"), binaries):
            with self.subTest(series=series), socketserver.ThreadingTCPServer(("127.0.0.1", 0), BootstrapDNS) as dns, \
                    socket.socket() as node_socket, socket.socket() as unused:
                dns.names = queue.Queue()
                threading.Thread(target=dns.serve_forever, daemon=True).start()
                try:
                    node_socket.bind(("127.0.0.1", 0))
                    node_socket.listen()
                    node_socket.settimeout(5)
                    unused.bind(("127.0.0.1", 0))
                    fixture.generate(series)
                    cfg, _ = fixture.render(series)
                    node = next(n for n in cfg["outbounds"] if n["type"] == "vless")
                    node.update(server="localhost", server_port=node_socket.getsockname()[1])
                    cfg["outbounds"] = [node, {"type": "direct", "tag": "direct"}]
                    # Send normal traffic through the generated node without starting a TUN or urltest.
                    port = self.free_port(socket.SOCK_STREAM)
                    cfg["inbounds"] = [dict(type="direct", tag="fixture", network="tcp", listen="127.0.0.1",
                                            listen_port=port, override_address="127.0.0.1", override_port=80)]
                    cfg["route"]["final"] = node["tag"]
                    cfg["route"]["rules"] = []
                    cfg["route"]["rule_set"] = [dict(type="inline", tag=r["tag"], rules=[{"domain": ["unused.invalid"]}])
                                                for r in cfg["route"]["rule_set"]]
                    # Keep generated DNS selection. Only dns-cn can answer, over a local TCP recorder.
                    # The full export's actual HTTPS transport is validated by test_client_dns_and_warp_profiles.
                    for i, upstream in enumerate(cfg["dns"]["servers"]):
                        tag = upstream["tag"]
                        dst = (dns.server_address if tag == "dns-cn" else unused.getsockname())[1]
                        cfg["dns"]["servers"][i] = (dict(tag=tag, address=f"tcp://127.0.0.1:{dst}", detour="direct")
                                                    if series == "1.10" else
                                                    dict(tag=tag, type="tcp", server="127.0.0.1", server_port=dst))
                    with self.run_core(binary, fixture, cfg), socket.create_connection(("127.0.0.1", port), 3) as client:
                        client.sendall(b"test")
                        with node_socket.accept()[0] as connected:
                            connected.settimeout(3)
                            self.assertTrue(connected.recv(1))
                        self.assertEqual(dns.names.get(timeout=1), "localhost")
                finally:
                    dns.shutdown()

    def test_native_connected_udp_accepts_only_its_peer(self):
        binary = os.getenv("SING_BOX_BIN")
        if not binary:
            self.skipTest("Set SING_BOX_BIN for the native connected-UDP check")
        fixture = self.fixture()
        fixture.generate()
        cfg = json.loads((fixture.box / "sb.json").read_text())
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as echo, \
             socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as other, \
             socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            echo.bind(("127.0.0.1", 0))
            other.bind(("127.0.0.1", 0))
            echo.settimeout(3)
            client.settimeout(3)
            port = self.free_port(socket.SOCK_DGRAM)
            cfg["inbounds"] = [dict(type="direct", tag="udp-test", network="udp", listen="127.0.0.1",
                                    listen_port=port, override_address="198.51.100.25", override_port=echo.getsockname()[1])]
            denied = self.free_port(socket.SOCK_DGRAM)
            cfg["inbounds"].append(dict(type="direct", tag="denied", network="udp", listen="127.0.0.1",
                                        listen_port=denied, override_address="127.0.0.1", override_port=other.getsockname()[1]))
            # Map the permitted documentation IP to our loopback echo after the generated guards.
            rules = cfg["route"]["rules"]
            connect = next(i for i, r in enumerate(rules) if r.get("udp_connect"))
            rules.insert(connect + 1, dict(ip_cidr=["198.51.100.25/32"], action="route",
                                           outbound="direct", override_address="127.0.0.1"))
            with self.run_core(binary, fixture, cfg):
                client.sendto(b"hello", ("127.0.0.1", port))
                data, source = echo.recvfrom(64)
                self.assertEqual(data, b"hello")
                other.sendto(b"other-peer", source)
                echo.sendto(b"expected-peer", source)
                self.assertEqual(client.recv(64), b"expected-peer")
                client.settimeout(.3)
                with self.assertRaises(socket.timeout):
                    client.recv(64)
                client.sendto(b"denied", ("127.0.0.1", denied))
                other.settimeout(1)
                with self.assertRaises(socket.timeout):
                    other.recv(64)

    def test_native_legacy_udp_fails_closed(self):
        binary = os.getenv("SING_BOX_110_BIN")
        if not binary:
            self.skipTest("Set SING_BOX_110_BIN for the native legacy UDP check")
        fixture = self.fixture()
        fixture.generate("1.10")
        cfg = json.loads((fixture.box / "sb.json").read_text())
        for rule in cfg["route"]["rules"]:
            rule.pop("geosite", None)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe, \
             socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            probe.bind(("127.0.0.1", 0))
            port = self.free_port(socket.SOCK_DGRAM)
            cfg["inbounds"] = [dict(type="direct", tag="udp-test", network="udp", listen="127.0.0.1",
                                    listen_port=port, override_address="198.51.100.25", override_port=probe.getsockname()[1])]
            # A failed block would go only to this test's loopback listener.
            next(o for o in cfg["outbounds"] if o["tag"] == "direct")["override_address"] = "127.0.0.1"
            with self.run_core(binary, fixture, cfg):
                client.sendto(b"udp", ("127.0.0.1", port))
                probe.settimeout(1)
                with self.assertRaises(socket.timeout):
                    probe.recv(64)
            self.assertIn("outbound/block[block]", (fixture.root / "core.log").read_text(errors="replace"))

    def test_native_client_private_dns_uses_internal_fakeip(self):
        binary = os.getenv("SING_BOX_BIN")
        if not binary:
            self.skipTest("Set SING_BOX_BIN for the native DNS hijack check")
        fixture = self.fixture()
        fixture.generate()
        cfg, _ = fixture.render()
        port = self.free_port(socket.SOCK_DGRAM)
        cfg["inbounds"] = [dict(type="direct", tag="tun-in", network="udp", listen="127.0.0.1",
                                listen_port=port, override_address="192.168.1.1", override_port=53)]
        # Route rules and FakeIP come from the export. Isolate remote data, DNS and egress.
        cfg["route"]["rule_set"] = [dict(type="inline", tag=r["tag"], rules=[{"domain": ["unused.invalid"]}])
                                     for r in cfg["route"]["rule_set"]]
        for i, upstream in enumerate(cfg["dns"]["servers"]):
            if upstream["tag"] != "fakeip":
                cfg["dns"]["servers"][i] = dict(type="hosts", tag=upstream["tag"])
        cfg["outbounds"] = [dict(type="socks", tag=tag, server="127.0.0.1", server_port=1) for tag in ("proxy", "direct")]
        query = bytes.fromhex("4a3501000001000000000000") + b"\x07fixture\x07invalid\x00\x00\x01\x00\x01"
        with self.run_core(binary, fixture, cfg), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(3)
            client.sendto(query, ("127.0.0.1", port))
            answer = client.recv(512)
            self.assertEqual(answer[:2], query[:2])
            self.assertEqual(int.from_bytes(answer[6:8], "big"), 1)
            self.assertIn(ipaddress.ip_address(answer[-4:]), ipaddress.ip_network("198.18.0.0/15"))

    def test_local_device_clash_controller_authentication(self):
        for variable in ("SING_BOX_110_BIN", "SING_BOX_BIN"):
            binary = os.getenv(variable)
            if not binary:
                self.skipTest("Set SING_BOX_110_BIN and SING_BOX_BIN to run the device profile check")
            with self.subTest(variable=variable), tempfile.TemporaryDirectory() as tmp, socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
                probe.close()
                token = secrets.token_hex(32)
                config = Path(tmp) / "device.json"
                config.write_text(json.dumps({"experimental": {"clash_api": {"external_controller": f"127.0.0.1:{port}",
                               "secret": token}}, "outbounds":[{"type":"direct", "tag":"direct"}]}))
                proc = subprocess.Popen([binary, "run", "-c", config], cwd=tmp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    for _ in range(60):
                        if proc.poll() is not None:
                            self.fail("client failed to start")
                        try:
                            socket.create_connection(("127.0.0.1", port), .15).close()
                            break
                        except OSError:
                            time.sleep(.05)
                    for secret, status in ((None, 401), (secrets.token_hex(32), 401), (token, 200)):
                        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                        try:
                            conn.request("GET", "/version", headers={"Authorization":"Bearer " + secret} if secret else {})
                            self.assertEqual(conn.getresponse().status, status)
                        finally:
                            conn.close()
                finally:
                    proc.terminate()
                    proc.wait(timeout=5)

    def test_generated_routes_with_native_cores(self):
        binaries = [os.getenv("SING_BOX_110_BIN"), os.getenv("SING_BOX_BIN")]
        if not all(binaries):
            self.skipTest("Set SING_BOX_110_BIN and SING_BOX_BIN for native policy checks")
        fixture = self.fixture()
        fixture.box.joinpath("server_ipcl.log").write_text("203.0.113.26")
        for server, handler in ((socketserver.UDPServer, DNS), (socketserver.ThreadingTCPServer, Recorder)):
            obj = server(("127.0.0.1", 0), handler)
            threading.Thread(target=obj.serve_forever, daemon=True).start()
            self.addCleanup(obj.server_close)
            self.addCleanup(obj.shutdown)
            if handler is DNS:
                dns_port = obj.server_address[1]
            else:
                sink = obj
                sink.targets = queue.Queue()
        for binary, series in zip(binaries, ("1.10", "1.14")):
            for strategy in ("prefer_ipv4", "prefer_ipv6", "ipv4_only", "ipv6_only"):
                with self.subTest(series=series, strategy=strategy):
                    fixture.run_code([("inssbjsonser", "snell_supported")],f"sbnh={series}; ipv={strategy}; inssbjsonser")
                    binary = str(Path(binary).resolve())
                    checked = subprocess.run([binary, "check", "-c", fixture.box / "sb.json"], cwd=fixture.root, capture_output=True)
                    self.assertEqual(checked.returncode, 0, checked.stderr)
                    conf = json.loads((fixture.box / "sb.json").read_text())
                    conf["log"] = {"level":"debug"}
                    with socket.socket() as sock:
                        sock.bind(("127.0.0.1", 0))
                        port = sock.getsockname()[1]
                    inbound = dict(type="mixed", tag="fixture", listen="127.0.0.1", listen_port=port)
                    if series == "1.10":
                        inbound["domain_strategy"] = conf["inbounds"][0]["domain_strategy"]
                        conf["dns"] = {"servers": [{"address": f"udp://127.0.0.1:{dns_port}", "detour": "dns-local"}]}
                        conf["outbounds"].append({"type":"direct", "tag":"dns-local"})
                    else:
                        conf["dns"] = {"servers": [{"type": "udp", "tag": "fixture", "server": "127.0.0.1",
                                                     "server_port": dns_port}]}
                    conf["inbounds"] = [inbound]
                    for rule in conf["route"]["rules"]:
                        # Old VPS profiles refer to an external geosite DB. This isolated test uses domain_suffix.
                        rule.pop("geosite", None)
                        if "domain_suffix" in rule:
                            v4 = rule.get("strategy") == "prefer_ipv4" or rule.get("outbound") in ("socks-out", "socks-IPv4-out")
                            v6 = rule.get("strategy") == "prefer_ipv6" or rule.get("outbound") in ("warp-out", "socks-IPv6-out")
                            rule["domain_suffix"] = ["split4.test" if v4 else "split6.test" if v6 else "unused.invalid"]
                        if rule.get("method") == "drop":
                            rule.pop("action")
                            rule.pop("method")
                            rule.update(outbound="socks-out", domain_suffix=["split6.test"])
                    for node in conf["outbounds"]:
                        if node["tag"] == "socks-out":
                            node["server_port"] = sink.server_address[1]
                        if node["tag"] == "direct" and series == "1.10":
                            node["detour"] = "socks-out"
                        elif node["tag"] == "direct":
                            node.update(type="socks", server="127.0.0.1", server_port=sink.server_address[1])
                    config = fixture.root / "runtime.json"
                    config.write_text(json.dumps(conf), encoding="utf8")
                    with (fixture.root / "core.log").open("w+") as log:
                        proc = subprocess.Popen([binary, "run", "-c", config], stdout=log, stderr=log)
                        try:
                            for _ in range(80):
                                if proc.poll() is not None:
                                    log.seek(0)
                                    self.fail(log.read())
                                try:
                                    socket.create_connection(("127.0.0.1", port), .15).close()
                                    break
                                except OSError:
                                    time.sleep(.05)
                            v4, v6 = "198.51.100.24", "2001:db8::24"
                            split4 = v6 if series == "1.10" and strategy == "ipv6_only" else v4
                            split6 = v4 if series == "1.10" and strategy == "ipv4_only" else v6
                            for name, expected in [
                                    ("127.0.0.1", None), ("[fd00::7]", None), ("100.64.0.7", None),
                                    ("203.0.113.26", None), ("[64:ff9b::a9fe:a9fe]", None),
                                    ("[2002:7f00:1::1]", None), ("dns64.default.test", None),
                                    (v4, v4), ("[" + v6 + "]", v6), ("denied.split4.test", None),
                                    ("mixed.split6.test", v4 if split6 == v4 else None),
                                    ("allowed.split4.test", split4), ("allowed.split6.test", split6),
                                    ("allowed.default.test", v6 if strategy in ("prefer_ipv6", "ipv6_only") else v4)]:
                                with self.subTest(name=name), socket.create_connection(("127.0.0.1", port), 4) as client:
                                    if name.startswith("["):
                                        addr = bytes([4]) + ipaddress.ip_address(name[1:-1]).packed
                                    elif name[0].isdigit():
                                        addr = bytes([1]) + ipaddress.ip_address(name).packed
                                    else:
                                        addr = bytes([3, len(name)]) + name.encode()
                                    client.sendall(b"\x05\x01\x00")
                                    self.assertEqual(client.recv(2), b"\x05\x00")
                                    client.sendall(b"\x05\x01\x00" + addr + bytes([0,80]))
                                    with contextlib.suppress(OSError):
                                        reply = client.recv(4)
                                        if reply[:2] == b"\x05\x00":
                                            client.recv(6 if reply[3] == 1 else 18)
                                            client.sendall(b"X")
                                            client.recv(1)
                                    actual = sink.targets.get_nowait() if not sink.targets.empty() else None
                                    log.flush()
                                    self.assertEqual(expected, actual, (fixture.root / "core.log").read_text(errors="replace")[-1800:])
                        finally:
                            proc.terminate()
                            proc.wait(timeout=5)
