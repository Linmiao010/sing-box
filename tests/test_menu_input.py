"""Exercise menu dispatch without running installation or service commands."""
import shlex

from shell_testlib import REPO, ShellTestCase


class MenuInputTests(ShellTestCase):
    def run_menu(self, inputs):
        source = (REPO / "sb.sh").read_text(encoding="utf-8")
        menu = source.rsplit("\necho\n", 1)[1]
        script = r'''
readp() { read -r "$2"; }
red() { printf '%s\n' "$*"; }
instsllsingbox() { printf 'INSTALL\n'; }
acme() { printf 'CERTIFICATE\n'; }
main_menu() {
''' + menu + "\n}\n"
        script += "printf '%s' " + shlex.quote(inputs) + " | main_menu\n"
        return self.run_shell(script, check=False)

    def test_invalid_input_reprompts_then_installs(self):
        result = self.run_menu("12\x7f\x7f\n1\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("输入无效", result.stdout)
        self.assertTrue(result.stdout.endswith("INSTALL\n"), result.stdout)
        self.assertNotIn("CERTIFICATE", result.stdout)

    def test_eof_after_invalid_input_does_not_loop(self):
        result = self.run_menu("invalid\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout.count("输入无效"), 1)
        self.assertNotIn("INSTALL", result.stdout)

    def test_zero_exits_without_running_an_action(self):
        result = self.run_menu("0\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
