"""Reconcile only sing-box-owned firewall rules. Configuration and state are root private."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path("/etc/s-box")
MARK = "sing-box:managed"
TAGS = {"vless-sb": "tcp", "vmess-sb": "tcp", "anytls-sb": "tcp", "trojan-sb": "tcp",
        "snell-sb": "tcp", "hy2-sb": "udp", "tuic5-sb": "udp"}
UDP_TAGS = ("hy2-sb", "tuic5-sb")


def cmd(*args, check=True):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          env=dict(os.environ, LC_ALL="C"), check=check)


def nft_objects():
    return json.loads(cmd("nft", "-j", "-a", "list", "ruleset").stdout)["nftables"]


def backend():
    if shutil.which("firewall-cmd") and cmd("firewall-cmd", "--state", check=False).returncode == 0:
        return "firewalld"
    if shutil.which("ufw") and "Status: active" in cmd("ufw", "status").stdout:
        return "ufw"
    if shutil.which("nft"):
        native = [x["chain"] for x in nft_objects() if "chain" in x and "hook" in x["chain"]
                  and x["chain"]["table"] not in ("filter", "nat", "mangle", "raw", "security")]
        if native or not shutil.which("iptables") or (
            shutil.which("systemctl") and cmd("systemctl", "is-active", "--quiet", "nftables", check=False).returncode == 0
        ):
            return "nft"
    if shutil.which("iptables"):
        return "iptables"
    raise ValueError("未找到活动的受支持防火墙；请在主机防火墙中放行实际节点端口")


def valid_range(value):
    if not re.fullmatch(r"[1-9][0-9]{0,4}(:[1-9][0-9]{0,4})?", value):
        raise ValueError("端口格式无效")
    ends = [int(s) for s in value.split(":")]
    if not 1 <= ends[0] <= ends[-1] <= 65535:
        raise ValueError("端口范围无效")
    return value


def listeners():
    nodes = {}
    files = [ROOT / "sb.json"]
    if (ROOT / "sb_extra.json").is_file():
        files.append(ROOT / "sb_extra.json")
    for file in files:
        for node in json.loads(file.read_text())["inbounds"]:
            tag = node.get("tag")
            if tag not in TAGS:
                continue
            addr = ipaddress.ip_address(node.get("listen", "127.0.0.1"))
            if addr.is_loopback:
                continue
            destinations = [str(addr)]
            if str(addr) == "::":
                destinations = ["0.0.0.0", "::"] if Path("/proc/net/if_inet6").exists() else ["0.0.0.0"]
            nodes[tag] = (destinations, valid_range(str(node["listen_port"])))
    return nodes


def desired(state, active):
    rules = set()
    zone = ""
    if active == "firewalld":
        zone = state.get("zone", "")
        if not zone:
            zones = [line for line in cmd("firewall-cmd", "--get-active-zones").stdout.splitlines()
                     if line and not line[0].isspace()]
            if len(zones) > 1:
                raise ValueError("存在多个活动 zone，请用 enable --zone <公网接口所属 zone> 选择范围")
            zone = zones[0] if zones else cmd("firewall-cmd", "--get-default-zone").stdout.strip()
            state["zone"] = zone
    for tag, (addresses, port) in listeners().items():
        for address in addresses:
            if state["allow"]:
                rules.add((active, zone, address, TAGS[tag], port, ""))
            for ports in state["hops"].get(tag, []):
                rules.add((active, zone, address, "udp", valid_range(ports), port))
    return rules


def iptables_rule(rule, want):
    engine, _, addr, proto, ports, target = rule
    v6 = ":" in addr
    binary = "ip6tables" if v6 else "iptables"
    table = "nat" if target else "filter"
    chain = "PREROUTING" if target else "INPUT"
    if engine == "ufw" and not target:
        chain = "ufw6-user-input" if v6 else "ufw-user-input"
    prefix = [binary, "-w", "5", "-t", table]
    cmd(*prefix, "-S", chain)   # Distinguish inaccessible table from an absent rule.
    spec = ["-p", proto, "--dport", ports]
    if not ipaddress.ip_address(addr).is_unspecified:
        spec += ["-d", addr]
    if target:
        spec += ["-m", "addrtype", "--dst-type", "LOCAL"]
    spec += ["-m", "comment", "--comment", MARK]
    spec += ["-j", "REDIRECT", "--to-ports", target] if target else ["-j", "ACCEPT"]
    exists = cmd(*prefix, "-C", chain, *spec, check=False)
    if exists.returncode not in (0, 1):
        raise ValueError("防火墙规则查询失败")
    if (exists.returncode == 0) == want:
        return False
    cmd(*prefix, "-I" if want else "-D", chain, *spec)
    return True


def firewalld_rule(rule, want, fresh=False):
    _, zone, addr, proto, ports, target = rule
    family = "ipv6" if ":" in addr else "ipv4"
    # Reserve this priority for managed rules. No zone port or unrelated forwarding rule is removed.
    rich = f'rule family="{family}" priority="-17461" '
    if not ipaddress.ip_address(addr).is_unspecified:
        rich += f'destination address="{addr}" '
    ports = ports.replace(":", "-")
    rich += (f'forward-port port="{ports}" protocol="udp" to-port="{target}"' if target
             else f'port port="{ports}" protocol="{proto}" accept')
    changed = []
    probes = []
    try:
        for options in (["--permanent"], []):
            prefix = ["firewall-cmd", *options, "--zone=" + zone]
            query = cmd(*prefix, "--query-rich-rule=" + rich, check=False)
            if query.returncode not in (0, 1):
                raise ValueError("firewalld 规则查询失败")
            exists = query.returncode == 0
            if fresh and exists:
                raise ValueError("firewalld 已有相同优先级和内容的规则；请管理员核对归属")
            probes.append((prefix, exists))
        for prefix, exists in probes:
            if exists != want:
                cmd(*prefix, ("--add-rich-rule=" if want else "--remove-rich-rule=") + rich)
                changed.append(prefix)
    except Exception:
        for prefix in reversed(changed):
            cmd(*prefix, ("--remove-rich-rule=" if want else "--add-rich-rule=") + rich)
        raise
    return bool(changed)


def nft_rule(rule, want):
    _, _, addr, proto, ports, target = rule
    family = "ip6" if ":" in addr else "ip"
    objects = nft_objects()
    marker = f"{MARK} {addr} {proto} {ports} {target or 'accept'}"
    existing = [x["rule"] for x in objects if x.get("rule", {}).get("comment") == marker]
    if not want:
        for r in existing:
            cmd("nft", "delete", "rule", r["family"], r["table"], r["chain"], "handle", str(r["handle"]))
        if target:
            # Keep tables extended by the administrator. Older nft JSON omits table comments,
            # so identify ours by its explicit sentinel rule in the project-owned chain.
            entries = [item for obj in nft_objects() for item in obj.values()
                       if isinstance(item, dict) and item.get("family") == family and
                       (item.get("table") == "sbox_hopping" or item.get("name") == "sbox_hopping")]
            if len(entries) == 3 and any(e.get("chain") == "prerouting" and e.get("comment") == MARK
                                         for e in entries) and any(e.get("name") == "prerouting" for e in entries):
                cmd("nft", "delete", "table", family, "sbox_hopping")
        return bool(existing)
    if existing:
        return False
    setup = ""
    if target:
        tables = [x["table"] for x in objects if "table" in x and x["table"]["family"] == family
                  and x["table"]["name"] == "sbox_hopping"]
        sentinel = any(x.get("rule", {}).get("family") == family and x["rule"].get("table") == "sbox_hopping"
                       and x["rule"].get("chain") == "prerouting" and x["rule"].get("comment") == MARK for x in objects)
        if tables and not sentinel:
            raise ValueError("nftables 的 sbox_hopping 表已有其他所有者")
        if not tables:
            setup = (f'add table {family} sbox_hopping; '
                     f'add chain {family} sbox_hopping prerouting {{ type nat hook prerouting priority -99; }}; '
                     f'add rule {family} sbox_hopping prerouting counter comment "{MARK}"; ')
        chain = {"family": family, "table": "sbox_hopping", "name": "prerouting"}
    else:
        chains = [x["chain"] for x in objects if "chain" in x and x["chain"].get("hook") == "input"
                  and x["chain"]["family"] in ("inet", family) and x["chain"].get("type") == "filter"]
        if not chains:
            return False   # Native nft has no input filter in this family.
        if len(chains) != 1:
            raise ValueError(f"存在多个 {family} input 基础链，请由防火墙管理员指定放行位置")
        chain = chains[0]
    terms = []
    if chain["family"] == "inet":
        terms += ["meta", "nfproto", "ipv6" if family == "ip6" else "ipv4"]
    if not ipaddress.ip_address(addr).is_unspecified:
        terms += [family, "daddr", addr]
    if target:
        terms += ["fib", "daddr", "type", "local"]
    terms += [proto, "dport", ports.replace(":", "-")]
    terms += ["redirect", "to", ":" + target] if target else ["accept"]
    terms += ["comment", json.dumps(marker)]
    if setup:
        cmd("nft", setup + f"insert rule {family} sbox_hopping prerouting " + " ".join(terms))
    else:
        cmd("nft", "insert", "rule", chain["family"], chain["table"], chain["name"], *terms)
    return True


def change(rule, want, fresh=False):
    if rule[0] == "firewalld":
        return firewalld_rule(rule, want, fresh)
    if rule[0] == "nft":
        return nft_rule(rule, want)
    return iptables_rule(rule, want)


def save(state):
    with tempfile.NamedTemporaryFile("w", dir=ROOT, delete=False) as f:
        json.dump(state, f)
    os.replace(f.name, ROOT / "firewall.json")


def manage(action, tag=None, ports=None, zone=None):
    path = ROOT / "firewall.json"
    if action in ("list", "sync", "remove", "clear") and not path.exists():
        return
    existed = path.exists()
    state = json.loads(path.read_text()) if existed else {"allow": False, "hops": {}, "rules": []}
    if action == "list":
        print(",".join(state["hops"].get(tag, [])))
        return
    old = {tuple(r) for r in state["rules"]}
    before = json.loads(json.dumps(state))
    if action == "enable":
        state["allow"] = True
        if zone:
            state["zone"] = zone
    elif action in ("add", "clear"):
        if tag not in UDP_TAGS:
            raise ValueError("端口跳跃仅支持 Hysteria2 和 TUIC")
        if action == "add":
            if tag not in listeners():
                raise ValueError("目标 UDP 入站未启用")
            hops = state["hops"].setdefault(tag, [])
            if valid_range(ports) not in hops:
                hops.append(ports)
        else:
            state["hops"].pop(tag, None)
    needed = desired(state, backend()) if action != "remove" else set()
    # A journal contains every rule possibly in flight. After interruption, sync converges by exact rule.
    state["rules"] = sorted(needed | old)
    save(state)
    changed = []
    try:
        for want, rules in [(True, needed), (False, old - needed)]:
            for rule in sorted(rules):
                if change(rule, want, fresh=want and rule not in old):
                    changed.append((rule, want))
    except Exception:
        for rule, want in reversed(changed):
            change(rule, not want)
        if existed:
            save(before)
        else:
            path.unlink()
        raise
    state["rules"] = sorted(needed)
    if action == "remove":
        path.unlink()
    else:
        save(state)


def main():
    import fcntl

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("enable", "sync", "remove", "add", "clear", "list"))
    parser.add_argument("tag", nargs="?", choices=UDP_TAGS)
    parser.add_argument("ports", nargs="?")
    parser.add_argument("--zone", help="firewalld zone for enable")
    args = parser.parse_args()
    if not (ROOT / "firewall.json").exists() and args.action in ("list", "sync", "remove", "clear"):
        return
    with (ROOT / ".firewall.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manage(args.action, args.tag, args.ports, args.zone)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"sing-box 防火墙操作失败：{exc}", file=sys.stderr)
        sys.exit(1)
