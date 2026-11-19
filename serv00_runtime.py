"""Install, refresh and supervise one Serv00 account from a reviewed local release."""
import argparse
import base64
import contextlib
import getpass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.error
import urllib.request
import uuid

SOURCE = Path(__file__).resolve().parent
BUNDLE = ("serv00.sh", "serv00keep.sh", "serv00_runtime.py", "app.js")
ASSETS = {
    # github.com/yonggekkk/Cloudflare-vless-trojan, release serv00
    # asset IDs 212842014 and 212841973, reviewed SHA-256 of release contents.
    "sb": "fd9578820fcc96fe478e14a02750bd1aec331fa8446d3c58608cbf2a0c0f081c",
    "server": "df31404b55ccfa76ed134b0f68e54f656a574d3abc0f5002ada8711131f981db",
}
RELEASE_URL = "https://github.com/yonggekkk/Cloudflare-vless-trojan/releases/download/serv00/"
PRODUCTS = ("v2sub.txt", "clashmeta.txt", "singbox.txt")
RESTRICTED = ["0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
              "172.16.0.0/12", "192.168.0.0/16", "224.0.0.0/4", "240.0.0.0/4", "::/96",
              "::ffff:0:0/96", "fc00::/7", "fe80::/10", "ff00::/8", "168.63.129.16/32"]
ORIGIN_NAME = "argo-origin.invalid"
SUB_DIRECTORY = "sing-box-sub"
HOME = Path.home()
ROOT = HOME / ".sing-box-serv00"
ACTIVE = ROOT / "active"
USER = getpass.getuser().lower()
HOST = socket.gethostname().lower()
HOST_ID = HOST.split(".")[0]
SITE = USER + (".serv00.net" if ".serv00." in HOST else ".useruno.com")
PUBLIC = HOME / "domains" / SITE / "public_html"
LEGACY = PUBLIC.parent / "logs"
PANEL = HOME / "domains" / (HOST_ID + "." + SITE) / "public_nodejs"


class SetupError(ValueError):
    """An operator-facing message without credentials or request URLs."""


def fail(message):
    raise SetupError(message)


def run(args, *, text=False, cwd=None, check=True):
    return subprocess.run(args, cwd=cwd, check=check, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=text)


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def switch(path, target):
    link = path.parent / (".link-" + secrets.token_hex(12))
    link.symlink_to(target)
    try:
        os.replace(link, path)
    finally:
        link.unlink(missing_ok=True)


def digest(path):
    with path.open("rb") as src:
        h = hashlib.sha256()
        for block in iter(lambda: src.read(1024 * 1024), b""):
            h.update(block)
        return h.hexdigest()


def install_asset(dest, name, expected, fetch):
    if not expected or not re.fullmatch("[0-9a-f]{64}", expected):
        fail("发布清单缺少有效摘要")
    if dest.exists() and digest(dest) == expected:
        return
    with tempfile.TemporaryDirectory(dir=dest.parent) as stage:
        candidate = Path(stage) / name
        fetch(RELEASE_URL + name, candidate)
        if digest(candidate) != expected:
            fail("发布物 SHA-256 不符")
        candidate.chmod(0o700)
        os.replace(candidate, dest)


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            fail("下载重定向未使用 HTTPS")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, path):
    try:
        with urllib.request.build_opener(HTTPSRedirect).open(url, timeout=60) as src, path.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    except (OSError, urllib.error.URLError):
        # The independently pinned digest still gates either download client.
        run(["curl", "--fail", "--silent", "--location", "--proto", "=https", "--proto-redir", "=https",
             "--connect-timeout", "10", "--max-time", "120", "--output", str(path), url])


def prepare_assets():
    for name, expected in ASSETS.items():
        install_asset(ROOT / name, name, expected, fetch)


def install_release():
    release_id = hashlib.sha256("".join(digest(SOURCE / f) for f in BUNDLE).encode()).hexdigest()
    dest = ROOT / ("release-" + release_id)
    if not dest.exists():
        with tempfile.TemporaryDirectory(dir=ROOT) as stage:
            out = Path(stage) / "bundle"
            out.mkdir()
            for name in BUNDLE:
                shutil.copyfile(SOURCE / name, out / name)
            os.replace(out, dest)
    bindir = HOME / "bin"
    bindir.mkdir(exist_ok=True)
    shortcut = bindir / "sb"
    if shortcut.is_symlink():
        fail("已有 sb 符号链接，请通过 SSH 核对并腾出 ~/bin/sb")
    if shortcut.exists():
        previous = shortcut.read_text(errors="replace")
        if ".sing-box-serv00/release/serv00.sh" not in previous and not (
            "vmess-ws-in" in previous and "install_singbox()" in previous and "devil" in previous
        ):
            fail("已有其他 ~/bin/sb，请先核对快捷方式归属")
    # A wrapper leaves BASH_SOURCE next to the rest of the installed release.
    with tempfile.NamedTemporaryFile(dir=bindir, mode="w", delete=False) as out:
        out.write('#!/usr/bin/env bash\nexec bash "$HOME/.sing-box-serv00/release/serv00.sh" "$@"\n')
    os.chmod(out.name, 0o700)
    os.replace(out.name, shortcut)
    if ".serv00." in HOST:
        panel_name = HOST_ID + "." + SITE
        new_vhost = panel_name not in run(["devil", "www", "list"], text=True).stdout.split()
        if new_vhost:
            run(["devil", "www", "add", panel_name, "nodejs", "/usr/local/bin/node18"])
        PANEL.mkdir(parents=True, exist_ok=True)
        app = PANEL / "app.js"
        if not new_vhost and app.exists():
            prior = app.read_text(errors="replace")
            if not (("runportCommand" in prior and "webport.sh" in prior) or
                    ("handleHealth" in prior and "/health" in prior)):
                fail("面板域名已有其他应用，请先在 SSH 核实")
        # A directory inherited writable from an old install cannot protect the panel.
        PANEL.chmod(0o700)
        install_panel(dest, PANEL)
        run(["devil", "www", "restart", panel_name])
    switch(ROOT / "release", dest.name)


def install_panel(bundle, panel):
    with tempfile.NamedTemporaryFile(dir=panel, delete=False) as out:
        out.write((bundle / "app.js").read_bytes())
    os.replace(out.name, panel / "app.js")


def valid_host(name):
    if not isinstance(name, str) or len(name) > 253 or not all(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", x)
        for x in name.split(".")
    ):
        fail("域名格式无效")
    return name


def port(value):
    n = int(value)
    if not 0 < n < 65536:
        fail("端口超出范围")
    return n


def allocated_ports():
    listing = run(["devil", "port", "list"], text=True).stdout
    result = {"tcp": [], "udp": []}
    for num, kind in re.findall(r"(?m)^\s*(\d+)\s+(tcp|udp)\b", listing):
        result[kind].append(port(num))
    return result


def allocate(kind):
    for _ in range(5):
        n = secrets.randbelow(55536) + 10000
        r = subprocess.run(["devil", "port", "add", kind, str(n)], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True)
        if r.returncode == 0:
            return n
        if not re.search(r"(already (in use|used|exists)|address in use)", r.stdout, re.I):
            fail("宿主拒绝分配端口")
    fail("端口分配连续冲突")


def choose_ports(config):
    listing = allocated_ports()
    separate = os.getenv("SERV00_ARGO_PORT") or config.get("argo_dedicated_port")
    if separate and port(separate) in listing["tcp"]:
        listing["tcp"].remove(port(separate))
    for kind, keys in (("tcp", ("vless_port", "vmess_port")), ("udp", ("hy2_port",))):
        for key in keys:
            if config.get(key):
                value = port(config[key])
                if value not in listing[kind]:
                    fail("所选端口未在宿主分配")
            else:
                value = listing[kind][0] if listing[kind] else allocate(kind)
            config[key] = value
            if value in listing[kind]:
                listing[kind].remove(value)


def identity(old, migrate=False):
    user_id = str(uuid.UUID(old["uuid"])) if old.get("uuid") else str(uuid.uuid4())
    raw = old.get("path", "")
    raw = re.sub(r"\?ed=\d+$", "", raw)
    path = "/" + raw.lstrip("/")
    compromised = user_id.lower() in path.lower()
    if compromised and not migrate:
        fail("旧路径包含节点凭据，请通过 SSH 显式运行 --migrate")
    if compromised or not raw:
        path = "/" + secrets.token_hex(16)
    if migrate:
        # All old logs directories had mode 0777, including private keys and UUID.
        user_id = str(uuid.uuid4())
    if any(ord(c) < 32 for c in path) or "?" in path or "#" in path:
        fail("WS 路径含不受支持的控制字符、查询或片段")
    return user_id, path


def legacy_warp_policy(route, policy_backup):
    if policy_backup is not None:
        if (not policy_backup.is_absolute() or policy_backup.is_symlink() or not policy_backup.is_file()
                or policy_backup.resolve().is_relative_to(LEGACY.resolve())
                or policy_backup.stat().st_uid != os.getuid() or policy_backup.stat().st_mode & 0o077):
            fail("请提供本账户私有且来源已核实的旧配置备份绝对路径（0600，位于旧目录以外）")
        route = load(policy_backup).get("route", {})
    rules = [r for r in route.get("rules", []) if r.get("outbound") == "wg"]
    referenced = {tag for rule in rules for tag in rule.get("rule_set", [])}
    sets = [r for r in route.get("rule_set", []) if r.get("tag") in referenced]
    for item in sets:
        if item.get("type") != "remote" or not item.get("url", "").startswith("https://"):
            fail("旧 WARP 规则集需要人工迁移")
    if any("override_address" in rule or rule.get("action", "route") != "route" for rule in rules):
        fail("请手工复核旧 WARP 的目标地址改写或特殊动作，迁移已暂停")
    if rules and policy_backup is None:
        print("拟导入以下匹配条件和远程规则集。命中流量通过 wg，无独立密钥时拒绝。", file=sys.stderr)
        print(json.dumps({"rules": rules, "rule_set": sets}, indent=2, ensure_ascii=True), file=sys.stderr)
        if not sys.stdin.isatty() or input("核实策略来源后输入 IMPORT-WARP 继续迁移：") != "IMPORT-WARP":
            fail("尚未批准旧策略。迁移已暂停，原节点及路由继续生效。可指定 SERV00_WARP_POLICY_BACKUP。")
    return {"warp_rules": rules, "warp_sets": sets, "warp_policy_reviewed": True}


def import_old(path, policy_backup=None):
    if path.is_symlink() or not path.is_file():
        fail("未找到旧配置")
    old = load(path)
    inbounds = {i["tag"]: i for i in old["inbounds"]}
    ws = inbounds["vmess-ws-in"]
    reality = inbounds["vless-reality-vesion"]
    hy = inbounds["hysteria-in1"]
    return {
        "uuid": ws["users"][0]["uuid"], "path": ws["transport"]["path"],
        "vmess_port": ws["listen_port"], "vless_port": reality["listen_port"],
        "hy2_port": hy["listen_port"], "ip": hy["listen"],
        "reality": valid_host(reality["tls"]["server_name"]),
        "addresses": [str(ipaddress.ip_address(i["listen"])) for i in inbounds.values()
                      if i["type"] == "hysteria2"],
        **legacy_warp_policy(old.get("route", {}), policy_backup),
    }


def acquire_state(old, migrate):
    state = dict(old)
    state["uuid"], state["path"] = identity(old, migrate)
    if migrate:
        state["legacy_uuid"] = str(uuid.UUID(old["uuid"]))
    state.setdefault("token", secrets.token_hex(24))
    state.setdefault("site", SITE)
    series = os.getenv("SERV00_CLIENT_SERIES") or state.get("client_series", "modern")
    if series not in ("modern", "1.10"):
        fail("SERV00_CLIENT_SERIES 支持 modern 或 1.10")
    state["client_series"] = series
    revision = os.getenv("SERV00_CLIENT_RULES_REVISION") or state.get("rules_revision", "sing")
    if revision != "sing" and not re.fullmatch(r"[a-f0-9]{40}", revision):
        fail("地域规则需使用已审查的 40 位小写提交号或 sing")
    state["rules_revision"] = revision
    cidrs = ROOT / "restricted-cidrs.json"
    values = load(cidrs) if cidrs.exists() else []
    if not isinstance(values, list) or not all(isinstance(x, str) for x in values):
        fail("restricted-cidrs.json 必须是 CIDR 字符串数组")
    state["restricted_cidrs"] = [str(ipaddress.ip_network(value, strict=True)) for value in values]
    state.setdefault("sni", os.getenv("SERV00_TLS_NAME", SITE))
    valid_host(state["sni"])
    state["reality"] = valid_host(state.get("reality") or os.getenv("REALITY") or SITE)
    ip = state.get("ip") or os.getenv("IP")
    if not state.get("addresses"):
        addresses = []
        for line in run(["devil", "vhost", "list"], text=True).stdout.splitlines():
            try:
                addr = str(ipaddress.ip_address(line.split()[0]))
                if addr not in addresses:
                    addresses.append(addr)
            except (ValueError, IndexError):
                continue
        state["addresses"] = addresses
        ip = ip or next(iter(addresses), None)
    state["ip"] = str(ipaddress.ip_address(ip))
    choose_ports(state)
    separate = os.getenv("SERV00_ARGO_PORT") or state.get("argo_dedicated_port")
    if separate:
        separate = port(separate)
        if separate not in allocated_ports()["tcp"] or separate in (state["vmess_port"], state["vless_port"]):
            fail("独立回环源站需要本账户已登记且未用于节点的 TCP 端口")
        state["backend"] = state["argo_dedicated_port"] = separate
    else:
        for value in [state["ip"], *state.get("addresses", [])]:
            addr = ipaddress.ip_address(value)
            if addr.is_loopback or addr.is_unspecified or addr.is_multicast or addr.is_link_local:
                fail("复用 VMess 端口时，对外监听必须使用具体单播地址")
        verified = state.get("argo_port_host") == HOST or os.getenv("SERV00_LOOPBACK_PORT_CONFIRMED") == "1"
        if not verified and sys.stdin.isatty():
            verified = input("宿主已确认该账号端口的 UID 约束覆盖回环且支持公网、回环分别绑定？输入 YES：") == "YES"
        if not verified:
            fail("请核实宿主 UID 保护及双地址绑定后设置 SERV00_LOOPBACK_PORT_CONFIRMED=1，或指定独立 SERV00_ARGO_PORT")
        state["argo_port_host"] = HOST
        state["backend"] = state["vmess_port"]
    return state


def certificate(stage, previous, state):
    cert, key = stage / "cert.pem", stage / "private.key"
    provided = [os.getenv("SERV00_TLS_CERT"), os.getenv("SERV00_TLS_KEY")]
    if any(provided):
        if not all(provided):
            fail("请同时提供 TLS 证书和私钥文件")
        shutil.copyfile(provided[0], cert)
        shutil.copyfile(provided[1], key)
        state["trusted_cert"] = True
    elif previous:
        shutil.copyfile(previous / "cert.pem", cert)
        shutil.copyfile(previous / "private.key", key)
    else:
        run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
             "-nodes", "-days", "365", "-keyout", str(key), "-out", str(cert), "-subj",
             "/CN=" + state["sni"], "-addext", "subjectAltName=DNS:" + state["sni"]])
        state["trusted_cert"] = False
    run(["openssl", "verify", "-partial_chain", "-trusted", str(cert),
         "-verify_hostname", state["sni"], str(cert)])
    der = run(["openssl", "x509", "-in", str(cert), "-outform", "der"]).stdout
    return hashlib.sha256(der).hexdigest()


def prepare_origin(state, prior):
    directory = ROOT / "origin-tls"
    if not directory.exists():
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            pair = Path(tmp) / "pair"
            pair.mkdir(mode=0o700)
            run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
                 "-nodes", "-days", "3650", "-keyout", str(pair / "private.key"), "-out", str(pair / "cert.pem"),
                 "-subj", "/CN=" + ORIGIN_NAME, "-addext", "subjectAltName=DNS:" + ORIGIN_NAME])
            os.replace(pair, directory)
    cert = directory / "cert.pem"
    run(["openssl", "verify", "-trusted", str(cert), "-verify_hostname", ORIGIN_NAME, str(cert)])
    if state.get("argo_token") and (not prior.get("origin_https") or any(
            state.get(k) != prior.get(k) for k in ("backend", "argo_token", "argo_domain"))):
        url = f"https://127.0.0.1:{state['backend']}"
        print(f"固定隧道控制台设置 Service={url}，Origin Server Name={ORIGIN_NAME}，CA Pool={cert}，关闭 No TLS Verify。",
              file=sys.stderr)
        if os.getenv("SERV00_FIXED_ORIGIN_CONFIRMED") != url and not (
                sys.stdin.isatty() and input("保存控制台设置后，输入上述 HTTPS 地址：").strip() == url):
            fail("固定隧道配置待确认。非交互操作请将 SERV00_FIXED_ORIGIN_CONFIRMED 设为已核对的完整 HTTPS 地址")
    state["origin_https"] = True


def server_series():
    version = run([ROOT / "sb", "version"], text=True).stdout
    match = re.search(r"(?m)^sing-box version (1\.(?:10|14)\.\d+)\s*$", version)
    if not match:
        fail("未识别服务端内核系列（支持已校验摘要的 1.10.x 或 1.14.x）")
    return match[1]


def server_config(s, private_key, warp, series="1.10.7"):
    auth = s["uuid"]
    listener = s["ip"]
    shared_tls = dict(enabled=True, certificate_path="cert.pem", key_path="private.key")
    transport = dict(type="ws", path=s["path"], early_data_header_name="Sec-WebSocket-Protocol",
                     max_early_data=2048)
    ws = dict(type="vmess", listen=listener, listen_port=s["vmess_port"], users=[{"uuid": auth}],
              transport=transport)
    # Preserve the three original Hysteria2 endpoints when the provider assigned them.
    hy = [dict(tag=f"hysteria-in{i}", type="hysteria2", listen=addr, listen_port=s["hy2_port"],
               users=[{"password": auth}], tls={**shared_tls, "alpn": ["h3"]},
               masquerade="https://www.bing.com") for i, addr in enumerate(s.get("addresses", [listener]), 1)]
    route = {"final": "direct"}
    if "warp_rules" in s:
        route.update(rules=s["warp_rules"], rule_set=s.get("warp_sets", []))
    elif re.fullmatch(r"s(14|15)", HOST_ID):
        route.update(rule_set=[dict(type="remote", tag="google-gemini", format="binary",
            url="https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/sing/geo/geosite/google-gemini.srs",
            download_detour="direct")],
            rules=[dict(domain=["jnn-pa.googleapis.com"], outbound="wg"),
                   dict(rule_set=["google-gemini"], outbound="wg")])
    reality = dict(type="vless", tag="vless-reality-vesion", listen=listener, listen_port=s["vless_port"],
             users=[dict(uuid=auth, flow="xtls-rprx-vision")], tls={"enabled": True,
             "server_name": s["reality"], "reality": {"enabled": True, "private_key": private_key,
             "short_id": [""], "handshake": {"server": s["reality"], "server_port": 443}}})
    others = []
    for i, addr in enumerate(s.get("addresses", [listener])[1:], 2):
        others.extend([{**reality, "tag": f"vless-in{i}", "listen": addr},
                       {**ws, "tag": f"vmess-wss-in{i}", "listen": addr, "tls": shared_tls}])
    inbounds = hy + others + [reality,
        {**ws, "tag": "vmess-ws-in", "tls": shared_tls},
        {**ws, "tag": "vmess-argo-in", "listen": "127.0.0.1", "listen_port": s["backend"],
         "tls": dict(enabled=True, certificate_path=str(ROOT / "origin-tls" / "cert.pem"),
                     key_path=str(ROOT / "origin-tls" / "private.key"))}]
    config = {"log": {"disabled": True}, "inbounds": inbounds,
              "outbounds": [{"type": "direct", "tag": "direct"}], "route": route}
    rules = list(route.get("rules", []))
    restricted = RESTRICTED + s.get("restricted_cidrs", [])
    no_address = {"ip_cidr": ["0.0.0.0/0", "::/0"], "invert": True}
    if series.startswith("1.10."):
        for inbound in inbounds:
            inbound["domain_strategy"] = "prefer_ipv4"
        config["outbounds"].extend([{"tag": "block", "type": "block"}, warp or {"type": "block", "tag": "wg"}])
        route["rules"] = [{"ip_cidr": restricted, "outbound": "block"}, {**no_address, "outbound": "block"}, *rules]
    elif series.startswith("1.14."):
        if warp:
            peer = {"address": warp["server"], "port": warp["server_port"], "public_key": warp["peer_public_key"],
                    "allowed_ips": ["0.0.0.0/0", "::/0"]}
            if "reserved" in warp:
                peer["reserved"] = warp["reserved"]
            addresses = warp["local_address"]
            config["endpoints"] = [{"type": "wireguard", "tag": "wg", "private_key": warp["private_key"],
                    "address": addresses if isinstance(addresses, list) else [addresses],
                    "mtu": warp.get("mtu", 1280), "peers": [peer]}]
        else:
            rules = [{**{k:v for k,v in r.items() if k != "outbound"}, "action": "reject"} for r in rules]
        detours = sorted({rs["download_detour"] for rs in route.get("rule_set", []) if "download_detour" in rs})
        if detours:
            config["http_clients"] = [{"tag": "rules-" + tag, "detour": tag} for tag in detours]
        route["rule_set"] = [{**{k:v for k,v in rs.items() if k != "download_detour"},
                              **({"http_client": "rules-" + rs["download_detour"]} if "download_detour" in rs else {})}
                             for rs in route.get("rule_set", [])]
        route["rules"] = [{"action": "resolve", "strategy": "prefer_ipv4"},
                          {"ip_cidr": restricted, "action": "reject"}, {**no_address, "action": "reject"}, *rules]
    else:
        fail("未知服务端版本")
    return config


def emit_clients(dest, s, argo, cert, fingerprint):
    addr, auth, name, path = s["ip"], s["uuid"], s["sni"], s["path"]
    cert_tls = {"enabled": True, "server_name": name}
    pin = {} if s.get("trusted_cert") else {"fingerprint": fingerprint}
    if pin:
        cert_tls["certificate"] = cert
    outbound = [
        dict(type="vless", tag="reality", server=addr, server_port=s["vless_port"], uuid=auth,
             flow="xtls-rprx-vision", tls=dict(enabled=True, server_name=s["reality"],
             utls=dict(enabled=True, fingerprint="chrome"), reality=dict(enabled=True, public_key=s["public_key"]))),
        dict(type="hysteria2", tag="hy2", server=addr, server_port=s["hy2_port"], password=auth,
             tls={**cert_tls, "alpn": ["h3"]}),
    ]
    proxies = [
        dict(name="reality", type="vless", server=addr, port=s["vless_port"], uuid=auth, tls=True,
             udp=True, flow="xtls-rprx-vision", servername=s["reality"],
             **{"reality-opts": {"public-key": s["public_key"]}, "client-fingerprint": "chrome"}),
        dict(name="hy2", type="hysteria2", server=addr, port=s["hy2_port"], password=auth,
             sni=name, alpn=["h3"], **{"skip-cert-verify": False}, **pin)
    ]
    vless_q = urllib.parse.urlencode({"encryption": "none", "flow": "xtls-rprx-vision", "security": "reality",
                                     "sni": s["reality"], "fp": "chrome", "pbk": s["public_key"], "type": "tcp"})
    hy_q = urllib.parse.urlencode({"sni": name, "alpn": "h3", "insecure": "0",
                                   **({"pinSHA256": fingerprint} if pin else {})})
    links = []
    for ip in s.get("addresses", [addr]):
        ip_uri = f"[{ip}]" if ":" in ip else ip
        links.append(f"vless://{auth}@{ip_uri}:{s['vless_port']}?{vless_q}#reality")
        links.append(f"hysteria2://{auth}@{ip_uri}:{s['hy2_port']}/?{hy_q}#hy2")
    endpoints = [("wss", addr, s["vmess_port"], name, cert_tls, pin)]
    if argo:
        valid_host(argo)
        endpoints.append(("argo-wss", argo, 443, argo, {"enabled": True, "server_name": argo}, {}))
    for tag, address, number, hostname, tls, pins in endpoints:
        outbound.append(dict(type="vmess", tag=tag, server=address, server_port=number, uuid=auth,
                             security="auto", tls=tls, transport=dict(type="ws", path=path,
                             headers={"Host": hostname})))
        proxies.append(dict(type="vmess", name=tag, server=address, port=number, uuid=auth, alterId=0,
                            cipher="auto", udp=True, tls=True, servername=hostname, network="ws",
                            **{"ws-opts": {"path": path, "headers": {"Host": hostname}}, "skip-cert-verify": False},
                            **pins))
        # VMess share URIs cannot portably embed a certificate. Deliver self-signed WSS in full profiles.
        if not pins:
            vm = dict(v="2", ps=tag, add=address, port=str(number), id=auth, aid="0", scy="auto",
                      net="ws", type="none", host=hostname, path=path, tls="tls", sni=hostname)
            links.append("vmess://" + base64.b64encode(json.dumps(vm).encode()).decode())
    tags = [o["tag"] for o in outbound]
    test_url = "https://www.gstatic.com/generate_204"
    sets = [("geosite-cn", "geosite/geolocation-cn"), ("geoip-cn", "geoip/cn"),
            ("geosite-geolocation-!cn", "geosite/geolocation-!cn")]
    full = {"log": {"level": "info"}, "inbounds": [{"type": "tun", "tag": "tun-in",
            "address": ["172.19.0.1/30", "fd00::1/126"], "auto_route": True, "strict_route": True}],
            "outbounds": [dict(type="selector", tag="select", outbounds=["auto"] + tags),
                          dict(type="urltest", tag="auto", outbounds=tags, url=test_url, interval="3m"),
                          *outbound, dict(type="direct", tag="direct")],
            "route": {"auto_detect_interface": True, "final": "select",
                      "rule_set": [dict(type="remote", tag=tag, format="binary",
                          url="https://cdn.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@" + s.get("rules_revision", "sing") + "/geo/" + path + ".srs",
                          download_detour="select", update_interval="1d") for tag, path in sets],
                      "rules": [{"ip_is_private": True, "outbound": "direct"},
                                {"rule_set": ["geoip-cn", "geosite-cn"], "outbound": "direct"},
                                {"rule_set": ["geosite-geolocation-!cn"], "outbound": "select"}]}}
    dns = {"final": "dns-proxy", "rules": [{"clash_mode": "Direct", "server": "dns-cn"},
            {"clash_mode": "Global", "server": "dns-proxy"}, {"rule_set": ["geosite-cn"], "server": "dns-cn"},
            {"query_type": ["A", "AAAA"], "server": "fakeip"}]}
    ranges = {"inet4_range": "198.18.0.0/15", "inet6_range": "fc00::/18"}
    full["experimental"] = {"cache_file": {"enabled": True}}
    full["dns"] = dns
    routes = full["route"]["rules"]
    routes[:0] = [{"clash_mode": "Direct", "outbound": "direct"}, {"clash_mode": "Global", "outbound": "select"}]
    if s.get("client_series") == "1.10":
        dns["servers"] = [{"tag": "dns-cn", "address": "223.5.5.5", "detour": "direct"},
                          {"tag": "dns-proxy", "address": "tls://8.8.8.8", "detour": "select"},
                          {"tag": "fakeip", "address": "fakeip"}]
        dns["fakeip"] = {"enabled": True, **ranges}
        full["experimental"]["cache_file"]["store_fakeip"] = True
        full["inbounds"][0]["sniff"] = True
        full["outbounds"].extend([{"type": "dns", "tag": "dns-out"}, {"type": "block", "tag": "block"}])
        routes[:0] = [{"protocol": "dns", "outbound": "dns-out"}, {"network": "udp", "port": 443, "outbound": "block"}]
    else:
        dns["servers"] = [{"tag": "dns-cn", "type": "udp", "server": "223.5.5.5"},
                          {"tag": "dns-proxy", "type": "tls", "server": "8.8.8.8", "detour": "select"},
                          {"tag": "fakeip", "type": "fakeip", **ranges}]
        full["route"]["default_domain_resolver"] = "dns-cn"
        routes[:0] = [{"inbound": "tun-in", "action": "sniff"}, {"protocol": "dns", "action": "hijack-dns"},
                      {"network": "udp", "port": 443, "action": "reject"}]
    mihomo = {"port": 7890, "allow-lan": False, "bind-address": "127.0.0.1",
              "lan-allowed-ips": ["127.0.0.1/32", "::1/128"], "mode": "rule",
              "dns": {"enable": False, "listen": "127.0.0.1:53"},
              "proxies": proxies, "proxy-groups": [
                  {"name": "Balance", "type": "load-balance", "proxies": tags, "url": test_url,
                   "interval": 300, "strategy": "round-robin"},
                  {"name": "Auto", "type": "url-test", "proxies": tags, "url": test_url, "interval": 300},
                  {"name": "Select", "type": "select", "proxies": ["Balance", "Auto", "DIRECT"] + tags}],
              "rules": ["GEOIP,LAN,DIRECT", "GEOIP,CN,DIRECT", "MATCH,Select"]}
    (dest / PRODUCTS[0]).write_text("\n".join(links) + "\n", encoding="utf-8")
    write_json(dest / PRODUCTS[1], mihomo)  # JSON is also valid YAML.
    write_json(dest / PRODUCTS[2], full)


def warp_outbound():
    file = ROOT / "warp.json"
    if not file.exists():
        return None
    if file.is_symlink() or file.stat().st_uid != os.getuid():
        fail("请在私有目录中放置本账号拥有的 warp.json")
    file.chmod(0o600)
    data = load(file)
    keys = {"server", "server_port", "local_address", "private_key", "peer_public_key", "reserved", "mtu"}
    if set(data) - keys - {"tag", "type"} or data.get("type", "wireguard") != "wireguard":
        fail("warp.json 必须是本实例的 WireGuard 出站")
    if len(base64.b64decode(data["private_key"], validate=True)) != 32:
        fail("WARP 私钥无效")
    if hashlib.sha256(data["private_key"].encode()).hexdigest() == "969825febadf8522b515e47c2fcd2bc8dcf5367143204d78d0f9edb4431506ef":
        fail("请更换旧版共享 WARP 私钥")
    return {**data, "tag": "wg", "type": "wireguard"}


def temp_argo():
    try:
        text = (ROOT / "tunnel.log").read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    hosts = re.findall(r"https://([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.trycloudflare\.com)(?=[\s/|]|$)", text)
    return hosts[-1] if hosts else ""


def running_argo(state):
    if state.get("argo_domain"):
        return state["argo_domain"]
    for _ in range(15):
        domain = temp_argo()
        if domain:
            return domain
        time.sleep(1)
    fail("连接器尚未公布临时域名。请检查 tunnel.log 后通过 --keep 重试")


def temporary_tunnel_healthy(domain):
    if not re.fullmatch(r"[a-z0-9-]+\.trycloudflare\.com", domain):
        return False
    try:
        with urllib.request.build_opener(HTTPSRedirect).open("https://" + domain, timeout=7) as response:
            return response.status == 200
    except urllib.error.HTTPError as err:
        with err:
            return err.code == 404
    except OSError:
        return False


def pid_of(name):
    try:
        pid = int((ROOT / (name + ".pid")).read_text())
    except (FileNotFoundError, ValueError):
        return None
    if pid <= 1:
        return None
    proc = run(["ps", "-p", str(pid), "-o", "stat=", "-o", "comm="], text=True, check=False)
    status, _, command = proc.stdout.strip().partition(" ")
    return pid if not status.startswith("Z") and Path(command.strip()).name == name else None


def stop_legacy():
    # A random basename alone is insufficient on a shared FreeBSD host.
    for marker in ("ag.txt", "sb.txt"):
        file = LEGACY / marker
        if not file.is_file() or file.is_symlink():
            continue
        name = file.read_text().strip()
        if not re.fullmatch(r"[A-Za-z0-9]{6}", name):
            fail("旧进程标记无法核实，请通过 SSH 停止原实例")
        for item in run(["pgrep", "-U", str(os.getuid()), "-x", name], text=True, check=False).stdout.split():
            pid = int(item)
            expected = str(LEGACY.resolve() / name)
            listing = run(["procstat", "-b", str(pid)], text=True).stdout.splitlines()
            if any(line.split()[-1:] == [expected] for line in listing):
                os.kill(pid, signal.SIGTERM)
                for _ in range(50):
                    if run(["ps", "-p", str(pid)], check=False).returncode:
                        break
                    time.sleep(.1)
                else:
                    fail("旧进程未退出，请通过 SSH 核实")


def quarantine_legacy_launchers():
    for name, markers in (("serv00keep.sh", ("vmess-ws-in", "devil port")),
                          ("webport.sh", ("resallport", "devil port"))):
        file = HOME / name
        if file.is_symlink() or not file.is_file() or file.stat().st_uid != os.getuid():
            continue
        text = file.read_text(errors="replace")
        if all(marker in text for marker in markers):
            file.chmod(0o600)
            os.replace(file, ROOT / ("legacy-" + name))


def stop(name):
    pid = pid_of(name)
    if not pid:
        return
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if not pid_of(name):
            return
        time.sleep(.1)
    fail("进程尚未结束，请在 SSH 检查")


def start(name, args, directory, env=None, log=None):
    with contextlib.ExitStack() as stack:
        out = stack.enter_context(log.open("wb")) if log else subprocess.DEVNULL
        proc = subprocess.Popen([ROOT / name, *args], cwd=directory, stdin=subprocess.DEVNULL,
                                stdout=out, stderr=subprocess.STDOUT, start_new_session=True, env=env)
        (ROOT / (name + ".pid")).write_text(str(proc.pid))
        time.sleep(.6)
        if proc.poll() is not None:
            fail("服务启动失败")


def ensure_running(directory, state):
    # Import and scheduling credentials should not live in either daemon's environment.
    env = {k:v for k,v in os.environ.items() if not k.startswith("TUNNEL_") and k not in
           {"ARGO_AUTH", "TUNNEL_TOKEN", "SUUID", "SSH_PASS", "SSHPASS", "ACCOUNTS"}}
    if not state.get("origin_https"):
        stop("server")
        fail("旧配置的 Argo 连接器已停止；请从受审发布目录执行 --update 完成 HTTPS 回源迁移")
    if state.get("warp_rules") and not state.get("warp_policy_reviewed"):
        stop("server")
        fail("旧 WARP 策略尚未确认。请在 SSH 使用 --update 核实策略后再启动；仅有旧 recovery 时重新执行 --migrate")
    if not pid_of("sb"):
        start("sb", ["run", "-c", "config.json"], directory, env)
    if not pid_of("server"):
        args = ["tunnel", "--no-autoupdate"]
        if state.get("argo_token"):
            env["TUNNEL_TOKEN"] = state["argo_token"]
            args += ["run"]
        else:
            args += ["--no-tls-verify=false", "--url", f"https://127.0.0.1:{state['backend']}", "--origin-server-name", ORIGIN_NAME,
                     "--origin-ca-pool", str(ROOT / "origin-tls" / "cert.pem")]
        start("server", args, directory, env, ROOT / "tunnel.log")


def new_public(directory, state):
    # Only the three subscription files cross into the static vhost. Group is the
    # serving identity configured by the provider. Temp generation stays private.
    gid = PUBLIC.stat().st_gid
    if os.getenv("SERV00_WEB_GROUP"):
        import grp
        gid = grp.getgrnam(os.environ["SERV00_WEB_GROUP"]).gr_gid
    store = PUBLIC.parent / ".sing-box-subscriptions"
    store.mkdir(mode=0o700, exist_ok=True)
    if store.is_symlink() or store.stat().st_uid != os.getuid():
        fail("订阅目录归属无效")
    os.chown(store, -1, gid)
    store.chmod(0o710)
    # The host may enable autoindex for public_html. Keep token names below a
    # dedicated index-disabled directory and leave unrelated site settings alone.
    entry = PUBLIC / SUB_DIRECTORY
    entry.mkdir(mode=0o700, exist_ok=True)
    if entry.is_symlink() or entry.stat().st_uid != os.getuid():
        fail("订阅入口目录归属无效")
    if any(x.name not in ("index.html", ".htaccess") and not
           (x.is_symlink() and re.fullmatch(r"[0-9a-f]{48}", x.name)) for x in entry.iterdir()):
        fail("订阅入口目录已有其他站点文件")
    for name, body in (("index.html", ""), (".htaccess", "Options -Indexes\n")):
        control = entry / name
        if control.is_symlink() or (control.exists() and control.read_text() != body):
            fail("订阅入口控制文件归属无效")
        if control.exists():
            os.chown(control, -1, gid)
            control.chmod(0o640)
            continue
        with tempfile.NamedTemporaryFile(dir=entry, mode="w", delete=False) as out:
            out.write(body)
        os.chown(out.name, -1, gid)
        os.chmod(out.name, 0o640)
        os.replace(out.name, entry / name)
    os.chown(entry, -1, gid)
    entry.chmod(0o710)
    public = Path(tempfile.mkdtemp(prefix=".sb-sub-", dir=store))
    try:
        for name in PRODUCTS:
            out = public / name
            shutil.copyfile(directory / name, out)
            os.chown(out, -1, gid)
            out.chmod(0o640)
        # For Apache-compatible virtual hosts, reject cleartext and folder indexes.
        htaccess = public / ".htaccess"
        htaccess.write_text("Options -Indexes\nRewriteEngine On\nRewriteCond %{HTTPS} !=on\nRewriteRule ^ - [F,L]\n")
        os.chown(htaccess, -1, gid)
        htaccess.chmod(0o640)
        os.chown(public, -1, gid)
        public.chmod(0o710)
        return public
    except BaseException:
        shutil.rmtree(public)
        raise


def publish(directory, state):
    public = new_public(directory, state)
    link = PUBLIC / SUB_DIRECTORY / state["token"]
    old = link.resolve() if link.is_symlink() else None
    try:
        switch(link, os.path.relpath(public, link.parent))
    except BaseException:
        shutil.rmtree(public)
        raise
    if old and old.parent == public.parent and old.name.startswith(".sb-sub-"):
        shutil.rmtree(old, ignore_errors=True)


def configure(operation, requested_ports=None):
    previous = ACTIVE.resolve() if ACTIVE.exists() else None
    backup = os.getenv("SERV00_WARP_POLICY_BACKUP")
    policy_backup = Path(backup) if backup else None
    if operation == "install" and (previous or (LEGACY / "config.json").exists()):
        fail("检测到已有安装。旧部署请显式使用 --migrate")
    if operation == "migrate":
        if previous:
            fail("已存在新版配置")
        if LEGACY.is_symlink() or LEGACY.stat().st_uid != os.getuid():
            fail("请从核实过的本账号旧配置恢复")
        LEGACY.chmod(0o700)
        old = import_old(LEGACY / "config.json", policy_backup)
    elif previous:
        old = load(previous / "state.json")
        if "warp_rules" in old and not old.get("warp_policy_reviewed"):
            old.update(legacy_warp_policy({"rules": old["warp_rules"], "rule_set": old.get("warp_sets", [])}, policy_backup))
    elif operation == "install":
        old = {"uuid": os.getenv("SUUID") or None}
        if sys.stdin.isatty():
            old["uuid"] = old["uuid"] or getpass.getpass("节点 UUID（回车随机生成）：") or None
            old["ip"] = os.getenv("IP") or input("对外 IP（回车选择宿主已分配地址）：").strip()
            old["reality"] = os.getenv("REALITY") or input("Reality 域名（回车使用账号域名）：").strip()
        for key, env_name in (("vless_port", "TCP1_PORT"), ("vmess_port", "TCP2_PORT"), ("hy2_port", "UDP_PORT")):
            if os.getenv(env_name):
                old[key] = port(os.environ[env_name])
    else:
        fail("请先安装或迁移")
    if operation == "ports":
        old.update(requested_ports)
    state = acquire_state(old, operation == "migrate")
    if operation == "rotate":
        state.update(uuid=str(uuid.uuid4()), path="/" + secrets.token_hex(16), token=secrets.token_hex(24))
    if operation in ("install", "migrate", "argo"):
        state["argo_domain"] = os.getenv("ARGO_DOMAIN", "")
        if not state["argo_domain"] and sys.stdin.isatty():
            state["argo_domain"] = input("固定 Argo 域名（回车使用临时隧道）：").strip()
        state["argo_token"] = ""
        if state["argo_domain"]:
            valid_host(state["argo_domain"])
            state["argo_token"] = os.getenv("ARGO_AUTH") or getpass.getpass("Argo token：")
            if not state["argo_token"]:
                fail("未设置固定隧道 token")
    # The SSH operator can import real provider addresses. Bind each distinct address once.
    addresses = [state["ip"]]
    for value in state.get("addresses", []):
        value = str(ipaddress.ip_address(value))
        if value not in addresses:
            addresses.append(value)
    state["addresses"] = addresses
    warp = warp_outbound()
    if not warp:
        print("WARP 未导入独立密钥，依赖 wg 的规则将拒绝连接", file=sys.stderr)
    prepare_assets()
    stage = Path(tempfile.mkdtemp(dir=ROOT, prefix="generation-"))
    changed = False
    try:
        fingerprint = certificate(stage, previous, state)
        series = server_series()
        prepare_origin(state, old)
        if previous:
            private_key = (previous / "reality.key").read_text()
        else:
            pair = run([ROOT / "sb", "generate", "reality-keypair"], text=True).stdout
            private_key = re.search(r"PrivateKey:\s*(\S+)", pair).group(1)
            state["public_key"] = re.search(r"PublicKey:\s*(\S+)", pair).group(1)
        (stage / "reality.key").write_text(private_key)
        write_json(stage / "config.json", server_config(state, private_key, warp, series))
        run([ROOT / "sb", "check", "-c", "config.json"], cwd=stage)
        write_json(stage / "state.json", state)
        if operation in ("install", "migrate", "update"):
            install_release()
        if operation == "migrate":
            # Keep the new, validated identity for repair if a live port bind fails
            # after stopping the former process. Reusing old exposed keys is unsafe.
            switch(ROOT / "recovery", stage.name)
            quarantine_legacy_launchers()
            stop_legacy()
        changed = True
        stop("server")
        stop("sb")
        ensure_running(stage, state)
        argo = running_argo(state)
        (stage / "argo-domain").write_text(argo)
        emit_clients(stage, state, argo, (stage / "cert.pem").read_text(), fingerprint)
        switch(ACTIVE, stage.name)
        publish(stage, state)
    except BaseException:
        if changed:
            stop("server")
            stop("sb")
            if previous:
                switch(ACTIVE, previous.name)
                try:
                    previous_state = load(previous / "state.json")
                    ensure_running(previous, previous_state)
                    if not previous_state.get("argo_domain"):
                        publish_tunnel_change(previous_state, running_argo(previous_state))
                except SetupError as err:
                    print(str(err), file=sys.stderr)
            elif ACTIVE.is_symlink():
                ACTIVE.unlink()
        recovery = ROOT / "recovery"
        if recovery.is_symlink() and recovery.resolve() == stage:
            print("已保留通过检查的新凭据配置。排除端口冲突后，运行 ~/bin/sb --recover；旧订阅仍保留。",
                  file=sys.stderr)
        elif not ACTIVE.is_symlink() or ACTIVE.resolve() != stage:
            shutil.rmtree(stage)
        raise
    if previous and old.get("token") != state["token"]:
        retire_token(old["token"])
    if operation == "migrate":
        (ROOT / "recovery").unlink(missing_ok=True)
        print("节点身份已轮换。请替换客户端订阅，验证后执行 --retire-legacy")
    if state.get("argo_domain"):
        print(f"固定隧道 HTTPS 回源为 127.0.0.1:{state['backend']}，证书名称为 {ORIGIN_NAME}")
    if previous:
        old_backup = (ROOT / "previous").resolve() if (ROOT / "previous").is_symlink() else None
        switch(ROOT / "previous", previous.name)
        if old_backup and old_backup != previous and old_backup.parent == ROOT and old_backup.name.startswith("generation-"):
            shutil.rmtree(old_backup)


def retire_token(token):
    if not re.fullmatch(r"[0-9a-f]{48}", token):
        fail("无效的订阅标识")
    link = PUBLIC / SUB_DIRECTORY / token
    if link.is_symlink():
        target = link.resolve()
        link.unlink()
        if target.parent == PUBLIC.parent / ".sing-box-subscriptions" and target.name.startswith(".sb-sub-"):
            shutil.rmtree(target)


def retire_legacy(state):
    old = state.get("legacy_uuid")
    if not old:
        fail("当前部署没有待迁移的旧标识")
    for name in PRODUCTS:
        expected = (ACTIVE / name).read_bytes()
        url = "https://" + SITE + "/" + SUB_DIRECTORY + "/" + state["token"] + "/" + name
        request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        # Never forward a subscription token through a redirect.
        class RejectRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args):
                fail("订阅发生重定向")
        with urllib.request.build_opener(RejectRedirect).open(request, timeout=10) as response:
            if response.status != 200 or response.read(len(expected) + 1) != expected:
                fail("HTTPS 订阅内容不一致，保留旧入口")
    for suffix in PRODUCTS:
        (PUBLIC / (str(uuid.UUID(old)) + "_" + suffix)).unlink(missing_ok=True)
    print("三个 HTTPS 订阅已校验，旧订阅已停止分发")


def reset_ports():
    state = load(ACTIVE / "state.json")
    if not state.get("origin_https") or (state.get("warp_rules") and not state.get("warp_policy_reviewed")):
        fail("请先从受审版本执行 --update 后再更换端口")
    assigned = allocated_ports()
    ports = {}
    original = []
    added = []
    for kind, key in (("tcp", "vless_port"), ("tcp", "vmess_port"), ("udp", "hy2_port")):
        value = input(f"{key}（输入已分配端口，回车随机替换当前项目端口 {state[key]}）：").strip()
        if value and port(value) not in assigned[kind]:
            fail("目标端口不在宿主已分配列表")
        if not value and state[key] not in assigned[kind]:
            fail("旧端口归属需与宿主核对")
        ports[key] = port(value) if value else None
    if ports["vless_port"] and ports["vless_port"] == ports["vmess_port"]:
        fail("两个 TCP 入站需要不同的端口")
    reconnect = bool(pid_of("server"))
    stop("server")
    try:
        for kind, key in (("tcp", "vless_port"), ("tcp", "vmess_port"), ("udp", "hy2_port")):
            if ports[key] is not None:
                continue
            old = state[key]
            if kind == "tcp" and old in (ports["vless_port"], ports["vmess_port"]):
                fail("目标端口与另一项待回收的原端口冲突")
            run(["devil", "port", "del", kind, str(old)])
            original.append((kind, old))
            ports[key] = allocate(kind)
            added.append((kind, ports[key]))
        configure("ports", ports)
    except BaseException:
        failed = False
        for cmd, entries in (("del", added), ("add", original)):
            for kind, n in entries:
                if run(["devil", "port", cmd, kind, str(n)], check=False).returncode:
                    failed = True
        if failed:
            fail("宿主未能恢复全部原端口，请在 SSH 对照 active/state.json 和 devil port list 恢复")
        if reconnect:
            ensure_running(ACTIVE.resolve(), state)
            publish_tunnel_change(state, running_argo(state))
        raise


def publish_tunnel_change(state, current):
    if current and current != (ACTIVE / "argo-domain").read_text():
        der = run(["openssl", "x509", "-in", str(ACTIVE / "cert.pem"), "-outform", "der"]).stdout
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            stage = Path(tmp)
            emit_clients(stage, state, current, (ACTIVE / "cert.pem").read_text(), hashlib.sha256(der).hexdigest())
            publish(stage, state)
            for name in PRODUCTS:
                os.replace(stage / name, ACTIVE / name)
        (ACTIVE / "argo-domain").write_text(current)


def perform(op):
    if op == "recover":
        recovery = ROOT / "recovery"
        if ACTIVE.exists() or not recovery.is_symlink():
            fail("当前没有待恢复的迁移")
        stage = recovery.resolve()
        if stage.parent != ROOT or not stage.name.startswith("generation-"):
            fail("待恢复配置目录无效")
        state = load(stage / "state.json")
        if not state.get("origin_https") or (state.get("warp_rules") and not state.get("warp_policy_reviewed")):
            fail("恢复快照早于回源和策略迁移。请从本受审发布目录重新执行 --migrate")
        run([ROOT / "sb", "check", "-c", "config.json"], cwd=stage)
        stop_legacy()
        ensure_running(stage, state)
        argo = running_argo(state)
        der = run(["openssl", "x509", "-in", str(stage / "cert.pem"), "-outform", "der"]).stdout
        emit_clients(stage, state, argo, (stage / "cert.pem").read_text(), hashlib.sha256(der).hexdigest())
        (stage / "argo-domain").write_text(argo)
        publish(stage, state)
        switch(ACTIVE, stage.name)
        recovery.unlink()
        print("新凭据已恢复。请刷新订阅并验证，再执行 --retire-legacy")
        return
    if op in ("install", "migrate", "update"):
        run(["devil", "binexec", "on"])
        if SITE not in run(["devil", "www", "list"], text=True).stdout.split():
            run(["devil", "www", "add", SITE, "php"])
        if not PUBLIC.is_dir():
            fail("宿主尚未创建静态站点目录")
    if op == "ports":
        reset_ports()
        print("已更新端口，请刷新订阅")
        return
    if op in ("install", "migrate", "update", "argo", "rotate", "refresh"):
        configure(op)
        print("配置已生效，通过 ~/bin/sb --show 查看订阅")
        return
    state = load(ACTIVE / "state.json")
    if op in ("keep", "restart"):
        if op == "restart":
            stop("server")
            stop("sb")
        elif not pid_of("sb"):
            stop("server")
        elif not state.get("argo_token") and pid_of("server") and not temporary_tunnel_healthy(temp_argo()):
            stop("server")
        ensure_running(ACTIVE.resolve(), state)
        publish_tunnel_change(state, running_argo(state))
    elif op == "retire-legacy":
        retire_legacy(state)
    elif op == "show":
        if not state.get("trusted_cert"):
            print("自签单节点 Hysteria2 URI 需先通过 SSH 取回并信任 ~/.sing-box-serv00/active/cert.pem；"
                  "完整订阅内已包含证书或指纹。")
        for name in PRODUCTS:
            print("https://" + SITE + "/" + SUB_DIRECTORY + "/" + state["token"] + "/" + name)
        print((ACTIVE / PRODUCTS[0]).read_text())
    elif op == "configs":
        for name in PRODUCTS[1:]:
            print((ACTIVE / name).read_text())
    elif op == "uninstall":
        if input("停止本项目进程并删除本项目私有目录及当前订阅？输入 DELETE：") != "DELETE":
            return
        stop("server")
        stop("sb")
        retire_token(state["token"])
        if (PANEL / "app.js").exists() and digest(PANEL / "app.js") == digest(SOURCE / "app.js"):
            (PANEL / "app.js").unlink()
            run(["devil", "www", "restart", HOST_ID + "." + SITE], check=False)
        wrapper = HOME / "bin" / "sb"
        if wrapper.is_file() and ".sing-box-serv00/release/serv00.sh" in wrapper.read_text():
            wrapper.unlink()
        shutil.rmtree(ROOT)


def main():
    import fcntl
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    for op in ("install", "keep", "migrate", "update", "ports", "argo", "restart", "show", "configs",
               "retire-legacy", "uninstall", "rotate", "refresh", "recover"):
        modes.add_argument("--" + op, action="store_const", const=op, dest="operation")
    parser.add_argument("--account-file", type=Path)
    args = parser.parse_args()
    if args.account_file:
        if args.operation not in ("install", "update"):
            fail("--account-file 仅用于通过 SSH 部署受审版本")
        values = load(args.account_file)
        for key in ("SUUID", "REALITY", "IP", "TCP1_PORT", "TCP2_PORT", "UDP_PORT", "ARGO_DOMAIN", "ARGO_AUTH",
                    "SERV00_ARGO_PORT", "SERV00_LOOPBACK_PORT_CONFIRMED", "SERV00_FIXED_ORIGIN_CONFIRMED", "SERV00_CLIENT_RULES_REVISION"):
            if values.get(key):
                os.environ[key] = str(values[key])
    os.umask(0o077)
    ROOT.mkdir(mode=0o700, exist_ok=True)
    if ROOT.is_symlink() or ROOT.stat().st_uid != os.getuid() or ROOT.stat().st_mode & 0o077:
        fail("私有目录须为本账号独占的 0700 目录")
    with (ROOT / "operation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        perform(args.operation)


if __name__ == "__main__":
    try:
        main()
    except SetupError as err:
        print(str(err), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration,
            subprocess.SubprocessError, EOFError) as err:
        # External output, paths, URLs and exception strings can contain credentials.
        print("Serv00 操作失败，类别 " + type(err).__name__, file=sys.stderr)
        sys.exit(1)
