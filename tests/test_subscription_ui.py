"""Exercise subscription UI and Telegram payloads without network access."""
import shlex
import unittest

from shell_testlib import ShellTestCase, section


class SubscriptionUITests(ShellTestCase):
    def setUp(self):
        super().setUp()
        self.config = self.root / "config"
        self.config.mkdir()
        self.write("subdomain.log", "sub.example.com\n")
        self.write("subtoken.log", "private_token\n")
        self.write("subport.log", "8899\n")
        self.write("server_ip.log", "192.0.2.10\n")
        self.write("clmi.yaml", "proxies:\n  - name: original-node\n")
        self.write("sbox.json", '{\n"outbounds": []\n}\n')
        self.write("sb.json", "{}\n")
        self.write("vl_reality.txt", "vless://original-node\n")
        self.write("hy2.txt", "hysteria2://original-node\n")
        self.write("tuic5.txt", "tuic://original-node\n")
        self.write("jhsub.txt", "vless://original-aggregate\n")
        self.preamble = r'''
red() { printf '%s\n' "$*"; }
green() { printf '%s\n' "$*"; }
yellow() { printf '%s\n' "$*"; }
white() { printf '%s\n' "$*"; }
blue() { printf '%s\n' "$*"; }
sleep() { :; }
sbactive() { :; }
sbshare() { return 0; }
allports() { :; }
sbymfl() { :; }
jq() { cat >/dev/null; printf 'true\n'; }
ps() { printf 'root 1 0 busybox httpd -p 8899\n'; }
sub_show_urls() { printf 'DOMAIN-LINKS mode=%s\n' "$1"; }
gitlabsubgo() { printf 'GITLAB-PUSH\n'; }
curl() { return 99; }
timeout() { printf '%s\n' "$*" >> requests; printf '{"ok":true}\n'; }
export -f timeout curl jq
'''

    def write(self, name, content):
        (self.config / name).write_text(content, encoding="utf-8", newline="\n")

    def functions(self, start, end):
        return section(start, end).replace("/etc/s-box", self.config.as_posix())

    def notice(self, setup="", command="tgnotice"):
        script = self.functions("tgsbshow", "changeserv") + self.preamble + r'''
curl(){
    printf '%s\n' "$@" >> ../curl.argv
    cat >> ../payload
    cat <&3 >> ../request.cfg
    printf '{"ok":true}\n'
}
export -f curl
''' + setup + "\n" + command
        return self.run_shell(script, check=False)

    def test_untrusted_legacy_script_requires_reentry(self):
        self.write("sbtg.sh", "touch executed\n")
        result = self.notice()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "executed").exists())
        self.assertFalse((self.root / "curl.argv").exists())

    def test_new_telegram_plain_text_and_private_inputs(self):
        for chat in ("12345", "-12345", "@valid_Name"):
            with self.subTest(chat=chat):
                self.write("vl_reality.txt", "vless://demo<example>\n")
                result = self.notice("readp(){ menu=1; }",
                                     "tgsbshow <<'INPUT'\n12345:fake_demo\n" + chat + "\nINPUT\n")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                cli = (self.root / "curl.argv").read_text()
                for secret in ("fake_demo", chat, "private_token", "vless://", "<example>"):
                    self.assertNotIn(secret, cli + result.stdout + result.stderr)
                self.assertNotIn("parse_mode", cli)
                cfg = (self.root / "request.cfg").read_text()
                self.assertIn("bot12345:fake_demo/sendMessage", cfg)
                self.assertIn("chat_id=" + chat, cfg)
                body = (self.root / "payload").read_text()
                self.assertIn("vless://demo<example>", body)
                self.assertIn("https://sub.example.com/private_token/sbox.json", body)
                self.assertNotIn(chat, (self.config / "sbtg.sh").read_text())
                self.assertEqual((self.config / "telegram.credentials").read_text(), "12345:fake_demo\n" + chat + "\n")

    def test_bad_credentials_do_not_replace_previous(self):
        self.write("telegram.credentials", "12345:existing\n-12345\n")
        for bot, chat in (("invalid", "12345"), ("12345:abc", "a b"), ("12345:abc", "@ab")):
            result = self.notice("readp(){ menu=1; }", f"tgsbshow <<'INPUT'\n{bot}\n{chat}\nINPUT\n")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((self.config / "telegram.credentials").read_text(), "12345:existing\n-12345\n")
        self.assertFalse((self.root / "curl.argv").exists())

    def test_migrated_script_reads_updated_token_and_ignores_legacy_gitlab(self):
        self.write("telegram.credentials", "12345:existing\n-12345\n")
        self.write("subtoken.log", "new_private\n")
        self.write("sing_box_gitlab.txt", "old-private-url")
        result = self.notice()
        self.assertEqual(result.returncode, 0, result.stderr)
        body = (self.root / "payload").read_text()
        self.assertIn("new_private/clmi.yaml", body)
        self.assertNotIn("old-private-url", body)

    def test_disable_removes_credentials_and_sender(self):
        self.write("telegram.credentials", "12345:existing\n-12345\n")
        self.write("sbtg.sh", "exit 99\n")
        result = self.notice("readp(){ menu=2; }", "tgsbshow && tgnotice")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.config / "telegram.credentials").exists())
        self.assertFalse((self.config / "sbtg.sh").exists())
        self.assertFalse((self.root / "curl.argv").exists())

    def test_failed_refresh_prevents_telegram_payloads(self):
        self.write("telegram.credentials", "12345:existing\n-12345\n")
        result = self.notice("sbshare(){ return 1; }")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "curl.argv").exists())

    def test_domain_refresh_without_publisher_displays_domain_links(self):
        script = self.preamble + self.functions("clash_sb_share", "acme")
        for menu in (1, 2):
            with self.subTest(menu=menu):
                result = self.run_shell(script + f"\nreadp() {{ menu={menu}; }}\nclash_sb_share\n")
                self.assertIn("DOMAIN-LINKS mode=", result.stdout)
                self.assertNotIn("GITLAB-PUSH", result.stdout)

    def test_domain_refresh_with_publisher_pushes_and_displays_only_caddy_links(self):
        self.write("gitlab-publisher", "private/project\nmain\nwrite-test-only\n")
        script = self.preamble + self.functions("clash_sb_share", "acme")
        result = self.run_shell(script + "\nreadp() { menu=2; }\nclash_sb_share\n")
        self.assertIn("GITLAB-PUSH", result.stdout)
        self.assertIn("DOMAIN-LINKS", result.stdout)
        self.assertNotIn("write-test-only", result.stdout + result.stderr)

    def test_failed_refresh_does_not_show_or_push_stale_links(self):
        script = self.preamble + self.functions("clash_sb_share", "acme")
        result = self.run_shell(script + "\nreadp() { menu=2; }\nsbshare() { return 1; }\nclash_sb_share\n", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("DOMAIN-LINKS", result.stdout)
        self.assertNotIn("GITLAB-PUSH", result.stdout)

    def test_protocol_summary_prefers_domain_links_without_qr(self):
        script = self.preamble + self.functions("showprotocol", "inssbwpph")
        result = self.run_shell(script + "\nshowprotocol\n")
        self.assertIn("DOMAIN-LINKS mode=noqr", result.stdout)
        self.assertNotIn("http://192.0.2.10:8899", result.stdout)

    def test_protocol_summary_with_old_http_does_not_distribute_public_urls(self):
        (self.config / "subdomain.log").unlink()
        script = self.preamble + self.functions("showprotocol", "inssbwpph")
        result = self.run_shell(script + "\nshowprotocol\n")
        self.assertNotIn("http://192.0.2.10", result.stdout)
        self.assertIn("DOMAIN-LINKS mode=noqr", result.stdout)


if __name__ == "__main__":
    unittest.main()
