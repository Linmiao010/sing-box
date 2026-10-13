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

    def legacy_telegram(self):
        source = r'''#!/bin/bash
# Keep the credentials and unrelated notifications unchanged.
URL="https://api.telegram.org/botpreserved-bot-token/sendMessage"
chat_id="preserved-chat-id"
m1=$(cat /etc/s-box/vl_reality.txt 2>/dev/null)
m9=$(cat /etc/s-box/sing_box_gitlab.txt 2>/dev/null)
m10=$(cat /etc/s-box/clash_meta_gitlab.txt 2>/dev/null)
timeout 20s curl -s -X POST "$URL" -d "chat_id=$chat_id" --data-urlencode "text=original:$m1"
if [[ -f /etc/s-box/sing_box_gitlab.txt ]]; then
timeout 20s curl -s -X POST "$URL" --data-urlencode "text=sing-box:$m9"
fi
if [[ -f /etc/s-box/clash_meta_gitlab.txt ]]; then
timeout 20s curl -s -X POST "$URL" --data-urlencode "text=mihomo:$m10"
fi
'''
        self.write("sbtg.sh", source.replace("/etc/s-box", self.config.as_posix()))

    def run_notice(self, extra=""):
        script = self.preamble + self.functions("tgsbshow", "changeserv") + extra + "\ntgnotice\n"
        return self.run_shell(script, check=False)

    def requests(self):
        return (self.root / "requests").read_text(encoding="utf-8")

    def test_existing_telegram_script_migrates_and_prefers_domain_without_gitlab(self):
        self.legacy_telegram()
        result = self.run_notice()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        requests = self.requests()
        self.assertIn("https://sub.example.com/private_token/sbox.json", requests)
        self.assertIn("https://sub.example.com/private_token/clmi.yaml", requests)
        self.assertIn("original:vless://original-node", requests)
        self.assertIn("botpreserved-bot-token/sendMessage", requests)
        self.assertIn("chat_id=preserved-chat-id", requests)
        migrated = (self.config / "sbtg.sh").read_text(encoding="utf-8")
        self.assertIn("# Keep the credentials and unrelated notifications unchanged.", migrated)

    def test_migrated_telegram_reads_current_domain_and_token_on_each_notice(self):
        self.legacy_telegram()
        self.write("sing_box_gitlab.txt", "https://gitlab.invalid/old-singbox\n")
        self.write("clash_meta_gitlab.txt", "https://gitlab.invalid/old-mihomo\n")
        self.run_notice()
        (self.root / "requests").unlink()
        self.write("subdomain.log", "next.example.com\n")
        self.write("subtoken.log", "next_token\n")
        result = self.run_notice()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("https://next.example.com/next_token/sbox.json", self.requests())
        self.assertIn("https://next.example.com/next_token/clmi.yaml", self.requests())
        self.assertNotIn("gitlab.invalid", self.requests())

    def test_new_telegram_template_uses_domain_and_keeps_node_notifications(self):
        source = self.functions("tgsbshow", "changeserv")
        inputs = r'''
tgnotice() { :; }
readp() {
    case "$2" in
        menu) menu=1 ;;
        token) token=test-bot-token ;;
        userid) userid=test-chat-id ;;
    esac
}
tgsbshow
'''
        self.run_shell(self.preamble + source + inputs)
        result = self.run_shell(self.preamble + "bash " + shlex.quote((self.config / "sbtg.sh").as_posix()))
        requests = self.requests()
        self.assertIn("https://sub.example.com/private_token/sbox.json", requests)
        self.assertIn("https://sub.example.com/private_token/clmi.yaml", requests)
        self.assertIn("vless://original-node", requests)
        self.assertIn("hysteria2://original-node", requests)
        self.assertIn("tuic://original-node", requests)
        self.assertIn("vless://original-aggregate", requests)
        self.assertIn("bottest-bot-token/sendMessage", requests)
        self.assertIn("chat_id=test-chat-id", requests)

    def test_invalid_existing_telegram_script_is_preserved_and_not_executed(self):
        self.legacy_telegram()
        with (self.config / "sbtg.sh").open("a", encoding="utf-8", newline="\n") as script:
            script.write("if then\n")
        original = (self.config / "sbtg.sh").read_bytes()
        result = self.run_notice()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.config / "sbtg.sh").read_bytes(), original)
        self.assertFalse((self.root / "requests").exists())

    def test_failed_refresh_prevents_telegram_payloads(self):
        self.legacy_telegram()
        result = self.run_notice("\nsbshare() { return 1; }\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "requests").exists())

    def test_domain_refresh_displays_domain_links_and_skips_gitlab_push(self):
        script = self.preamble + self.functions("clash_sb_share", "acme")
        for menu in (1, 2):
            with self.subTest(menu=menu):
                result = self.run_shell(script + f"\nreadp() {{ menu={menu}; }}\nclash_sb_share\n")
                self.assertIn("DOMAIN-LINKS mode=", result.stdout)
                self.assertNotIn("GITLAB-PUSH", result.stdout)

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

    def test_protocol_summary_keeps_local_ip_links_without_domain(self):
        (self.config / "subdomain.log").unlink()
        script = self.preamble + self.functions("showprotocol", "inssbwpph")
        result = self.run_shell(script + "\nshowprotocol\n")
        self.assertIn("http://192.0.2.10:8899/private_token/sbox.json", result.stdout)
        self.assertNotIn("DOMAIN-LINKS", result.stdout)


if __name__ == "__main__":
    unittest.main()
