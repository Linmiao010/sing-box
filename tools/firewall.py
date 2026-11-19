"""Reconcile only sing-box-owned firewall rules. Configuration and state are root private."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
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
    return json.loads(cmd("nft", "-j", "-n", "-a", "list", "ruleset").stdout)["nftables"]


def backend():
    if shutil.which("firewall-cmd") and cmd("firewall-cmd", "--state", check=False).returncode == 0:
        return "firewalld"
    if shutil.which("ufw") and "Status: active" in cmd("ufw", "status").stdout:
        return "ufw"
    if shutil.which("nft"):
        native = [x["chain"] for x in nft_objects() if "chain" in x and "hook" in x["chain"]
                  and (x["chain"].get("family") == "inet" or
                       x["chain"]["table"] not in ("filter", "nat", "mangle", "raw", "security", "sbox_hopping"))]
        if native or not shutil.which("iptables") or (
            shutil.which("systemctl") and cmd("systemctl", "is-active", "--quiet", "nftables", check=False).returncode == 0
        ):
            return "nft"
    if shutil.which("iptables"):
        return "iptables"
    raise ValueError("未找到活动的受支持防火墙；请在主机防火墙中放行实际节点端口")


def valid_range(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,4}(:[1-9][0-9]{0,4})?", value):
        raise ValueError("端口格式无效")
    ends = [int(s) for s in value.split(":")]
    if not 1 <= ends[0] <= ends[-1] <= 65535:
        raise ValueError("端口范围无效")
    return value


def listeners(include_loopback=False):
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
            if addr.is_loopback and not include_loopback:
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
            if state["allow"] and (active == "firewalld" or state.get("append_backend") == active):
                rules.add((active, zone, address, TAGS[tag], port, ""))
            for ports in state["hops"].get(tag, []):
                rules.add((active, zone, address, "udp", valid_range(ports), port))
    return rules


def interval(ports):
    first, _, last = valid_range(str(ports)).partition(":")
    return int(first), int(last or first)


def destination(addr):
    net = ipaddress.ip_network(addr, strict=False)
    if net.prefixlen == net.max_prefixlen and net.network_address.is_unspecified:
        return net.supernet(new_prefix=0)
    if net.version == 6 and net.prefixlen == 128 and net.network_address.ipv4_mapped:
        return destination(str(net.network_address.ipv4_mapped))
    return net


def udp_sockets():
    # Query each family separately. A UDP6 wildcard may also hold the IPv4 port.
    for version in (4, 6):
        listing = cmd("ss", "-" + str(version), "-H", "-a", "-n", "-u", "-e")
        if listing.stderr.strip():
            raise ValueError(f"ss 报告读取 IPv{version} socket 信息不完整，请手动核对")
        rows = listing.stdout.splitlines()
        for row in rows:
            fields = row.split()
            if len(fields) < 5:
                raise ValueError("无法解析 ss 的 UDP 监听结果，请手动核对")
            host, _, port = fields[3].rpartition(":")
            host = host.strip("[]").split("%")[0]
            if host == "*":
                host = "::" if version == 6 else "0.0.0.0"
            yield destination(host), interval(port), "宿主 UDP socket"
            if version == 6 and host == "::" and "v6only:1" not in fields:
                yield destination("0.0.0.0"), interval(port), "宿主双栈 UDP socket"


def iptables_forwards(binary, version, old):
    any_addr = "::" if version == 6 else "0.0.0.0"
    for line in cmd(binary, "-w", "5", "-t", "nat", "-S").stdout.splitlines():
        words = shlex.split(line)
        def option(*names):
            for i, word in enumerate(words[:-1]):
                if word in names and (i == 0 or words[i - 1] != "!"):
                    return words[i + 1]
            return ""
        if option("-j", "--jump") not in ("DNAT", "REDIRECT", "NETMAP"):
            continue
        if option("-p", "--protocol") not in ("", "all", "0", "17", "udp"):
            continue
        addr = option("-d", "--destination") or any_addr
        ports = option("--dport", "--dports", "--destination-port", "--destination-ports") or "1:65535"
        target = option("--to-ports")
        # Exclude only a journaled redirect with its managed match, address, port and target.
        if (option("--comment") == MARK and option("-j") == "REDIRECT" and
                option("-A") == "PREROUTING" and option("--dst-type") == "LOCAL" and
                any(r[0] in ("iptables", "ufw") and destination(r[2]) == destination(addr)
                    and r[3:] == ("udp", ports, target) for r in old)):
            continue
        for part in ports.split(","):
            # xtables accepts open ranges and port zero. The project reserves only 1..65535.
            lo, sep, hi = part.partition(":")
            first = int(lo or 0)
            last = int(hi or 65535) if sep else first
            to = option("--to-destination", "--to")
            if firewalld_owns(old, option("-A"), destination(addr), (first, last),
                              target or (to[1:] if re.fullmatch(r":[0-9]+", to) else "")):
                continue
            yield destination(addr), (first, last), binary + " " + (option("-A") or "NAT")


def firewalld_owns(old, chain, addr, pair, target):
    # firewalld compiles our rich rule into its zone-specific pre chain, without a comment.
    return any(r[0] == "firewalld" and r[-1] and str(target) == r[-1] and
               chain in (f"nat_PRE_{r[1]}_pre", f"PRE_{r[1]}_pre") and
               destination(r[2]) == addr and interval(r[4]) == pair for r in old)


def nft_port_intervals(value):
    if isinstance(value, int):
        return [(value, value)]
    if isinstance(value, dict):
        if "elem" in value:
            return nft_port_intervals(value["elem"].get("val"))
        if "range" in value and len(value["range"]) == 2 and all(isinstance(i, int) for i in value["range"]):
            return [tuple(value["range"])]
        if isinstance(value.get("set"), list):
            return [pair for item in value["set"] for pair in nft_port_intervals(item)]
    # Named sets/maps or unsupported expressions may match any port.
    return [(1, 65535)]


def nft_elements(value, rule, named):
    if isinstance(value, str) and value.startswith("@"):
        return named.get((rule["family"], rule["table"], value[1:]))
    if isinstance(value, dict) and "set" in value:
        return value["set"]
    return None


def nft_map_keys(nat, rule, named):
    """Return destination-address/port constraints from a supported DNAT lookup key.

    An opaque lookup covers every port. A known empty map has no matches.
    """
    for value in (nat.get("addr"), nat.get("port")):
        lookup = value.get("map") if isinstance(value, dict) else None
        if not isinstance(lookup, dict):
            continue
        key = lookup.get("key")
        keys = key.get("concat", [key]) if isinstance(key, dict) else []
        fields = [k.get("payload", {}) if isinstance(k, dict) else {} for k in keys]
        if any(p.get("protocol") == "tcp" for p in fields):
            return []
        entries = nft_elements(lookup.get("data"), rule, named)
        if entries is None:
            continue
        result = []
        for item in entries:
            if not isinstance(item, list) or len(item) != 2:
                return [(None, (1, 65535))]
            match = item[0]
            if isinstance(match, dict) and "elem" in match:
                match = match["elem"].get("val")
            values = match.get("concat", [match]) if isinstance(match, dict) else [match]
            if len(values) != len(fields):
                return [(None, (1, 65535))]
            addr, ports = None, [(1, 65535)]
            for p, v in zip(fields, values):
                if p.get("protocol") in ("udp", "th") and p.get("field") == "dport":
                    ports = nft_port_intervals(v)
                if p.get("protocol") in ("ip", "ip6") and p.get("field") == "daddr":
                    if isinstance(v, dict) and "prefix" in v:
                        v = f"{v['prefix']['addr']}/{v['prefix']['len']}"
                    if isinstance(v, str):
                        addr = destination(v)
            result.extend((addr, pair) for pair in ports)
        return result
    return [(None, (1, 65535))]


def nft_forwards(old):
    objects = nft_objects()
    named = {}
    for obj in objects:
        for key in ("set", "map", "element"):
            if key in obj:
                table = obj[key]
                entries = table.get("elem", [])
                named.setdefault((table["family"], table["table"], table["name"]), []).extend(
                    entries if isinstance(entries, list) else [entries])
    for obj in objects:
        rule = obj.get("rule", {})
        expr = rule.get("expr", [])
        if not any("dnat" in e or "redirect" in e or e.get("xt", {}).get("name") in
                   ("DNAT", "REDIRECT", "NETMAP") for e in expr):
            continue
        families = {"ip": {4}, "ip6": {6}, "inet": {4, 6}}.get(rule["family"], {4, 6})
        ports = [(1, 65535)]
        keys = [(None, (1, 65535))]
        addrs = {}
        target = None
        for e in expr:
            nat = e.get("redirect") or e.get("dnat")
            if isinstance(nat, dict):
                keys = nft_map_keys(nat, rule, named)
                if nat.get("family") in ("ip", "ip6"):
                    families &= {4 if nat["family"] == "ip" else 6}
                if "addr" not in nat:
                    target = nat.get("port")
            match = e.get("match", {})
            left, right = match.get("left", {}), match.get("right")
            if match.get("op") not in ("==", "in") or not isinstance(left, dict):
                continue
            payload = left.get("payload", {})
            proto, field = payload.get("protocol"), payload.get("field")
            if (proto == "tcp" or (left.get("meta") == {"key": "l4proto"} and right in ("tcp", 6))
                    or (proto in ("ip", "ip6") and field in ("protocol", "nexthdr") and right in ("tcp", 6))):
                families = set()
            if left.get("meta") == {"key": "nfproto"} and right in ("ipv4", "ipv6", 2, 10):
                families &= {4 if right in ("ipv4", 2) else 6}
            if proto in ("udp", "th") and field == "dport":
                elems = nft_elements(right, rule, named)
                ports = nft_port_intervals({"set": elems} if elems is not None else right)
            if proto in ("ip", "ip6") and field == "daddr":
                version = 4 if proto == "ip" else 6
                families &= {version}
                if isinstance(right, dict) and "prefix" in right:
                    prefix = right["prefix"]
                    right = f"{prefix['addr']}/{prefix['len']}"
                if isinstance(right, str) and not right.startswith("@"):
                    addrs[version] = destination(right)
        for version in families:
            net = addrs.get(version, destination("::" if version == 6 else "0.0.0.0"))
            for addr, (lo, hi) in keys:
                if addr and (addr.version != version or not net.overlaps(addr)):
                    continue
                matched = addr if addr and addr.prefixlen > net.prefixlen else net
                for first, last in ports:
                    pair = max(lo, first), min(hi, last)
                    if pair[0] > pair[1] or (rule.get("table") == "firewalld" and
                            firewalld_owns(old, rule["chain"], matched, pair, target)):
                        continue
                    # A shared comment alone cannot establish ownership of a redirect.
                    if any(r[-1] and str(target) == r[-1] and interval(r[4]) == pair and
                           destination(r[2]) == matched and (
                               (r[0] == "nft" and rule["table"] == "sbox_hopping" and rule["chain"] == "prerouting"
                                and rule.get("comment") == f"{MARK} {r[2]} udp {r[4]} {r[5]}") or
                               (r[0] in ("iptables", "ufw") and rule["table"] == "nat" and
                                rule["chain"] == "PREROUTING" and rule.get("comment") == MARK)) for r in old):
                        continue
                    yield matched, pair, f"nft {rule['table']}/{rule['chain']}"


def forwarding_ranges(active, old, versions):
    seen = set()
    if active == "nft" or shutil.which("nft"):
        yield from nft_forwards(old)
        seen.update((4, 6))
    for version in versions:
        binary = "ip6tables" if version == 6 else "iptables"
        candidates = [binary] if active in ("iptables", "ufw") or shutil.which(binary) else []
        tables = Path("/proc/net/" + ("ip6" if version == 6 else "ip") + "_tables_names")
        if tables.exists() and "nat" in tables.read_text().splitlines():
            if shutil.which(binary + "-legacy"):
                candidates.append(binary + "-legacy")
            elif not candidates or "nf_tables" in cmd(binary, "--version").stdout:
                raise ValueError(f"存在活动的 IPv{version} 传统 NAT 表，缺少 {binary}-legacy 读取其转发")
        for name in candidates:
            yield from iptables_forwards(name, version, old)
            seen.add(version)
    if versions - seen:
        raise ValueError("无法读取该地址族的现有 NAT 转发，请手动核对并配置端口跳跃")


def check_hops(state, old, active):
    occupied = []
    hops = []
    for tag, (addresses, port) in listeners(include_loopback=True).items():
        if tag in UDP_TAGS:
            occupied.extend((destination(a), interval(port), tag) for a in addresses)
            hops.extend((destination(a), interval(span), tag) for a in addresses
                        if not ipaddress.ip_address(a).is_loopback for span in state["hops"].get(tag, []))
    occupied.extend(udp_sockets())
    occupied.extend(forwarding_ranges(active, old, {net.version for net, _, _ in hops}))
    for net, (first, last), tag in hops:
        for addr, (low, high), owner in occupied:
            start, end = max(first, low), min(last, high)
            if start <= end and addr.version == net.version and addr.overlaps(net):
                span = str(start) if start == end else f"{start}:{end}"
                raise ValueError(f"UDP 跳跃冲突 {addr} 与 {net}，端口 {span}（{owner}）")
        occupied.append((net, (first, last), tag + " 已配置跳跃"))


def filter_needs_reposition(rule):
    engine, _, addr, _, _, _ = rule
    if engine == "nft":
        objects = nft_objects()
        family = "ip6" if ":" in addr else "ip"
        chains = [c["chain"] for c in objects if c.get("chain", {}).get("hook") == "input"
                  and c["chain"].get("type") == "filter" and c["chain"]["family"] in ("inet", family)]
        if not chains:
            return False
        if len(chains) != 1:
            raise ValueError(f"无法唯一确定 {family} input 基础链，请在主机防火墙手动放行")
        chain = chains[0]
        flags = [r["rule"].get("comment", "").startswith(MARK + " ") for r in objects if "rule" in r
                 and (r["rule"]["family"], r["rule"]["table"], r["rule"]["chain"]) ==
                     (chain["family"], chain["table"], chain["name"])]
    else:
        v6 = ":" in addr
        chain = ("ufw6-user-input" if v6 else "ufw-user-input") if engine == "ufw" else "INPUT"
        lines = cmd("ip6tables" if v6 else "iptables", "-w", "5", "-t", "filter", "-S", chain).stdout.splitlines()
        flags = [MARK in shlex.split(line) for line in lines if line.startswith("-A ")]
    return any(flags) and not all(flags[flags.index(True):])


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
    cmd(*prefix, "-A" if want else "-D", chain, *spec)
    return True


class IncompleteChange(ValueError):
    """A single backend operation changed only part of its runtime/persistent state."""


def firewalld_rule(rule, want, fresh=False, check_only=False):
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
        if check_only:
            return False
        for prefix, exists in probes:
            if exists != want:
                cmd(*prefix, ("--add-rich-rule=" if want else "--remove-rich-rule=") + rich)
                changed.append(prefix)
    except Exception as original:
        failures = []
        for prefix in reversed(changed):
            try:
                cmd(*prefix, ("--remove-rich-rule=" if want else "--add-rich-rule=") + rich)
            except Exception as exc:
                failures.append(str(exc))
        if failures:
            raise IncompleteChange("firewalld 运行或持久规则回滚失败：" + "；".join(failures)) from original
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
            raise ValueError(f"存在多个 {family} input 基础链，请在主机防火墙手动放行")
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
        cmd("nft", setup + f"add rule {family} sbox_hopping prerouting " + " ".join(terms))
    else:
        cmd("nft", "add", "rule", chain["family"], chain["table"], chain["name"], *terms)
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


def manage(action, tag=None, ports=None, zone=None, input_position=None):
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
    active = backend() if action != "remove" else None
    if action == "enable":
        if active != "firewalld":
            if input_position == "append":
                state["append_backend"] = active
            if state.get("append_backend") != active:
                raise ValueError("请核对既有限制、拒绝规则及默认策略；确认链尾位置后传 --input-position append，或手动放行")
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
    needed = desired(state, active) if active else set()
    if active and active != "firewalld" and state["allow"] and state.get("append_backend") != active:
        print("尚未确认新后端的放行位置，已登记的旧放行条目将撤下；请核实链尾或手动放行", file=sys.stderr)
    if any(rule[-1] for rule in needed):
        check_hops(state, old, active)
    if active == "firewalld":
        for rule in needed - old:
            firewalld_rule(rule, True, fresh=True, check_only=True)
    # A journal contains every rule possibly in flight. After interruption, sync converges by exact rule.
    state["rules"] = sorted(needed | old)
    save(dict(state, append_backend=before.get("append_backend")))
    changed = []
    try:
        # A first explicit confirmation upgrades legacy head-inserted allows.
        # Remove these exact entries first so the existence test cannot retain their position.
        if active in ("iptables", "ufw", "nft"):
            for rule in sorted(old & needed):
                if (rule[0] == active and not rule[-1] and (before.get("append_backend") != active
                        or filter_needs_reposition(rule)) and change(rule, False)):
                    changed.append((rule, False))
        stale = old - needed
        # UFW and iptables program the same NAT chain, with the same rule signature.
        if active in ("iptables", "ufw"):
            stale = {r for r in stale if not (r[0] in ("iptables", "ufw") and r[-1]
                     and (active, *r[1:]) in needed)}
        for want, rules in [(True, needed), (False, stale)]:
            for rule in sorted(rules):
                if change(rule, want, fresh=want and rule not in old):
                    changed.append((rule, want))
        state["rules"] = sorted(needed)
        if action == "remove":
            path.unlink()
        else:
            save(state)
    except Exception as original:
        failures = [str(original)] if isinstance(original, IncompleteChange) else []
        uncertain = {rule} if failures else set()
        for rule, want in reversed(changed):
            try:
                change(rule, not want)
            except Exception as exc:
                failures.append(str(exc))
                uncertain.add(rule)
        if failures:
            # A failed ownership check cannot make an administrator's rule ours.
            save(dict(before, rules=sorted(old | uncertain)))
            raise ValueError("防火墙回滚未完成，已记录原策略，请修复后运行 sync：" + "；".join(failures)) from original
        if existed:
            save(before)
        else:
            path.unlink()
        raise


def main():
    import fcntl

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("enable", "sync", "remove", "add", "clear", "list"))
    parser.add_argument("tag", nargs="?", choices=UDP_TAGS)
    parser.add_argument("ports", nargs="?")
    parser.add_argument("--zone", help="firewalld zone for enable")
    parser.add_argument("--input-position", choices=("append",), help="管理员确认在现有过滤规则后追加放行")
    args = parser.parse_args()
    if not (ROOT / "firewall.json").exists() and args.action in ("list", "sync", "remove", "clear"):
        return
    with (ROOT / ".firewall.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manage(args.action, args.tag, args.ports, args.zone, args.input_position)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"sing-box 防火墙操作失败：{exc}", file=sys.stderr)
        sys.exit(1)
