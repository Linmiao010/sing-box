import os
import shlex
import shutil

from shell_testlib import REPO, ShellTestCase, section


class SubscriptionTests(ShellTestCase):
    def run_shell(self, script, check=True, env=None):
        # Git Bash can represent relative POSIX links even on Windows without
        # native symlink privileges. Read snapshot contents through the same Bash.
        variables = {"MSYS": "winsymlinks:sys"} if os.name == "nt" else {}
        variables.update(env or {})
        return super().run_shell(script, check=check, env=variables)

    def functions(self):
        funcs = section("sub_valid_domain", "vmesscfadd")
        # ShellTestCase runs Bash with cwd at its private temporary directory.
        for path, local in (("/etc/s-box", "s-box"), ("/etc/caddy", "caddy"),
                            ("/etc/local.d", "local.d"), ("/etc/init.d", "init.d"),
                            ("/etc/systemd/system", "systemd"), ("/root/websbox", "legacy-websbox"),
                            ("/var/lib/sbox-subscription", "publish")):
            funcs = funcs.replace(path, local)
        return funcs + r'''
flock(){ [[ $1 == -x && $2 == 9 ]] && : >&9; }
sub_service_user(){ [[ $1 == sboxsub || $1 == sboxcaddy ]]; }
install(){
    local directory=0 mode=''
    while [[ $1 == -* ]]; do
        case $1 in
            -d) directory=1; shift ;;
            -m) mode=$2; shift 2 ;;
            -g) [[ $2 == sboxsub || $2 == sboxcaddy ]] || return 1; shift 2 ;;
            -o) [[ $2 == root ]] || return 1; shift 2 ;;
            *) return 1 ;;
        esac
    done
    if (( directory )); then
        [[ $mode == 0750 && ( $1 == publish || $1 == publish-https ) && $2 == "$1/www" ]] && mkdir -p -- "$@"
    else
        [[ $mode == 640 && $1 == s-box/* && $2 == publish*/release.*/* ]] && cp -- "$1" "$2"
    fi
}
chgrp(){ [[ ( $1 == sboxsub || $1 == sboxcaddy ) && -d $2 ]]; }
sub_service(){ case $1 in status|enabled) [[ -f "service-$1" ]];; *) return 0;; esac; }
sub_httpd_running(){ [[ ${1:-} != legacy-websbox && -f httpd-active ]]; }
sub_httpd_start(){ touch httpd-active; }
sub_httpd_stop(){ rm -f httpd-active; }
sub_caddy_reload(){ :; }
systemctl(){ :; }
crontab(){ if [[ $1 == -l ]]; then cat cron; else cp "$1" cron; fi; }
command(){
    if [[ $1 == -v && $2 == rc-service ]]; then return 1; fi
    builtin command "$@"
}
'''

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

    def test_caddy_serves_only_subscription_files_from_snapshot(self):
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
        patterns = [node["path_regexp"]["pattern"] for node in nodes if "path_regexp" in node]
        self.assertEqual(patterns, ["^/abc_123-xyz/(sbox[.]json|clmi[.]yaml|jhsub[.]txt)$"])
        self.assertEqual([node["dial"] for node in nodes if "dial" in node], [])
        self.assertTrue(any(node.get("handler") == "file_server" for node in nodes))
        self.assertTrue(any(node.get("status_code") == 404 for node in nodes))

    def test_failed_activation_preserves_previous_links_and_configuration(self):
        box = self.root / "s-box"
        box.mkdir()
        for name, value in {"subdomain.log": "old.example.com", "subtoken.log": "old_token_12345678", "subport.log": "18080", "sub-caddy-mode": "managed", "sub-httpd.sh": "old backend\n", "sub-Caddyfile": "old caddy\n"}.items():
            (box / name).write_text(value, encoding="utf-8")
        old_dir = self.root / "legacy-websbox" / "old_token_12345678"
        old_dir.mkdir(parents=True)
        (old_dir / "sbox.json").write_text("old", encoding="utf-8")
        for name in ("sbox.json", "clmi.yaml", "jhsub.txt"):
            (box / name).write_text("current", encoding="utf-8")
        script = self.functions() + r'''
red(){ echo "$*" >&2; }
green(){ :; }
sub_caddy_prepare(){ echo managed; }
touch httpd-active service-status service-enabled
sub_caddy_apply(){ touch rejected-activation; return 1; }
sub_https_ready(){ return 0; }
sbshare(){ return 0; }
sub_apply new.example.com new_token_12345678 18081 && exit 90
[[ -f rejected-activation && -f httpd-active && ! -e publish/www/new_token_12345678 ]]
'''
        self.run_shell(script)
        self.assertEqual((box / "subdomain.log").read_text(), "old.example.com")
        self.assertEqual((box / "subtoken.log").read_text(), "old_token_12345678")
        self.assertEqual((box / "sub-httpd.sh").read_text(), "old backend\n")
        self.assertTrue((old_dir / "sbox.json").exists())
        self.assertFalse((self.root / "publish" / "www" / "new_token_12345678").exists())

    def test_rejected_input_does_not_change_existing_files(self):
        box = self.root / "s-box"
        box.mkdir()
        (box / "subtoken.log").write_text("original", encoding="utf-8")
        script = self.functions() + '\nred(){ :; }\nsub_apply example.com "../outside" 18080 && exit 80\nexit 0\n'
        self.run_shell(script)
        self.assertEqual((box / "subtoken.log").read_text(), "original")

    def test_backend_start_script_binds_both_modes_to_loopback_as_reader(self):
        (self.root / "s-box").mkdir()
        binary_dir = self.root / "bin"
        binary_dir.mkdir()
        for binary in ("busybox", "busybox-extras"):
            stub = binary_dir / binary
            stub.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n', encoding="utf-8", newline="\n")
            stub.chmod(0o755)
        for domain in ("sub.example.com", ""):
            with self.subTest(domain=domain):
                result = self.run_shell(self.functions() + f'''
export PATH="$(cd {shlex.quote(binary_dir.as_posix())} && pwd):$PATH"
sub_httpd_write {shlex.quote(domain)} 18080 || exit 20
bash {shlex.quote((self.root / 's-box' / 'sub-httpd.sh').as_posix())}
''')
                self.assertEqual(result.stdout.splitlines(), ["httpd", "-f", "-p", "127.0.0.1:18080",
                                                              "-u", "sboxsub:sboxsub", "-h", "publish/www"])

    def test_successful_rotation_commits_only_after_https_probe(self):
        box = self.root / "s-box"
        box.mkdir()
        (self.root / "caddy").mkdir()
        for name, value in {"subdomain.log": "old.example.com", "subtoken.log": "old_token_12345678", "subport.log": "18080", "sub-caddy-mode": "managed", "sbox.json": "{}", "clmi.yaml": "proxies: []", "jhsub.txt": "vless://test"}.items():
            (box / name).write_text(value, encoding="utf-8")
        old_dir = self.root / "legacy-websbox" / "old_token_12345678"
        old_dir.mkdir(parents=True)
        script = self.functions() + r'''
red(){ echo "$*" >&2; }
sub_caddy_prepare(){ echo managed; }
touch httpd-active service-status service-enabled
sub_caddy_apply(){ sub_caddy_site "$1" "$2" "$3" > site.caddy; }
sub_https_ready(){
    [[ $(cat s-box/subdomain.log) == old.example.com && $(cat s-box/subtoken.log) == old_token_12345678 ]] || return 1
    [[ $(cat "publish-https/www/$(sub_https_key old_token_12345678)/sbox.json") == old-private ]] || return 1
    local file
    for file in sbox.json clmi.yaml jhsub.txt; do
        cmp -s "s-box/$file" "publish-https/www/$(sub_https_key "$2")/$file" || return 1
    done
    touch probe-passed
}
sbshare(){ return 0; }
mkdir -p publish-https/www
mv s-box/sbox.json s-box/next
echo old-private > s-box/sbox.json
sub_root=publish-https sub_group=sboxcaddy sub_links "$(sub_https_key old_token_12345678)" || exit 74
mv s-box/next s-box/sbox.json
sub_apply new.example.com new_token_12345678 18081 || exit 75
[[ ! -L publish-https/www/$(sub_https_key old_token_12345678) ]] || exit 76
[[ -f probe-passed && $(cat publish-https/www/$(sub_https_key new_token_12345678)/sbox.json) == '{}' ]]
'''
        self.run_shell(script)
        self.assertEqual((box / "subdomain.log").read_text().strip(), "new.example.com")
        self.assertEqual((box / "subtoken.log").read_text().strip(), "new_token_12345678")
        self.assertFalse((self.root / "httpd-active").exists())
        self.assertFalse(old_dir.exists())
        self.assertTrue((self.root / "probe-passed").is_file())

    def test_inactive_managed_service_can_be_removed(self):
        box = self.root / "s-box"
        box.mkdir()
        (self.root / "caddy").mkdir()
        (box / "sub-caddy-mode").write_text("managed")
        (box / "subdomain.log").write_text("sub.example.com")
        (box / "subtoken.log").write_text("valid_token_123456")
        keep = "0 1 * * * keep-other-job\n@reboot /usr/local/bin/websbox-stats\n"
        (self.root / "cron").write_text(keep + "@reboot busybox httpd -p 18080 -h legacy-websbox\n"
                                       + "@reboot /bin/bash s-box/sub-httpd.sh\n")
        script = self.functions() + r'''
sub_service(){ [[ $1 != status && $1 != stop ]]; }
sub_httpd_stop(){ :; }
crontab(){ if [[ $1 == -l ]]; then cat cron; else cp "$1" cron; fi; }
systemctl(){ :; }
sub_remove
'''
        self.run_shell(script)
        self.assertFalse((box / "subdomain.log").exists())
        self.assertEqual((self.root / "cron").read_text(), keep)

    def test_boot_setup_replaces_old_project_jobs_exactly_once(self):
        cron = self.root / "cron"
        keep = "@reboot /usr/local/bin/websbox-stats\n"
        cron.write_text(keep + "@reboot busybox httpd -p 18080 -h legacy-websbox\n"
                        + "@reboot /bin/bash s-box/sub-httpd.sh\n")
        self.run_shell(self.functions() + "\nsub_httpd_boot && sub_httpd_boot\n")
        self.assertEqual(cron.read_text(), keep + "@reboot sleep 10 && /bin/bash s-box/sub-httpd.sh >/dev/null 2>&1 &\n")

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
sub_httpd_boot(){ touch boot-attempt; echo 'bad partial boot change' > cron; return 1; }
crontab(){ if [[ $1 == -l ]]; then cat cron; else cp "$1" cron; fi; }
sub_apply '' first_token_123456 18080 && exit 70
[[ -f boot-attempt ]]
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
