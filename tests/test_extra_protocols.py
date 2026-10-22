"""Exercise additional protocols without running the VPS installer."""
import configparser
import json
import os
from pathlib import Path
import shlex
import shutil
from urllib.parse import parse_qs, unquote, urlsplit

import test_publish
from shell_testlib import REPO, ShellTestCase, section

try:
    import yaml
except ImportError:
    yaml = None


class ExtraProtocolTests(ShellTestCase):
    def setUp(self):
        super().setUp()
        self.output = self.root / "published"
        self.output.mkdir()
        self.base = {"inbounds": [{"type": "vless", "listen_port": 443},
                                  {"type": "vmess", "tls": {"enabled": False}, "listen_port": 8443}]}
        (self.output / "sb.json").write_text(json.dumps(self.base), encoding="utf-8")
        self.password = 'secret "quote" \\ slash : # 中文\nnext-line'
        self.psk = 'independent "PSK" \\ : # 中文'

    def source(self, start, end):
        return section(start, end).replace("/etc/s-box", self.output.as_posix())

    def jq_script(self):
        jq = os.environ.get("JQ") or shutil.which("jq")
        local = REPO / ".test-tools" / "jq.exe"
        if not jq and local.is_file():
            jq = str(local)
        if not jq:
            self.skipTest("Install jq or set JQ to test real JSON generation")
        return f'\njq() {{ {shlex.quote(Path(jq).as_posix())} "$@"; }}\n'

    def functions(self):
        return self.source("snell_supported", "sbservice") + self.jq_script()

    def inbounds(self, certificate=None):
        return [
            {"type": "trojan", "tag": "trojan-sb", "listen": "::", "listen_port": 9443,
             "users": [{"password": self.password}],
             "tls": {"enabled": True, "server_name": "tls.example.com",
                     "certificate_path": certificate or (self.output / "cert.pem").as_posix(),
                     "key_path": (self.output / "private.key").as_posix()}},
            {"type": "snell", "tag": "snell-sb", "listen": "::", "listen_port": 9444,
             "version": 5, "psk": self.psk},
        ]

    def write_extra(self, inbounds=None):
        (self.output / "sb_extra.json").write_text(
            json.dumps({"inbounds": self.inbounds() if inbounds is None else inbounds}),
            encoding="utf-8",
        )

    def client_script(self, processes="", extra=""):
        # Import the module rather than its TestCase, so unittest does not collect
        # all publication tests again. Reuse only its isolated generator fixture.
        return self.functions() + test_publish.PublishTests.client_script(
            self, processes=processes, extra=extra, version="1.14")

    def read_yaml(self):
        if yaml is None:
            self.skipTest("PyYAML is required to validate complete client YAML")
        return yaml.safe_load((self.output / "clmi.yaml").read_text(encoding="utf-8"))

    def test_snell_version_gate_rejects_old_and_accepts_supported_series(self):
        script = self.functions()
        for version in ("1.10", "1.13", "1.14", "1.14.0", "1.15.2", "2.0"):
            script += f'if snell_supported {shlex.quote(version)}; then echo yes; else echo no; fi\n'
        result = self.run_shell(script)
        self.assertEqual(result.stdout.splitlines(), ["no", "no", "yes", "yes", "yes", "yes"])

    def test_inbound_generation_preserves_secrets_and_tls_paths(self):
        cert = (self.output / 'certificate "name".pem').as_posix()
        key = (self.output / "private key.pem").as_posix()
        command = "extra_inbound " + " ".join(map(shlex.quote,
            ["trojan", "9443", self.password, cert, key, "tls.example.com"]))
        trojan = json.loads(self.run_shell(self.functions() + command).stdout)
        self.assertEqual(trojan["type"], "trojan")
        self.assertEqual(trojan["tag"], "trojan-sb")
        self.assertEqual(trojan["listen_port"], 9443)
        self.assertEqual(trojan["users"][0]["password"], self.password)
        self.assertEqual(trojan["tls"], {"enabled": True, "server_name": "tls.example.com",
                                      "certificate_path": cert, "key_path": key})
        command = "extra_inbound snell 9444 " + shlex.quote(self.psk)
        snell = json.loads(self.run_shell(self.functions() + command).stdout)
        self.assertEqual(snell["type"], "snell")
        self.assertEqual(snell["tag"], "snell-sb")
        self.assertEqual(snell["listen_port"], 9444)
        self.assertEqual(snell["version"], 5)
        self.assertEqual(snell["psk"], self.psk)

    def test_client_data_keeps_ipv6_secrets_sni_and_certificate_policy(self):
        for certificate, insecure in (((self.output / "cert.pem").as_posix(), True),
                                      ("/root/cert.crt", False)):
            with self.subTest(certificate=certificate):
                self.write_extra(self.inbounds(certificate))
                result = self.run_shell(self.functions() + """
server_ipcl=2001:db8::42
hostname=test-node
extra_client_data
""")
                data = json.loads(result.stdout)
                for kind in ("singbox", "mihomo"):
                    nodes = {node["type"]: node for node in data[kind]}
                    self.assertEqual(set(nodes), {"trojan", "snell"})
                    self.assertEqual(nodes["trojan"]["server"], "2001:db8::42")
                    self.assertEqual(nodes["snell"]["server"], "2001:db8::42")
                    self.assertEqual(nodes["trojan"]["password"], self.password)
                    self.assertEqual(nodes["snell"]["psk"], self.psk)
                    self.assertEqual(nodes["snell"]["version"], 4)
                trojan = next(node for node in data["singbox"] if node["type"] == "trojan")
                self.assertTrue(trojan["tls"]["enabled"])
                self.assertEqual(trojan["tls"]["server_name"], "tls.example.com")
                self.assertIs(trojan["tls"]["insecure"], insecure)
                mihomo = {node["type"]: node for node in data["mihomo"]}
                self.assertEqual(mihomo["trojan"]["sni"], "tls.example.com")
                self.assertIs(mihomo["trojan"]["skip-cert-verify"], insecure)
                self.assertTrue(mihomo["snell"]["udp"])

    def test_all_argo_branches_include_extra_nodes_in_every_group(self):
        self.write_extra()
        for fixed, temporary in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(fixed=fixed, temporary=temporary):
                processes = ("cloudflared tunnel run\n" if fixed else "") + (
                    "cloudflared --url localhost:8443\n" if temporary else "")
                self.run_shell(self.client_script(processes, "server_ipcl=2001:db8::42"))
                config = json.loads((self.output / "sbox.json").read_text(encoding="utf-8"))
                nodes = {node["type"]: node for node in config["outbounds"]
                         if node["type"] in ("trojan", "snell")}
                self.assertEqual(set(nodes), {"trojan", "snell"})
                self.assertEqual(nodes["trojan"]["password"], self.password)
                self.assertEqual(nodes["snell"]["psk"], self.psk)
                self.assertEqual(nodes["snell"]["version"], 4)
                tags = {node["tag"] for node in nodes.values()}
                groups = [node for node in config["outbounds"] if node["type"] in ("selector", "urltest")]
                self.assertGreaterEqual(len(groups), 2)
                for group in groups:
                    self.assertTrue(tags.issubset(group["outbounds"]), group["tag"])
                clash = self.read_yaml()
                proxies = {node["type"]: node for node in clash["proxies"]
                           if node["type"] in ("trojan", "snell")}
                self.assertEqual(set(proxies), {"trojan", "snell"})
                self.assertEqual(proxies["trojan"]["password"], self.password)
                self.assertEqual(proxies["snell"]["psk"], self.psk)
                self.assertTrue(proxies["snell"]["udp"])
                names = {node["name"] for node in proxies.values()}
                self.assertEqual(len(clash["proxy-groups"]), 3)
                for group in clash["proxy-groups"]:
                    self.assertTrue(names.issubset(group["proxies"]), group["name"])
                for node in (*nodes.values(), *proxies.values()):
                    self.assertEqual(node["server"], "2001:db8::42")

    def test_no_extra_file_keeps_existing_client_nodes(self):
        self.run_shell(self.client_script())
        config = json.loads((self.output / "sbox.json").read_text(encoding="utf-8"))
        types = {node["type"] for node in config["outbounds"]}
        self.assertTrue({"vless", "vmess", "hysteria2", "tuic", "anytls"}.issubset(types))
        self.assertFalse({"trojan", "snell"} & types)
        clash = self.read_yaml()
        self.assertFalse(any(node["type"] in ("trojan", "snell") for node in clash["proxies"]))

    def transaction_script(self, body, check_status=0, version="1.14.0", service_status=0, sockets=""):
        binary = self.output / "sing-box"
        binary.write_text("""#!/usr/bin/env bash
case "$1" in
version) printf 'sing-box version %s\\n' "$FAKE_VERSION" ;;
generate)
    [[ $2 == rand && $3 == --base64 && $4 == 32 ]] || exit 87
    printf '%s\\n' "$FAKE_PSK"
    ;;
check)
    printf '%s\\0' "$@" > "$LOG_ROOT/check-args"
    shift
    count=0
    while (( $# )); do
        if [[ $1 == -c ]]; then
            cp -- "$2" "$LOG_ROOT/checked-$count.json" || exit 89
            count=$((count + 1))
            shift 2
        else
            shift
        fi
    done
    exit "$FAKE_CHECK_STATUS"
    ;;
*) exit 88 ;;
esac
""", encoding="utf-8", newline="\n")
        binary.chmod(0o755)
        return self.functions() + f"""
export LOG_ROOT={shlex.quote(self.root.as_posix())}
export FAKE_VERSION={shlex.quote(version)}
export FAKE_CHECK_STATUS={check_status}
export FAKE_PSK={shlex.quote(self.psk)}
sbnh={shlex.quote('.'.join(version.split('.')[:2]))}
red() {{ printf '%s\\n' "$*" >&2; }}
green() {{ :; }}
white() {{ :; }}
yellow() {{ :; }}
ss() {{ printf '%s\\n' {shlex.quote(sockets)}; }}
netstat() {{ :; }}
lsof() {{ return 1; }}
sbservice() {{ printf 'apply\\n' >> "$LOG_ROOT/service-log"; return {service_status}; }}
restartsb() {{ printf 'restart\\n' >> "$LOG_ROOT/service-log"; }}
{body}
"""

    def candidate(self):
        candidate = self.root / "candidate.json"
        nodes = self.inbounds()
        nodes[1]["listen_port"] = 9555
        self.candidate_data = {"inbounds": nodes}
        candidate.write_text(json.dumps(self.candidate_data), encoding="utf-8")
        return candidate

    def assert_checked_both_configs(self):
        args = (self.root / "check-args").read_bytes().decode("utf-8").split("\0")[:-1]
        self.assertEqual(args[0], "check")
        self.assertEqual(args.count("-c"), 2, args)
        paths = [args[index + 1] for index, arg in enumerate(args) if arg == "-c"]
        self.assertIn((self.output / "sb.json").as_posix(), paths)
        checked = [json.loads((self.root / f"checked-{index}.json").read_text(encoding="utf-8"))
                   for index in range(2)]
        self.assertIn(self.base, checked)
        self.assertIn(self.candidate_data, checked)

    def test_failed_kernel_check_preserves_both_configs_and_does_not_restart(self):
        self.write_extra()
        candidate = self.candidate()
        before = {name: (self.output / name).read_bytes() for name in ("sb.json", "sb_extra.json")}
        script = self.transaction_script("extra_apply " + shlex.quote(candidate.as_posix()), check_status=27)
        result = self.run_shell(script, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assert_checked_both_configs()
        for name, content in before.items():
            self.assertEqual((self.output / name).read_bytes(), content)
        self.assertFalse((self.root / "service-log").exists())

    def test_failed_first_apply_does_not_create_extra_configuration(self):
        candidate = self.candidate()
        before = (self.output / "sb.json").read_bytes()
        result = self.run_shell(self.transaction_script(
            "extra_apply " + shlex.quote(candidate.as_posix()), check_status=27), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assert_checked_both_configs()
        self.assertFalse((self.output / "sb_extra.json").exists())
        self.assertEqual((self.output / "sb.json").read_bytes(), before)
        self.assertFalse((self.root / "service-log").exists())

    def test_successful_apply_checks_both_configs_and_keeps_base_bytes(self):
        self.write_extra()
        candidate = self.candidate()
        base_bytes = (self.output / "sb.json").read_bytes()
        self.run_shell(self.transaction_script("extra_apply " + shlex.quote(candidate.as_posix())))
        self.assert_checked_both_configs()
        self.assertEqual(json.loads((self.output / "sb_extra.json").read_text(encoding="utf-8")),
                         self.candidate_data)
        self.assertEqual((self.output / "sb.json").read_bytes(), base_bytes)
        self.assertIn("apply", (self.root / "service-log").read_text().splitlines())

    def test_service_failure_restores_old_extra_and_base_bytes(self):
        self.write_extra()
        candidate = self.candidate()
        before = {name: (self.output / name).read_bytes() for name in ("sb.json", "sb_extra.json")}
        result = self.run_shell(self.transaction_script(
            "extra_apply " + shlex.quote(candidate.as_posix()), service_status=28), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assert_checked_both_configs()
        for name, content in before.items():
            self.assertEqual((self.output / name).read_bytes(), content)
        self.assertEqual((self.root / "service-log").read_text().splitlines(), ["apply", "restart"])

    def test_terminated_service_activation_restores_previous_configuration(self):
        self.write_extra()
        candidate = self.candidate()
        before = {name: (self.output / name).read_bytes() for name in ("sb.json", "sb_extra.json")}
        script = self.transaction_script(f"""
sbservice() {{
    printf 'apply\\n' >> "$LOG_ROOT/service-log"
    kill -TERM "$BASHPID"
}}
extra_apply {shlex.quote(candidate.as_posix())}
""")
        result = self.run_shell(script, check=False)
        self.assertNotEqual(result.returncode, 0)
        for name, content in before.items():
            self.assertEqual((self.output / name).read_bytes(), content)
        self.assertEqual((self.root / "service-log").read_text().splitlines(), ["apply", "restart"])

    def test_snell_set_rejects_old_kernel_without_modifying_configuration(self):
        self.write_extra()
        before = (self.output / "sb_extra.json").read_bytes()
        for version in ("1.10.7", "1.13.12"):
            with self.subTest(version=version):
                result = self.run_shell(self.transaction_script(
                    "extra_set snell 9555 new-independent-psk", version=version), check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((self.output / "sb_extra.json").read_bytes(), before)
                self.assertFalse((self.root / "service-log").exists())

    def test_set_rejects_base_and_other_extra_protocol_port_conflicts(self):
        self.write_extra()
        before = {name: (self.output / name).read_bytes() for name in ("sb.json", "sb_extra.json")}
        for port in (443, 8443, 9443):
            with self.subTest(port=port):
                result = self.run_shell(self.transaction_script(
                    f"extra_set snell {port} new-independent-psk"), check=False)
                self.assertNotEqual(result.returncode, 0)
                for name, content in before.items():
                    self.assertEqual((self.output / name).read_bytes(), content)
                self.assertFalse((self.root / "service-log").exists())

    def test_set_replaces_one_protocol_preserving_other_and_base(self):
        self.write_extra()
        base_bytes = (self.output / "sb.json").read_bytes()
        self.run_shell(self.transaction_script("extra_set snell 9555 " + shlex.quote(self.psk + "-new")))
        nodes = json.loads((self.output / "sb_extra.json").read_text(encoding="utf-8"))["inbounds"]
        self.assertEqual(len(nodes), 2)
        by_type = {node["type"]: node for node in nodes}
        self.assertEqual(by_type["trojan"], self.inbounds()[0])
        self.assertEqual(by_type["snell"]["listen_port"], 9555)
        self.assertEqual(by_type["snell"]["psk"], self.psk + "-new")
        self.assertEqual((self.output / "sb.json").read_bytes(), base_bytes)

    def test_set_rejects_tcp_port_occupied_by_another_process(self):
        self.write_extra()
        before = (self.output / "sb_extra.json").read_bytes()
        result = self.run_shell(self.transaction_script(
            "extra_set snell 9555 new-independent-psk",
            sockets="LISTEN 0 4096 [::]:9555 [::]:*"), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.output / "sb_extra.json").read_bytes(), before)
        self.assertFalse((self.root / "check-args").exists())
        self.assertFalse((self.root / "service-log").exists())

    def test_remove_only_selected_protocol_preserves_other_and_base(self):
        base_bytes = (self.output / "sb.json").read_bytes()
        for remove, keep in (("trojan", "snell"), ("snell", "trojan")):
            with self.subTest(remove=remove):
                self.write_extra()
                self.run_shell(self.transaction_script("extra_remove " + remove))
                nodes = json.loads((self.output / "sb_extra.json").read_text(encoding="utf-8"))["inbounds"]
                self.assertEqual(nodes, [node for node in self.inbounds() if node["type"] == keep])
                self.assertEqual((self.output / "sb.json").read_bytes(), base_bytes)

    def test_extra_share_round_trips_ipv6_password_sni_and_tag(self):
        nodes = self.inbounds()
        password = self.password + " @/?&%+"
        hostname = '节点 "quote" / # & + %'
        sni = "节点.example.com"
        nodes[0]["users"][0]["password"] = password
        nodes[0]["tls"]["server_name"] = sni
        self.write_extra(nodes)
        result = self.run_shell(self.functions() + f"""
red() {{ :; }}
qrencode() {{ :; }}
server_ipcl=2001:db8::42
hostname={shlex.quote(hostname)}
extra_share
""")
        link = (self.output / "trojan.txt").read_text(encoding="utf-8").strip()
        parsed = urlsplit(link)
        self.assertEqual(parsed.scheme, "trojan")
        self.assertEqual(unquote(parsed.username), password)
        self.assertIsNone(parsed.password)
        self.assertEqual(parsed.hostname, "2001:db8::42")
        self.assertEqual(parsed.port, 9443)
        self.assertEqual(parse_qs(parsed.query), {
            "security": ["tls"], "type": ["tcp"], "sni": [sni], "allowInsecure": ["1"]})
        self.assertEqual(unquote(parsed.fragment), "trojan-" + hostname)
        self.assertIn(link, result.stdout.splitlines())
        self.assertNotIn("snell://", result.stdout)

    def combined_share_script(self):
        # Keep both subscription publishers real; only the old node generators
        # and display/network probes are irrelevant to this protocol test.
        share = self.source("sbshare", "clash_sb_share") + f"""
output={shlex.quote(self.output.as_posix())}
white() {{ :; }}
red() {{ :; }}
sleep() {{ :; }}
qrencode() {{ :; }}
result_vl_vm_hy_tu() {{ tls=false; }}
resvless() {{ printf 'vless://old-node\\n' > "$output/vl_reality.txt"; }}
resvmess() {{ printf 'vmess://old-node\\n' > "$output/vm_ws.txt"; }}
reshy2() {{ printf 'hysteria2://old-node\\n' > "$output/hy2.txt"; }}
restu5() {{ printf 'tuic://old-node\\n' > "$output/tuic5.txt"; }}
resan() {{ printf 'anytls://old-node\\n' > "$output/an.txt"; }}
server_ipcl=2001:db8::42
sbshare
exit $?
"""
        return self.client_script(extra=share)

    def test_combined_share_adds_trojan_and_removes_stale_link_after_disable(self):
        self.write_extra()
        self.run_shell(self.combined_share_script())
        old_links = ["vless://old-node", "vmess://old-node", "hysteria2://old-node",
                     "tuic://old-node", "anytls://old-node"]
        trojan = (self.output / "trojan.txt").read_text(encoding="utf-8").strip()
        links = (self.output / "jhsub.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(links, old_links + [trojan])
        self.assertNotIn("snell://", "\n".join(links))
        self.run_shell(self.transaction_script("extra_remove trojan"))
        self.run_shell(self.combined_share_script())
        self.assertEqual((self.output / "jhsub.txt").read_text(encoding="utf-8").splitlines(), old_links)
        self.assertFalse((self.output / "trojan.txt").exists())
        clash_types = {node["type"] for node in self.read_yaml()["proxies"]}
        self.assertNotIn("trojan", clash_types)
        self.assertIn("snell", clash_types)

    def test_first_install_selects_supported_protocols_without_changing_base_or_services(self):
        base_bytes = (self.output / "sb.json").read_bytes()
        for version, protocols in (("1.14.0", {"trojan", "snell"}), ("1.10.7", {"trojan"})):
            with self.subTest(version=version):
                (self.output / "sb_extra.json").unlink(missing_ok=True)
                script = self.transaction_script(f"""
port_tr=9443
port_sn=9444
uuid={shlex.quote(self.password)}
certificatec_hy2={shlex.quote((self.output / 'cert.pem').as_posix())}
certificatep_hy2={shlex.quote((self.output / 'private.key').as_posix())}
extra_install
""", version=version)
                self.run_shell(script)
                actual = json.loads((self.output / "sb_extra.json").read_text(encoding="utf-8"))
                expected = [node for node in self.inbounds() if node["type"] in protocols]
                expected[0]["tls"]["server_name"] = "www.bing.com"
                self.candidate_data = {"inbounds": expected}
                self.assertEqual(actual, self.candidate_data)
                self.assert_checked_both_configs()
                self.assertEqual((self.output / "sb.json").read_bytes(), base_bytes)
                self.assertFalse((self.root / "service-log").exists())

    def test_service_definitions_load_both_configs_for_systemd_and_openrc(self):
        self.write_extra()
        self.candidate_data = {"inbounds": self.inbounds()}
        init = self.root / "init.d"
        systemd = self.root / "systemd"
        init.mkdir()
        systemd.mkdir()
        definition = (self.source("sbservice", "ipuuid")
                      .replace("/etc/init.d", init.as_posix())
                      .replace("/etc/systemd/system", systemd.as_posix())
                      .replace("/var/run", (self.root / "run").as_posix())
                      .replace("/root", (self.root / "home").as_posix()))
        expected = [(self.output / "sing-box").as_posix(), "run", "-c",
                    (self.output / "sb.json").as_posix(), "-c",
                    (self.output / "sb_extra.json").as_posix()]
        for mode in ("systemd", "openrc"):
            with self.subTest(mode=mode):
                (self.root / "service-log").unlink(missing_ok=True)
                body = definition + f"""
command() {{
    if [[ $1 == -v && $2 == apk ]]; then
        [[ {shlex.quote(mode)} == openrc ]]
        return
    fi
    builtin command "$@"
}}
systemctl() {{ printf 'systemctl %s\\n' "$*" >> "$LOG_ROOT/service-log"; }}
rc-update() {{ printf 'rc-update %s\\n' "$*" >> "$LOG_ROOT/service-log"; }}
rc-service() {{ printf 'rc-service %s\\n' "$*" >> "$LOG_ROOT/service-log"; }}
sbservice
"""
                self.run_shell(self.transaction_script(body))
                self.assert_checked_both_configs()
                events = (self.root / "service-log").read_text().splitlines()
                if mode == "systemd":
                    unit = configparser.RawConfigParser()
                    unit.read(systemd / "sing-box.service", encoding="utf-8")
                    self.assertEqual(shlex.split(unit["Service"]["ExecStart"]), expected)
                    self.assertIn("systemctl daemon-reload", events)
                    self.assertIn("systemctl restart sing-box", events)
                else:
                    result = self.run_shell(f"""
source {shlex.quote((init / 'sing-box').as_posix())} || exit 1
printf '%s\\n%s\\n' "$command" "$command_args"
""")
                    executable, arguments = result.stdout.splitlines()
                    self.assertEqual([executable] + shlex.split(arguments), expected)
                    self.assertIn("rc-update add sing-box default", events)
                    self.assertIn("rc-service sing-box restart", events)

