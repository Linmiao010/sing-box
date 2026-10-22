"""Exercise core replacement and rollback with offline release archives."""
import io
import shlex
import tarfile

from shell_testlib import ShellTestCase, section


class CoreUpdateTests(ShellTestCase):
    def setUp(self):
        super().setUp()
        self.box = self.root / "s-box"
        self.box.mkdir()
        self.old_core = b"#!/bin/bash\nprintf 'sing-box version 1.14.2\\n'\n"
        self.old_config = b'{\n  "inbounds": [], "route": {"final": "old"}\n}\n'
        self.extra = b'{\n "inbounds": [{"type": "snell", "tag": "snell-sb"}]\n}\n'
        (self.box / "sing-box").write_bytes(self.old_core)
        (self.box / "sb.json").write_bytes(self.old_config)
        (self.box / "sb_extra.json").write_bytes(self.extra)
        (self.box / "sb10.json").write_bytes(b'{"inbounds": [], "route": {"final": "legacy"}}\n')
        (self.box / "sb11.json").write_bytes(b'{"inbounds": [], "route": {"final": "modern"}}\n')
        self.payload = br'''#!/bin/bash
case "$1" in
    version)
        [[ "$TEST_SCENARIO" != version_failure ]] || exit 1
        printf 'sing-box version %s\n' "$TEST_ACTUAL_VERSION"
        ;;
    check)
        shift
        printf '%s\n' "$@" > "$TEST_ROOT/check-args"
        while (( $# )); do
            case "$1" in
                -c|--config)
                    shift
                    [[ -s "$1" ]] || exit 2
                    if [[ "$TEST_SCENARIO" == incompatible ]] && grep -q '"type": "snell"' "$1"; then
                        printf 'unknown inbound type: snell\n' >&2
                        exit 1
                    fi
                    ;;
            esac
            shift
        done
        ;;
    *) exit 2 ;;
esac
'''

    def make_release(self, version):
        with tarfile.open(self.root / "release.tar.gz", "w:gz") as archive:
            member = tarfile.TarInfo(f"sing-box-{version}-linux-amd64/sing-box")
            member.size = len(self.payload)
            member.mode = 0o755
            archive.addfile(member, io.BytesIO(self.payload))

    def run_update(self, version="1.14.3", scenario="ok", actual_version=None):
        self.make_release(version)
        if scenario == "bad_archive":
            (self.root / "release.tar.gz").write_bytes(b"not a gzip archive")
        functions = section("core_update", "upsbcroe").replace("/etc/s-box", "s-box")
        stubs = r'''
export TEST_ROOT="$(pwd)"
cpu=amd64
sbnh=1.14
red(){ printf '%s\n' "$*"; }
yellow(){ printf '%s\n' "$*"; }
green(){ printf '%s\n' "$*"; }
blue(){ printf '%s\n' "$*"; }
sleep(){ :; }
curl(){
    local target='' url='' argument
    printf '%s\n' "$*" >> "$TEST_ROOT/downloads"
    while (( $# )); do
        argument=$1; shift
        case "$argument" in
            -o|--output) target=$1; shift ;;
            https://*) url=$argument ;;
        esac
    done
    [[ -n "$target" ]] || return 2
    [[ "$url" == "https://github.com/SagerNet/sing-box/releases/download/v$TEST_REQUESTED_VERSION/sing-box-$TEST_REQUESTED_VERSION-linux-amd64.tar.gz" ]] || return 22
    if [[ "$TEST_SCENARIO" == download_failure ]]; then
        printf 'partial archive' > "$target"
        return 28
    fi
    cp "$TEST_ROOT/release.tar.gz" "$target"
}
restartsb(){
    local running
    running=$("$TEST_ROOT/s-box/sing-box" version) || return 1
    printf '%s\n' "$running" >> "$TEST_ROOT/restarts"
    cp "$TEST_ROOT/s-box/sb.json" "$TEST_ROOT/restarted-config"
    if [[ "$TEST_SCENARIO" == interrupted_restart && "$running" != 'sing-box version 1.14.2' ]]; then
        kill -TERM "$BASHPID"
    fi
    if [[ "$TEST_SCENARIO" == restart_failure && "$running" != 'sing-box version 1.14.2' ]]; then
        return 1
    fi
}
sbshare(){ printf 'published\n' > "$TEST_ROOT/published"; }
chmod +x s-box/sing-box
'''
        return self.run_shell(
            stubs + functions + "\ncore_update " + shlex.quote(version) + "\n",
            check=False,
            env={
                "TEST_SCENARIO": scenario,
                "TEST_REQUESTED_VERSION": version,
                "TEST_ACTUAL_VERSION": actual_version or version,
            },
        )

    def assert_installed_state_preserved(self):
        self.assertEqual((self.box / "sing-box").read_bytes(), self.old_core)
        self.assertEqual((self.box / "sb.json").read_bytes(), self.old_config)
        self.assertEqual((self.box / "sb_extra.json").read_bytes(), self.extra)
        self.assertFalse((self.root / "published").exists())

    def test_incompatible_extra_is_checked_before_replacing_core(self):
        result = self.run_update("1.10.7", "incompatible")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_installed_state_preserved()
        self.assertFalse((self.root / "restarts").exists())
        arguments = (self.root / "check-args").read_text(encoding="utf-8")
        self.assertIn("sb10.json", arguments)
        self.assertIn("sb_extra.json", arguments)

    def test_failed_download_archive_or_version_keeps_installed_state(self):
        for scenario in ("download_failure", "bad_archive", "version_failure"):
            with self.subTest(scenario=scenario):
                result = self.run_update(scenario=scenario)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assert_installed_state_preserved()
                self.assertFalse((self.root / "check-args").exists())
                self.assertFalse((self.root / "restarts").exists())

    def test_failed_restart_restores_core_config_and_restarts_previous_core(self):
        result = self.run_update(scenario="restart_failure")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_installed_state_preserved()
        self.assertEqual(
            (self.root / "restarts").read_text(encoding="utf-8").splitlines(),
            ["sing-box version 1.14.3", "sing-box version 1.14.2"],
        )
        self.assertEqual((self.root / "restarted-config").read_bytes(), self.old_config)

    def test_termination_during_restart_restores_and_restarts_previous_core(self):
        result = self.run_update(scenario="interrupted_restart")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_installed_state_preserved()
        self.assertEqual(
            (self.root / "restarts").read_text(encoding="utf-8").splitlines(),
            ["sing-box version 1.14.3", "sing-box version 1.14.2"],
        )
        self.assertEqual((self.root / "restarted-config").read_bytes(), self.old_config)

    def test_success_uses_actual_core_version_and_publishes_after_restart(self):
        for actual_version, template, with_extra in (
            ("1.10.7", "sb10.json", False),
            ("1.14.3", "sb11.json", True),
        ):
            with self.subTest(actual_version=actual_version, with_extra=with_extra):
                (self.root / "restarts").unlink(missing_ok=True)
                (self.root / "published").unlink(missing_ok=True)
                if with_extra:
                    (self.box / "sb_extra.json").write_bytes(self.extra)
                else:
                    (self.box / "sb_extra.json").unlink(missing_ok=True)
                result = self.run_update(actual_version=actual_version)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual((self.box / "sing-box").read_bytes(), self.payload)
                expected_config = (self.box / template).read_bytes()
                self.assertEqual((self.box / "sb.json").read_bytes(), expected_config)
                self.assertEqual((self.root / "restarted-config").read_bytes(), expected_config)
                self.assertEqual(
                    (self.root / "restarts").read_text(encoding="utf-8").splitlines(),
                    [f"sing-box version {actual_version}"],
                )
                self.assertTrue((self.root / "published").exists())
                arguments = (self.root / "check-args").read_text(encoding="utf-8")
                self.assertIn(template, arguments)
                if with_extra:
                    self.assertIn("sb_extra.json", arguments)
                    self.assertEqual((self.box / "sb_extra.json").read_bytes(), self.extra)

    def test_invalid_version_is_rejected_before_downloading(self):
        for version in ("", "latest", "1.14.3;false"):
            with self.subTest(version=version):
                result = self.run_update(version)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assert_installed_state_preserved()
                self.assertFalse((self.root / "downloads").exists())
                self.assertFalse((self.root / "restarts").exists())
