"""Exercise IP priority changes with real jq and isolated server templates."""
import base64
import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess

from shell_testlib import REPO, ShellTestCase, section


STRATEGIES = ("prefer_ipv4", "prefer_ipv6", "ipv4_only", "ipv6_only")
CONFIGS = ("sb10.json", "sb11.json", "sb.json")


class IpPriorityTests(ShellTestCase):
    def setUp(self):
        super().setUp()
        jq = shutil.which("jq")
        if not jq and (REPO / ".test-tools/jq.exe").is_file():
            jq = str(REPO / ".test-tools/jq.exe")
        if not jq:
            self.skipTest("Install jq or provide .test-tools/jq.exe for real JSON transforms")
        self.shell_env = {"PATH": str(Path(jq).parent) + os.pathsep + os.environ["PATH"]}
        self.box = self.root / "s-box"
        self.box.mkdir()

    def source(self, start, end):
        source = section(start, end)
        if start == "ip_priority_get":
            source = section("ip_literal_valid", "warpcheck") + "\n" + source
        return source.replace("/etc/s-box", "s-box")

    def generate(self, version="1.14", initial="prefer_ipv4"):
        values = {
            "sbnh": version, "uuid": "11111111-1111-1111-1111-111111111111",
            "port_vl_re": "19441", "port_vm_ws": "19442", "port_hy2": "19443",
            "port_tu": "19444", "port_an": "19445", "ym_vl_re": "example.com",
            "ym_vm_ws": "localhost", "tlsyn": "false", "short_id": "abcd",
            "private_key": base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("="),
            "ipv": initial, "endip": "192.0.2.2", "v6": "fd00::2",
            "pvk": base64.b64encode(bytes(range(32))).decode(), "res": "[1,2,3]",
        }
        for protocol in ("vmess_ws", "hy2", "tuic", "an"):
            values["certificatec_" + protocol] = (self.box / "cert.pem").as_posix()
            values["certificatep_" + protocol] = (self.box / "private.key").as_posix()
        script = self.source("inssbjsonser", "snell_supported") + "\n"
        script += "\n".join(f"{key}={shlex.quote(value)}" for key, value in values.items())
        self.run_shell(script + "\ninssbjsonser\n", env=self.shell_env)
        self.version = version

    def read_config(self, name):
        return json.loads((self.box / name).read_text(encoding="utf-8"))

    def write_config(self, name, data):
        (self.box / name).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def default_resolves(config):
        # A default resolve has action options only, and no route match fields.
        options = {"action", "strategy", "server", "disable_cache", "rewrite_ttl", "client_subnet"}
        return [rule for rule in config["route"]["rules"]
                if rule.get("action") == "resolve" and set(rule).issubset(options)]

    def snapshot(self):
        return {name: (self.box / name).read_bytes() for name in CONFIGS}

    def assert_preserved(self, before):
        self.assertEqual(self.snapshot(), before)

    def prepare_apply(self):
        for name in CONFIGS:
            shutil.copyfile(self.box / name, self.root / ("before-" + name))
        for name in ("check-args", "restart-count", "checked-config", "restarted-config",
                     "prompts", "probes", "navigation", "service-checks"):
            (self.root / name).unlink(missing_ok=True)
        # Only core validation and restart are simulated. jq edits real JSON.
        core = r'''#!/bin/bash
[[ "$1" == check ]] || exit 2
shift
printf '%s\n' "$@" > check-args
for name in sb10.json sb11.json sb.json; do
    cmp -s "s-box/$name" "before-$name" || exit 91
done
first=true
while (( $# )); do
    case "$1" in
        -c|--config)
            shift
            jq -e . "$1" >/dev/null || exit 3
            if $first; then cp "$1" checked-config; first=false; fi
            if [[ "$TEST_SCENARIO" == extra_failure && "$1" == *sb_extra.json ]]; then exit 4; fi
            ;;
    esac
    shift
done
[[ "$TEST_SCENARIO" != check_failure ]]
'''
        (self.box / "sing-box").write_text(core, encoding="utf-8", newline="\n")
        script = self.source("ip_priority_get", "tgsbshow") + r'''
red(){ :; }
yellow(){ :; }
green(){ :; }
blue(){ :; }
sleep(){ :; }
restartsb(){
    local count=0
    [[ ! -f restart-count ]] || read -r count < restart-count
    count=$((count + 1))
    printf '%s\n' "$count" > restart-count
    cp s-box/sb.json restarted-config
    [[ "$TEST_SCENARIO" != restart_failure || "$count" != 1 ]]
}
systemctl(){
    printf 'systemd %s\n' "$*" >> service-checks
    [[ "$*" == 'is-active --quiet sing-box' ]] || return 2
    [[ "$TEST_SCENARIO" != service_failure || "$(cat restart-count)" != 1 ]]
}
rc-service(){
    printf 'openrc %s\n' "$*" >> service-checks
    [[ "$*" == 'sing-box status' ]] || return 2
    if [[ "$TEST_SCENARIO" == service_failure && "$(cat restart-count)" == 1 ]]; then
        printf ' * status: stopped\n'; return 1
    fi
    printf ' * status: started\n'
}
if [[ "$TEST_INIT" == openrc ]]; then apk(){ :; }; fi
chmod +x s-box/sing-box
'''
        return script + f"\nsbnh={shlex.quote(self.version)}\n"

    def apply(self, strategy, scenario="ok"):
        script = self.prepare_apply() + f"ip_priority_apply {shlex.quote(strategy)}\n"
        return self.run_shell(script, check=False,
                              env={**self.shell_env, "TEST_SCENARIO": scenario})

    def change_priority(self, choices, scenario="ok", probe_failure=False, init="systemd"):
        (self.root / "menu-input").write_text(choices, encoding="utf-8", newline="\n")
        script = self.prepare_apply() + r'''
red(){ printf '%s\n' "$*"; }
yellow(){ printf '%s\n' "$*"; }
blue(){ printf '%s\n' "$*"; }
readp(){ printf 'prompt\n' >> prompts; IFS= read -r "$2"; }
public_ip(){
    printf '%s\n' "$1" >> probes
    [[ "$TEST_PROBE_FAILURE" != true ]] || return 1
    if [[ "$1" == 4 ]]; then printf '203.0.113.7\n'; else printf '2001:db8::7\n'; fi
}
sing_box(){ printf 'main\n' >> navigation; }
changeserv(){ printf 'service\n' >> navigation; }
changeip < menu-input
'''
        return self.run_shell(script, check=False, env={
            **self.shell_env, "TEST_SCENARIO": scenario, "TEST_INIT": init,
            "TEST_PROBE_FAILURE": str(probe_failure).lower(),
        })

    def get_priority(self, filename=None):
        command = "ip_priority_get" + (" " + shlex.quote("s-box/" + filename) if filename else "")
        result = self.run_shell(self.source("ip_priority_get", "tgsbshow") + "\n" + command + "\n",
                                env=self.shell_env)
        return result.stdout.strip()

    def assert_strategy(self, strategy):
        legacy = self.read_config("sb10.json")
        direct = next(node for node in legacy["outbounds"] if node["tag"] == "direct")
        self.assertEqual(direct["domain_strategy"], strategy)
        modern = self.read_config("sb11.json")
        defaults = self.default_resolves(modern)
        self.assertEqual(len(defaults), 1)
        self.assertEqual(defaults[0]["strategy"], strategy)
        self.assertEqual(modern["route"]["rules"][-2], defaults[0])
        self.assertEqual(modern["route"]["rules"][-1]["outbound"], "direct")
        template = "sb10.json" if self.version == "1.10" else "sb11.json"
        self.assertEqual(self.read_config("sb.json"), self.read_config(template))
        self.assertEqual(self.get_priority(), strategy)
        self.assertEqual(self.get_priority("sb10.json"), strategy)
        self.assertEqual(self.get_priority("sb11.json"), strategy)

    def test_all_strategies_update_both_templates_and_active_version(self):
        for version in ("1.10", "1.14"):
            for strategy in STRATEGIES:
                with self.subTest(version=version, strategy=strategy):
                    self.generate(version)
                    before = {name: self.read_config(name) for name in CONFIGS}
                    result = self.apply(strategy)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assert_strategy(strategy)
                    self.assertEqual((self.root / "restart-count").read_text().strip(), "1")
                    self.assertEqual(json.loads((self.root / "checked-config").read_text()),
                                     self.read_config("sb.json"))
                    for name in ("sb10.json", "sb11.json"):
                        after = self.read_config(name)
                        self.assertEqual(after["route"]["rules"][:5], before[name]["route"]["rules"][:5])
                        if name == "sb10.json":
                            self.assertEqual(after["route"], before[name]["route"])
                        self.assertEqual(after["inbounds"], before[name]["inbounds"])
                        self.assertEqual([n for n in after["outbounds"] if n["tag"] != "direct"],
                                         [n for n in before[name]["outbounds"] if n["tag"] != "direct"])

    def test_active_config_keeps_changes_independent_of_stored_template(self):
        for version in ("1.10", "1.14"):
            with self.subTest(version=version):
                self.generate(version)
                active = self.read_config("sb.json")
                active["log"]["level"] = "warn"
                active["inbounds"][0]["listen_port"] = 29541
                self.write_config("sb.json", active)
                result = self.apply("prefer_ipv6")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                after = self.read_config("sb.json")
                self.assertEqual(after["log"], active["log"])
                self.assertEqual(after["inbounds"], active["inbounds"])
                self.assertEqual(self.get_priority(), "prefer_ipv6")
                template = "sb10.json" if version == "1.10" else "sb11.json"
                self.assertEqual(self.read_config(template)["log"]["level"], "info")

    def test_older_modern_install_migrates_without_changing_existing_routes(self):
        self.generate()
        modern = self.read_config("sb11.json")
        defaults = self.default_resolves(modern)
        modern["route"]["rules"] = [r for r in modern["route"]["rules"] if r not in defaults]
        # Customized existing split rules must retain their values and indexes.
        modern["route"]["rules"][1]["domain_suffix"] = ["ipv4.example"]
        modern["route"]["rules"][2]["domain_suffix"] = ["ipv6.example"]
        modern["route"]["rules"][3]["domain_suffix"] = ["proxy.example"]
        for name in ("sb11.json", "sb.json"):
            self.write_config(name, modern)
        self.assertEqual(self.get_priority(), "default")
        before = copy.deepcopy(modern["route"]["rules"])
        for strategy in ("prefer_ipv6", "ipv4_only", "prefer_ipv4", "ipv6_only", "ipv6_only"):
            result = self.apply(strategy)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assert_strategy(strategy)
            after = self.read_config("sb11.json")["route"]["rules"]
            self.assertEqual(after[:-2], before[:-1])
            self.assertEqual(after[-1], before[-1])

    def test_new_install_uses_detected_initial_priority(self):
        for version in ("1.10", "1.14"):
            for initial in ("prefer_ipv4", "prefer_ipv6"):
                with self.subTest(version=version, initial=initial):
                    self.generate(version, initial)
                    self.assertEqual(self.get_priority(), initial)
                    self.assert_strategy(initial)

    def test_fixed_line_fields_remain_compatible_after_switching(self):
        self.generate()
        for strategy in STRATEGIES:
            result = self.apply(strategy)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            modern = (self.box / "sb11.json").read_text().splitlines()
            for line in (177, 184, 190, 196):
                self.assertEqual(modern[line - 1].strip(), '"yg_kkk"', f"sb11.json:{line}")
            legacy = (self.box / "sb10.json").read_text().splitlines()
            self.assertEqual(json.loads("{" + legacy[110] + "}"), {"domain_strategy": strategy})

    def test_invalid_strategies_leave_files_unchanged_without_restart(self):
        self.generate()
        before = self.snapshot()
        for strategy in ("", "default", "prefer_ipv5", "prefer_ipv4;false"):
            with self.subTest(strategy=strategy):
                result = self.apply(strategy)
                self.assertNotEqual(result.returncode, 0)
                self.assert_preserved(before)
                self.assertFalse((self.root / "check-args").exists())
                self.assertFalse((self.root / "restart-count").exists())

    def test_malformed_json_in_any_config_leaves_all_files_unchanged(self):
        for name in CONFIGS:
            with self.subTest(name=name):
                self.generate()
                (self.box / name).write_text('{"broken":', encoding="utf-8")
                before = self.snapshot()
                result = self.apply("ipv6_only")
                self.assertNotEqual(result.returncode, 0)
                self.assert_preserved(before)
                self.assertFalse((self.root / "restart-count").exists())

    def test_failed_core_check_does_not_replace_configs_or_restart(self):
        for version in ("1.10", "1.14"):
            with self.subTest(version=version):
                self.generate(version)
                before = self.snapshot()
                result = self.apply("prefer_ipv6", "check_failure")
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue((self.root / "check-args").is_file())
                self.assert_preserved(before)
                self.assertFalse((self.root / "restart-count").exists())

    def test_extra_config_is_checked_together_and_preserved_on_failure(self):
        self.generate()
        extra = b'{"inbounds":[{"type":"snell","tag":"custom-snell"}]}\n'
        (self.box / "sb_extra.json").write_bytes(extra)
        before = self.snapshot()
        result = self.apply("prefer_ipv6", "extra_failure")
        self.assertNotEqual(result.returncode, 0)
        arguments = (self.root / "check-args").read_text().splitlines()
        self.assertEqual(arguments.count("-c"), 2)
        self.assertTrue(any(path.endswith("sb_extra.json") for path in arguments))
        self.assert_preserved(before)
        self.assertEqual((self.box / "sb_extra.json").read_bytes(), extra)
        self.assertFalse((self.root / "restart-count").exists())

    def test_failed_restart_restores_all_configs_and_restarts_previous_config(self):
        for version in ("1.10", "1.14"):
            with self.subTest(version=version):
                self.generate(version)
                before = self.snapshot()
                result = self.apply("ipv6_only", "restart_failure")
                self.assertNotEqual(result.returncode, 0)
                self.assert_preserved(before)
                self.assertEqual((self.root / "restart-count").read_text().strip(), "2")
                self.assertEqual((self.root / "restarted-config").read_bytes(), before["sb.json"])

    def test_menu_options_apply_expected_strategy(self):
        for choice, strategy, family in (("1", "prefer_ipv4", "4"), ("2", "prefer_ipv6", "6"),
                                         ("3", "ipv4_only", "4"), ("4", "ipv6_only", "6")):
            with self.subTest(choice=choice):
                self.generate()
                result = self.change_priority(choice + "\n")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assert_strategy(strategy)
                self.assertEqual((self.root / "probes").read_text().splitlines(), [family])
                self.assertIn("当前已更换", result.stdout)
                self.assertEqual((self.root / "navigation").read_text().splitlines(), ["main"])

    def test_menu_probe_failure_allows_preferences_but_rejects_only_modes(self):
        for choice, strategy in enumerate(STRATEGIES, 1):
            with self.subTest(choice=choice):
                self.generate(initial="prefer_ipv6" if choice == 1 else "prefer_ipv4")
                before = self.snapshot()
                result = self.change_priority(f"{choice}\n", probe_failure=True)
                self.assertIn("公网探测未成功", result.stdout)
                if strategy.endswith("_only"):
                    self.assertNotEqual(result.returncode, 0)
                    self.assert_preserved(before)
                    self.assertFalse((self.root / "check-args").exists())
                    self.assertFalse((self.root / "restart-count").exists())
                    self.assertNotIn("当前已更换", result.stdout)
                else:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(self.get_priority(), strategy)
                    self.assertIn("当前已更换", result.stdout)

    def test_menu_check_and_restart_failures_do_not_report_success(self):
        for scenario in ("check_failure", "restart_failure"):
            with self.subTest(scenario=scenario):
                self.generate()
                before = self.snapshot()
                result = self.change_priority("2\n", scenario=scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue((self.root / "check-args").exists())
                self.assert_preserved(before)
                self.assertNotIn("当前已更换", result.stdout)
                self.assertFalse((self.root / "navigation").exists())

    def test_inactive_service_restores_configs_without_reporting_success(self):
        for init in ("systemd", "openrc"):
            with self.subTest(init=init):
                self.generate()
                before = self.snapshot()
                result = self.change_priority("2\n", scenario="service_failure", init=init)
                self.assertNotEqual(result.returncode, 0)
                self.assert_preserved(before)
                self.assertEqual((self.root / "restart-count").read_text().strip(), "2")
                self.assertEqual((self.root / "restarted-config").read_bytes(), before["sb.json"])
                checks = (self.root / "service-checks").read_text().splitlines()
                self.assertEqual(len(checks), 2)
                self.assertTrue(all(line.startswith(init + " ") for line in checks))
                self.assertNotIn("当前已更换", result.stdout)
                self.assertFalse((self.root / "navigation").exists())

    def test_menu_invalid_input_reprompts_and_eof_returns_failure(self):
        self.generate()
        result = self.change_priority("invalid\n2\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("输入无效", result.stdout)
        self.assertEqual((self.root / "prompts").read_text().splitlines(), ["prompt", "prompt"])
        self.assertEqual((self.root / "probes").read_text().splitlines(), ["6"])
        self.assertEqual(self.get_priority(), "prefer_ipv6")
        before = self.snapshot()
        result = self.change_priority("invalid\n")
        self.assertNotEqual(result.returncode, 0)
        self.assert_preserved(before)
        self.assertEqual((self.root / "prompts").read_text().splitlines(), ["prompt", "prompt"])
        self.assertFalse((self.root / "probes").exists())
        self.assertFalse((self.root / "check-args").exists())
        self.assertNotIn("当前已更换", result.stdout)

    def test_native_sing_box_modern_priority_strategies(self):
        binary = os.environ.get("SING_BOX_BIN")
        if not binary:
            self.skipTest("Set SING_BOX_BIN for native modern-core validation")
        for variable, name in (("SB_TEST_CERT", "cert.pem"), ("SB_TEST_KEY", "private.key")):
            if not os.environ.get(variable):
                self.skipTest(f"Set {variable} to test-only TLS material for native validation")
            shutil.copyfile(os.environ[variable], self.box / name)
        self.generate()
        for strategy in STRATEGIES:
            with self.subTest(strategy=strategy):
                result = self.apply(strategy)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                checked = subprocess.run([binary, "check", "-c", str(self.box / "sb.json")],
                                         capture_output=True, text=True, timeout=30)
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
