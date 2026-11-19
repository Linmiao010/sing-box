"""Verify generation, the HTTPS symlink, and GitLab readers share one commit boundary."""
import os
import shutil
import unittest

from shell_testlib import ShellTestCase, section


class PublicationLockTests(ShellTestCase):
    def fixture(self):
        source = "\n".join(section(*pair) for pair in (
            ("sub_valid_token", "sub_service"), ("sub_unlink", "sub_apply"),
            ("gitlab_copy_subscriptions", "gitlab_publish"), ("sbshare", "clash_sb_share"),
            ("client_warp_apply", "client_warp_egress_status")))
        source = source.replace("/etc/s-box", "box").replace("/var/lib/sbox-subscription", "public")
        source = source.replace(" -g sboxsub ", " ").replace('chgrp sboxsub "$snapshot" && ', "")
        if os.name == "nt":
            source += "\nflock(){ :; }\n"  # Only the rollback test runs on Windows.
        return source + r'''
mkdir -p box public/www reader
token=test-token
printf '%s\n' "$token" > box/subtoken.log
for name in sbox.json clmi.yaml jhsub.txt; do echo initial > "box/$name"; done
sub_links "$token" || exit 10
sbnh=1.10
blue(){ :; }; white(){ :; }; red(){ :; }
result_vl_vm_hy_tu(){
    tls=false; tu5_self=true
    if [[ ${1:-} == client-only ]]; then touch warp-entered; else touch "entered-$generation"; fi
}
resvless(){ echo "$generation" > box/vl_reality.txt; }
resvmess(){ :; }
reshy2(){ echo "$generation" > box/hy2.txt; }
restu5(){ :; }
sb_client(){
    local value=$generation
    if [[ -n ${client_warp_config_file:-} ]]; then value=$(cat box/sbox.json); fi
    echo "$value" > "$1/sbox.json" && echo "$value" > "$1/clmi.yaml"
}
client_warp_validate(){ cat "$1"; }
'''

    def test_failed_local_replace_restores_all_products_and_public_link(self):
        script = self.fixture() + r'''
generation=changed
saved=$(readlink public/www/test-token)
mv(){
    if [[ ${!#} == box/clmi.yaml ]]; then return 1; fi
    command mv "$@"
}
sbshare && exit 11
for name in sbox.json clmi.yaml jhsub.txt; do
    [[ $(cat "box/$name") == initial && $(cat "public/www/test-token/$name") == initial ]] || exit 12
done
[[ $(readlink public/www/test-token) == "$saved" ]]
'''
        self.run_shell(script, env={"MSYS":"winsymlinks:sys"} if os.name == "nt" else None)

    def test_failed_warp_publication_restores_client_and_account(self):
        script = self.fixture() + r'''
echo initial-account > box/client_warp.json
echo changed-account > candidate
saved=$(readlink public/www/test-token)
sub_links(){ return 1; }
generation=test
client_warp_apply candidate && exit 13
[[ $(cat box/client_warp.json) == initial-account && $(cat box/clmi.yaml) == initial ]] || exit 14
[[ $(readlink public/www/test-token) == "$saved" ]]
'''
        self.run_shell(script, env={"MSYS":"winsymlinks:sys"} if os.name == "nt" else None)

    @unittest.skipUnless(os.name == "posix" and shutil.which("flock"), "requires native flock")
    def test_real_flock_serializes_generators_and_git_reader_during_partial_commit(self):
        script = self.fixture() + r'''
mv(){
    command mv "$@" || return 1
    if [[ ${!#} == box/sbox.json && $generation == first ]]; then
        touch partial
        while [[ ! -f release-first ]]; do sleep 0.03; done
    fi
}
generation=first sbshare >/dev/null & writer=$!
for i in {1..150}; do [[ -f partial ]] && break; sleep 0.03; done
[[ -f partial && $(cat box/clmi.yaml) == initial ]] || exit 20
( touch waiting-reader; gitlab_copy_subscriptions reader ) & reader_pid=$!
( touch waiting-second; generation=second sbshare >/dev/null ) & second=$!
echo example > candidate
( touch waiting-warp; generation=warp client_warp_apply candidate ) & warp=$!
for i in {1..150}; do
    [[ -f waiting-reader && -f waiting-second && -f waiting-warp ]] && break
    sleep 0.03
done
sleep 0.2
blocked=1
[[ -e reader/sbox.json || -e entered-second || -e warp-entered ]] && blocked=0
touch release-first
wait "$writer" && wait "$reader_pid" && wait "$second" && wait "$warp" || exit 21
[[ $blocked == 1 && -e entered-second && -e warp-entered ]] || exit 22
for root in reader public/www/test-token box; do
    marker=$(cat "$root/sbox.json")
    [[ $marker == first || $marker == second ]] || exit 23
    [[ $(cat "$root/clmi.yaml") == "$marker" && $(head -1 "$root/jhsub.txt") == "$marker" ]] || exit 24
done
# An endpoint switch already owns the lock. Generate private candidates without changing the live token.
( flock -x 9 && generation=next-endpoint sbshare subscription-switch >/dev/null ) 9>box/subscription.lock || exit 25
[[ $(cat box/sbox.json) == next-endpoint && $(cat public/www/test-token/sbox.json) != next-endpoint ]]
'''
        self.run_shell(script)
