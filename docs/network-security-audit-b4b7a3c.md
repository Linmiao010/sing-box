# 代码库当前网络安全漏洞审计报告（基线：`b4b7a3c`）

审计日期：2026-11-19

本次审计以提交 `b4b7a3c` 为基线，覆盖当前工作区全部生产代码：[`sb.sh`](../sb.sh)、[`serv00_runtime.py`](../serv00_runtime.py)、[`serv00.sh`](../serv00.sh)、[`serv00keep.sh`](../serv00keep.sh)、[`app.js`](../app.js)、[`tools/firewall.py`](../tools/firewall.py)、[`tools/network_probe.py`](../tools/network_probe.py)、[`tools/compare_client_configs.py`](../tools/compare_client_configs.py)、[`tools/releases.sha256`](../tools/releases.sha256)、[`kp.sh`](../kp.sh)、[`workers_keep.js`](../workers_keep.js) 以及 GitHub Actions 工作流模板（[`.github/workflows/main.yml`](../.github/workflows/main.yml)、[`SSH.yml`](../SSH.yml)、[`serv00.yml`](../serv00.yml)）。上一轮基线 `b0a8951` 的复核见[上一轮审计报告](network-security-audit.md)与[安全迁移与验收](security-migration.md)。

按照 [`AGENT.md`](../AGENT.md) 约束，本报告仅收录**跨越真实不可信边界、具备明确触发路径与实际安全后果**的网络安全问题（包含对 `b0a8951` / `b4b7a3c` 修复的绕过点与新引入回归）。

---

## 漏洞总览

| 编号 | 风险等级 | 模块 | 漏洞摘要 | 类型 |
| :--- | :--- | :--- | :--- | :--- |
| **1** | **高危** | VPS & Serv00 服务端路由 | 多路复用 UDP 会话（`PacketConn`）仅首包匹配路由，绕过回环/私网/元数据 SSRF 拦截 | 修复绕过 |
| **2** | **高危** | VPS 客户端订阅 (`sbox.json`) | 1.14.x 模板遗漏 `{"action": "sniff"}`，`hijack-dns` 永远失效，导致全量明文 DNS 泄露与污染 | 新引入回归 |
| **3** | **高危** | 主机防火墙 (`firewall.py`) | `firewalld` 硬编码 `priority="-17461"` 且触发 `ct status dnat accept`，绕过来源封禁与默认拒绝 | 修复遗漏 |
| **4** | **高危** | Serv00 & VPS 证书管理 | Serv00 自签证书默认 `CA:TRUE` 且引导用户信任；外部导入证书跳过 CA 链核验；`--rotate` 不轮换私钥 | 证书与密钥安全 |
| **5** | **中高危** | VPS & Serv00 服务端路由 | `RESTRICTED` 遗漏 NAT64/6to4 与本机公网单播 IP（Hairpin SSRF），`socks-out` 存在二次解析重绑定 | 修复不完整 |
| **6** | **中高危** | VPS GitLab 发布 | 仅靠无认证 API `404` 判断私有性，`internal` 仓库或含扩展名后缀的公开仓库可绕过检查泄露全部凭据 | 鉴权校验绕过 |
| **7** | **中高危** | VPS & Serv00 客户端配置 | `default_domain_resolver` 明文 UDP 解析私有节点域名；WARP 前置在隧道外泄露节点域名；Serv00 Mihomo 全量泄露 DNS | 流量与隐私泄露 |
| **8** | **中高危** | 主机防火墙 (`firewall.py`) | `PREROUTING REDIRECT` 改写目的端口与多 IP 网卡副地址，且 `::ffff:127.0.0.1` 未被识别为回环 | 防火墙逻辑缺陷 |
| **9** | **中危** | VPS & Serv00 Argo 隧道 | 多用户 VPS 回环源站仍为明文 HTTP 且停服不停隧道；Serv00 固定隧道缺本机校验、`--ports` 提前释放端口 | 多租户回环/端口竞争 |
| **10** | **中危** | Serv00 订阅分发 | 拒绝明文 HTTP 的 `.htaccess` 仅置于符号链接指向的 `public_html` 外部目录，特定配置下失效 | 传输加密边界 |
| **11** | **中危** | Serv00 服务端分流与探测 | `s14`/`s15` 硬编码可变 `@sing` 规则集；`legacy_warp_policy` 漏检字符串/逻辑规则与直连 SSRF；1.14 域名 WARP 校验失败 | 供应链与规则绕过 |
| **12** | **中低危** | ACME / 探测 / SSH 自动化 | `acme()` 换域名未同步配置与重载；`network_probe.py` 跨主机重定向探测内网；`kp.sh` 无 PTY 断连残留凭据 | 运维与边界加固 |

---

## 详细漏洞分析与最小化修复建议

### 1. 【高危】服务端多路复用 UDP 会话（`PacketConn`）绕过内网与回环 SSRF 拦截

- **涉及位置**
  - [`sb.sh`](../sb.sh#L1591-L1624)（`secure_server_routes`）、[`sb.sh`](../sb.sh#L1374-L1377)、[`sb.sh`](../sb.sh#L1562-L1565)
  - [`serv00_runtime.py`](../serv00_runtime.py#L440-L467)（`server_config`）
  - 对照测试：[`tests/test_security_server_routes.py`](../tests/test_security_server_routes.py#L191)（仅测试了 TCP `SOCKS5 CONNECT` `0x01`）
- **漏洞机理与触发路径**
  - 提交 `b4b7a3c` 在 `route.rules` 前部加入 `ip_cidr` 黑名单来阻断回环、私网和云元数据访问。但在 `sing-box`（1.10.7 与 1.14.3）中，`Router.RoutePacketConnection` **仅在每个 `PacketConn` 会话建立首包时执行一次路由匹配**。
  - 项目启用的全部代理协议均支持在单一 `PacketConn` 上逐包指定不同目标地址（Full-Cone UDP 多路复用）：
    - **Hysteria2**（同一 `session_id` 的后续数据报自带独立 `addr` 字段）；
    - **TUIC v5**（`native` 模式下同一 `assoc_id` 的每个 `PACKET` 帧自带独立目标地址）；
    - **VLESS / VMess / AnyTLS / Trojan / Snell**（XUDP、`packetaddr` 或 UoT `sp.v2.udp-over-tcp.arpa` 非连接帧模式）。
  - 客户端只需令会话第 1 个 UDP 包指向任意合法公网地址（如 `1.1.1.1:53`）通过路由检查，服务端 `direct` 出站便会打开一个**未连接（unconnected）**的宿主 UDP 套接字直接转发后续报文；第 2 个包起即可将目标改为 `127.0.0.1:<port>`、`[::1]:<port>`、`169.254.169.254` 或 `100.64.0.0/10`，完全绕过 `route.rules`。
- **实际后果**
  - 持有单节点分享链接的任意客户端仍可探测并访问 VPS 本机回环 UDP 服务（如 `127.0.0.1:53` 本地 DNS、QUIC/HTTP3 服务、内网监控/管理端口）以及同机其他 Serv00 租户的 UDP 监听服务。
- **最小修复建议**
  - 在 1.14.x 服务端路由中，在放行至 `direct` 前增加 `{"network": "udp", "action": "route-options", "udp_connect": true}`，使底层 UDP 套接字通过 `connect()` 锁定到首包经校验的单一目标 `IP:Port`；
  - 对不支持 `route-options` 的 1.10.7（以及 VPS 纵深隔离），可将 `sing-box` 改为独立低权限系统用户运行并在主机防火墙 `OUTPUT` 链拒绝该 UID 访问回环（除显式启用的本机 `socks-out` TCP 端口外）及受限元数据网段，或明确提示 `1.10.7` 的多目标 UDP 限制。

---

### 2. 【高危】VPS 新版客户端配置 `sbox.json` 遗漏 `sniff` 动作，导致 TUN DNS 劫持失效与全量明文 DNS 泄露

- **涉及位置**
  - [`sb.sh`](../sb.sh#L2691-L2772)（`sball` 的 `tun-in` 与 `route.rules`）
  - 对照位置：[`sb.sh`](../sb.sh#L3088)（1.10 分支显式补了 `.sniff=true`）、[`serv00_runtime.py`](../serv00_runtime.py#L556)（显式含 `{"inbound": "tun-in", "action": "sniff"}`）
- **漏洞机理与触发路径**
  - `sball()` 生成的 1.14.x 客户端配置中，`tun-in` 未设置旧版 `sniff`，`route.rules` 中也**完全缺失 `{"inbound": ["tun-in"], "action": "sniff"}`**。
  - 在 `sing-box` 1.12+/1.14.x 中，第 2725–2733 行的 `{"inbound": ["tun-in"], "protocol": ["dns"], "action": "hijack-dns"}` 依赖嗅探器识别出的 `metadata.Protocol == "dns"`。缺少前置 `sniff` 动作时，`metadata.Protocol` 恒为空串，**`hijack-dns` 永远不会命中**。
  - 在常见的家用/办公局域网环境（系统 DNS 为 `192.168.x.x`、`10.x.x.x` 等私网网关地址）下，所有进入 `tun-in` 的 DNS 查询会直接命中第 2740 行的 `{"ip_is_private": true, "action": "route", "outbound": "direct"}` 明文直连发出。
- **实际后果**
  - 客户端 FakeIP 与远程加密 DNS（`dns-proxy`）完全失效；用户访问的所有境外受限域名均以明文 UDP 发送给本地路由器/ISP，导致全量 DNS 泄露与抢答污染；连接阶段又因无 FakeIP 映射且无 TLS/HTTP `sniff` 而无法命中 `geosite-cn` 等域名分流规则。
- **最小修复建议**
  - 在 `sball()` 的 `route.rules` 首项加入 `{"inbound": ["tun-in"], "action": "sniff"}`，同时在 `hijack-dns` 规则旁对 `port: 53` 流量做兜底劫持（并在第 3088–3090 行的 1.10 降级转换中剔除 `action == "sniff"` 项）。

---

### 3. 【高危】`firewalld` 后端使用负优先级 `priority="-17461"` 且触发 `ct status dnat accept`，绕过既有来源封禁与默认拒绝（审计项 #9 修复遗漏）

- **涉及位置**
  - [`tools/firewall.py`](../tools/firewall.py#L402-L412)（`firewalld_rule`）
  - [`tools/firewall.py`](../tools/firewall.py#L528-L534)、[`tools/firewall.py`](../tools/firewall.py#L549)（`manage`）
  - [`sb.sh`](../sb.sh#L534-L538)（`openyn`）
- **漏洞机理与触发路径**
  1. **负优先级 `accept` 先于 `_deny` 封禁链：** 提交 `b4b7a3c` 在第 529 行通过 `if active != "firewalld":` 豁免了 `firewalld` 的位置检查，并在第 406 行保留了 `priority="-17461"`。根据 `firewalld` 链结构，`priority < 0` 的富规则编译进 `filter_IN_<zone>_pre`，**先于** `filter_IN_<zone>_deny`（所有默认 `priority="0"` 的管理员 `drop`/`reject` 规则及 `fail2ban` 封禁所在链）以及任何 `priority > -17461`（如 `-100`）的黑名单执行。
  2. **UDP 端口跳跃通过 `ct status dnat` 击穿整条区域过滤链：** 即使管理员保持默认 `allow: False`（未开启自动放行、打算自行限制主端口来源 IP），只要执行菜单 `4` 添加 Hysteria2/TUIC 跳跃端口，`firewalld_rule()` 就会在 `nat_PRE_<zone>_pre` 写入无来源限制的 `forward-port`。外部数据包经 `PREROUTING` 重定向后自动带有 `ct status dnat` 状态；在 `firewalld` 默认 `StrictForwardPorts=no` 下，顶层 `filter_INPUT` 基础链会在进入 `filter_INPUT_ZONES` **之前**直接命中 `ct status dnat accept` 放行。
- **实际后果**
  - 在使用 `firewalld` 的 CentOS/RHEL/Rocky/Debian 主机上，启用项目防火墙管理会使已配置的恶意 IP 封禁对代理端口全部失效；配置 UDP 跳跃端口更会绕过整个 zone 的来源白名单、黑名单乃至默认拒绝策略，将 UDP 节点直接暴露给任意公网来源。
- **最小修复建议**
  - 移除 `accept` 规则的负优先级（改为正优先级置于 `_post` 或 `priority >= 0` 使 `_pre` 与 `_deny` 先执行，同样要求管理员确认链尾语义）；在 `firewalld` 下添加无来源限制的 `forward-port` 前核对 `allow` / `StrictForwardPorts` 状态并要求管理员显式确认。

---

### 4. 【高危】Serv00 自签证书默认签发为根 CA（`CA:TRUE`）且引导用户信任，外部证书缺乏信任链核验，`--rotate` 未轮换传输层私钥

- **涉及位置**
  - [`serv00_runtime.py`](../serv00_runtime.py#L349-L370)（`certificate`）、[`serv00_runtime.py`](../serv00_runtime.py#L378-L381)（`prepare_origin`）
  - [`serv00_runtime.py`](../serv00_runtime.py#L474-L477)、[`serv00_runtime.py`](../serv00_runtime.py#L514-L517)（`emit_clients`）
  - [`serv00_runtime.py`](../serv00_runtime.py#L817-L818)、[`serv00_runtime.py`](../serv00_runtime.py#L846-L852)（`configure`）
  - [`serv00_runtime.py`](../serv00_runtime.py#L1048-L1050)（`perform("show")`）
  - 关联 VPS 位置：[`sb.sh`](../sb.sh#L642-L670)、[`README.md`](../README.md#L50)（手工准备 `/root/ygkkkca/cert.crt`）
- **漏洞机理与触发路径**
  1. **共享宿主机上的 `CA:TRUE` 私钥风险：** `certificate()` 调用 `openssl req -x509` 未指定 `-addext basicConstraints=critical,CA:FALSE`（对比 VPS [`sb.sh`](../sb.sh#L635) 已加 `CA:FALSE`），OpenSSL 默认生成 `CA:TRUE` 根证书；而第 1049 行 `--show` 明确提示用户取回并信任 `~/.sing-box-serv00/active/cert.pem`。由于对应 `private.key` 明文存放在不可信的免费共享宿主机（Serv00/Hostuno 运维侧及宿主机备份天然可读），一旦用户将该 `CA:TRUE` 证书导入系统级根证书库，持有该私钥者即可为任意第三方域名签发受客户端系统信任的伪造证书。
  2. **外部证书自举校验导致固定指纹被剥离：** 传入 `SERV00_TLS_CERT` 时第 357 行直接令 `trusted_cert = True`，第 366 行却用 `openssl verify -partial_chain -trusted <cert> ... <cert>` 将待测证书自身设为信任锚（任何自签证书均能通过），随后 `emit_clients()` 省去了 `certificate`、`fingerprint` 和 `pinSHA256`。（VPS 侧用户手工放置 `/root/ygkkkca/cert.crt` 时同样未校验证书 SAN 与系统 CA 链。）
  3. **`--rotate` 不轮换任何非对称私钥：** 执行 `~/bin/sb --rotate` 时仅更换 `uuid`、`path` 和 `token`，原样复制旧 `private.key`（叶证书指纹不变）、`reality.key` 和 `origin-tls/private.key`。若此前目录或备份泄露，攻击者仍持有现行有效的 TLS、REALITY 与回环源站私钥。
- **最小修复建议**
  - 在 `serv00_runtime.py` 的 `certificate()` 与 `prepare_origin()` 中补齐 `-addext basicConstraints=critical,CA:FALSE -addext keyUsage=critical,digitalSignature,keyEncipherment -addext extendedKeyUsage=serverAuth`，单节点提示改为仅在客户端内针对该节点固定证书/指纹而非导入系统根库；
  - 对 `SERV00_TLS_CERT` 及 VPS `/root/ygkkkca/cert.crt` 使用系统默认 CA 信任库（移除 `-trusted <self>`）核验完整链、有效期、公私钥配对及 `SAN`；校验不过时拒绝或按非受信证书下发固定指纹；
  - 在 `--rotate`（以及 VPS 完整凭据轮换选项）中同步轮换自签 TLS 证书对、`reality.key` 与 `origin-tls`。

---

### 5. 【中高危】服务端 `RESTRICTED` 遗漏 NAT64/6to4 与本机公网地址，且 `socks-out` 转发未绑定已校验 IP

- **涉及位置**
  - [`sb.sh`](../sb.sh#L1523-L1528)、[`sb.sh`](../sb.sh#L1597-L1600)
  - [`serv00_runtime.py`](../serv00_runtime.py#L35-L37)、[`serv00_runtime.py`](../serv00_runtime.py#L440-L467)
- **漏洞机理与实际后果**
  1. **NAT64 / IPv4 嵌入型 IPv6 绕过：** `RESTRICTED` 未包含 `64:ff9b::/96`（RFC 6052 知名 NAT64）、`64:ff9b:1::/48`（RFC 8215 本地 NAT64）、`::ffff:0:0:0/96`、`2002::/16`（6to4）、`100::/64`、`192.0.0.0/24` 及 `198.18.0.0/15`。在启用了 DNS64/NAT64 的纯 IPv6 VPS（含 `v4orv6()` 配置的 DNS64 场景）或云厂商 VPC NAT64 环境中，访问 `[64:ff9b::a9fe:a9fe]` 或解析仅含私网 `A` 记录的域名会被合成为 `64:ff9b::/96` 地址并直接放行至内网或元数据目标。
  2. **本机公网/共享宿主 IP 回环绕过（Hairpin SSRF）：** 服务端未拦截 VPS 自身配置的全局公网 IP，也未拦截 Serv00 宿主机共享的公网单播地址（`state["addresses"]`）。客户端通过代理连接 `<服务端公网IP>:<端口>` 时，流量在内核内部走 `lo` 闭环，绕过云平台安全组及仅限制外部网卡（如 `-i eth0`）的主机防火墙，可访问本机绑定在 `0.0.0.0`/`::` 上的内部管理端口或同机其他 Serv00 租户绑定在宿主公网 IP 上的后端端口。
  3. **`socks-out` 二次 DNS 解析：** `sb11.json` 中分流至 `socks-out`（`127.0.0.1:40000`）的域名流量虽经路由层 `resolve` 检查，但 `socks` 出站未绑定已解析地址，仍将原始域名（`ATYP=0x03`）发给下游 SOCKS5 进程重新解析，存在 DNS Rebinding 时间窗。
- **最小修复建议**
  - 将 `64:ff9b::/96`、`64:ff9b:1::/48`、`::ffff:0:0:0/96`、`2002::/16`、`100::/64`、`192.0.0.0/24`、`198.18.0.0/15` 纳入默认 `RESTRICTED`，并在生成/更新配置时自动并入本机全局单播地址（`/32`、`/128`）及 Serv00 `state["addresses"]`；
  - 为 `sb11.json` 的 `socks-out` 显式配置解析策略使其使用受控解析后的 IP 地址拨号。

---

### 6. 【中高危】GitLab 订阅发布仓库可见性检查可被绕过，导致凭据与私钥公开泄露

- **涉及位置**
  - [`sb.sh`](../sb.sh#L4875-L4884)（`gitlab_settings_valid`）
  - [`sb.sh`](../sb.sh#L4904-L4908)（`gitlab_publish`）
- **漏洞机理与触发路径**
  - `gitlab_publish()` 仅发起一次**不带凭据**的请求 `GET https://gitlab.com/api/v4/projects/${project//\//%2F}`，只要 HTTP 状态码为 `404` 就认定仓库非匿名公开并继续执行 `git push`：
    1. **GitLab `internal` 仓库绕过：** 可见性为 `internal` 的仓库对匿名请求同样返回 `404`，但**平台上任何已登录用户**均可搜索、浏览并克隆仓库；
    2. **路径后缀截断绕过（公开仓库误判为 `404`）：** `gitlab_settings_valid()` 允许路径段含 `.`（例如管理员习惯性输入 `group/repo.git`，或项目名以 `.json`、`.xml`、`.atom`、`.txt` 结尾）。GitLab Grape 路由会将 URL 末尾扩展名作为格式后缀剥离后再查找项目，导致即使该仓库**完全公开（Public）**，API 仍返回 `404` 通过校验，随后 `git push` 将包含全部节点密码及客户端 WARP 私钥的三份订阅文件推送至公开仓库；
    3. 第 4905 行的探测 `curl` 缺少 `--noproxy '*' --proto '=https'`。
- **最小修复建议**
  - `gitlab_settings_valid()` 明确拒绝 `.git` 结尾；
  - `gitlab_publish()` 改为通过受保护配置传入发布令牌（`PRIVATE-TOKEN`）调用 `https://gitlab.com/api/v4/projects/${project//\//%2F}`（加 `--noproxy '*' --proto '=https'`），用 `jq -e` 严格校验返回 JSON 的 `.visibility == "private"` 且 `.path_with_namespace`（忽略大小写）与 `$project` 一致，同时核对匿名请求 `https://gitlab.com/$project.git/info/refs?service=git-upload-pack` 返回 `401`/`404` 而非 `200`。

---

### 7. 【中高危】客户端订阅配置存在三处明文 DNS 泄露（暴露私有代理域名与受代理访问记录）

- **涉及位置**
  - VPS 新版 `sbox.json`：[`sb.sh`](../sb.sh#L2650-L2662)、[`sb.sh`](../sb.sh#L2706)
  - VPS 客户端 WARP 前置 `clmi.yaml`：[`sb.sh`](../sb.sh#L2360-L2369)、[`sb.sh`](../sb.sh#L2885-L2887)
  - Serv00 `singbox.txt` 与 `clashmeta.txt`：[`serv00_runtime.py`](../serv00_runtime.py#L552-L566)
- **漏洞机理与实际后果**
  1. **私有节点与 Argo 域名经明文 UDP 53 查询：** VPS `sbox.json` 与 Serv00 `singbox.txt` 均将 `route.default_domain_resolver` 设为明文 UDP `223.5.5.5:53`。客户端连接任何域名型出站（自有 ACME 节点域名、`*.trycloudflare.com`、固定 Argo 域名、`dns.google`）及每次 `urltest` 测速时，均直接发送明文 UDP DNS 请求，向本地网络暴露私有代理域名且极易被抢答阻断。
  2. **客户端 WARP 前置在隧道外解析 VPS 域名：** 开启 `客户端 → WARP → VPS`（菜单 `3 → 12`）时，`clmi.yaml` 中的 `WARP-前置` 未开启 `remote-dns-resolve: true`，而 `proxy-server-nameserver` 配置为境内 `223.5.5.5` / `doh.pub`，致使本应借由 WARP 隐藏的后端 VPS 域名/Argo 域名仍在隧道外直接向境内解析器暴露。
  3. **Serv00 `clashmeta.txt` 全量访问域名泄露：** 第 560、566 行令 `dns.enable: false`，同时规则列表使用未加 `no-resolve` 的 `GEOIP,LAN,DIRECT` 与 `GEOIP,CN,DIRECT`。客户端经 `127.0.0.1:7890` 访问任何网站时，Mihomo 为判断 GeoIP 会调用操作系统默认 DNS 明文解析所有目标域名，且一旦遭遇污染返回私网/境内 IP 还会错误直连。
- **最小修复建议**
  - 将客户端 `dns-cn` / `default_domain_resolver` 升级为加密 DoH（如 `https://223.5.5.5/dns-query`，以 IP 直连无需额外引导域名）；
  - 客户端 WARP 前置模式下为 `WARP-前置` 启用 `remote-dns-resolve: true`（配 `dns: [1.1.1.1, 8.8.8.8]`）或将域名节点的解析路由进前置隧道；
  - Serv00 `clashmeta.txt` 对齐 VPS 方案启用监听 `127.0.0.1` 的加密 DNS/FakeIP，或前置 `GEOSITE,CN,DIRECT` 并为 `GEOIP` 规则补齐 `,no-resolve`。

---

### 8. 【中高危】`iptables`/`ufw`/`nftables` 的 `PREROUTING REDIRECT` 改写目的端口与副 IP，且漏判 `::ffff:` 映射回环地址

- **涉及位置**
  - [`tools/firewall.py`](../tools/firewall.py#L67-L74)（`listeners`）
  - [`tools/firewall.py`](../tools/firewall.py#L372-L395)（`iptables_rule`）、[`tools/firewall.py`](../tools/firewall.py#L443-L499)（`nft_rule`）
- **漏洞机理与实际后果**
  1. **跳跃端口在 `INPUT` 过滤前被改写：** `nat PREROUTING` 优先级（`-100`/`-99`）先于 `filter INPUT`（`0`），将目的端口从跳跃区间改写为节点主端口，导致管理员在 `INPUT` 中针对跳跃区间端口配置的封禁或限速规则（未使用 `conntrack --ctorigdstport` 时）失效。
  2. **`REDIRECT` 篡改多 IP 网卡上的副目的地址：** Netfilter 的 `REDIRECT` 在 `PREROUTING` 中会将数据包目的 IP 强制改写为入站网卡的**主 IP**（`ifa_local`）。若节点监听同一网卡的副 IP `<IP2>`，发往 `<IP2>:<跳跃端口>` 的报文会被改写到 `<主IP>:<目标端口>`（可能误投至绑定在 `<主IP>` 的另一服务，或被 `INPUT -d <IP2>` 规则拦截）。
  3. **`::ffff:` 映射地址识别错误：** 第 68 行 `ipaddress.ip_address("::ffff:127.0.0.1").is_loopback` 在 Python 标准库中返回 **`False`**，且第 374、404、445 行仅用 `":" in addr` 判断 IPv6，导致绑定 `::ffff:127.0.0.1` 的回环入站未被跳过，还会向错误的 IPv6 防火墙表写入规则。
- **最小修复建议**
  - 在 `listeners()` 读取地址后立即展开 `addr = addr.ipv4_mapped or addr`；
  - 当入站绑定具体单播地址而非通配地址时，使用仅改写目的端口的 `DNAT --to-destination <addr>:<target>`（`nft dnat to`）代替 `REDIRECT`。

---

### 9. 【中危】多用户 VPS 的 Argo 回环源站仍为明文 HTTP 且停服不停隧道；Serv00 固定隧道缺本机校验且改端口存在抢注窗口

- **涉及位置**
  - VPS：[`sb.sh`](../sb.sh#L3165)、[`sb.sh`](../sb.sh#L3245)、[`sb.sh`](../sb.sh#L5158-L5169)
  - Serv00：[`serv00_runtime.py`](../serv00_runtime.py#L384-L392)、[`serv00_runtime.py`](../serv00_runtime.py#L707-L709)、[`serv00_runtime.py`](../serv00_runtime.py#L958-L970)
- **漏洞机理与实际后果**
  1. **多用户 VPS 回环源站抢占：** VPS 的临时与固定 Argo 隧道仍指向明文 `http://127.0.0.1:$vm_port`（非特权高位端口），且管理员执行菜单 `6 → 2`（`singbox_stop`）关闭 `sing-box` 时，`argo.service` 与临时 `cloudflared` 保持运行。在多用户 VPS 上，停服或重启空窗期内本机非特权用户可绑定 `127.0.0.1:$vm_port`，从 `cloudflared` 明文转发请求中获取连接客户端的真实 IP（`CF-Connecting-IP`）与 WS 路径。
  2. **Serv00 固定隧道与改端口竞争：** 固定隧道（`TUNNEL_TOKEN`）无法由本机命令行强制开启源站证书校验，若远端控制台误开 `No TLS Verify` 且 `sb` 在 10 分钟保活间隔内退出，同机租户在未开启回环 UID 隔离的宿主上可劫持回源；此外 `reset_ports()` 在停止 `sb` 及完成 `argo_dedicated_port` 冲突校验**之前**就调用 `devil port del` 释放旧端口，失败回滚前同机其他租户可通过 `devil port add` 抢占该端口。
- **最小修复建议**
  - VPS 侧在 `singbox_stop` 时同步停止 `argo` 隧道（并在多用户主机支持启用本地自签 HTTPS 回源）；
  - Serv00 `reset_ports()` 先完成全部端口冲突检查并停止 `sb` 后，再执行端口释放与申请。

---

### 10. 【中危】Serv00 订阅入口的 HTTPS 强制规则位于符号链接目标目录（`public_html` 之外）

- **涉及位置**
  - [`serv00_runtime.py`](../serv00_runtime.py#L723-L767)（`new_public`）
- **漏洞机理与实际后果**
  - `public_html/sing-box-sub/.htaccess`（第 738 行）仅写入了 `Options -Indexes`；而强制 HTTPS 的 `RewriteCond %{HTTPS} !=on` 仅写在 `public_html` 之外的 `~/domains/<site>/.sing-box-subscriptions/.sb-sub-xxx/.htaccess`（第 762 行），通过符号链接暴露。当宿主 Apache/LiteSpeed 将 `AllowOverride` 作用域限定在 `<Directory ".../public_html">` 目录树内时，跳出 `public_html` 的子 `.htaccess` 不会生效，导致客户端误用 `http://` 请求时不会返回 `403`，在网络路径上明文暴露 48 位订阅令牌与节点凭据。
- **最小修复建议**
  - 将 `RewriteEngine On` / `RewriteCond %{HTTPS} !=on` / `RewriteRule ^ - [F,L]` 直接一并写入 `public_html/sing-box-sub/.htaccess`。

---

### 11. 【中危】Serv00 服务端 `s14`/`s15` 引用可变分支规则集、`legacy_warp_policy()` 校验绕过与 1.14 域名型 WARP 启动失败

- **涉及位置**
  - [`serv00_runtime.py`](../serv00_runtime.py#L249-L269)（`legacy_warp_policy`）
  - [`serv00_runtime.py`](../serv00_runtime.py#L418-L423)、[`serv00_runtime.py`](../serv00_runtime.py#L448-L466)（`server_config`）
  - [`serv00_runtime.py`](../serv00_runtime.py#L610-L620)（`temporary_tunnel_healthy`）
- **漏洞机理与实际后果**
  1. **服务端硬编码可变 `@sing` 分支：** `s14`/`s15` 服务端默认从 `MetaCubeX/meta-rules-dat/sing` 动态拉取 `google-gemini.srs`（不受 `SERV00_CLIENT_RULES_REVISION` 固定），且未配置 `warp.json` 时命中域名会被转为 `reject`/`block`，上游规则变更可直接改变服务端分流或阻断范围。
  2. **旧版策略迁移绕过与直连 SSRF：** `legacy_warp_policy()` 第 257 行 `for tag in rule.get("rule_set", [])` 在旧配置 `"rule_set"` 为单个字符串（`sing-box` 合法语法）时会按字符迭代，漏掉对该 `rule_set` URL 的检查；同时未递归检查 `type: "logical"` 规则内部嵌套的 `override_address` 与 `rule_set`；且远端 `rule_set` 通过 `download_detour: "direct"` 下载，绕过 `RESTRICTED`（若旧 `0777` 目录曾被植入 `https://127.0.0.1:...` 等地址且管理员指定备份恢复，服务端将直连请求该内网/回环 URL）。此外 `temporary_tunnel_healthy()` 使用 `HTTPSRedirect` 而非禁止重定向，若隧道返回 `30x` 可诱导服务端探测 `https://127.0.0.1:...`。
  3. **1.14.x 域名型 WARP 对端不兼容：** 当 `warp.json` 的 `server` 为域名（如 `engage.cloudflareclient.com`）时，1.14.x 服务端配置未定义 `dns` 与 `route.default_domain_resolver`，导致 `sing-box check` 直接报错退出。
- **最小修复建议**
  - 服务端远程规则集同样绑定受审 40 位提交号；`legacy_warp_policy()` 兼容字符串/列表型 `rule_set`、拒绝含 `logical` 复杂嵌套或指向私网/本机的 URL；`temporary_tunnel_healthy()` 改用 `RejectRedirect`；为 1.14.x 服务端配置补充默认本地/受控 `domain_resolver`。

---

### 12. 【中低危】ACME 域名变更状态失步、`network_probe.py` 跨源重定向探测与 `kp.sh` 非 PTY 断连凭据残留

- **涉及位置**
  - [`sb.sh`](../sb.sh#L5567-L5589)（`acme`）
  - [`tools/network_probe.py`](../tools/network_probe.py#L73-L91)、[`tools/network_probe.py`](../tools/network_probe.py#L138-L150)
  - [`kp.sh`](../kp.sh#L27-L38)、[`kp.sh`](../kp.sh#L47)；[`serv00_runtime.py`](../serv00_runtime.py#L1081-L1089)
  - [`workers_keep.js`](../workers_keep.js#L13)
- **漏洞机理与实际后果**
  1. **`acme()` 未收紧既有目录权限且未同步运行态：** `mkdir -p -m 700 /root/ygkkkca` 不会收紧已存在目录或手工放入私钥的权限；重新申请新域名证书后未更新 `sb.json`/`sb_extra.json` 中的 `.tls.server_name` 也未重启 `sing-box`，导致客户端订阅新 SNI 与服务端内存旧证书/配置旧 SNI 失配。
  2. **`network_probe.py` 跟随跨源重定向与未校验代理协议：** `curl` 启用 `--location --max-redirs 10`，当被测外部 URL 返回 `302` 指向 `https://127.0.0.1:...`（或 HTTP 目标重定向到 `http://169.254.169.254/...`）时，工具会绕过环境代理直接探测内网/回环并输出状态码、字节数与耗时；且 `--proxy` 未限制协议类型（如公网明文 `http://user:pass@...` 会明文发送代理凭据）。
  3. **`kp.sh` 无 PTY 断连不触发远端 `SIGHUP`：** `kp.sh` 使用 `ssh -T`，若部署期间网络断开或 CI 任务取消，远端 `sshd` 关闭管道但**不会**向无控制终端的 `/bin/sh` 发送 `SIGHUP`，导致含明文 `SUUID` 与 `ARGO_AUTH` 的 `$HOME/.serv00-deploy.XXXXXX/input.json` 在子进程超时退出前持续留在磁盘。此外 [`workers_keep.js`](../workers_keep.js#L13) 的 `await res.text()` 未像 [`.github/workflows/main.yml`](../.github/workflows/main.yml#L28) 那样限制读取长度，异常大响应会耗尽 Worker 内存。
- **最小修复建议**
  - `acme()` 显式执行 `chmod 700 /root/ygkkkca` 及私钥权限核验，换证后同步关联入站的 `server_name`、重载服务并刷新订阅；
  - `network_probe.py` 默认关闭跨源重定向（或校验重定向目标非私网/回环），并校验 `--proxy` URL 协议方案；
  - 在 `serv00_runtime.py` 第 1084 行 `load(args.account_file)` 读取完成后立即 `args.account_file.unlink(missing_ok=True)`，并在 `workers_keep.js` 中限制响应字节读取上限。

---

## 已核验通过的基线模块

以下模块在提交 `b4b7a3c` 经逐行核查未发现可触发的网络漏洞：
- [`app.js`](../app.js)：仅监听 `127.0.0.1` 并严格匹配只读 `GET`/`HEAD /health`，无子进程调用、无进程信息或环境变量回显。
- [`serv00.sh`](../serv00.sh) / [`serv00keep.sh`](../serv00keep.sh)：严格 `set -eu`、`umask 077`，仅从受审本地发布目录调用 `serv00_runtime.py`。
- [`.github/workflows/main.yml`](../.github/workflows/main.yml)：最小空权限 `permissions: {}`，强制 `https`、精确 `/health` 路径、禁用重定向并限制读取 4 字节，日志脱敏。
- [`tools/releases.sha256`](../tools/releases.sha256) 与 [`tools/compare_client_configs.py`](../tools/compare_client_configs.py)：二进制及规则库摘要固定为 64 位 SHA-256；配置对比使用 `yaml.safe_load` 且不输出标量敏感值。
