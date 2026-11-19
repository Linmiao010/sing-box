# 网络安全审计报告与修改方案

审计日期：2026-11-19

本次复核以提交 `b0a8951` 为准，覆盖 VPS 与 Serv00 的服务端配置、客户端订阅、运维工具和自动化工作流。以下结论来自源码及所引上游实现。未连接实际 VPS、Serv00 或客户端，涉及主机权限、网络可达性和服务状态的影响均以各条列出的条件为前提。上一轮改造见[安全迁移与验收](security-migration.md)。

## 现有安全基线核对

当前代码采用以下安全机制。

- 安装脚本对发布清单中列出的资产校验固定 SHA-256 摘要，Serv00 二进制的摘要记录在 `ASSETS` 中。客户端运行期间另外下载的分流规则见文末“分流规则供应链风险”。[VPS 发布清单](../tools/releases.sha256) · [Serv00 资产清单](../serv00_runtime.py#L27-L32)
- 主机防火墙工具维护项目规则，保留管理员已有条目。第 9 项说明新增规则的顺序如何影响既有封禁，第 13 项说明 UDP 转发范围重叠的影响。[防火墙管理](../tools/firewall.py)
- VPS 公网订阅经 Caddy HTTPS 分发，回环 HTTP 后端以 `sboxsub` 低权限账户只读发布快照；GitLab 写凭据通过独立 `GIT_ASKPASS` 仅用于服务端推送。
- Serv00 网页面板 [`app.js`](../app.js) 仅监听 `127.0.0.1` 并暴露只读 `/health`；批量 SSH 脚本 [`kp.sh`](../kp.sh) 强制 `StrictHostKeyChecking=yes`。
- 自签证书节点在完整订阅中携带公证书 PEM 或 SHA-256 指纹校验，Mihomo 默认关闭 `allow-lan`。

---

## 待修复漏洞清单

### 1. 【高危】服务端代理出站未拦截回环、内网与云元数据地址（代理 SSRF 击穿本机隔离）

- **涉及位置**
  - [`sb.sh`](../sb.sh) 第 1168–1236 行（`inssbjsonser` 的 `sb10.json` `route.rules`）
  - [`sb.sh`](../sb.sh) 第 1387–1424 行（`inssbjsonser` 的 `sb11.json` `route.rules`）及第 3273–3333 行（`ip_priority_get` / `ip_priority_patch`）
  - [`serv00_runtime.py`](../serv00_runtime.py) 第 337–358 行（`server_config` 的 `route`）
- **问题机理与实际后果**
  - 客户端订阅模板配置了 `ip_is_private` 规则，但**服务端**配置（`sb10.json`、`sb11.json` 及 Serv00 `config.json`）默认将未命中分流规则的请求直连转发（`direct`），未拒绝回环、RFC1918 私网、CGNAT 与链路本地地址。
  - **击穿本机回环隔离边界：** 任何仅持有单个节点分享链接的连接者，均可经由服务端代理访问 VPS 的 `127.0.0.1` 服务，包括：
    - 菜单 `3 → 8 → 1` 的本机 HTTP 订阅后台。连到端口后仍需知道有效的订阅路径令牌才能读取三个文件。仅在已启用客户端 WARP 前置时，`clmi.yaml` 才含该私钥。
    - `shared` 模式下系统 Caddy 默认监听在 `127.0.0.1:2019` 的无鉴权 Admin API（可读取 `/etc/caddy/s-box-subscription.caddy` 中的私密订阅路径令牌，或通过 `POST /load` 修改运行配置）；
    - `127.0.0.1:40000` 的 `sbwpph` SOCKS5 代理及宿主机其他回环服务（如 Redis、MySQL 等）。
  - 在云主机上，影响还取决于元数据服务是否启用、能否从代理进程到达以及平台要求的认证条件。可达时可能暴露实例元数据。临时凭据的泄露还要求实例已获授相应身份且请求满足平台的访问策略。
  - **Serv00 多租户横向探测：** Serv00/Hostuno 同机租户共享内核网络栈与 `127.0.0.1`，代理客户端可探测同宿主机其他租户监听在回环地址上的后端端口。
- **修改方案**
  - **地址范围。** sing-box 1.14.3 的 `ip_is_private` 判断包含回环、私网和链路本地地址，但漏掉 CGNAT 共享网段 `100.64.0.0/10`。阿里云元数据地址 `100.100.100.200` 落在该段。要达到本项声明的保护范围，应显式拒绝该网段，并纳入部署环境特有的元数据地址。[1.14.3 匹配逻辑](https://github.com/SagerNet/sing-box/blob/v1.14.3/route/rule/rule_item_ip_is_private.go#L16-L36) · [该版本使用的地址分类](https://github.com/SagerNet/sing/blob/0ad23b637bd4/common/network/addr.go#L17-L24)
  - **规则顺序。** 检查应发生在任何可提前选定直连、SOCKS 或 WARP 出站的规则之前。对域名，应检查解析结果并确保后续连接使用已检查的地址。新版模板当前在默认 `resolve` 之前已有按域名选出站的规则。调整顺序时，应保留已有 IPv4/IPv6 分流策略，同步更新 `ip_priority_get()` 和 `ip_priority_patch()` 对相邻下标的依赖。[服务端分流顺序](../sb.sh#L1387-L1423) · [地址族策略维护](../sb.sh#L3273-L3333)
  - **服务端版本。** 1.10.7 模板使用旧式 `block` 出站，并须保证在路由匹配时已经取得目标地址。1.14.3 使用路由动作 `reject`。旧式 `block` 从 1.13.0 起已移除。Serv00 应依据实际服务端内核生成并校验相应配置，`SERV00_CLIENT_SERIES` 仅决定发给客户端的配置格式。[上游版本说明](https://sing-box.sagernet.org/deprecated/#legacy-special-outbounds) · [Serv00 配置生成与校验](../serv00_runtime.py#L715-L721)

---

### 2. 【高危】Serv00 多租户环境下 Argo 回环源站使用未受 UID 保护的临时端口（端口抢占与客户端真实 IP 泄露）

- **涉及位置**
  - [`serv00_runtime.py`](../serv00_runtime.py) 第 295–299、356、578–585、731–735、884–888 行
- **问题机理与实际后果**
  - `acquire_state()` 向系统申请临时回环端口，记录端口号后关闭探测套接字。该端口未通过 `devil` 登记给本账户。临时隧道使用明文 HTTP 回源，固定隧道的脚本提示也要求配置 HTTP。风险成立的前提是同机租户共享回环网络，且宿主允许其他账户绑定这个端口。
  - `--restart`、`--update`、`--ports` 和 `--rotate` 可在 `cloudflared` 仍运行时停止 `sb`。若请求到达错误的源站，接收方可见 WS 路径，以及源请求中实际携带的客户端 IP 转发头。端口被占用也会阻止 `sb` 恢复监听。实际可见地址以隧道配置和转发请求为准。
  - **显式端口绑定限制：** 在仅豁免 `sin_port == 0`（`autoport_exempt=1`）的 `mac_portacl` 配置下，探测套接字关闭后 `sing-box` 显式绑定非登记端口会被内核拒绝。
- **修改方案**
  - 优先使用宿主已分配给本账户的端口。若确认端口的 UID 保护覆盖回环地址，且公网监听绑定到具体单播地址，可让回环源站复用 `vmess_port`。实际保护范围和能否在两个地址分别绑定，需在目标宿主核实。
  - **临时和固定隧道须同时迁移。** 源站启用 TLS 后，临时隧道在本机启动参数中配置 HTTPS 回源、证书名称和信任根。固定隧道以 `TUNNEL_TOKEN` 启动，回源配置由 Cloudflare 控制台下发，需在控制台配置相同的 HTTPS 地址及校验参数，并确保连接器可读取所需信任根。当前脚本及手册仍提示 HTTP，单改临时隧道参数会使仍按原配置运行的固定隧道中断。切换时分别验收两种模式，并核实连接器会拒绝不受信的源站证书。[隧道启动分支](../serv00_runtime.py#L578-L585) · [脚本提示](../serv00_runtime.py#L769-L770) · [迁移手册](security-migration.md#L66)

---

### 3. 【高危】`warpcheck()` 关闭 TLS 证书校验（`-k`）及 `v6()` 未经确认覆写主机 `/etc/resolv.conf`

- **涉及位置**
  - [`sb.sh`](../sb.sh) 第 398–442、1797–1813 行
- **问题机理与实际后果**
  - `warpcheck()` 执行 `curl -s6m5 https://www.cloudflare.com/cdn-cgi/trace -k | grep warp | cut -d= -f2`，显式使用 `-k` 禁用了 TLS 证书校验，且缺少 `-q --noproxy '*'`。网络路径上的中间人可伪造响应：
    - 伪造 `warp=on` 会进入 `v6()` 与 `wgcfgo()` 的 `else` 分支，无条件执行 `systemctl enable warp-go` 与 `systemctl start wg-quick@wgcf / warp-go`，强行拉起管理员已停用的全局隧道并可能影响 SSH 路由；
    - 在已开启全局 WARP 的主机上伪造 `warp=off`，会导致 `ipuuid()` 不停用 WARP 即探测公网 IP，将 WARP 出口地址误写入 `/etc/s-box/server_ip.log`，使生成的客户端配置失效。
  - `v4orv6()`（第 413–418 行）在单次 IPv4 探测超时且 IPv6 探测成功时，未经提示直接执行 `> /etc/resolv.conf` 写入第三方公共 NAT64/DNS64（`2a00:1098:2b::1`、`2a00:1098:2c::1`），既会破坏 `systemd-resolved` 符号链接目标或云内网 DNS，也会使整机及代理后续访问 IPv4 域名的流量全部经由第三方 NAT64 网关转发。
- **修改方案**
  - 移除 `warpcheck()` 中的 `-k`，改为 `curl -q -fsS -4/-6 --noproxy '*' --connect-timeout 2 --max-time 5 https://www.cloudflare.com/cdn-cgi/trace | sed -n 's/^warp=//p'`；仅记录并恢复调用前实际处于活动状态的 `wg-quick@wgcf` / `warp-go`，不再无条件 `enable/start`。
  - `v4orv6()` 仅在本机无全局 IPv4 地址且经管理员确认后才备份并更新 `/etc/resolv.conf`（若为符号链接先解除链接再创建普通文件）。

---

### 4. 【高危】Telegram 脚本生成存在注入风险，推送与二维码命令暴露凭据

- **涉及位置**
  - [`sb.sh`](../sb.sh) 第 3555–3669 行（Telegram）、第 1915、1992、4369 行（二维码）、第 4001、4027–4028 行（订阅验收）
- **问题机理与实际后果**
  - **代码/命令注入：** `tgsbshow()` 未对 `token` 与 `userid` 做格式校验，直接通过 `sed -i "s/telegram_token/$telegram_token/g" /etc/s-box/sbtg.sh` 写入未加引号的脚本模板，随后由 `tgnotice()` 以 `root` 执行 `bash /etc/s-box/sbtg.sh`。输入若包含 `/`、`&`、空白、引号或 `$`，会破坏脚本或注入 `root` 命令、篡改 `curl` 目标 URL，造成全部节点密码及客户端 WARP 私钥外泄。
  - **进程参数泄露 Bot Token：** `sbtg.sh` 连续调用十余次 `curl -s -X POST https://api.telegram.org/bot<token>/sendMessage ...`。在默认未挂载 `/proc` `hidepid=2` 的 Linux 主机上，本机低权限服务账户（如 `sboxsub`、`sboxcaddy`）可在推送期间从 `/proc/<pid>/cmdline` 读取完整 Bot Token 与 `chat_id`。
  - **未转义 `parse_mode=HTML`：** 消息正文不含任何 HTML 标签却指定了 `MODE=HTML`，节点名或内容一旦包含 `<`、`>` 会被 Telegram API 拒绝（HTTP 400）。
  - 在允许跨用户读取进程参数的主机上，推送正文也会暴露凭据。`--data-urlencode` 的参数含节点分享链接、完整客户端配置，以及启用客户端 WARP 时的私钥。常规订阅刷新也会把完整节点 URI 交给 `qrencode`，订阅验收和订阅二维码则会把带路径令牌的 URL 交给外部进程。这些常规操作在 Telegram 关闭时仍会发生。[推送正文](../sb.sh#L3636-L3648) · [节点二维码](../sb.sh#L1911-L1915) · [HTTPS 验收](../sb.sh#L4022-L4031) · [订阅二维码](../sb.sh#L4364-L4370)
- **修改方案**
  - 严格校验 Bot Token（`^[0-9]+:[A-Za-z0-9_-]+$`）与 `chat_id`（`^-?[0-9]+$` 或 `^@[A-Za-z0-9_]{5,}$`），以 `0600` 权限单独存入 `/etc/s-box/telegram.json`（或凭据文件），`sbtg.sh` 改为静态脚本（权限 `0700`），取消 `sed` 代码替换。
  - 推送时通过受限文件或文件描述符分别传入含 Bot Token 的 URL 和消息正文。对 Telegram 请求限定 HTTPS，移除未使用的 HTML 解析模式。二维码从标准输入读取内容，订阅验收也从受保护的输入读取 URL。验收应同时覆盖 Telegram 关闭时的订阅刷新。

---

### 5. 【中高危】菜单 14（`inssbwpph`）开机命令注入、明文 HTTP 探测与宽泛 `kill` 误杀宿主进程

- **涉及位置**
  - [`sb.sh`](../sb.sh) 第 5255–5370、4956–4957、2949–2981 行
- **问题机理与实际后果**
  - **`@reboot` 命令展开注入与 Alpine 启动异常：** `inssbwpph()` 未校验 `port` 与国家代码 `guojia`，直接拼入 `/etc/s-box/sbwpph.log`，并在 root crontab 中执行 `/bin/bash -c "nohup $(cat /etc/s-box/sbwpph.log 2>/dev/null) &"`（双引号内先展开文件内容再作为 Shell 命令执行）；而在 Alpine 分支（第 5288 行）中，`nohup $(cat /etc/s-box/sbwpph.log)` 会将末尾的 `>/dev/null 2>&1` 当作字面量参数传给 `sbwpph`。
  - **明文 HTTP 探测：** 第 5312–5313、5360–5361 行使用明文 `http://icanhazip.com` 且未加 `-f` 与 IP 格式校验，出口返回任意非空错误页均会被误判为成功。
  - **卸载与重置时宽泛 `kill` 误杀宿主其他服务：** 第 4956 行（`unins`）及第 2949、2981 行（`cfargo`）执行 `ps -ef | grep "[l]ocalhost:$(config_get vmess-sb '.listen_port')" | awk '{print $2}' | xargs kill`。一旦卸载时 `/etc/s-box/sb.json` 已缺失或损坏，`config_get` 返回空串，正则退化为 `[l]ocalhost:`，**将以 `root` 身份终止宿主机上所有命令行含 `localhost:` 的无关服务进程**；即便端口非空，前缀匹配及 `grep '[s]bwpph'` 也可能误杀其他进程。
- **修改方案**
  - 校验 `sub_valid_port "$port"` 与 `[[ "$guojia" =~ ^[A-Z]{2}$ ]]`；参考 `sub-httpd.sh` 生成 `0700` 的固定启动脚本 `/etc/s-box/sbwpph-run.sh`（内部执行 `exec /etc/s-box/sbwpph ...`），替代 `$(cat /etc/s-box/sbwpph.log)`；探测改为 `https://icanhazip.com` 并加上 `-q -fsS` 与 `ip_literal_valid` 校验。
  - 停止进程前先校验端口有效性，并使用精确匹配限定项目二进制路径：
    - `cloudflared`：`pkill -f "^/etc/s-box/cloudflared tunnel --url http://(localhost|127\.0\.0\.1):${vm_port}([[:space:]]|$)"`
    - `sbwpph`：`pkill -f "^/etc/s-box/sbwpph([[:space:]]|$)"`

---

### 6. 【中危】部分域名与优选地址未校验拼入配置模板（YAML/JSON/URI 注入）及 Argo 临时隧道细节缺陷

- **涉及位置**
  - [`sb.sh`](../sb.sh) 第 3053–3057 行（`changeym` Reality 域名）、第 2871–2917 行（`cfargoym` 固定隧道域名）、第 4442–4462 行（`vmesscfadd` 优选地址）
  - [`sb.sh`](../sb.sh) 第 1824、2950–2955 行（`cfargo` 临时隧道日志提取与回源地址）
  - [`sb.sh`](../sb.sh) 第 2312–2756 行（`sb_client` 模板渲染）
- **问题机理与实际后果**
  - **缺少输入校验与直接文本插值：** `changeym` 的 Reality `domain`、`cfargoym` 的 `argoym`（第 2917 行使用未加引号的 `echo ${argoym}`，存在路径通配符展开）、`vmesscfadd` 的优选地址及外部准备的 `/root/ygkkkca/ca.log` 未经 `sub_valid_domain` / `ip_literal_valid` 校验，随后直接通过 Bash heredoc 拼入 `clmi.yaml`、`sbox.json` 与分享 URI；含换行、引号、`&` 或 `#` 的值可破坏订阅结构或注入 `skip-cert-verify: true` / `insecure=1`。此外，`vmesscfadd` 写入的 `/etc/s-box/cfymjx.txt` 在全脚本中未被读取，导致 CDN 优选 Host/SNI 设置未生效。
  - **Argo 临时隧道解析与双栈回源不一致：** 第 1824、2952 行从持续写入的 `argo.log` 按固定 `NR==2` 提取域名且缺少主机名正则校验，第 2952 行 `curl -sL https://$(...)/ -I` 未加引号且未限制 `--proto-redir '=https'`；同时 Argo 模式下 `vmess-sb` 仅绑定 IPv4 `127.0.0.1`，而 `cfargo` 启动 `cloudflared` 传入 `http://localhost:$port`，在 `localhost` 优先解析为 `::1` 的主机上会导致回源失败。
  - **客户端测速明文 URL：** 第 2745 行 `sbox.json` 的 `urltest` 仍使用 `http://www.gstatic.com/generate_204`。
- **修改方案**
  - 对 `domain`、`argoym`、`ca.log` 统一调用 `sub_valid_domain`；对 `cfvmadd_local.txt` / `cfvmadd_argo.txt` 校验 `sub_valid_domain` 或 `ip_literal_valid`，并补齐或清理 `cfymjx.txt` 的读取逻辑。
  - 临时隧道域名提取统一为严格匹配：`grep -aoE 'https://[a-z0-9-]+\.trycloudflare\.com' /etc/s-box/argo.log 2>/dev/null | tail -n1 | sed 's|^https://||'`；`cloudflared --url` 统一指向 `http://127.0.0.1:$port`，探测 `curl` 加引号及 `--proto '=https' --proto-redir '=https'`；第 2745 行测速地址改为 `https://www.gstatic.com/generate_204`。

---

### 7. 【中低危】信号中断时临时凭据文件残留及 `network_probe.py` 重定向降级与参数暴露

- **涉及位置**
  - [`kp.sh`](../kp.sh) 第 23–35 行、[`sb.sh`](../sb.sh) 第 4571–4573、4609–4610 行
  - [`tools/network_probe.py`](../tools/network_probe.py) 第 57–63 行（Python 入口）、第 125–136 行（curl 子进程）
- **问题机理与实际后果**
  - `kp.sh` 的本地和远端部署目录含 `SUUID` 与 `ARGO_AUTH`，`gitlabsub()` 和 `changewg()` 的临时文件分别含 GitLab 写令牌与 WARP 私钥。这些路径仅注册了 `EXIT` 清理。中断时能否完成清理取决于信号、接收进程和 shell 的退出行为。若作业在清理前终止，私有临时目录中可能残留明文凭据。
  - 探测工具允许 HTTPS 请求重定向到 HTTP。`--url` 和 `--proxy` 同时出现在 Python 父进程与 curl 子进程的命令行中。若 URL 含订阅令牌或代理地址含认证信息，能读取命令行的本机用户在整个采集批次期间都可能从父进程取得这些值。[入口参数](../tools/network_probe.py#L56-L63) · [子进程参数](../tools/network_probe.py#L125-L136)
- **修改方案**
  - 在 `kp.sh`（本机及远端 `provision` 片段）、`gitlabsub()`、`changewg()` 中补齐 `trap 'exit 1' HUP INT TERM`。
  - 从 HTTPS 开始的探测只允许 HTTPS 重定向。为敏感 URL 和代理凭据提供标准输入或权限受限的配置文件入口，再经标准输入交给 curl，保证父子进程的参数都不含明文凭据。

---

### 8. 【高危，限多用户 VPS】Caddy 公网订阅未验证回环源站身份

**成立条件与影响。** Caddy 只核对三个订阅路径，随后把含令牌的请求原样转给回环 TCP 端口。BusyBox 由后台命令启动，默认选择高位端口，开机任务延迟启动。如果同机非受信账户可以在后台停服期间绑定该端口，Caddy 就可能把订阅请求交给错误的服务。客户端仍在访问原来的 HTTPS 域名，却可能收到被替换的节点或路由配置。设置阶段的三文件内容比对仅能验证当次切换。[Caddy 站点](../sb.sh#L3781-L3792) · [后台与开机启动](../sb.sh#L3995-L4015) · [内容比对](../sb.sh#L4022-L4034) · [默认端口](../sb.sh#L4416-L4419)

**修改方案。** 让 Caddy 用只读权限直接提供当前快照中的三个白名单文件，保留令牌轮换和其他路径返回 404 的行为。需要独立后端时，使用位于受保护目录中的 Unix socket。验收应包括后台不可用时客户端收到错误、令牌轮换后旧地址失效。

### 9. 【中高危】自动放行可能先于同一防火墙链中的已有封禁

**成立条件与影响。** 管理员显式启用项目防火墙管理后，iptables 将没有来源限制的节点端口 `ACCEPT` 插到 `INPUT` 链首。UFW 路径在对应的用户输入链执行相同操作，原生 nftables 路径也将 `accept` 插入所选输入链前部。若该链原有针对节点端口的来源封禁、接口限制或限速，数据包可能先命中项目放行。其他链及云安全组仍按各自规则处理。[iptables 与 UFW](../tools/firewall.py#L96-L119) · [nftables 链选择和插入](../tools/firewall.py#L189-L210)

**修改方案。** 由管理员确定项目放行规则的位置，使需要保留的限制在放行前执行。无法确定安全顺序时保留现有策略，提示手动放行。验收时分别核对受限来源和允许来源对同一节点端口的访问结果。

### 10. 【中高危】并发修改服务端配置可能恢复已撤销的 UUID

**成立条件与影响。** 轮换 UUID 与修改端口或证书都可能写同一组服务端配置。`config_apply()` 读取现行文件、生成候选、逐文件替换并重启，失败后从本次备份整份恢复。两个管理会话重叠时，较早生成的候选或备份可能在另一会话轮换成功后被写回，节点随之重新接受旧 UUID。现有 `subscription.lock` 保护订阅发布，两次服务端修改仍可并发。[提交与回滚](../sb.sh#L841-L900) · [证书变更](../sb.sh#L3085-L3091) · [凭据轮换](../sb.sh#L3244-L3257) · [订阅发布锁](../sb.sh#L4982-L4992)

**修改方案。** 让所有修改相同服务端配置的入口共用一把写锁，从读取当前状态持续到提交、重启、防火墙同步或回滚结束。验收覆盖两个管理会话重叠且其中一次提交失败的情形，检查运行配置和两个版本模板均保留已生效的新 UUID。

### 11. 【中危】管理员关闭节点后，默认定时任务可重新启动服务

**成立条件与影响。** 安装会向 root crontab 写入每天主机时间 01:00 无条件重启 sing-box 的任务。菜单 `6 → 2` 停止服务后仍保留该任务。在 cron 正常运行的主机上，已停用的节点可能在下一次调度恢复监听。Alpine 的关闭分支还保留了 OpenRC 开机启动项。这会影响因凭据泄露而紧急停服的处置。[安装入口](../sb.sh#L3024-L3029) · [关闭与每日重启](../sb.sh#L4725-L4751) · [OpenRC 自启](../sb.sh#L1722-L1725)

**修改方案。** 定时维护只重启正在运行的实例。管理员明确关闭服务时，同步撤销该重启任务及对应的自启项，并检查停服结果。验收应确认下一次调度和主机重启后节点均保持关闭。

### 12. 【中危，限共享客户端】完整 sing-box 订阅默认启用无鉴权的 Clash API

**成立条件与影响。** VPS 与 Serv00 生成的完整 sing-box 配置都在**导入订阅的客户端设备**上启用 `127.0.0.1:9090`，未设置 API 密钥。能访问同一回环接口的其他用户或进程可以变更代理模式。模板将 `Direct` 模式映射到直连，切换后目标服务可能看到客户端的实际出口 IP。[VPS 模板与路由](../sb.sh#L2478-L2503) · [Serv00 模板与路由](../serv00_runtime.py#L427-L431) · [上游 Clash API 鉴权说明](https://sing-box.sagernet.org/configuration/experimental/clash-api/#secret)

**修改方案。** 完整订阅默认关闭控制接口。需要面板的设备保留回环监听，并在本地生成独立随机密钥。验收应确认启用接口的设备会拒绝未认证的管理请求。

### 13. 【中危，影响可用性】UDP 跳跃端口范围可覆盖同机其他服务

**成立条件与影响。** 添加 Hysteria2 或 TUIC 跳跃端口时，工具校验范围格式、目标入站及当前协议下完全相同的条目。它没有检查新范围与另一协议的端口范围、同地址 UDP 监听及已有转发是否重叠。配置重叠时，发往原服务的数据报可能先被重定向至节点。这与第 9 项的输入过滤顺序是两处不同的配置操作。[菜单入口](../sb.sh#L3114-L3124) · [范围登记](../tools/firewall.py#L243-L254) · [入站重定向](../tools/firewall.py#L96-L118)

**修改方案。** 写入前按地址族、目标地址和 UDP 端口区间检查实际冲突，覆盖项目另一协议、宿主监听和已有转发。发生重叠时指出冲突的端口区间并保留当前规则。

### 14. 【中危，限旧 Serv00 迁移】旧目录中的 WARP 分流策略被继承

**成立条件与影响。** 历史提交 `4a8e812` 的安装脚本在创建日志目录时将权限设为 `0777`。新版迁移先将旧目录收紧为 `0700`，再读取其中的 `config.json`，导入指向 `wg` 的路由和这些路由引用的 HTTPS 远程规则集。若同机其他账户此前有权进入并替换旧目录中的文件，被改写的分流规则或规则来源会随迁移进入新配置。迁移时轮换节点 UUID 无法恢复原始策略。[旧版目录创建逻辑](https://github.com/Linmiao010/sing-box/blob/4a8e812c7285d1721d1fc4dffec6893aeffafd68/serv00.sh#L24-L28) · [导入字段](../serv00_runtime.py#L242-L264) · [服务端使用继承规则](../serv00_runtime.py#L337-L345) · [迁移入口](../serv00_runtime.py#L658-L668)

**修改方案。** 从可信备份恢复 WARP 策略，或在迁移启用前列出实际继承的规则、远程来源及出站，由管理员核对。来源无法确认时，暂停导入该策略并提示其对相关流量的影响。

## 分流规则供应链风险

VPS 与 Serv00 生成的 sing-box 客户端配置均从第三方仓库的可变 `sing` 分支获取地域规则。默认规则模式下，匹配指定地域的目标会直连，部分域名查询也会交给直连侧 DNS。Serv00 模板将更新周期设为一天。若上游更新误收某个目标或分发内容被篡改，已导入且配置未变的客户端也可能改变出口，向该目标暴露实际出口 IP。[VPS 规则来源](../sb.sh#L2426-L2442) · [VPS 出站选择](../sb.sh#L2454-L2477) · [Serv00 更新周期与路由](../serv00_runtime.py#L417-L425)

对要求直连范围可审计的部署，建议将发布给客户端的规则固定到经审查的完整提交，随发行显式升级，或分发已校验的本地规则文件。若保留自动跟随上游的方式，应在订阅说明中写明谁能改变直连范围。此处记录的是运行期信任关系，本次审查没有发现上游已遭篡改的证据。
