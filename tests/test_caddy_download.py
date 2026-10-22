"""Exercise Caddy installation with offline release artifacts and failed downloads."""
import hashlib
import io
import json
import os
import shlex
import tarfile

from shell_testlib import REPO, ShellTestCase, section


class CaddyDownloadTests(ShellTestCase):
    def setUp(self):
        super().setUp()
        self.box = self.root / "s-box"
        self.box.mkdir()
        self.payload = b"#!/bin/bash\nprintf 'v2.11.7-test\\n'\nprintf 'checked\\n' >> version-checked\n"
        (self.root / "binary").write_bytes(self.payload)
        self.make_release()

    def make_release(self, payload=None, bad_checksum=False, bad_archive=False):
        archive = self.root / "release.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            body = self.payload if payload is None else payload
            entry = tarfile.TarInfo("caddy")
            entry.size = len(body)
            entry.mode = 0o755
            bundle.addfile(entry, io.BytesIO(body))
        if bad_archive:
            archive.write_bytes(b"not an archive")
        digest = "0" * 64 if bad_checksum else hashlib.sha256(archive.read_bytes()).hexdigest()
        assets = [{
            "name": f"caddy_2.11.7_linux_{arch}.tar.gz",
            "browser_download_url": f"https://github.com/caddyserver/caddy/releases/download/v2.11.7/caddy_2.11.7_linux_{arch}.tar.gz",
            "digest": f"sha256:{digest}",
        } for arch in ("amd64", "arm64", "armv7")]
        (self.root / "release.json").write_text(json.dumps({"tag_name": "v2.11.7", "assets": assets}), encoding="utf-8")

    def run_prepare(self, scenario="timeout", machine="x86_64", extra=""):
        functions = (section("sub_caddy_site", "sub_caddy_reload")
                     .replace("/etc/s-box", "s-box")
                     .replace("/etc/caddy", "system-caddy"))
        stubs = r'''
red(){ printf '%s\n' "$*"; }
blue(){ printf '%s\n' "$*"; }
yellow(){ printf '%s\n' "$*"; }
sub_service(){ return 1; }
ss(){ :; }
uname(){ printf '%s\n' "$TEST_MACHINE"; }
curl(){
    local target='' url='' arg
    printf '%s\n' "$*" >> requests
    while (( $# )); do
        arg=$1; shift
        case "$arg" in
            -o|--output) target=$1; shift ;;
            https://*) url=$arg ;;
        esac
    done
    case "$url" in
        https://caddyserver.com/api/download\?*)
            case "$TEST_SCENARIO" in
                primary_ok) cp binary "$target" ;;
                primary_invalid) printf '<html>error</html>\n' > "$target" ;;
                *) printf 'partial' > "$target"; return 28 ;;
            esac
            ;;
        https://api.github.com/repos/caddyserver/caddy/releases/latest)
            [[ "$TEST_SCENARIO" != both_fail ]] || return 28
            cp release.json "$target"
            ;;
        https://github.com/caddyserver/caddy/releases/download/v2.11.7/caddy_2.11.7_linux_*.tar.gz)
            if [[ "$TEST_SCENARIO" == release_failure ]]; then
                printf 'partial archive' > "$target"
                return 28
            fi
            cp release.tar.gz "$target"
            ;;
        *) return 22 ;;
    esac
}
'''
        script = functions + stubs + extra + '\nmode=$(sub_caddy_prepare) || exit $?\nprintf "%s\\n" "$mode"\n'
        return self.run_shell(script, check=False, env={
            "TEST_SCENARIO": scenario,
            "TEST_MACHINE": machine,
            "PATH": str(REPO / ".test-tools") + os.pathsep + os.environ["PATH"],
        })

    def assert_no_temporary_downloads(self):
        self.assertEqual(list(self.box.glob(".caddy.*")), [])

    def test_primary_success_installs_without_github_and_keeps_stdout_clean(self):
        result = self.run_prepare("primary_ok")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "managed\n")
        self.assertEqual((self.box / "caddy").read_bytes(), self.payload)
        self.assertNotIn("github.com", (self.root / "requests").read_text())
        self.assert_no_temporary_downloads()

    def test_primary_timeout_uses_matching_official_release_for_each_architecture(self):
        for machine, arch in (("x86_64", "amd64"), ("aarch64", "arm64"), ("armv7l", "armv7")):
            with self.subTest(machine=machine):
                (self.box / "caddy").unlink(missing_ok=True)
                (self.root / "requests").unlink(missing_ok=True)
                result = self.run_prepare(machine=machine)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "managed\n")
                self.assertEqual((self.box / "caddy").read_bytes(), self.payload)
                requests = (self.root / "requests").read_text()
                self.assertIn(f"/caddy_2.11.7_linux_{arch}.tar.gz", requests)
                self.assertIn("GitHub", result.stderr)
                self.assert_no_temporary_downloads()

    def test_primary_invalid_binary_also_uses_fallback(self):
        result = self.run_prepare("primary_invalid")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.box / "caddy").read_bytes(), self.payload)
        self.assert_no_temporary_downloads()

    def test_failed_sources_preserve_existing_incomplete_binary(self):
        target = self.box / "caddy"
        target.write_text("previous incomplete download", encoding="utf-8")
        for scenario in ("both_fail", "release_failure"):
            with self.subTest(scenario=scenario):
                result = self.run_prepare(scenario, extra="\nchmod 600 " + shlex.quote(target.as_posix()) + "\n")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertEqual(target.read_text(), "previous incomplete download")
                self.assert_no_temporary_downloads()

    def test_release_without_checksum_or_official_url_is_rejected(self):
        metadata = self.root / "release.json"
        original = json.loads(metadata.read_text(encoding="utf-8"))
        for key, value in (("digest", None), ("browser_download_url", "https://example.com/caddy.tar.gz")):
            with self.subTest(field=key):
                release = json.loads(json.dumps(original))
                release["assets"][0][key] = value
                metadata.write_text(json.dumps(release), encoding="utf-8")
                result = self.run_prepare()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertFalse((self.box / "caddy").exists())
                self.assertFalse((self.root / "version-checked").exists())
                self.assert_no_temporary_downloads()

    def test_bad_checksum_never_executes_or_installs_release(self):
        self.make_release(bad_checksum=True)
        result = self.run_prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertFalse((self.box / "caddy").exists())
        self.assertFalse((self.root / "version-checked").exists())
        self.assert_no_temporary_downloads()

    def test_invalid_release_archive_or_binary_is_not_installed(self):
        for fixture in ({"bad_archive": True}, {"payload": b"#!/bin/bash\nexit 1\n"}):
            with self.subTest(fixture=fixture):
                self.make_release(**fixture)
                result = self.run_prepare()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertFalse((self.box / "caddy").exists())
                self.assert_no_temporary_downloads()

    def test_installed_binary_does_not_download_again(self):
        target = self.box / "caddy"
        target.write_bytes(self.payload)
        result = self.run_prepare(extra="\nchmod 755 " + shlex.quote(target.as_posix()) + "\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "managed\n")
        self.assertFalse((self.root / "requests").exists())

    def test_shared_caddy_does_not_download(self):
        system_config = self.root / "system-caddy"
        system_config.mkdir()
        (system_config / "Caddyfile").write_text("# existing sites\n", encoding="utf-8")
        result = self.run_prepare(extra="\ncaddy(){ :; }\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "shared\n")
        self.assertFalse((self.root / "requests").exists())

    def test_termination_cleans_download_without_starting_fallback(self):
        result = self.run_prepare(extra='\ncurl(){ kill -TERM "$BASHPID"; }\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("GitHub", result.stderr)
        self.assertFalse((self.box / "caddy").exists())
        self.assert_no_temporary_downloads()
