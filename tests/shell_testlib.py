"""Run selected Bash functions without executing the VPS installer."""
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
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
    body = source[start.start():start.end() + end.start()]
    # Functions extracted from the installer share its JSON helpers, without startup.
    helpers_start = re.search(r"^config_jq\(\)\s*\{", source, re.M)
    helpers_end = re.search(r"^inssbjsonser\(\)\s*\{", source, re.M)
    if helpers_start and helpers_end and not (helpers_start.start() <= start.start() < helpers_end.start()):
        body = source[helpers_start.start():helpers_end.start()] + body
    for first, after in (("valid_node_address", "config_jq"), ("sub_valid_domain", "sub_service"),
                         ("ip_literal_valid", "public_ip")):
        if first + "(){" not in body:
            body = source[source.index(first + "(){"):source.index(after + "(){", source.index(first + "(){"))] + body
    if ("singbox_unit_state" in body or "singbox_require_owned" in body) and "singbox_unit_state(){" not in body:
        body = source[source.index("singbox_unit_state(){"):source.index("\nsbservice(){")] + "\n" + body
    return body


class ShellTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sbox-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def run_shell(self, script, check=True, env=None):
        variables = os.environ.copy()
        script = script.replace("/run/s-box-config.lock", (self.root / "config.lock").as_posix())
        if "secure_server_routes(){" in script:
            # Isolate the generator from the test host's interface list (including hosts without iproute2).
            script = 'ip(){ [[ $* != "-o addr show scope global" ]] || return 0; command ip "$@"; }\n' + script
        if os.name == "nt":
            # Native jq.exe must receive Linux config values such as /test literally.
            variables["MSYS2_ARG_CONV_EXCL"] = "*"
            # Match Linux jq line endings, including the final field of @tsv rows.
            script = 'jq(){ command jq -b "$@"; }\n' + script
            # Assign after Bash startup, which otherwise converts TMPDIR back to /tmp.
            script = "export TMPDIR=" + shlex.quote(self.root.as_posix()) + "\n" + script
            # Git Bash lacks Linux flock. Exercise overlapping transactions with a
            # directory mutex, and leave native flock untouched on Linux.
            script = script.replace("flock -x 8 || exit 1", "test_config_flock || exit 1")
            script = r"""
test_config_flock(){
    local i
    for i in {1..600}; do
        if mkdir "$TMPDIR/config-mutex" 2>/dev/null; then
            trap 'rmdir "$TMPDIR/config-mutex"' EXIT
            return 0
        fi
        sleep .03
    done
    return 1
}
""" + script
            script = "python3(){ " + shlex.quote(Path(sys.executable).as_posix()) + ' "$@"; }\n' + script
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
