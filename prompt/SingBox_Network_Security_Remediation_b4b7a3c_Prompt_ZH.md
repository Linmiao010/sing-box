# sing-box 网络安全审计修复实施 Prompt（基线：`b4b7a3c`）

请在 `Linmiao010/sing-box` 仓库中，依据[网络安全审计报告（基线 `b4b7a3c`）](../docs/network-security-audit-b4b7a3c.md)实施修复。实施前先阅读 [`AGENT.md`](../AGENT.md)，核对当前 Git 提交与工作树，再从实际安装、菜单、配置修补、订阅生成、防火墙对账和自动化入口追踪全部数据流。采用满足当前需求的最小充分方案，不引入无关重构或多余抽象层。若某项已经修复，给出当前代码位置与回归验证结果；涉及旧部署行为变化或迁移步骤更新的，同步维护 [`docs/README.md`](../docs/README.md) 与 [`docs/security-migration.md`](../docs/security-migration.md)。

沿用审计报告编号 `1`–`12`。优先处理高危项 `1`、`2`、`3`、`4`，其次处理中高危项 `5`、`6`、`7`、`8`，最后收口 `9`、`10`、`11`、`12`。修改过程中必须完整保留已建立的安全不变量：发布清单 SHA-256 摘要校验、SSH `StrictHostKeyChecking=yes` 主机密钥校验、订阅读写凭据隔离、共用锁顺序（`s-box-config.lock` 先于 `subscription.lock`）以及候选配置校验失败后的原子回滚。

---

## 修复任务与验收条件

### 1. 阻断多路复用 UDP 会话（`PacketConn`）的目标重定绕过

- **修改范围**：[`sb.sh`](../sb.sh) 的 `inssbjsonser()`、`secure_server_routes()`、`ip_priority_get()`、`ip_priority_patch()`，以及 [`serv00_runtime.py`](../serv00_runtime.py) 的 `server_config()`。
- **实施要求**：
  - 在 `1.14.x` 服务端路由中，在受限地址拦截之后、进入 `direct` 出站之前对 UDP 流量应用 `{"network": "udp", "action": "route-options", "udp_connect": true}`（或等价受控机制），确保每个 `direct` UDP 套接字锁定在首包已通过受限检查的单一目标 `IP:Port` 上，拒绝同一 `PacketConn` 后续数据报改写目的地址发往回环、私网或元数据地址。同步确认不影响 `ip_priority_get()` / `ip_priority_patch()` 对规则顺序的读取与修补。
  - 对不支持 `route-options` 的 `1.10.7`，评估并采用最小可行隔离措施（如在 Linux systemd/OpenRC 下使用独立运行身份配合主机 `OUTPUT` 规则拦截回环与受限网段，或在保持兼容性的前提下限制非连接态多目标 UDP 直连并在迁移文档注明平台约束）。
- **验收条件**：在 [`tests/test_security_server_routes.py`](../tests/test_security_server_routes.py) 新增真实内核 UDP 会话测试（模拟同一 UDP 关联/会话首包发往允许的公网 IP、第 2 包改发 `127.0.0.1` 或 `100.64.0.7`），验证第 1 包可达、第 2 包被拒绝且本地 UDP 探针未收到报文。

### 2. 修复 VPS 新版客户端 `sbox.json` 缺失 `sniff` 导致的 TUN DNS 泄露

- **修改范围**：[`sb.sh`](../sb.sh) 的 `sb_client()`（`sball` 模板及 `1.10` 降级分支）。
- **实施要求**：
  - 在 `1.14.x` 客户端 `sbox.json` 的 `route.rules` 最前部加入 `{"inbound": ["tun-in"], "action": "sniff"}`，确保 `{"inbound": ["tun-in"], "protocol": ["dns"], "action": "hijack-dns"}` 以及后续 `geosite-cn` 域名分流正常生效；同时对发往端口 `53` 的流量提供可靠的 DNS 劫持兜底，防止系统配置私网网关 DNS（如 `192.168.1.1:53`）时落入 `ip_is_private -> direct` 明文泄露。
  - 在 `sbnh == 1.10` 的 `jq` 降级转换中清理新版专属 `action == "sniff"` 规则，保持 `1.10.7` 配置校验通过。
- **验收条件**：分别用 `sing-box` `1.10.7` 与 `1.14.3` 执行 `check` 校验生成的 `sbox.json`；在回归测试中断言新版 `sbox.json` 顺序为 `sniff` 先于 `hijack-dns`、先于 `ip_is_private`，并在回环模拟环境下验证发往私网 DNS 地址的查询被 `sing-box` 内部 DNS 接管而非直连透传。

### 3. 修正 `firewalld` 富规则优先级与 UDP 端口跳跃的 `ct status dnat` 绕过

- **修改范围**：[`tools/firewall.py`](../tools/firewall.py) 的 `firewalld_rule()`、`manage()`、`desired()`，以及 [`sb.sh`](../sb.sh) 的 `openyn()` / `--firewall-enable`。
- **实施要求**：
  - 将 `firewalld` 纳入与 `iptables`、`ufw`、`nft` 一致的放行顺序保护：移除 `accept` 富规则上的负优先级 `priority="-17461"`，改为在 `_pre` 与 `_deny` 之后执行的优先级（如正优先级追加至 `_post`，或经核实在无后置全拒规则时使用不早于 `_deny` 的固定正优先级），保留基于规则精确内容的归属识别与增删对账，升级时平滑清理旧版 `-17461` 条目。
  - 处理 `firewalld` 下 `forward-port` 在 `PREROUTING` 打上 `ct status dnat` 后绕过 `filter_INPUT_ZONES` 的问题：未显式启用项目放行或未确认 `StrictForwardPorts` / 区域封禁影响前，不得通过 `add` 静默绕过管理员来源封禁开放公网 UDP 入站。
- **验收条件**：在 [`tests/test_security_firewall_release.py`](../tests/test_security_firewall_release.py) 覆盖 `firewalld` 后端的新装、旧 `-17461` 规则迁移、失败回滚，以及存在默认 `priority=0` 来源 `drop` 富规则时允许来源与受限来源的匹配顺序验证。

### 4. 加固 Serv00 与 VPS 证书约束、外部证书链核验及 `--rotate` 私钥轮换

- **修改范围**：[`serv00_runtime.py`](../serv00_runtime.py) 的 `certificate()`、`prepare_origin()`、`configure()`、`perform("show")`，以及 [`sb.sh`](../sb.sh) 的 ACME/外部证书读取与凭据轮换逻辑。
- **实施要求**：
  - 在 `serv00_runtime.py` 生成节点自签证书与 `origin-tls` 证书时，显式添加 `-addext basicConstraints=critical,CA:FALSE`、`-addext keyUsage=critical,digitalSignature,keyEncipherment` 与 `-addext extendedKeyUsage=serverAuth`；修改 `--show` 的提示文案，明确不要将共享宿主机上的自签证书导入操作系统或浏览器全局根信任库。
  - 当通过 `SERV00_TLS_CERT` / `SERV00_TLS_KEY` 或 VPS `/root/ygkkkca/{ca.log,cert.crt,private.key}` 提供外部证书时，核验私钥与证书公钥匹配、证书未过期、主机名匹配 `SAN`，并使用系统受信 CA 根库（禁止 `-trusted <待测证书自身>` 自举）判断是否为公共受信证书；非公共受信证书不得剥离客户端完整订阅中的 `certificate` / `fingerprint` / `pinSHA256`。
  - `serv00_runtime.py` 执行 `--rotate` 时，除轮换 `uuid`、`path`、`token` 外，同步重新生成自签节点证书与私钥（若未使用外部受信证书）、`reality.key`/`public_key`（以及未绑定未确认固定隧道时的 `origin-tls`，或提供明确轮换路径）。
- **验收条件**：用 OpenSSL 检查新生成的 Serv00 自签证书包含 `CA:FALSE`；模拟向 `SERV00_TLS_CERT` 传入自签证书，确认不会生成无固定指纹的裸客户端配置；验证 `--rotate` 后新旧订阅中的证书指纹、`public_key` 与 `uuid` 均已更新且旧凭据失效。

### 5. 补齐服务端受限地址范围（NAT64 / 6to4 / 本机公网 IP）并收紧 `socks-out` 解析

- **修改范围**：[`sb.sh`](../sb.sh) 的 `secure_server_routes()`、`inssbjsonser()`，以及 [`serv00_runtime.py`](../serv00_runtime.py) 的 `RESTRICTED`、`server_config()`。
- **实施要求**：
  - 在两端默认受限网段中补充 `64:ff9b::/96`、`64:ff9b:1::/48`、`::ffff:0:0:0/96`、`2002::/16`、`100::/64`、`192.0.0.0/24`、`198.18.0.0/15`。
  - 将当前实例已知的本机全局单播地址（VPS 本机公网 IPv4/IPv6 `/32`、`/128`，Serv00 的 `state["addresses"]`）并入服务端受限目标列表，防止代理流量经本机回环绕过云安全组、外部网卡防火墙或探测同宿主机其他 Serv00 租户端口。
  - 为 `sb11.json` 中的 `socks-out` 绑定受控解析结果（如设置匹配的 `domain_strategy` 或解析器约束），避免路由层检查通过后下游 SOCKS5 按原始域名二次解析重绑定。
- **验收条件**：在路由回归测试中验证 `[64:ff9b::a9fe:a9fe]`、`[2002:7f00:1::1]`、本机公网地址及经 DNS64 合成的地址均被拒绝，且 `socks-out` 收到的目标为经校验的 IP 地址而非未决域名。

### 6. 修复 GitLab 订阅仓库可见性的鉴权核验绕过

- **修改范围**：[`sb.sh`](../sb.sh) 的 `gitlab_settings_valid()` 与 `gitlab_publish()`。
- **实施要求**：
  - `gitlab_settings_valid()` 拒绝 `.git` 结尾的项目路径。
  - `gitlab_publish()` 对 GitLab API 的 `curl` 调用补齐 `--noproxy '*' --proto '=https'`，并通过受保护输入（如 `--config /dev/fd/3` 或只读凭据头，不在命令行参数暴露令牌）携带发布令牌查询 `https://gitlab.com/api/v4/projects/${project//\//%2F}`，使用 `jq -e` 精确校验返回对象的 `.visibility == "private"` 且 `.path_with_namespace | ascii_downcase` 与 `${project,,}` 完全一致；同时保留匿名请求 `info/refs?service=git-upload-pack` 非 `200` 的双重校验。
- **验收条件**：在本地替身服务中分别模拟 `public`、`internal`、`private` 仓库以及项目名含 `.json` / `.git` 后缀的公开仓库，验证仅真正的 `private` 仓库允许执行 `git push`，其余均在推送前中止且进程命令行不含明文令牌。

### 7. 消除客户端订阅中的节点域名与访问域名明文 DNS 泄露

- **修改范围**：[`sb.sh`](../sb.sh) 的 `sb_client()`、`client_warp_read()`，以及 [`serv00_runtime.py`](../serv00_runtime.py) 的 `emit_clients()`。
- **实施要求**：
  - 将 VPS `sbox.json` 与 Serv00 `singbox.txt` 中默认用于直连及节点域名解析的 `dns-cn` 从明文 UDP `223.5.5.5:53` 升级为 IP 直连的加密 DNS（如 `https://223.5.5.5/dns-query`，兼容 `1.10` 与 `1.14` 各自的服务器配置语法）。
  - 在启用客户端 WARP 前置（`clmi.yaml`）时，为 `WARP-前置` 配置 `remote-dns-resolve: true` 与 `dns: ["1.1.1.1", "8.8.8.8"]`（或确保经 `WARP-前置` 拨号的域名型节点不在隧道外向境内 DNS 泄露节点域名）。
  - 修正 Serv00 `clashmeta.txt`：避免在 `dns.enable: false` 下对所有代理域名触发系统明文 DNS 解析以匹配 `GEOIP,CN`（启用受控本机回环加密 DNS/FakeIP，或前置 `GEOSITE,CN,DIRECT` 并为 `GEOIP` 规则加上 `,no-resolve`）。
- **验收条件**：分别用原生 `sing-box`（`1.10.7`、`1.14.3`）与 `mihomo` 校验生成的客户端配置；核对节点域名及受代理域名不会触发明文 UDP 53 查询。

### 8. 修正 `firewall.py` 对 `::ffff:` 映射地址的识别与单播地址端口转发方式

- **修改范围**：[`tools/firewall.py`](../tools/firewall.py) 的 `listeners()`、`iptables_rule()`、`nft_rule()`、`iptables_forwards()`、`nft_forwards()`。
- **实施要求**：
  - 在 `listeners()` 入口对 `ipaddress.ip_address(...)` 执行 `addr = addr.ipv4_mapped or addr` 归一化，确保 `::ffff:127.0.0.1` 被正确识别为回环地址跳过，且 `::ffff:<ipv4>` 归入 IPv4 规则表。
  - 当入站监听具体单播 IP（而非 `0.0.0.0` / `::` 通配地址）时，UDP 端口跳跃使用保持原目的单播 IP 不变的 `DNAT --to-destination <addr>:<target>`（及 `nft` 对应 `dnat to`），避免 `REDIRECT` 在多 IP 网卡上把发往副 IP 的流量改写到主 IP；同步更新 `iptables_forwards()` 与 `nft_forwards()` 的本项目的规则归属排除逻辑。
- **验收条件**：补充单元测试覆盖 `::ffff:127.0.0.1`（不生成公网放行/转发）、`::ffff:198.51.100.2`（生成 IPv4 `DNAT`）以及多单播 IP 共存时的精确匹配与清理。

### 9. 收紧多用户 VPS 的 Argo 生命周期与回源保护，以及 Serv00 改端口顺序

- **修改范围**：[`sb.sh`](../sb.sh) 的 `singbox_stop()`、`cfargo()`、`cfargoym()`，以及 [`serv00_runtime.py`](../serv00_runtime.py) 的 `reset_ports()`、`prepare_origin()`。
- **实施要求**：
  - VPS 管理员通过菜单 `6 → 2`（`singbox_stop`）关闭服务时，同步停止活动的临时或固定 Argo 隧道进程/服务（并在文档或回源配置中消除停服期间回环明文端口悬空被非特权用户抢占的风险；如支持回环自签 TLS 回源则保持与固定隧道提示一致）。
  - Serv00 的 `reset_ports()` 先完成新端口合法性、互不相同及与 `argo_dedicated_port` 不冲突的全部前置校验，并先停止 `sb` 释放绑定后，再执行 `devil port del` 与新端口分配。若 `origin-tls` 被重建而 `argo_token` 存在，必须重新触发固定隧道控制台确认提示。
- **验收条件**：模拟 VPS 启用 Argo 后执行停服，确认 `sing-box` 与 `cloudflared` 均停止；模拟 Serv00 `--ports` 输入与 `argo_dedicated_port` 冲突的端口，确认 `devil port del` 调用次数为零且原服务保持可用。

### 10. 补齐 Serv00 `public_html/sing-box-sub/.htaccess` 的 HTTPS 强制规则

- **修改范围**：[`serv00_runtime.py`](../serv00_runtime.py) 的 `new_public()`。
- **实施要求**：在 `PUBLIC / SUB_DIRECTORY / ".htaccess"` 中一并写入 `Options -Indexes` 与 `RewriteEngine On` / `RewriteCond %{HTTPS} !=on` / `RewriteRule ^ - [F,L]`，兼容已存在旧版仅含 `Options -Indexes\n` 的受控升级更新，不因升级自检报错中断。
- **验收条件**：覆盖全新安装与从 `b4b7a3c` 升级两种路径，核对入口目录与快照目录两处 `.htaccess` 均包含 HTTPS 强制规则及 `0640` 权限。

### 11. 收紧 Serv00 服务端远程规则集、`legacy_warp_policy()` 校验与 1.14 域名型 WARP 解析

- **修改范围**：[`serv00_runtime.py`](../serv00_runtime.py) 的 `legacy_warp_policy()`、`server_config()`、`temporary_tunnel_healthy()`。
- **实施要求**：
  - 将 `s14`/`s15` 服务端 `google-gemini.srs` 的默认分支改为经审查的固定 40 位提交号（或与受审规则版本绑定）。
  - 修复 `legacy_warp_policy()`：兼容 `"rule_set"` 字段为字符串或字符串数组两种格式；拒绝含 `type == "logical"` 未审嵌套、`override_address` 或指向回环/私网/IP 字面量/非受信主机的 `rule_set.url`。
  - `temporary_tunnel_healthy()` 改用禁止重定向的 opener（`RejectRedirect`），防止 `30x` 跳转到内网 HTTPS 地址。
  - 在 `server_config()` 的 `1.14.` 分支补充受控 `dns` 与 `route.default_domain_resolver`，使 `warp.json` 中 `server` 为域名时也能通过 `sing-box check` 并正常启动。
- **验收条件**：分别验证字符串型 `"rule_set": "tag"`、嵌套 `logical` 规则、内网 `https://127.0.0.1/...` 规则集 URL 均被拦截；验证 `1.14.3` 内核配合域名型 `warp.json` 通过 `sing-box check`。

### 12. 修复 `acme()` 状态同步、`network_probe.py` 重定向边界、`kp.sh` 凭据清理与 `workers_keep.js` 响应上限

- **修改范围**：[`sb.sh`](../sb.sh) 的 `acme()`，[`tools/network_probe.py`](../tools/network_probe.py)，[`serv00_runtime.py`](../serv00_runtime.py) 的 `main()`，[`kp.sh`](../kp.sh)，[`SSH.yml`](../SSH.yml)，[`serv00.yml`](../serv00.yml)，[`workers_keep.js`](../workers_keep.js)。
- **实施要求**：
  - `acme()` 显式收紧 `/root/ygkkkca` 目录权限（`chmod 700`）并校验私钥权限与证书域名匹配；若当前 `sb.json` 或 `sb_extra.json` 已有入站使用 `/root/ygkkkca/cert.crt`，在证书域名变更或续签后通过 `config_locked` 同步更新对应 `.tls.server_name`、重启/重载 `sing-box` 并刷新订阅。
  - `tools/network_probe.py` 默认禁用跨主机重定向（或移除 `--location`，仅在显式参数要求且目标非私网/回环时允许），并校验 `--proxy`（含 `--private-stdin` 中的 `proxy`）的协议方案合法性。
  - `serv00_runtime.py` 在 `main()` 读取完 `--account-file` 后立即执行 `unlink(missing_ok=True)` 删除临时凭据文件，消除 `ssh -T` 断连不发 `SIGHUP` 时的凭据残留窗口；`SSH.yml` 与 `serv00.yml` 的 `ssh-agent` 清理补齐 `HUP INT TERM`；`kp.sh` 补充连接保活超时参数。
  - `workers_keep.js` 读取响应体时限制最大字节数（例如流式读取不超过 16 字节即中止），避免异常大响应耗尽内存。
- **验收条件**：在单元测试中验证 `network_probe.py` 拒绝跨源内网重定向与非法代理协议、`--account-file` 在进程阻塞期间已被立即删除、`acme()` 换域名后服务端 SNI 与客户端订阅保持一致。

---

## 验证与交付要求

1. **测试与回归验证**：运行 `python -m unittest discover -s tests -v`，确保现有回归与本次新增的 12 项针对性测试全部通过（若本地具备 `SING_BOX_110_BIN`、`SING_BOX_BIN`、`MIHOMO_BIN`、`caddy`，一并运行原生验证并记录实测版本）。所有测试须在临时目录、进程替身或回环套接字内完成，严禁修改当前开发机的防火墙、DNS 或系统服务。同时通过 `bash -n`、`node --check` 与 `python -m py_compile` 语法检查。
2. **修复结果清单**：按编号 `1`–`12` 逐项列出修改文件、核心改动逻辑、执行的验证命令与实际结果，区分本地已验证项与需在目标 Linux VPS / FreeBSD Serv00 实机复核的前提条件。
3. **旧部署迁移说明**：说明从 `b4b7a3c`（及更早基线）升级时的操作顺序（包括 `firewalld` 旧负优先级规则替换、服务端路由更新、Serv00 自签证书重新签发与 `--rotate` 轮换、客户端重新拉取订阅），标明会短暂中断现有连接的步骤与失败回滚方法。
