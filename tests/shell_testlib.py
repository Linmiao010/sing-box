"""Run selected Bash functions without executing the VPS installer."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash") or r"C:\Program Files\Git\bin\bash.exe"


def section(start_function, end_function):
    source = (REPO / "sb.sh").read_text(encoding="utf-8")
    start = re.search(r"^" + re.escape(start_function) + r"\(\)\s*\{", source, re.M)
    if not start:
        raise AssertionError(f"Missing function: {start_function}")
    end = re.search(r"^" + re.escape(end_function) + r"\(\)\s*\{", source[start.end():], re.M)
    if not end:
        raise AssertionError(f"Missing end function: {end_function}")
    return source[start.start():start.end() + end.start()]


class ShellTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sbox-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def run_shell(self, script, check=True, env=None):
        variables = os.environ.copy()
        if env:
            variables.update(env)
        script_file = self.root / "run-test.sh"
        script_file.write_text(script, encoding="utf-8", newline="\n")
        result = subprocess.run(
            [BASH, "--noprofile", "--norc", script_file.as_posix()],
            cwd=self.root, env=variables, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result
