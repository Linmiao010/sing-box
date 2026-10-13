"""Offline checks for installing and upgrading the VPS shortcut."""
import shlex
import unittest

from shell_testlib import REPO, ShellTestCase, section


SOURCE = "https://raw.githubusercontent.com/Linmiao010/sing-box/main"


class InstallTests(ShellTestCase):
    def setUp(self):
        super().setUp()
        (self.root / "bin").mkdir()
        (self.root / "config").mkdir()
        self.shortcut = self.root / "bin" / "sb"
        self.shortcut.write_text("#!/bin/bash\necho old\n", encoding="utf-8")
        self.old_script = self.shortcut.read_bytes()
        self.version = self.root / "config" / "v"
        self.version.write_text("v26.4.11\n", encoding="utf-8")
        self.payload = "#!/bin/bash\nprintf 'upgraded\\n'\n"
        (self.root / "payload").write_text(self.payload, encoding="utf-8", newline="\n")

    def run_install(self, entry="lnsb", mode="ok"):
        source = (REPO / "sb.sh").read_text(encoding="utf-8")
        declarations = source.split("export LANG=", 1)[0]
        functions = section("lnsb", "lapre")
        functions = functions.replace("/usr/bin/", f"{self.root.as_posix()}/bin/")
        functions = functions.replace("/etc/s-box", f"{self.root.as_posix()}/config")
        script = declarations + f"\nmode={shlex.quote(mode)}\n" + r'''
red() { printf '%s\n' "$*"; }
green() { printf '%s\n' "$*"; }
sleep() { :; }
sb() { printf 'restarted\n' > restarted; }
curl() {
    local target= url= argument
    printf '%s\n' "$*" >> curl.log
    while (( $# )); do
        argument=$1
        shift
        case "$argument" in
            -o|--output) target=$1; shift ;;
            --retry) shift ;;
            https://*) url=$argument ;;
        esac
    done
    if [[ "$mode" == download_failure && "$url" == */sb.sh ]]; then
        [[ -n "$target" ]] && printf 'partial download\n' > "$target"
        return 22
    fi
    case "$url" in
        https://raw.githubusercontent.com/Linmiao010/sing-box/main/sb.sh|https://raw.githubusercontent.com/yonggekkk/sing-box-yg/main/sb.sh)
            [[ -n "$target" ]] || return 2
            cat payload > "$target"
            ;;
        https://raw.githubusercontent.com/Linmiao010/sing-box/main/version|https://raw.githubusercontent.com/yonggekkk/sing-box-yg/main/version)
            [[ "$mode" != version_failure ]] || return 22
            printf 'v26.10.13更新内容：\n测试更新\n'
            ;;
        *) return 22 ;;
    esac
}
''' + functions + f"\n{entry}\n"
        return self.run_shell(script, check=False)

    def assert_shortcut_preserved(self):
        self.assertTrue(self.shortcut.exists(), "The installed shortcut was removed")
        self.assertEqual(self.shortcut.read_bytes(), self.old_script)
        self.assertEqual(sorted(p.name for p in (self.root / "bin").iterdir()), ["sb"])

    def test_install_uses_fork_and_keeps_shortcut_executable(self):
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.shortcut.read_text(encoding="utf-8"), self.payload)
        self.assertEqual(self.run_shell("test -x bin/sb && ./bin/sb").stdout, "upgraded\n")
        requests = (self.root / "curl.log").read_text(encoding="utf-8")
        self.assertIn(SOURCE + "/sb.sh", requests)
        self.assertNotIn("--insecure", requests)

    def test_failed_download_keeps_previous_shortcut(self):
        result = self.run_install(mode="download_failure")
        self.assertNotEqual(result.returncode, 0)
        self.assert_shortcut_preserved()

    def test_invalid_bash_keeps_previous_shortcut(self):
        (self.root / "payload").write_text("#!/bin/bash\nif then\n", encoding="utf-8")
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assert_shortcut_preserved()

    def test_empty_download_keeps_previous_shortcut(self):
        (self.root / "payload").write_text("", encoding="utf-8")
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assert_shortcut_preserved()

    def test_upgrade_failure_does_not_claim_success_or_restart(self):
        result = self.run_install("upsbyg", mode="download_failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("升级成功", result.stdout)
        self.assertFalse((self.root / "restarted").exists())
        self.assertEqual(self.version.read_text(encoding="utf-8"), "v26.4.11\n")
        self.assert_shortcut_preserved()

    def test_upgrade_uses_same_source_for_script_and_version(self):
        result = self.run_install("upsbyg")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.shortcut.read_text(encoding="utf-8"), self.payload)
        self.assertEqual(self.version.read_text(encoding="utf-8"), "v26.10.13\n")
        self.assertTrue((self.root / "restarted").exists())
        self.assertIn("升级成功", result.stdout)
        requests = (self.root / "curl.log").read_text(encoding="utf-8")
        self.assertIn(SOURCE + "/sb.sh", requests)
        self.assertIn(SOURCE + "/version", requests)

    def test_version_download_failure_preserves_installed_state(self):
        result = self.run_install("upsbyg", mode="version_failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("升级成功", result.stdout)
        self.assertFalse((self.root / "restarted").exists())
        self.assertEqual(self.version.read_text(encoding="utf-8"), "v26.4.11\n")
        self.assert_shortcut_preserved()


if __name__ == "__main__":
    unittest.main()
