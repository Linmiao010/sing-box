"""Publication tests run only extracted generators, never the VPS installer."""
import json
import os
from pathlib import Path
import shlex
import shutil

from shell_testlib import REPO, ShellTestCase, section

try:
    import yaml
except ImportError:
    yaml = None


class PublishTests(ShellTestCase):
    def setUp(self):
        super().setUp()
        self.output = self.root / "published"
        self.output.mkdir()
        self.previous = {
            "sbox.json": '{"previous": true}\n',
            "clmi.yaml": "previous: true\n",
            "jhsub.txt": "vless://previous\n",
        }
        for name, value in self.previous.items():
            (self.output / name).write_text(value, encoding="utf-8")
            os.link(self.output / name, self.root / (name + ".previous"))
        (self.output / "sb.json").write_text(
            '{"inbounds":[{}, {"tls":{"enabled":false},"listen_port":8443}]}',
            encoding="utf-8",
        )

    def source(self, start, end):
        return section(start, end).replace("/etc/s-box", self.output.as_posix())

    def assert_previous(self, *names):
        for name in names:
            self.assertEqual((self.output / name).read_text(encoding="utf-8"), self.previous[name])

    def assert_clean(self):
        self.assertEqual([p.name for p in self.output.iterdir() if p.is_dir()], [])

    def client_script(self, processes="", extra="", version="1.12"):
        jq = os.environ.get("JQ") or shutil.which("jq")
        local_jq = REPO / ".test-tools" / "jq.exe"
        if not jq and local_jq.is_file():
            jq = str(local_jq)
        if not jq:
            self.skipTest("Install jq or set JQ to run the real client template tests")
        return self.source("sb_client", "cfargo_ym") + f"""
jq() {{ {shlex.quote(Path(jq).as_posix())} "$@"; }}
ps() {{ printf '%s\\n' {shlex.quote(processes)}; }}
hostname=test-node
sbnh={shlex.quote(version)}
server_ipcl=192.0.2.1
uuid=11111111-1111-1111-1111-111111111111
vl_port=443 vl_name=example.com public_key=test-key short_id=abcd
vm_port=8443 vmadd_local=192.0.2.1 vm_name=example.com ws_path=/test
vmadd_argo=cdn.example.com argo=temporary.example.com argogd=fixed.example.com
hy2_port=8444 cl_hy2_ip=192.0.2.1 hy2_name=example.com hy2_ins=false
tu5_port=8445 cl_tu5_ip=192.0.2.1 tu5_name=example.com tu5_ins=false
an_port=8446 sb_an_ip=192.0.2.1 cl_an_ip=192.0.2.1 an_name=example.com an_ins=false
{extra}
if sb_client; then exit 0; else exit 1; fi
"""

    def share_script(self, extra=""):
        output = shlex.quote(self.output.as_posix())
        return self.source("sbshare", "clash_sb_share") + f"""
output={output}
sbnh=1.12
white() {{ :; }}
red() {{ :; }}
sleep() {{ :; }}
result_vl_vm_hy_tu() {{ tls=false; }}
resvless() {{ printf 'vless://new\\n' > "$output/vl_reality.txt"; }}
resvmess() {{ printf 'vmess://new\\n' > "$output/vm_ws.txt"; }}
reshy2() {{ printf 'hysteria2://new\\n' > "$output/hy2.txt"; }}
restu5() {{ printf 'tuic://new\\n' > "$output/tuic5.txt"; }}
resan() {{ printf 'anytls://new\\n' > "$output/an.txt"; }}
sb_client() {{ :; }}
{extra}
if sbshare; then exit 0; else exit 1; fi
"""

    def test_all_client_branches_replace_files_without_truncating_old_inode(self):
        for fixed, temporary in ((False, False), (True, False), (False, True), (True, True)):
            for version in ("1.10", "1.12"):
                with self.subTest(fixed=fixed, temporary=temporary, version=version):
                    for name, value in self.previous.items():
                        previous_link = self.root / (name + ".previous")
                        previous_link.unlink()
                        (self.output / name).write_text(value, encoding="utf-8")
                        os.link(self.output / name, previous_link)
                    processes = ("cloudflared tunnel run\n" if fixed else "") + (
                        "cloudflared --url localhost:8443\n" if temporary else ""
                    )
                    self.run_shell(self.client_script(processes, version=version))
                    config = json.loads((self.output / "sbox.json").read_text(encoding="utf-8"))
                    tags = [item["tag"] for item in config["outbounds"]]
                    self.assertIn("vless-test-node", tags)
                    self.assertEqual("anytls-test-node" in tags, version != "1.10")
                    self.assertEqual("vmess-tls-argo固定-test-node" in tags, fixed)
                    self.assertEqual("vmess-tls-argo临时-test-node" in tags, temporary)
                    self.assertTrue((self.output / "clmi.yaml").read_text(encoding="utf-8").startswith("port: 7890\n"))
                    for name in ("sbox.json", "clmi.yaml"):
                        self.assertEqual((self.root / (name + ".previous")).read_text(encoding="utf-8"), self.previous[name])
                    self.assert_clean()

    def test_domestic_dns_policy_is_generated_without_changing_routing(self):
        if yaml is None:
            self.skipTest("PyYAML is required to inspect the generated DNS policy")
        for tls in (False, True):
            for fixed, temporary in ((False, False), (True, False), (False, True), (True, True)):
                with self.subTest(tls=tls, fixed=fixed, temporary=temporary):
                    (self.output / "sb.json").write_text(json.dumps({"inbounds": [
                        {}, {"tls": {"enabled": tls}, "listen_port": 8443}]}), encoding="utf-8")
                    processes = ("cloudflared tunnel run\n" if fixed else "") + (
                        "cloudflared --url localhost:8443\n" if temporary else "")
                    self.run_shell(self.client_script(processes))
                    config = yaml.safe_load((self.output / "clmi.yaml").read_text(encoding="utf-8"))
                    dns = config["dns"]
                    self.assertEqual(dns.get("nameserver-policy"), {
                        "geosite:cn": ["https://223.5.5.5/dns-query", "https://doh.pub/dns-query"]})
                    self.assertEqual(dns["nameserver"], ["https://1.1.1.1/dns-query", "https://8.8.8.8/dns-query"])
                    self.assertEqual(dns["proxy-server-nameserver"], [
                        "https://223.5.5.5/dns-query", "https://doh.pub/dns-query"])
                    self.assertEqual(dns["enhanced-mode"], "fake-ip")
                    self.assertTrue(dns["respect-rules"])
                    self.assertTrue(dns["ipv6"])
                    self.assertEqual(config["rules"], ["GEOIP,LAN,DIRECT", "GEOSITE,CN,DIRECT",
                                                     "GEOIP,CN,DIRECT", "MATCH,🌍选择代理节点"])

    def test_invalid_json_retains_both_previous_client_files(self):
        result = self.run_shell(self.client_script(extra="vl_port=invalid-number"), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assert_previous("sbox.json", "clmi.yaml")
        self.assert_clean()

    def test_failed_template_command_retains_previous_client_files(self):
        result = self.run_shell(self.client_script(extra='cat() { command cat "$@"; return 29; }'), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assert_previous("sbox.json", "clmi.yaml")
        self.assert_clean()

    def test_empty_template_output_is_not_published(self):
        result = self.run_shell(self.client_script(extra="cat() { :; }"), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assert_previous("sbox.json", "clmi.yaml")
        self.assert_clean()

    def test_client_rename_failure_keeps_previous_file_and_reports_failure(self):
        result = self.run_shell(self.client_script(extra="mv() { return 31; }"), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assert_previous("sbox.json", "clmi.yaml")
        self.assert_clean()

    def test_yaml_write_error_retains_both_previous_client_files(self):
        extra = """
mktemp() {
    local directory
    directory=$(command mktemp "$@") || return 1
    mkdir "$directory/clmi.yaml" || return 1
    printf '%s\\n' "$directory"
}
"""
        result = self.run_shell(self.client_script(extra=extra), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assert_previous("sbox.json", "clmi.yaml")
        self.assert_clean()

    def test_temporary_directory_failure_retains_previous_publications(self):
        for script in (self.client_script(extra="mktemp() { return 32; }"),
                       self.share_script(extra="mktemp() { return 32; }")):
            result = self.run_shell(script, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assert_previous("sbox.json", "clmi.yaml", "jhsub.txt")
            self.assert_clean()

    def test_share_replaces_file_without_truncating_old_inode(self):
        self.run_shell(self.share_script())
        self.assertEqual(
            (self.output / "jhsub.txt").read_text(encoding="utf-8"),
            "vless://new\nvmess://new\nhysteria2://new\ntuic://new\nanytls://new\n",
        )
        self.assertEqual((self.root / "jhsub.txt.previous").read_text(encoding="utf-8"), self.previous["jhsub.txt"])
        self.assert_clean()

    def test_failed_node_generator_retains_previous_share(self):
        result = self.run_shell(self.share_script(extra="resvless() { return 23; }"), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assert_previous("jhsub.txt")
        self.assert_clean()

    def test_node_write_failure_is_not_hidden_by_successful_display(self):
        for function, end, prefix in (
            ("resvless", "resvmess", "vless://"),
            ("resvmess", "reshy2", "vmess://"),
            ("reshy2", "restu5", "hysteria2://"),
            ("restu5", "resan", "tuic://"),
            ("resan", "sb_client", "anytls://"),
        ):
            with self.subTest(function=function):
                extra = self.source(function, end) + f"""
ps() {{ :; }}
jq() {{ printf '8443\\n'; }}
qrencode() {{ :; }}
echo() {{
    builtin echo "$@"
    if [[ "$1" == {shlex.quote(prefix)}* ]]; then return 29; fi
}}
"""
                result = self.run_shell(self.share_script(extra=extra), check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assert_previous("jhsub.txt")
                self.assert_clean()

    def test_empty_required_node_retains_previous_share(self):
        result = self.run_shell(self.share_script(extra='resvless() { : > "$output/vl_reality.txt"; }'), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assert_previous("jhsub.txt")
        self.assert_clean()

    def test_failed_vmess_encoding_retains_previous_share(self):
        extra = self.source("resvmess", "reshy2") + """
ps() { :; }
jq() { printf '8443\\n'; }
qrencode() { :; }
base64() { return 27; }
"""
        result = self.run_shell(self.share_script(extra=extra), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assert_previous("jhsub.txt")
        self.assert_clean()

    def test_failed_client_generation_retains_previous_share(self):
        result = self.run_shell(self.share_script(extra="sb_client() { return 24; }"), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assert_previous("jhsub.txt")
        self.assert_clean()

    def test_share_write_error_is_not_hidden_by_later_success(self):
        result = self.run_shell(self.share_script(extra='cat() { command cat "$@"; return 28; }'), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assert_previous("jhsub.txt")
        self.assert_clean()

    def test_share_rename_failure_retains_previous_share(self):
        result = self.run_shell(self.share_script(extra="mv() { return 31; }"), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assert_previous("jhsub.txt")
        self.assert_clean()
