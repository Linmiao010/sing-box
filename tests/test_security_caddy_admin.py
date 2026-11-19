"""Adapt real shared Caddy configurations and verify the management boundary."""
from pathlib import Path
import shlex
import shutil

from shell_testlib import REPO, ShellTestCase, section


class SharedCaddyAdminTests(ShellTestCase):
    def setUp(self):
        super().setUp()
        self.caddy_dir = self.root / "caddy"
        self.caddy_dir.mkdir()
        (self.root / "s-box").mkdir()
        (self.root / "private").mkdir()
        self.code = section("sub_caddy_admin_uid", "sub_caddy_group")
        self.code = self.code.replace("/etc/caddy", self.caddy_dir.as_posix()).replace("/etc/s-box", "s-box")
        for name in ("caddy", "jq"):
            binary = shutil.which(name) or str(REPO / ".test-tools" / (name + ".exe"))
            if not Path(binary).is_file():
                self.skipTest(f"{name} is required for adapter verification")
            self.code += "\n" + name + "(){ " + shlex.quote(Path(binary).as_posix()) + ' "$@"; }\n'
        self.code += r'''
red(){ printf '%s\n' "$1" >&2; }
sub_service(){ printf '%s\n' "$*" >> manager; }
sub_caddy_admin_uid(){ echo 1234; }
stat(){
    case "${!#}" in
        */private) printf '%s %s\n' "${fixture_owner:-1234}" "${fixture_mode:-700}";;
        *) printf '%s\n' "0 ${ancestor_mode:-755}";;
    esac
}
'''

    def configuration(self, directive, imported=False):
        text = "{\n" + directive + "\n}\n" if directive else ""
        text += "http://localhost:8475 {\n respond ok\n}\n"
        if imported:
            snippet = self.caddy_dir / "s-box-subscription.caddy"
            snippet.write_text("http://127.0.0.1:8476 {\n respond /sample-private-path ok\n}\n", encoding="utf-8")
            text += f"import {snippet.as_posix()} # s-box subscription\n"
        (self.caddy_dir / "Caddyfile").write_text(text, encoding="utf-8")

    def test_default_tcp_denied_before_any_snippet_or_restart(self):
        self.configuration("")
        result = self.run_shell(self.code + "\nsub_caddy_prepare", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.caddy_dir / "s-box-subscription.caddy").exists())
        self.assertFalse((self.root / "manager").exists())
        self.configuration("admin 127.0.0.1:5555", imported=True)
        result = self.run_shell(self.code + "\nsub_caddy_reload shared", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("sample-private-path", result.stdout + result.stderr)
        self.assertFalse((self.root / "manager").exists())

    def test_off_and_explicit_single_user_apply_via_service_manager(self):
        for directive, setting in (("admin off", ""), ("", "SBOX_CADDY_SINGLE_USER=1")):
            with self.subTest(directive=directive):
                self.configuration(directive, imported=True)
                result = self.run_shell(self.code + "\n" + setting + "\nsub_caddy_prepare && sub_caddy_reload shared")
                self.assertEqual(result.stdout.strip(), "shared")
                self.assertNotIn("sample-private-path", result.stderr)
        self.assertEqual((self.root / "manager").read_text().splitlines(), ["restart caddy"] * 2)
        self.configuration("admin :5555")
        self.assertNotEqual(self.run_shell(self.code + "\nSBOX_CADDY_SINGLE_USER=1 sub_caddy_prepare", check=False).returncode, 0)

    def test_unix_endpoint_requires_private_directory_and_safe_ancestors(self):
        path = self.run_shell("pwd -P").stdout.strip() + "/private/admin.sock"
        self.configuration(f"admin unix/{path}|0600")
        self.run_shell(self.code + "\nsub_caddy_prepare")
        for setting in ("fixture_owner=9999", "fixture_mode=750", "ancestor_mode=777"):
            with self.subTest(setting=setting):
                self.assertNotEqual(self.run_shell(self.code + "\n" + setting + " sub_caddy_prepare", check=False).returncode, 0)
