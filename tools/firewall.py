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
FORWARD_CONFIRMATION = ("firewalld UDP 端口跳跃可能因 DNAT 提前放行并跳过区域来源封禁。请核实 "
                        "StrictForwardPorts 及该 zone 的实际策略，再用 enable --input-position append "
                        "--confirm-forward-ports 明确授权，或由管理员手动配置")
FIREWALLD_UNICAST = ("firewalld 具体地址的端口跳跃已暂停。请手动配置保留该目的 IP 的本机端口转换；"
                      "firewalld 的 to-addr 会启用主机 IP 转发")


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
            if addr.version == 6 and addr.ipv4_mapped:
                addr = addr.ipv4_mapped
            if addr.is_loopback and not include_loopback:
                continue
            destinations = [str(addr)]
            if str(addr) == "::":
                destinations = ["0.0.0.0", "::"] if Path("/proc/net/if_inet6").exists() else ["0.0.0.0"]
            nodes[tag] = (destinations, valid_range(str(node["listen_port"])))
    return nodes


def firewalld_zone(state):
    if state.get("zone"):
        return state["zone"]
    zones = [line for line in cmd("firewall-cmd", "--get-active-zones").stdout.splitlines()
             if line and not line[0].isspace()]
    if len(zones) > 1:
        raise ValueError("存在多个活动 zone，请用 enable --zone <公网接口所属 zone> 选择范围")
    return zones[0] if zones else cmd("firewall-cmd", "--get-default-zone").stdout.strip()


def desired(state, active):
    rules = set()
    zone = state["zone"] if active == "firewalld" else ""
    allow = state["allow"] and state.get("append_backend") == active
    for tag, (addresses, port) in listeners().items():
        for address in addresses:
            if allow:
                rules.add((active, zone, address, TAGS[tag], port, ""))
            if active == "firewalld" and not (allow and state.get("forward_zone") == zone):
                continue
            if active == "firewalld" and state["hops"].get(tag) and not ipaddress.ip_address(address).is_unspecified:
                print(FIREWALLD_UNICAST, file=sys.stderr)
                continue
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
    owned = []
    for r in old:
        if not r[5] or (":" in r[2]) != (version == 6):
            continue
        if r[0] in ("firewalld", "firewalld-legacy"):
            owned.append((firewalld_nat_chain(r), normalized_iptables_spec(iptables_spec(r))))
        elif r[0].split("-")[0] in ("iptables", "ufw"):
            owned.append(("PREROUTING", normalized_iptables_spec(iptables_spec(r))))
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
        signature = (option("-A"), normalized_iptables_spec(words[2:]))
        if signature in owned:
            owned.remove(signature)
            continue
        for part in ports.split(","):
            # xtables accepts open ranges and port zero. The project reserves only 1..65535.
            lo, sep, hi = part.partition(":")
            first = int(lo or 0)
            last = int(hi or 65535) if sep else first
            yield destination(addr), (first, last), binary + " " + (option("-A") or "NAT")


def firewalld_nat_chain(rule):
    return f"PRE_{rule[1]}_" + ("pre" if rule[0] == "firewalld-legacy" else "post")


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
    journal = {r for r in old if r[5]}
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
        owned = next((r for r in journal if nft_nat_location(r, rule) and nft_matches(r, rule)), None)
        if owned:
            journal.remove(owned)
            continue
        families = {"ip": {4}, "ip6": {6}, "inet": {4, 6}}.get(rule["family"], {4, 6})
        ports = [(1, 65535)]
        keys = [(None, (1, 65535))]
        addrs = {}
        for e in expr:
            nat = e.get("redirect") or e.get("dnat")
            if isinstance(nat, dict):
                keys = nft_map_keys(nat, rule, named)
                if nat.get("family") in ("ip", "ip6"):
                    families &= {4 if nat["family"] == "ip" else 6}
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
                    if pair[0] <= pair[1]:
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


def check_hops(needed, old, active):
    occupied = []
    hops = []
    for tag, (addresses, port) in listeners(include_loopback=True).items():
        if tag in UDP_TAGS:
            occupied.extend((destination(a), interval(port), tag) for a in addresses)
            hops.extend((destination(r[2]), interval(r[4]), tag) for r in sorted(needed)
                        if r[5] == port and r[2] in addresses)
    occupied.extend(udp_sockets())
    occupied.extend(forwarding_ranges(active, old, {net.version for net, _, _ in hops}))
    for net, (first, last), tag in hops:
        for addr, (low, high), owner in occupied:
            start, end = max(first, low), min(last, high)
            if start <= end and addr.version == net.version and addr.overlaps(net):
                span = str(start) if start == end else f"{start}:{end}"
                raise ValueError(f"UDP 跳跃冲突 {addr} 与 {net}，端口 {span}（{owner}）")
        occupied.append((net, (first, last), tag + " 已配置跳跃"))


def filter_needs_reposition(rule, recorded):
    engine, _, addr, _, _, _ = rule
    managed = [r for r in recorded if r[0] == engine and not r[5] and (":" in r[2]) == (":" in addr)]
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
        flags = [any(nft_matches(m, r["rule"]) for m in managed) for r in objects if "rule" in r
                 and (r["rule"]["family"], r["rule"]["table"], r["rule"]["chain"]) ==
                     (chain["family"], chain["table"], chain["name"])]
    else:
        v6 = ":" in addr
        chain = ("ufw6-user-input" if v6 else "ufw-user-input") if engine == "ufw" else "INPUT"
        lines = cmd("ip6tables" if v6 else "iptables", "-w", "5", "-t", "filter", "-S", chain).stdout.splitlines()
        signatures = [normalized_iptables_spec(iptables_spec(r)) for r in managed]
        flags = [normalized_iptables_spec(shlex.split(line)[2:]) in signatures for line in lines if line.startswith("-A ")]
    return any(flags) and not all(flags[flags.index(True):])


def specific_dnat(rule):
    return bool(rule[5] and not rule[0].endswith("-legacy") and not ipaddress.ip_address(rule[2]).is_unspecified)


def nat_endpoint(addr, port):
    return f"[{addr}]:{port}" if ":" in addr else f"{addr}:{port}"


def iptables_spec(rule):
    _, _, addr, proto, ports, target = rule
    spec = ["-p", proto, "--dport", ports]
    if not ipaddress.ip_address(addr).is_unspecified:
        spec += ["-d", addr]
    if rule[0].startswith("firewalld"):
        return spec + ["-j", "DNAT", "--to-destination", ":" + target]
    if target:
        spec += ["-m", "addrtype", "--dst-type", "LOCAL"]
    spec += ["-m", "comment", "--comment", MARK]
    if specific_dnat(rule):
        return spec + ["-j", "DNAT", "--to-destination", nat_endpoint(addr, target)]
    return spec + (["-j", "REDIRECT", "--to-ports", target] if target else ["-j", "ACCEPT"])


def normalized_iptables_spec(words):
    # iptables -S may reorder matches, print host prefixes and add the implicit UDP module.
    if len(words) % 2 or "!" in words:
        return None
    pairs = []
    for opt, value in zip(words[::2], words[1::2]):
        if opt == "-m" and value in ("tcp", "udp") and ("-p", value) in zip(words[::2], words[1::2]):
            continue
        if opt == "-d":
            value = str(ipaddress.ip_network(value, strict=False))
        pairs.append(("--to-destination" if opt == "--to" else opt, value))
    return sorted(pairs)


def iptables_rule(rule, want, fresh=False, check_only=False):
    engine, _, addr, _, _, target = rule
    v6 = ":" in addr
    binary = "ip6tables" if v6 else "iptables"
    table = "nat" if target else "filter"
    chain = "PREROUTING" if target else "INPUT"
    if engine == "ufw" and not target:
        chain = "ufw6-user-input" if v6 else "ufw-user-input"
    prefix = [binary, "-w", "5", "-t", table]
    cmd(*prefix, "-S", chain)   # Distinguish inaccessible table from an absent rule.
    spec = iptables_spec(rule)
    exists = cmd(*prefix, "-C", chain, *spec, check=False)
    if exists.returncode not in (0, 1):
        raise ValueError("防火墙规则查询失败")
    if fresh and exists.returncode == 0:
        raise ValueError("已有相同的防火墙规则；请管理员核对归属")
    if check_only or (exists.returncode == 0) == want:
        return False
    cmd(*prefix, "-A" if want else "-D", chain, *spec)
    return True


class IncompleteChange(ValueError):
    """A single backend operation changed only part of its runtime/persistent state."""


def firewalld_rule(rule, want, fresh=False, check_only=False, levels=(True, False)):
    _, zone, addr, proto, ports, target = rule
    family = "ipv6" if ":" in addr else "ipv4"
    # Reserve this priority for managed rules. No zone port or unrelated forwarding rule is removed.
    priority = -17461 if rule[0] == "firewalld-legacy" else 32767
    rich = f'rule family="{family}" priority="{priority}" '
    if not ipaddress.ip_address(addr).is_unspecified:
        rich += f'destination address="{addr}" '
    ports = ports.replace(":", "-")
    rich += (f'forward-port port="{ports}" protocol="udp" to-port="{target}"' if target
             else f'port port="{ports}" protocol="{proto}" accept')
    changed = []
    probes = []
    try:
        for permanent in levels:
            options = ["--permanent"] if permanent else []
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
    return tuple("--permanent" in prefix for prefix in changed)


def nft_nat_location(rule, entry):
    where = entry["table"], entry["chain"]
    if rule[0].startswith("firewalld"):
        chain = firewalld_nat_chain(rule)
        return where in (("firewalld", "nat_" + chain), ("nat", chain))
    if rule[0] in ("nft", "nft-legacy"):
        return where == ("sbox_hopping", "prerouting")
    return where == ("nat", "PREROUTING")


def nft_matches(rule, entry):
    """Match the journaled rule's family, marker, matches and verdict before touching its handle."""
    _, _, addr, proto, ports, target = rule
    family = "ip6" if ":" in addr else "ip"
    firewalld = rule[0].startswith("firewalld")
    xtables = rule[0].split("-")[0] in ("iptables", "ufw") or (firewalld and entry["table"] == "nat")
    marker = "" if firewalld else MARK if xtables else f"{MARK} {addr} {proto} {ports} {target or 'accept'}"
    if entry.get("comment", "") != marker or entry["family"] not in (family, "inet"):
        return False
    if target and not firewalld and entry["family"] != family:
        return False
    expected = []
    if entry["family"] == "inet":
        expected.append(("family", family))
    if not ipaddress.ip_address(addr).is_unspecified:
        expected.append(("address", str(ipaddress.ip_address(addr))))
    if target and not firewalld:
        expected.append(("local", True))
    expected.extend([("ports", interval(ports)), ("dnat" if specific_dnat(rule) else "redirect" if target else "accept",
                     nat_endpoint(addr, target) if specific_dnat(rule) else target)])
    observed = []
    opaque = False
    for item in entry.get("expr", []):
        if "match" in item:
            m = item["match"]
            left, right = m.get("left"), m.get("right")
            if m.get("op") not in ("==", "in") or not isinstance(left, dict):
                return False
            if left == {"meta": {"key": "nfproto"}} and right in ("ipv4", "ipv6", 2, 10):
                observed.append(("family", "ip" if right in ("ipv4", 2) else "ip6"))
            elif left == {"payload": {"protocol": family, "field": "daddr"}} and isinstance(right, str):
                observed.append(("address", str(ipaddress.ip_address(right))))
            elif left.get("fib") in ({"result": "type", "flags": ["daddr"]}, {"result": "type", "flags": "daddr"}) and right in ("local", 2):
                observed.append(("local", True))
            elif left == {"payload": {"protocol": proto, "field": "dport"}}:
                if isinstance(right, int):
                    observed.append(("ports", (right, right)))
                elif isinstance(right, dict) and isinstance(right.get("range"), list):
                    observed.append(("ports", tuple(right["range"])))
                else:
                    return False
            elif xtables and left == {"meta": {"key": "l4proto"}} and right in ("udp", 17):
                continue
            else:
                return False
        elif "dnat" in item or "redirect" in item:
            kind = "dnat" if "dnat" in item else "redirect"
            nat = dict(item[kind])
            if nat.pop("family", family) != family:
                return False
            if kind == "dnat" and set(nat) == {"addr", "port"}:
                observed.append((kind, nat_endpoint(nat["addr"], nat["port"])))
            elif (kind == "redirect" or (firewalld and xtables)) and set(nat) == {"port"}:
                observed.append(("redirect", str(nat["port"])))
            else:
                return False
        elif item == {"accept": None}:
            observed.append(("accept", ""))
        elif xtables and item.get("xt") == {"type": "match", "name": "addrtype"} and target and not firewalld:
            # libnftables may omit the extension's match arguments. Query the nft-backed
            # xtables API below before treating this possible LOCAL match as ours.
            observed.append(("local", True))
            opaque = True
        elif not (xtables and "counter" in item):
            return False
    # nft may elide an inet nfproto check already implied by a typed IP destination.
    if entry["family"] == "inet" and ("address", str(ipaddress.ip_address(addr))) in observed and ("family", family) not in observed:
        observed.append(("family", family))
    if sorted(expected) != sorted(observed):
        return False
    if opaque:
        binary = "ip6tables" if family == "ip6" else "iptables"
        if shutil.which(binary + "-nft"):
            binary += "-nft"
        elif not shutil.which(binary) or "nf_tables" not in cmd(binary, "--version").stdout:
            return False
        return cmd(binary, "-w", "5", "-t", "nat", "-C", entry["chain"],
                   *iptables_spec(rule), check=False).returncode == 0
    return True


def nft_rule(rule, want, fresh=False, check_only=False):
    _, _, addr, proto, ports, target = rule
    family = "ip6" if ":" in addr else "ip"
    objects = nft_objects()
    marker = f"{MARK} {addr} {proto} {ports} {target or 'accept'}"
    locations = {(family, "sbox_hopping", "prerouting")} if target else {
        (c["family"], c["table"], c["name"]) for item in objects if "chain" in item for c in [item["chain"]]
        if c.get("type") == "filter" and c.get("hook") == "input"}
    existing = [r for item in objects if "rule" in item for r in [item["rule"]]
                if (r["family"], r["table"], r["chain"]) in locations and nft_matches(rule, r)]
    if len(existing) > 1 or (fresh and existing):
        raise ValueError("已有相同的 nft 防火墙规则；请管理员核对归属")
    if check_only:
        return False
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
    if specific_dnat(rule):
        terms += ["dnat", "to", nat_endpoint(addr, target)]
    else:
        terms += ["redirect", "to", ":" + target] if target else ["accept"]
    terms += ["comment", json.dumps(marker)]
    if setup:
        cmd("nft", setup + f"add rule {family} sbox_hopping prerouting " + " ".join(terms))
    else:
        cmd("nft", "add", "rule", chain["family"], chain["table"], chain["name"], *terms)
    return True


def change(rule, want, fresh=False, levels=(True, False), check_only=False):
    if rule[0] in ("firewalld", "firewalld-legacy"):
        return firewalld_rule(rule, want, fresh, check_only, levels)
    if rule[0] in ("nft", "nft-legacy"):
        return nft_rule(rule, want, fresh, check_only)
    return iptables_rule(rule, want, fresh, check_only)


def save(state):
    with tempfile.NamedTemporaryFile("w", dir=ROOT, delete=False) as f:
        json.dump(state, f)
    os.replace(f.name, ROOT / "firewall.json")


def manage(action, tag=None, ports=None, zone=None, input_position=None, confirm_forward_ports=False):
    path = ROOT / "firewall.json"
    if action in ("list", "sync", "remove", "clear") and not path.exists():
        return
    existed = path.exists()
    state = json.loads(path.read_text()) if existed else {"allow": False, "hops": {}, "rules": []}
    if action == "list":
        # Subscriptions consume this output. Retain paused intent on disk without advertising it.
        spans = state["hops"].get(tag, [])
        addresses, port = listeners().get(tag, ([], "")) if spans else ([], "")
        applied = {r[4] for r in state["rules"] if r[2] in addresses and r[3] == "udp" and r[5] == port}
        print(",".join(span for span in spans if span in applied))
        return
    before = json.loads(json.dumps(state))
    old = set()
    for r in state["rules"]:
        engine, zone_name, addr, proto, span, target = r
        # The old six-field entry described REDIRECT or a negative-priority rich rule.
        # Keep both exact signatures in the in-flight journal while exchanging them.
        if state.get("rules_version", 1) == 1 and (engine == "firewalld" or specific_dnat(r)):
            engine += "-legacy"
        old.add((engine, zone_name, addr, proto, span, target))
    active = backend() if action != "remove" else None
    if confirm_forward_ports and (action != "enable" or active != "firewalld"):
        raise ValueError("--confirm-forward-ports 仅用于 firewalld 的 enable 操作")
    if action == "enable":
        if input_position == "append":
            state["append_backend"] = active
        if state.get("append_backend") != active:
            raise ValueError("请核对既有限制、拒绝规则及默认策略；确认追加位置后传 --input-position append，或手动放行")
        state["allow"] = True
        if zone:
            state["zone"] = zone
    elif action in ("add", "clear"):
        if tag not in UDP_TAGS:
            raise ValueError("端口跳跃仅支持 Hysteria2 和 TUIC")
        if action == "add":
            if active == "firewalld" and not (state["allow"] and state.get("append_backend") == active
                    and state.get("zone") and state.get("forward_zone") == state["zone"]):
                raise ValueError(FORWARD_CONFIRMATION)
            addresses, _ = listeners().get(tag, ([], ""))
            if not addresses:
                raise ValueError("目标 UDP 入站未启用")
            if active == "firewalld" and any(not ipaddress.ip_address(a).is_unspecified for a in addresses):
                raise ValueError(FIREWALLD_UNICAST)
            hops = state["hops"].setdefault(tag, [])
            if valid_range(ports) not in hops:
                hops.append(ports)
        else:
            state["hops"].pop(tag, None)
    if active == "firewalld":
        state["zone"] = firewalld_zone(state)
        if confirm_forward_ports:
            state["forward_zone"] = state["zone"]
        elif action == "enable" or state.get("forward_zone") != state["zone"]:
            state.pop("forward_zone", None)
        if state["hops"] and state.get("forward_zone") != state["zone"]:
            print(FORWARD_CONFIRMATION, file=sys.stderr)
    else:
        state.pop("forward_zone", None)
    needed = desired(state, active) if active else set()
    if active and state["allow"] and state.get("append_backend") != active:
        print("尚未确认新后端的放行位置，已登记的旧放行条目将撤下；请核实链尾或手动放行", file=sys.stderr)
    if any(rule[-1] for rule in needed):
        check_hops(needed, old, active)
    # UFW and iptables share the NAT chain and exact rule format.
    shared_nat = {r for r in old - needed if active in ("iptables", "ufw") and r[0] in ("iptables", "ufw")
                  and r[5] and (active, *r[1:]) in needed}
    fresh = needed - old - {(active, *r[1:]) for r in shared_nat}
    for rule in fresh:
        change(rule, True, fresh=True, check_only=True)
    # A journal contains every rule possibly in flight. After interruption, sync converges by exact rule.
    state["rules_version"] = 2
    save(dict(before, rules_version=2, rules=sorted(needed | old)))
    changed = []
    try:
        # A first explicit confirmation upgrades legacy head-inserted allows.
        # Remove these exact entries first so the existence test cannot retain their position.
        if active in ("iptables", "ufw", "nft"):
            for rule in sorted(old & needed):
                if (rule[0] == active and not rule[-1] and (before.get("append_backend") != active
                        or filter_needs_reposition(rule, old)) and change(rule, False)):
                    changed.append((rule, False, True))
        stale = old - needed - shared_nat
        for want, rules in [(True, needed), (False, stale)]:
            for rule in sorted(rules):
                affected = change(rule, want, fresh=want and rule in fresh)
                if affected:
                    changed.append((rule, want, affected))
        state["rules"] = sorted(needed)
        if action == "remove":
            path.unlink()
        else:
            save(state)
    except Exception as original:
        failures = [str(original)] if isinstance(original, IncompleteChange) else []
        uncertain = {rule} if failures else set()
        for rule, want, affected in reversed(changed):
            try:
                change(rule, not want, levels=affected)
            except Exception as exc:
                failures.append(str(exc))
                uncertain.add(rule)
        if failures:
            # A failed ownership check cannot make an administrator's rule ours.
            save(dict(before, rules_version=2, rules=sorted(old | uncertain)))
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
    parser.add_argument("--confirm-forward-ports", action="store_true", help=FORWARD_CONFIRMATION)
    args = parser.parse_args()
    if not (ROOT / "firewall.json").exists() and args.action in ("list", "sync", "remove", "clear"):
        return
    with (ROOT / ".firewall.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manage(args.action, args.tag, args.ports, args.zone, args.input_position, args.confirm_forward_ports)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"sing-box 防火墙操作失败：{exc}", file=sys.stderr)
        sys.exit(1)
