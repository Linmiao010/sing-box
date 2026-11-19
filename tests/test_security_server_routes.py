"""Exercise post-remediation policy using test DNS records and a recording SOCKS sink."""
import contextlib
import ipaddress
import http.client
import json
import os
from pathlib import Path
import queue
import secrets
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


class ServerRoutePolicy(unittest.TestCase):
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
        fixture = identity.SecurityIdentityTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
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
