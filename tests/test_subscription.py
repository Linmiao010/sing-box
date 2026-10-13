import os
import shlex
import shutil

from shell_testlib import REPO, ShellTestCase, section


class SubscriptionTests(ShellTestCase):
    def functions(self):
        return (section("sub_valid_domain", "vmesscfadd")
                .replace("/etc/s-box", (self.root / "s-box").as_posix())
                .replace("/etc/caddy", (self.root / "caddy").as_posix())
                .replace("/etc/local.d", (self.root / "local.d").as_posix())
                .replace("/etc/init.d", (self.root / "init.d").as_posix())
                .replace("/etc/systemd/system", (self.root / "systemd").as_posix())
                .replace("/root/websbox", (self.root / "web").as_posix()))

    def test_inputs_cannot_escape_paths_or_inject_caddy(self):
        script = self.functions() + "\n"
        for value in ("sub.example.com", "xn--fiqs8s.example.com", "a-b.example.org"):
            script += f"sub_valid_domain {shlex.quote(value)} || exit 10\n"
        for value in ("https://example.com", "../etc", "a..com", "-a.com", "a.com:443", "a.com {", "127.0.0.1", "a\nb.com"):
            script += f"! sub_valid_domain {shlex.quote(value)} || exit 11\n"
        for value in ("abc_123-xyz", "a" * 64):
            script += f"sub_valid_token {shlex.quote(value)} || exit 12\n"
        for value in ("", "..", "../a", "a/b", "a b", "a\n", 'a"', "a" * 129):
            script += f"! sub_valid_token {shlex.quote(value)} || exit 13\n"
        for value in ("1024", "18080", "65535"):
            script += f"sub_valid_port {value} || exit 14\n"
        for value in ("0", "-1", "65536", "80x", "1;echo", "080"):
            script += f"! sub_valid_port {shlex.quote(value)} || exit 15\n"
        self.run_shell(script)

    def test_caddy_routes_only_subscription_files_to_loopback(self):
        caddy = REPO / ".test-tools" / ("caddy.exe" if os.name == "nt" else "caddy")
        caddy_cmd = str(caddy) if caddy.exists() else shutil.which("caddy")
        if not caddy_cmd:
            self.skipTest("Caddy adapter validation requires caddy on PATH or in .test-tools")
        output = self.run_shell(self.functions() + "\nsub_caddy_site sub.example.com abc_123-xyz 18080\n").stdout
        (self.root / "site.caddy").write_text(output, encoding="utf-8")
        result = self.run_shell(
            shlex.quote(caddy_cmd.replace("\\", "/"))
            + " adapt --config site.caddy --adapter caddyfile",
            env={"MSYS_NO_PATHCONV": "1"},
        )
        import json
        config = json.loads(result.stdout)
        server = next(iter(config["apps"]["http"]["servers"].values()))
        route = server["routes"][0]
        self.assertEqual(route["match"][0]["host"], ["sub.example.com"])
        # Walk the adapted config: assert the actual match/forward contract.
        def walk(item):
            if isinstance(item, dict):
                yield item
                for value in item.values():
                    yield from walk(value)
            elif isinstance(item, list):
                for value in item:
                    yield from walk(value)
        nodes = list(walk(config))
        paths = [node["path"] for node in nodes if "path" in node]
        self.assertEqual(paths, [["/abc_123-xyz/sbox.json", "/abc_123-xyz/clmi.yaml", "/abc_123-xyz/jhsub.txt"]])
        self.assertEqual([node["dial"] for node in nodes if "dial" in node], ["127.0.0.1:18080"])
        self.assertTrue(any(node.get("status_code") == 404 for node in nodes))

    def test_failed_activation_preserves_previous_links_and_configuration(self):
        box = self.root / "s-box"
        box.mkdir()
        for name, value in {"subdomain.log": "old.example.com", "subtoken.log": "old_token_12345678", "subport.log": "18080", "sub-caddy-mode": "managed", "sub-httpd.sh": "old backend\n", "sub-Caddyfile": "old caddy\n"}.items():
            (box / name).write_text(value, encoding="utf-8")
        old_dir = self.root / "web" / "old_token_12345678"
        old_dir.mkdir(parents=True)
        (old_dir / "sbox.json").write_text("old", encoding="utf-8")
        for name in ("sbox.json", "clmi.yaml", "jhsub.txt"):
            (box / name).write_text("current", encoding="utf-8")
        script = self.functions() + r'''
red(){ echo "$*" >&2; }
green(){ :; }
sub_caddy_prepare(){ echo managed; }
sub_httpd_running(){ return 0; }
sub_service(){ return 0; }
sub_httpd_stop(){ :; }
sub_httpd_start(){ return 0; }
sub_httpd_boot(){ return 0; }
sub_caddy_apply(){ return 1; }
sub_https_ready(){ return 0; }
sbshare(){ return 0; }
sub_apply new.example.com new_token_12345678 18081 && exit 90
exit 0
'''
        self.run_shell(script)
        self.assertEqual((box / "subdomain.log").read_text(), "old.example.com")
        self.assertEqual((box / "subtoken.log").read_text(), "old_token_12345678")
        self.assertEqual((box / "sub-httpd.sh").read_text(), "old backend\n")
        self.assertTrue((old_dir / "sbox.json").exists())
        self.assertFalse((self.root / "web" / "new_token_12345678").exists())

    def test_rejected_input_does_not_change_existing_files(self):
        box = self.root / "s-box"
        box.mkdir()
        (box / "subtoken.log").write_text("original", encoding="utf-8")
        script = self.functions() + '\nred(){ :; }\nsub_apply example.com "../outside" 18080 && exit 80\nexit 0\n'
        self.run_shell(script)
        self.assertEqual((box / "subtoken.log").read_text(), "original")

    def test_backend_start_script_preserves_ip_mode_and_binds_domain_to_loopback(self):
        (self.root / "s-box").mkdir()
        binary_dir = self.root / "bin"
        binary_dir.mkdir()
        for binary in ("busybox", "busybox-extras"):
            stub = binary_dir / binary
            stub.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n', encoding="utf-8", newline="\n")
            stub.chmod(0o755)
        for domain, expected in (("sub.example.com", "127.0.0.1:18080"), ("", "18080")):
            with self.subTest(domain=domain):
                result = self.run_shell(self.functions() + f'''
export PATH="$(cd {shlex.quote(binary_dir.as_posix())} && pwd):$PATH"
sub_httpd_write {shlex.quote(domain)} 18080 || exit 20
bash {shlex.quote((self.root / 's-box' / 'sub-httpd.sh').as_posix())}
''')
                self.assertEqual(result.stdout.splitlines(), ["httpd", "-f", "-p", expected, "-h", (self.root / "web").as_posix()])

    def test_successful_rotation_commits_only_after_https_probe(self):
        box = self.root / "s-box"
        box.mkdir()
        (self.root / "caddy").mkdir()
        for name, value in {"subdomain.log": "old.example.com", "subtoken.log": "old_token_12345678", "subport.log": "18080", "sub-caddy-mode": "managed", "sbox.json": "{}", "clmi.yaml": "proxies: []", "jhsub.txt": "vless://test"}.items():
            (box / name).write_text(value, encoding="utf-8")
        old_dir = self.root / "web" / "old_token_12345678"
        old_dir.mkdir(parents=True)
        script = self.functions() + r'''
red(){ echo "$*" >&2; }
sub_caddy_prepare(){ echo managed; }
sub_httpd_running(){ return 0; }
sub_service(){ return 0; }
sub_httpd_stop(){ :; }
sub_httpd_start(){ return 0; }
sub_httpd_boot(){ return 0; }
sub_caddy_apply(){ sub_caddy_site "$1" "$2" "$3" > site.caddy; }
sub_https_ready(){
    [[ $(cat s-box/subdomain.log) == old.example.com && $(cat s-box/subtoken.log) == old_token_12345678 ]]
}
sbshare(){ return 0; }
sub_apply new.example.com new_token_12345678 18081
'''
        self.run_shell(script)
        self.assertEqual((box / "subdomain.log").read_text().strip(), "new.example.com")
        self.assertEqual((box / "subtoken.log").read_text().strip(), "new_token_12345678")
        self.assertEqual((box / "subport.log").read_text().strip(), "18081")
        self.assertFalse(old_dir.exists())
        self.assertEqual((self.root / "web" / "new_token_12345678" / "sbox.json").read_text(), "{}")

    def test_inactive_managed_service_can_be_removed(self):
        box = self.root / "s-box"
        box.mkdir()
        (self.root / "caddy").mkdir()
        (box / "sub-caddy-mode").write_text("managed")
        (box / "subdomain.log").write_text("sub.example.com")
        (box / "subtoken.log").write_text("valid_token_123456")
        (self.root / "cron").write_text("0 1 * * * keep-other-job\n")
        script = self.functions() + r'''
sub_service(){ [[ $1 != status && $1 != stop ]]; }
sub_httpd_stop(){ :; }
crontab(){ if [[ $1 == -l ]]; then cat cron; else cp "$1" cron; fi; }
systemctl(){ :; }
sub_remove
'''
        self.run_shell(script)
        self.assertFalse((box / "subdomain.log").exists())
        self.assertEqual((self.root / "cron").read_text(), "0 1 * * * keep-other-job\n")

    def test_boot_setup_failure_restores_previous_cron(self):
        box = self.root / "s-box"
        box.mkdir()
        for name in ("sbox.json", "clmi.yaml", "jhsub.txt"):
            (box / name).write_text("current")
        original = "0 1 * * * keep-other-job\n"
        (self.root / "cron").write_text(original)
        script = self.functions() + r'''
red(){ :; }
sbshare(){ return 0; }
sub_httpd_running(){ return 1; }
sub_httpd_start(){ return 0; }
sub_httpd_stop(){ :; }
sub_httpd_boot(){ echo 'bad partial boot change' > cron; return 1; }
crontab(){ if [[ $1 == -l ]]; then cat cron; else cp "$1" cron; fi; }
sub_apply '' first_token_123456 18080 && exit 70
exit 0
'''
        self.run_shell(script)
        self.assertEqual((self.root / "cron").read_text(), original)
        self.assertFalse((box / "subtoken.log").exists())

    def test_empty_domain_menu_input_never_enables_plain_http(self):
        (self.root / "s-box").mkdir()
        script = self.functions() + r'''
red(){ :; }
blue(){ :; }
yellow(){ :; }
green(){ :; }
readp(){ read -r "$2"; }
sub_apply(){ echo "$*" > applied; }
sub_show_urls(){ :; }
if printf '5\n\nabcdefghijklmnop\n18080\n' | ipsub; then exit 60; fi
'''
        self.run_shell(script)
        self.assertFalse((self.root / "applied").exists())


if __name__ == "__main__":
    import unittest
    unittest.main()
