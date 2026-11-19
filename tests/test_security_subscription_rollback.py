"""Exercise snapshot exchange, POSIX signals, and retirement of the legacy backend."""
import os
import shlex
import socket
import sys

from shell_testlib import ShellTestCase, section


class SubscriptionRollbackTests(ShellTestCase):
    def scenario(self, fault, legacy=False):
        # MSYS special files provide POSIX symlinks without Windows symlink privilege.
        env = {"MSYS": "winsymlinks:sys"} if os.name == "nt" else {}
        funcs = section("sub_valid_domain", "sub_remove")
        for path, local in (("/etc/s-box", "box"), ("/root/websbox", "oldweb"),
                            ("/var/lib/sbox-subscription", "pub"), ("/etc/caddy", "caddy"),
                            ("/etc/local.d", "local.d"), ("/etc/init.d", "init.d"),
                            ("/etc/systemd/system", "systemd")):
            funcs = funcs.replace(path, local)
        # The identity/permissions of the publishing group are tested separately.
        funcs = funcs.replace(' -g "$group" ', " ").replace('chgrp "$group" "$snapshot" && ', "")
        script = funcs + f"\nfault={shlex.quote(fault)}\nlegacy_case={int(legacy)}\n" + r'''
set -u
key=$(sub_https_key same-token) || exit 2
mkdir -p box pub/www pub-https/www caddy systemd init.d local.d oldweb
red(){ printf '%s\n' "$*" >&2; }
sub_prepare_reader(){ :; }
sub_caddy_prepare(){ echo managed; }
sub_service(){
    if [[ $fault == openrc-failure && $2 == sbox-sub-caddy ]]; then
        case "$1" in
        status) if [[ -e caddy-running ]]; then return 0; else return 1; fi ;;
        enabled) return 1 ;;
        stop) [[ -e init.d/sbox-sub-caddy ]] || return 1; rm -f caddy-running || return 1; return 0 ;;
        esac
    fi
    return 0
}
if [[ $fault == openrc-failure ]]; then rc-service(){ :; }; fi
sub_caddy_apply(){
    echo candidate > caddy/s-box-subscription.caddy
    if [[ $fault == openrc-failure ]]; then touch init.d/sbox-sub-caddy caddy-running; fi
}
sub_caddy_reload(){ :; }
systemctl(){ :; }
crontab(){ :; }
sub_httpd_write(){ echo "$2" > box/sub-httpd.sh; }
sub_httpd_boot(){ :; }
sub_httpd_running(){
    if [[ ${1:-} == oldweb ]]; then [[ -e old-running ]]; else [[ -e new-running ]]; fi
}
sub_httpd_stop(){
    if [[ ${1:-} == oldweb ]]; then
        rm -f old-running
        if [[ $fault == legacy-failure ]]; then return 1; fi
        if [[ $fault == legacy-signal ]]; then kill -TERM "$BASHPID"; fi
    else rm -f new-running; fi
}
sub_httpd_start(){ printf '%s\n' "$1" > new-running; }
sub_restore_legacy_httpd(){
    [[ $1 == "" && $2 == old-token && $3 == 18080 ]] || return 1
    touch old-running
}
sub_https_ready(){
    [[ $(cat "pub-https/www/$key/sbox.json") == next ]] || return 1
    if (( ! legacy_case )); then [[ $(cat "pub-https/www/$saved/sbox.json") == initial ]] || return 1; fi
    case $fault in
        fail|openrc-failure) return 1 ;;
        HUP|TERM) kill "-$fault" "$BASHPID" ;;
    esac
    return 0
}
sbshare(){
    for item in sbox.json clmi.yaml jhsub.txt; do echo next > "box/$item"; done
}
for item in sbox.json clmi.yaml jhsub.txt; do echo initial > "box/$item"; done
echo 18080 > box/subport.log
echo original-launcher > box/sub-httpd.sh
echo prior > caddy/s-box-subscription.caddy
if (( legacy_case )); then
    echo old-token > box/subtoken.log
    mkdir -p oldweb/old-token
    touch old-running
else
    echo same-token > box/subtoken.log
    echo old.example.test > box/subdomain.log
    echo 18080 > new-running
    sub_root=pub-https sub_links "$key" || exit 20
    saved=$(readlink pub-https/www/$key) || exit 21
fi
if (( legacy_case )); then
    for item in sbox.json clmi.yaml jhsub.txt; do echo next > "box/$item"; done
fi
if sub_apply_locked new.example.test same-token 18081; then status=0; else status=$?; fi
if [[ $fault == ok ]]; then
    [[ $status == 0 && $(cat pub-https/www/$key/sbox.json) == next ]] || exit 31
    [[ ! -d pub-https/www/$saved ]] || exit 32
else
    [[ $status != 0 && $(cat box/subport.log) == 18080 && $(cat box/sub-httpd.sh) == original-launcher ]] || exit 33
    [[ $(cat caddy/s-box-subscription.caddy) == prior ]] || exit 34
    [[ ! -e caddy-running && ! -e init.d/sbox-sub-caddy ]] || exit 38
    if (( legacy_case )); then
        [[ -e old-running && ! -e new-running && -d oldweb/old-token && ! -L pub-https/www/$key ]] || exit 35
    else
        for item in sbox.json clmi.yaml jhsub.txt; do [[ $(cat "box/$item") == initial ]] || exit 39; done
        [[ $(readlink pub-https/www/$key) == "$saved" && $(cat pub-https/www/$key/sbox.json) == initial ]] || exit 36
        [[ $(cat new-running) == 18080 && $(cat box/subdomain.log) == old.example.test ]] || exit 37
    fi
fi
shopt -s nullglob
backups=(box/.subscription.* pub/.restore.*)
[[ ${#backups[@]} == 0 ]] || exit 40
snapshots=(pub-https/release.*)
(( ${#snapshots[@]} == 1 - legacy_case ))
'''
        self.run_shell(script, env=env)

    def test_same_token_commits_and_retires_previous_snapshot(self):
        self.scenario("ok")

    def test_same_token_failed_https_restores_original_snapshot(self):
        self.scenario("fail")

    def test_hangup_during_https_restores_original_snapshot(self):
        self.scenario("HUP")

    def test_termination_during_https_restores_original_snapshot(self):
        self.scenario("TERM")

    def test_openrc_can_stop_candidate_before_removing_its_init_script(self):
        self.scenario("openrc-failure")

    def test_regular_https_refresh_switches_all_three_files_and_preserves_last_good_snapshot(self):
        source = section("sub_valid_domain", "sub_apply")
        source = source.replace("/etc/s-box", "box").replace("/var/lib/sbox-subscription", "pub")
        source = source.replace(' -g "$group" ', " ").replace('chgrp "$group" "$snapshot" && ', "")
        script = source + r'''
mkdir -p box pub-https/www stage || exit 1
token=fictional-subscription-token
key=$(sub_https_key "$token") || exit 1
echo "$token" > box/subtoken.log
echo sub.example.test > box/subdomain.log
names=(sbox.json clmi.yaml jhsub.txt)
for name in "${names[@]}"; do echo first > "box/$name"; done
sub_root=pub-https sub_links "$key" || exit 1
for name in "${names[@]}"; do echo second > "stage/$name"; done
sub_publish_current stage "${names[@]}" || exit 1
saved=$(readlink "pub-https/www/$key") || exit 1
for name in "${names[@]}"; do
    [[ $(cat "pub-https/www/$key/$name") == second && $(cat "box/$name") == second ]] || exit 1
    echo third > "stage/$name"
done
install(){ [[ ${!#} != */clmi.yaml ]] && command install "$@"; }
if sub_publish_current stage "${names[@]}"; then exit 1; fi
[[ $(readlink "pub-https/www/$key") == "$saved" ]] || exit 1
for name in "${names[@]}"; do
    [[ $(cat "pub-https/www/$key/$name") == second && $(cat "box/$name") == second ]] || exit 1
done
'''
        self.run_shell(script, env={"MSYS": "winsymlinks:sys"} if os.name == "nt" else None)

    def test_legacy_backend_stopped_before_failed_commit_is_restarted(self):
        self.scenario("legacy-failure", legacy=True)

    def test_legacy_backend_stopped_before_interrupted_commit_is_restarted(self):
        self.scenario("legacy-signal", legacy=True)

    def test_legacy_restore_launches_former_root_and_checks_its_token(self):
        oldweb = self.root / "oldweb"
        (oldweb / "old-token").mkdir(parents=True)
        (oldweb / "old-token/sbox.json").write_text("old working subscription")
        (self.root / "box").mkdir()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        # The BusyBox CLI substitute accepts only the original local binding and root.
        # One real HTTP request must reach the old token before the shell returns success.
        (self.root / "once.py").write_text("""
from http.server import HTTPServer, SimpleHTTPRequestHandler
from functools import partial
from pathlib import Path
import sys
class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        Path("request-path").write_text(self.path)
        super().do_GET()
with HTTPServer(("127.0.0.1", int(sys.argv[1])), partial(Handler, directory="oldweb")) as server:
    server.timeout = 6
    server.handle_request()
""", encoding="utf-8")
        for binary in ("busybox", "busybox-extras"):
            stub = self.root / binary
            stub.write_text(f"""#!/bin/bash
[[ "$*" == 'httpd -f -p 127.0.0.1:{port} -h oldweb' ]] || exit 18
exec {shlex.quote(sys.executable.replace(os.sep, "/"))} once.py {port}
""", encoding="utf-8", newline="\n")
            stub.chmod(0o700)
        script = section("sub_valid_token", "sub_service") + section("sub_restore_legacy_httpd", "sub_publish_current")
        script = script.replace("/root/websbox", "oldweb").replace("/etc/s-box", "box")
        if os.name == "nt":
            script = script.replace('-o /dev/null', '-o health-response')
        self.run_shell(script + f'\nPATH="$PWD:$PATH"\nsub_httpd_running(){{ return 1; }}\n'
                       f'sub_restore_legacy_httpd sub.example.test old-token {port} || exit 1\nwait\n')
        self.assertEqual((self.root / "request-path").read_text(), "/old-token/sbox.json")
