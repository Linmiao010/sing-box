"""Exercise concurrent node changes and administrator stop decisions with local services."""
import json
import os
from pathlib import Path
import shutil

from shell_testlib import REPO, ShellTestCase, section


OLD = "11111111-1111-4111-a111-111111111111"
NEW = "22222222-2222-4222-b222-222222222222"
CONFIGS = ("sb.json", "sb10.json", "sb11.json")


class NodeLifecycleTests(ShellTestCase):
    def setUp(self):
        super().setUp()
        self.box = self.root / "box"
        self.box.mkdir()
        self.core = self.box / "sing-box"
        self.core.write_text("""#!/bin/bash
case "$1" in
version) echo 'sing-box version 1.14.3';;
check) printf 'check %s\\n' "$SESSION" >> events; exit 0;;
*) exit 1;;
esac
""", newline="\n")
        self.core.chmod(0o700)

    def test_overlapping_rotation_and_failing_port_change(self):
        jq = shutil.which("jq") or str(REPO / ".test-tools/jq.exe")
        if not Path(jq).is_file():
            self.skipTest("requires jq")
        env = {"PATH": str(Path(jq).parent) + os.pathsep + os.environ["PATH"]}
        nodes = [{"tag": "vless-sb", "listen_port": 8443, "users": [{"uuid": OLD}]},
                 {"tag": "vmess-sb", "transport": {"path": "/independent"}, "users": [{"uuid": OLD}]},
                 {"tag": "hy2-sb", "users": [{"password": OLD}]},
                 {"tag": "tuic5-sb", "users": [{"uuid": OLD, "password": OLD}]},
                 {"tag": "anytls-sb", "users": [{"password": OLD}]}]
        self.box.joinpath("firewall.json").write_text("{}")
        body = section("config_jq", "inssbjsonser").replace("/etc/s-box", "box") + r"""
wait_for(){
    local i
    for i in {1..160}; do [[ ! -e $1 ]] || return 0; sleep .05; done
    return 1
}
ip_priority_restart(){ cp box/sb.json running.json; }
firewall_sync(){
    printf 'sync %s\n' "$SESSION" >> events
    if [[ $SESSION == "$FIRST" && ! -e first-entered ]]; then
        touch first-entered
        wait_for release-first || return 1
    fi
    [[ $SESSION != port || $(jq -r '.inbounds[0].listen_port' box/sb.json) != 9555 ]]
}
red(){ echo "$*" >&2; }
transaction(){
    export SESSION=$1
    if [[ $1 == identity ]]; then
        config_apply config_patch_identity "$NEW_UUID" /independent
    else
        config_apply config_patch_inbound vless-sb '{"listen_port":9555}'
    fi
}
transaction "$FIRST" & first_pid=$!
wait_for first-entered || exit 31
(touch contender-started; transaction "$SECOND") & second_pid=$!
wait_for contender-started || exit 32
# While the first transaction is paused after its restart, the other process
# must not reach candidate validation, even in the reverse (rollback-first) case.
sleep .35
if grep -Fxq "check $SECOND" events; then touch lock-broken; fi
touch release-first
wait "$first_pid"; first_result=$?
wait "$second_pid"; second_result=$?
[[ ! -e lock-broken ]] || exit 33
if [[ $FIRST == identity ]]; then [[ $first_result == 0 && $second_result != 0 ]];
else [[ $first_result != 0 && $second_result == 0 ]]; fi
"""
        for first, second in (("identity", "port"), ("port", "identity")):
            with self.subTest(first=first):
                for marker in ("first-entered", "contender-started", "release-first", "events", "lock-broken"):
                    (self.root / marker).unlink(missing_ok=True)
                for name in CONFIGS:
                    selected = nodes[:-1] if name == "sb10.json" else nodes
                    self.box.joinpath(name).write_text(json.dumps({"inbounds": selected}))
                self.run_shell(body, env=dict(env, FIRST=first, SECOND=second, NEW_UUID=NEW))
                self.assertEqual((self.root / "running.json").read_bytes(), (self.box / "sb.json").read_bytes())
                events = (self.root / "events").read_text().splitlines()
                self.assertEqual(events.count("sync port"), 2, "failed commit and rollback must both sync")
                self.assertLess(events.index("sync " + first), events.index("check " + second))
                for name in CONFIGS:
                    saved = json.loads(self.box.joinpath(name).read_text())["inbounds"]
                    self.assertEqual(saved[0]["listen_port"], 8443)
                    self.assertEqual({v for node in saved for v in node["users"][0].values()}, {NEW})

    def test_service_ownership_guards_install_rewrite_uninstall_and_control(self):
        groups = [("singbox_unit_state", "ipuuid"), ("instsllsingbox", "changeym"),
                  ("unins", "sblog"), ("restartsb", "cronsb"), ("extra_apply", "extra_set")]
        body = "\n".join(section(*group) for group in groups)
        for original, fixture in (("/etc/s-box", "box"), ("/etc/init.d", "init.d"),
                                  ("/etc/systemd/system", "systemd"), ("/run/systemd/system", "runtime-units"),
                                  ("/usr/lib/systemd/system", "vendor"), ("/lib/systemd/system", "vendor-lib"),
                                  ("/usr/local/lib/systemd/system", "local-vendor")):
            body = body.replace(original, fixture)
        script = r'''
mkdir -p init.d systemd vendor
command(){
    if [[ $1 == -v && ( $2 == apk || $2 == rc-service ) ]]; then [[ $backend == openrc ]]; return; fi
    builtin command "$@"
}
systemctl(){
    if [[ $1 == show ]]; then
        case $3 in LoadState) echo "$load";; FragmentPath) echo "$fixture_fragment";; ExecStart) echo "$fixture_effective";; esac
    else printf 'systemctl %s\n' "$*" >> service-changes; fi
}
rc-service(){ if [[ $1 == -e ]]; then return 1; else printf 'rc-service %s\n' "$*" >> service-changes; fi; }
sub_service(){ if [[ $1 == status ]]; then return 0; else printf '%s\n' "$*" >> service-changes; fi; }
ensure_dependencies(){ touch unexpected-install; return 1; }
extra_config_init(){ touch unexpected-rewrite; return 1; }
firewall_legacy(){ touch unexpected-uninstall; }
sub_remove(){ return 1; } # Stop this fixture before any filesystem removal can run.
red(){ :; }
foreign(){
    [[ $(singbox_unit_state) == foreign ]] || exit 11
    instsllsingbox_locked && exit 12
    sbservice checked && exit 13
    unins_locked && exit 14
    restartsb checked && exit 15
    singbox_maintenance && exit 16
    singbox_stop && exit 17
    singbox_enable && exit 18
    [[ ! -e box/admin-stopped && ! -e unexpected-install && ! -e unexpected-rewrite &&
       ! -e unexpected-uninstall && ! -e service-changes ]] || exit 19
}
backend=systemd; load=not-found
[[ $(singbox_unit_state) == absent ]] || exit 1
touch vendor/sing-box.service
foreign
rm vendor/sing-box.service
mkdir systemd/sing-box.service.d
touch systemd/sing-box.service.d/override.conf
foreign
rm systemd/sing-box.service.d/override.conf
load=loaded; fixture_fragment=vendor/sing-box.service; fixture_effective='{ path=/usr/bin/sing-box ; argv[]=/usr/bin/sing-box run ; }'
foreign
for more in '' ' -c box/sb_extra.json'; do
    args="box/sing-box run -c box/sb.json$more"
    printf '%s\n' "ExecStart=$args" > systemd/sing-box.service
    fixture_fragment=systemd/sing-box.service; fixture_effective="{ path=box/sing-box ; argv[]=$args ; }"
    [[ $(singbox_unit_state) == project ]] || exit 20
done
# A drop-in may override the on-disk base unit after it was installed.
fixture_effective='{ path=box/sing-box ; argv[]=box/sing-box run -c box/sb.json.other ; }'
foreign
rm systemd/sing-box.service
backend=openrc
[[ $(singbox_unit_state) == absent ]] || exit 21
for more in '' ' -c box/sb_extra.json'; do
    printf '%s\n' 'command="box/sing-box"' "command_args=\"run -c box/sb.json$more\"" > init.d/sing-box
    [[ $(singbox_unit_state) == project ]] || exit 22
done
printf '%s\n' 'command="/usr/bin/sing-box"' 'command_args="run -c box/sb.json"' > init.d/sing-box
foreign
printf '%s\n' 'command="box/sing-box"' 'command_args="run -c box/sb.json.other"' > init.d/sing-box
foreign
'''
        self.run_shell(body + script)

    def test_stop_closes_tunnels_before_releasing_origin(self):
        body = section("restartsb", "cronsb").replace("/etc/s-box", "box").replace("/etc/local.d", "local")
        self.run_shell(body + r'''
singbox_require_owned(){ :; }
argo_unit_owned(){ return 0; }
crontab(){ return 0; }
cronsb(){ echo "cron $*" >> events; }
sub_service(){
    case $1 in
        enabled) [[ $2 == argo ]];;
        status) return 1;;
        *) echo "$1 $2" >> events;;
    esac
}
project_process(){ echo "$1 $3" >> events; [[ $1 != temporary-all || ! -f simulate-failure ]]; }
red(){ :; }
singbox_stop || exit 1
[[ -e box/admin-stopped ]] || exit 2
echo "stop argo
disable argo
fixed stop
temporary-all stop
cron remove
stop sing-box" > expected
cmp expected events || exit 3
touch simulate-failure
: > events
if singbox_stop; then exit 4; fi
! grep -Fxq 'stop sing-box' events
''')

    def test_vmess_port_change_keeps_origin_until_tunnels_are_closed(self):
        node = {"tag": "vmess-sb", "listen": "127.0.0.1", "listen_port": 9000, "tls": {"enabled": False}}
        for name in CONFIGS:
            (self.box / name).write_text(json.dumps({"inbounds": [node]}))
        body = (section("config_jq", "inssbjsonser") + section("argo_stop_fixed", "singbox_finish_stop"))
        self.run_shell(body.replace("/etc/s-box", "box") + r'''
sbnh=1.14
config_lock_held=1
red(){ :; }; argo_unit_owned(){ return 1; }; crontab(){ return 0; }
project_process(){
    [[ $(jq -r '.inbounds[0].listen_port' box/sb.json) == 9000 ]] || return 2
    [[ $1 != temporary-all || -e allow-stop ]]
}
ip_priority_restart(){ echo restarted >> events; }
change_port(){ config_apply config_patch_inbound vmess-sb '{"listen_port":9001}'; }
if change_port; then exit 1; fi
[[ $(jq -r '.inbounds[0].listen_port' box/sb.json) == 9000 ]] || exit 2
touch allow-stop
change_port || exit 3
for file in box/{sb,sb10,sb11}.json; do
    [[ $(jq -r '.inbounds[0].listen_port' "$file") == 9001 ]] || exit 4
done
''')

    def test_stop_schedule_boot_and_explicit_enable(self):
        legacy = "0 1 * * * systemctl restart sing-box;rc-service sing-box restart"
        unrelated = "15 6 * * * /usr/local/bin/other-maintenance"
        self.box.joinpath("sb.json").write_text("{}")
        paths = ("runlevels/default", "runlevels/boot", "systemd", "init.d")
        for name in paths:
            self.root.joinpath(name).mkdir(parents=True)
        # The service manager, runlevel registrations and root crontab are all local fixtures.
        body = (section("sub_service", "sub_caddy_site") + section("sbservice", "ipuuid") +
                section("restartsb", "uncronsb") + section("ip_priority_restart", "ip_priority_apply"))
        for deployed, fixture in (("/etc/s-box", "box"), ("/etc/runlevels/", "runlevels/"),
                                  ("/etc/systemd/system/", "systemd/"), ("/etc/init.d/", "init.d/")):
            body = body.replace(deployed, fixture)
        body += r"""
set -e
command(){
    if [[ $1 == -v && ( $2 == rc-service || $2 == apk ) ]]; then [[ $BACKEND == openrc ]]; return; fi
    builtin command "$@"
}
green(){ :; }; red(){ echo "$*" >&2; }
pgrep(){ return 1; } # The isolated service-manager fixture has no cloudflared processes.
readp(){ printf -v "$2" '%s' "$MENU"; }
crontab(){ if [[ $1 == -l ]]; then cat cron; else cp "$1" cron; fi; }
interrupt_at(){
    if [[ ${INTERRUPT_AT:-} == "$1" && ! -e signal-sent ]]; then
        touch signal-sent
        kill -s "$SIGNAL" "$BASHPID"
    fi
}
state_change(){
    case "$1" in
    restart) [[ ! -e fail-start ]] || return 1; echo restart >> restarts; touch active ;;
    stop) rm -f active; interrupt_at stop ;;
    status) [[ -e active ]] ;;
    *) return 1 ;;
    esac
}
systemctl(){
    case "$1" in
    is-active) state_change status ;; is-enabled) [[ -e enabled ]] ;;
    restart|stop) state_change "$1" ;; enable) touch enabled; interrupt_at enable ;; disable) rm -f enabled ;;
    show)
        case "$3" in
            LoadState) if [[ -f systemd/sing-box.service ]]; then echo loaded; else echo not-found; fi ;;
            FragmentPath) echo systemd/sing-box.service ;;
            ExecStart) printf '{ path=box/sing-box ; argv[]=%s ; }\n' "$(sed -n 's/^ExecStart=//p' systemd/sing-box.service)" ;;
        esac ;;
    daemon-reload) : ;; *) return 1 ;;
    esac
}
rc-service(){ [[ $1 == sing-box ]] && state_change "$2"; }
rc-update(){
    case "$1" in
    add) ln -sf ../../init.d/sing-box "runlevels/$3/$2"; interrupt_at enable ;;
    del) rm -f "runlevels/$3/$2" ;;
    show) [[ ! -L runlevels/default/sing-box ]] || echo sing-box ;;
    *) return 1 ;;
    esac
}
extra_config_init(){ :; }
if [[ $BACKEND == openrc ]]; then
    printf '%s\n' 'command="box/sing-box"' 'command_args="run -c box/sb.json"' > init.d/sing-box
    rc-update add sing-box default
    rc-update add sing-box boot
    [[ -L runlevels/default/sing-box && -L runlevels/boot/sing-box ]]
else
    printf '%s\n' 'ExecStart=box/sing-box run -c box/sb.json' > systemd/sing-box.service
    touch enabled
fi
touch active
singbox_maintenance
[[ $(wc -l < restarts) -eq 1 ]]
rm -f active
singbox_maintenance
[[ ! -e active && $(wc -l < restarts) -eq 1 ]]
touch active
MENU=2 stclre
[[ -e box/admin-stopped && ! -e active && ! -e enabled ]]
[[ ! -L runlevels/boot/sing-box && ! -L runlevels/default/sing-box ]]
[[ $(cat cron) == "$UNRELATED" ]]
# Simulate the activities that might run before the next deliberate enable.
singbox_maintenance
ip_priority_restart checked
cronsb
sbservice checked
if [[ $BACKEND == openrc ]]; then
    grep -q 'admin-stopped' init.d/sing-box
    bash -c 'source init.d/sing-box; ! start_pre'
else
    grep -Fqx 'ConditionPathExists=!box/admin-stopped' systemd/sing-box.service
fi
[[ ! -e active && ! -e enabled && ! -L runlevels/default/sing-box && $(cat cron) == "$UNRELATED" ]]
touch fail-start
if MENU=1 stclre; then exit 45; fi
[[ -e box/admin-stopped && ! -e active && $(cat cron) == "$UNRELATED" ]]
rm fail-start
MENU=1 stclre
[[ ! -e box/admin-stopped && -e active && $(wc -l < restarts) -eq 2 ]]
sub_service enabled sing-box
grep -Fxq '0 1 * * * /usr/bin/sing_box --maintenance' cron
grep -Fxq "$UNRELATED" cron
# Interrupt after the old unit has stopped, before legacy scheduling/boot entries
# are withdrawn, and after a requested start has re-enabled the service manager.
for SIGNAL in HUP INT TERM; do
    if [[ $BACKEND == openrc ]]; then rc-update add sing-box boot; fi
    echo '0 1 * * * systemctl restart sing-box;rc-service sing-box restart' >> cron
    for interrupted in stop enable; do
        rm -f signal-sent
        choice=2; [[ $interrupted != enable ]] || choice=1
        if INTERRUPT_AT=$interrupted MENU=$choice stclre; then exit 51; fi
        [[ -e signal-sent && -e box/admin-stopped && ! -e active && ! -e enabled ]]
        [[ ! -L runlevels/default/sing-box && ! -L runlevels/boot/sing-box ]]
        [[ $(cat cron) == "$UNRELATED" ]]
        singbox_maintenance
        [[ ! -e active ]]
    done
    MENU=1 stclre
done
"""
        for backend in ("systemd", "openrc"):
            with self.subTest(backend=backend):
                for name in ("active", "enabled", "restarts", "events"):
                    (self.root / name).unlink(missing_ok=True)
                (self.root / "cron").write_text(legacy + "\n" + unrelated + "\n")
                self.run_shell(body, env={"BACKEND": backend, "UNRELATED": unrelated, "MSYS": "winsymlinks:sys"})
