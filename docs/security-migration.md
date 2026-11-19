# 旧部署安全迁移与验收

适用于从审计基线 `b4b7a3c`、`b0a8951` 或旧版 `4a8e812` 布局升级。先取得受信提交号和独立 SSH 或控制台访问。备份含私钥的配置时使用 root 或账户专用的 `0700` 目录，并将备份存放在订阅站点之外。

## 从 b4b7a3c 升级

1. 记录内核版本、服务运行状态、隧道类型、活动防火墙和区域。备份 `/etc/s-box` 中的配置、证书、订阅和 `firewall.json`，另存 `/root/ygkkkca` 及其符号链接实际指向的证书和私钥。Serv00 备份账户私有目录、原 CA Pool 路径和端口分配。更早版本按下文相应基线的步骤一并迁移。
2. 在 VPS 用菜单 **7** 切换到包含本次修改的受审发布目录。保持一个独立 SSH 会话。若 firewalld 原先交由本项目管理，先核对区域、来源封禁和尾部拒绝策略。确认放行位置后运行 `sing_box --firewall-enable --input-position append --zone <区域>`，替换已登记的旧 `-17461` 规则。采用手动放行时运行 `sing_box --firewall-sync`，撤下未确认的旧项目放行。仅在确认通配监听、`StrictForwardPorts` 与该区域的 DNAT 入站效果后才加 `--confirm-forward-ports`。跳跃范围在 PREROUTING 被改写，按原跳跃端口封禁需匹配连接的原目的端口。两族分别复核 SSH、受限来源和允许来源。
3. 运行 `sing_box --secure-routes`。活动节点将短暂重启。1.14 的直连 UDP 固定到通过检查的首个目的端点。1.10.7 阻断代理请求的 UDP 出站，Hysteria2/TUIC 自身的 QUIC 入站传输仍可承载 TCP。依赖 UDP 转发的部署先切换受支持的 1.14 内核。安装时按地址族保存已核实的公网地址。菜单 **15** 在广告新节点地址前先确保生效配置覆盖该地址，已有规则覆盖时不会因此重启。变更其他网卡地址后重新运行这条迁移命令。未出现在接口上的云 NAT 公网地址可加入 `/etc/s-box/restricted-cidrs.json`。
4. VPS 使用 Certbot 时，从菜单 **12** 重新应用现有证书。这一步安装续期部署钩子，将证书保存在权限受限的本地目录，更新使用该证书的入站 SNI，并刷新订阅。服务正在运行时会重启。外部证书须提供匹配的私钥、有效的 DNS SAN 和完整中间链。无法连到系统受信根的合格叶证书会随节点下发。回滚时同时恢复证书、匹配私钥、域名记录、服务端 JSON 和对应订阅。[Certbot 续期与部署钩子](https://eff-certbot.readthedocs.io/en/stable/using.html#renewing-certificates)
5. Serv00 从完整新发布目录执行 `bash serv00.sh --update`。若仍是早期共享目录布局，先按下文执行 `--migrate`。固定隧道按脚本打印的**实际绝对路径**更新 Cloudflare 的 CA Pool，源站名称使用 `argo-origin.invalid`，关闭 No TLS Verify。已有固定隧道更换源站密钥时，按提示核对新叶证书摘要并提供完整确认值，再次执行原命令。保留旧控制台设置至新配置验证成功；若恢复旧 generation，控制台也须恢复原 CA Pool。
6. 在维护窗口执行 `~/bin/sb --rotate`，重新取得 `~/bin/sb --show` 显示的地址。旧 UUID、路径、订阅令牌和 REALITY 密钥会更换，自签 TLS 密钥也会重签。使用外部公共证书时由证书所有者管理私钥。此步会中断旧客户端。尽快在每个客户端重新拉取完整订阅，复核证书、TCP、所需 UDP 功能及旧令牌失效。旧自签 CA 曾导入系统或浏览器根证书库的用户应移除该信任。
7. VPS 运行 **9 → 2** 重新生成订阅。使用 GitLab 的发布端需具备项目 `read_api` 和目标分支的 `write_repository` 权限。若远端发布失败，本地 HTTPS 订阅可单独刷新。已暂停的 UDP 跳跃需要重新下发订阅才能从客户端移除。[GitLab 项目 API](https://docs.gitlab.com/api/projects/#retrieve-a-project)

VPS 已有旧 Argo 开机项的部署，应在维护窗口用新版 **6 → 2** 按顺序停止连接器与源站，再用 **6 → 1** 启用节点，从隧道菜单重新建立所需隧道及开机项。临时隧道的公网域名可能变化。改回环源站端口也会停止相关连接器，需相应更新固定隧道控制台并重新启用。VPS 的回环源站使用 HTTP，多租户机器上突发进程退出或手动绕过管理菜单重启仍有端口抢占窗口。此类机器应选用已有的公网 WSS 直连模式，或使用能验证 TLS 源站且隔离端口的独立部署。

配置检查失败时保留原配置。若新版启动或发布失败，先核对实际运行进程、两族过滤规则、旧订阅快照与回环源站。需要手工回退时，从备份恢复同一代配置、证书和防火墙登记，保持管理员原先的停服状态。凭据已泄露时应以新密钥重建恢复配置。

### b4b7a3c 审计修复与验证

最终发布副本串行运行 `python -m unittest discover -s tests -v`，18 个模块共 176 项，172 项通过，4 项因平台条件跳过，耗时 450.541 秒。四项分别需要 Linux 网络命名空间中的真实防火墙、原生 `flock`、VPS 跨 UID 文件权限、Serv00 跨 UID 文件权限。另已通过四个 Shell 入口的 `bash -n`、两个 JavaScript 文件的 `node --check`、Python 文件的 `py_compile`、两个 SSH 工作流的 YAML 解析及 `git diff --check`。原生 HTTPS 用例使用 Caddy 2.11.7。未部署到 Linux VPS 或 FreeBSD Serv00。

本机工作目录还保留被 Git 忽略的历史测试。直接在该目录发现的 396 项中有失败。对照原始 `b4b7a3c` 逐个复跑，13 个历史模块中报错的 55 个不同用例在原始基线上也失败，其中包括期望从可变分支下载、使用早期模板和缺少证书材料的夹具。本轮将与改动直接相关的网络探测、IP 发现、菜单调度测试纳入发布副本，并更新了重定向预期和受管理节点的测试夹具。并发跑两套测试时，曾有一次地址刷新用例测到 3.087 秒；保留 3 秒门槛后，单模块和最终串行全套复测均通过。

下表保留本轮审计编号。测试文件位于 `tests/`。例如，第 1 项可用 `python -m unittest discover -s tests -p test_security_server_routes.py -v` 复测，其余项将 `-p` 后的值换成对应文件名。原生回环检查使用 sing-box 1.10.7、1.14.3 和 Mihomo 1.19.33。本地测试中的 IP、域名、端口和证书均为测试专用。

完整复测命令为 `python -m unittest discover -s tests -v`。将 `SING_BOX_110_BIN`、`SING_BOX_BIN`、`MIHOMO_BIN` 分别指向上述三个程序；Serv00 用例还读取 `SERV00_TEST_SB`、`SERV00_TEST_MH`。离线校验 Mihomo 完整配置时，将 `MIHOMO_DATA_DIR` 和 `MIHOMO_TEST_DATA` 都指向已有 `GeoSite.dat`、`geoip.metadb` 的目录。真实 SSH 主机密钥用例需 OpenSSH 和 Paramiko，Caddy 需在 PATH 中。Windows 下使用独立、无空格的临时测试路径，以满足 Unix 管理套接字的路径约束。

1.14.3 的 `udp_connect` 让直连出站先连接到获准目的端点。随后数据报的地址字段不能改变这个套接字的对端，载荷仍可能发往首个目的端，发送方也可能得不到错误。验收应检查受限目标是否收到了报文。[sing-box 1.14.3 连接管理](https://github.com/SagerNet/sing-box/blob/v1.14.3/route/conn.go#L158-L192) · [对应版本的数据报写入实现](https://github.com/SagerNet/sing/blob/0ad23b637bd4/common/bufio/bind.go#L69-L104)

| 编号 | 修改文件与生效入口 | 本地验证内容及边界 |
| --- | --- | --- |
| 1 | `sb.sh`、`serv00_runtime.py` 的新装与路由迁移在地址拒绝规则后固定 1.14 UDP 目的端，1.10 阻断代理 UDP | `test_security_server_routes.py` 用原生内核验证允许目的收包、非对端回包隔离、独立受限请求无报文、旧版命中 block。同一 PacketConn 连续改写目的地址的客户端场景只核对了内核实现。 |
| 2 | `sb.sh` 的 1.14 客户端在私网直连前执行 TUN sniff、53 端口和 DNS 协议劫持。1.10 转换到入站 sniff 与 dns-out | `test_security_server_routes.py` 校验两版生成物，回环保留导出的路由规则，模拟发往私网网关的查询并收到内部 FakeIP。真实 TUN 网卡未改动。 |
| 3 | `tools/firewall.py`、`sb.sh` 为 firewalld 使用正优先级，按登记逐条替换旧规则，另行确认区域 UDP 转发 | `test_security_firewall_release.py` 对比允许和禁止来源、未确认路径、旧条目迁移，以及运行态和持久态部分失败后的恢复。真实 zone 重载与云安全组需在部署机核对。 |
| 4 | `serv00_runtime.py` 签发非 CA 叶证书并轮换节点、REALITY 和回源密钥。`sb.sh` 与 Serv00 均按实际密钥、SAN、有效期及系统信任链生成各节点信任信息 | `test_security_serv00.py`、`test_security_identity.py` 用 OpenSSL、隔离根库和回环客户端验证私有叶证书、缺失信任根、密钥不匹配、证书轮换与旧身份。固定隧道控制台由部署者更新。 |
| 5 | `sb.sh`、`serv00_runtime.py` 扩充转换地址、保留网段及本机地址。VPS SOCKS 出站使用前置受控解析结果 | `test_security_server_routes.py` 记录内核向 SOCKS 发送的实际 IP，验证四种选族策略、DNS64、6to4、本机地址和重复迁移。 |
| 6 | `sb.sh` 的 GitLab 发布先认证查询精确项目和 private 可见性，再匿名探测 Git refs。凭据经标准输入供 API 使用 | `test_security_distribution.py` 的回环 Git 服务接受私有仓库真实推送，拒绝公开、internal、路径不一致、后缀歧义及只读身份，并检查 curl 参数。真实 GitLab 受保护分支权限需由项目管理员核对。 |
| 7 | `sb.sh`、`serv00_runtime.py` 生成加密引导 DNS。VPS Mihomo 的 WARP 内解析开启，Serv00 Mihomo 使用回环 FakeIP 与 no-resolve 地域规则 | `test_security_server_routes.py`、`test_security_serv00.py` 校验两代配置及 Mihomo，使用本地 DNS 记录器确认域名节点的解析路径。公网 DoH 服务和客户端网络需另测。 |
| 8 | `tools/firewall.py` 归一化 IPv4 映射地址，iptables/UFW/nft 对单播 UDP 只改变端口，以原地址 DNAT。firewalld 暂停单播自动跳跃 | `test_security_firewall_release.py` 验证映射回环忽略、副 IP 精确匹配、同端口多 IP 共存和仅清理自有规则。 |
| 9 | `sb.sh` 在停服及改回环 VMess 端口前处理隧道与开机任务。`serv00_runtime.py` 在操作宿主端口前检查冲突，先停连接器和节点，确认新的固定回源证书 | `test_security_node_lifecycle.py`、`test_serv00_recovery.py`、`test_security_serv00.py` 验证先停隧道再释放源站、冲突零删除、失败恢复次序及重试确认。 |
| 10 | `serv00_runtime.py` 在公开入口与快照目录都写入 HTTPS 拒绝明文规则，支持替换项目旧控制文件 | `test_security_serv00.py` 覆盖全新目录和原 `Options -Indexes` 文件升级。上线时用无凭据测试路径通过 HTTP 验证拒绝结果，不要以明文发送订阅令牌。 |
| 11 | `serv00_runtime.py` 固定 s14/s15 的 Gemini 规则集，复核旧 WARP 规则及下载主机，隧道探测禁重定向，1.14 配置受控解析器 | `test_security_serv00.py`、`test_serv00_recovery.py` 覆盖字符串／列表引用、嵌套规则和非受信 URL、域名型 WARP 对端及探测器响应处理。 |
| 12 | `sb.sh` 使用同一配置锁切换 ACME 证书、入站 SNI 和订阅。`tools/network_probe.py` 禁止跟随跳转并检查代理。`serv00_runtime.py` 在等待操作锁前清除账户文件。`kp.sh`、`SSH.yml`、`serv00.yml` 和 `workers_keep.js` 收紧保活、清理和读取上限 | `test_security_identity.py`、`test_security_shell.py`、`test_security_serv00.py` 覆盖换域和发布失败回滚、HTTPS 回环重定向、非法代理、阻塞时删除凭据和 Worker 分块响应。真实 Certbot 签发及续期任务需在部署机运行。 |

## 修复范围与验证入口

| 项目 | 实现与默认行为 | 本地验收 |
| --- | --- | --- |
| 防火墙与 SSH 通道 | `sb.sh`、`tools/firewall.py` 默认保留主机策略，可显式按实际入站放行或配置 UDP 跳跃 | `test_security_firewall_release.py` 对比两族的 SSH 放行、其他应用 NAT 和默认策略，检查重复放行、改端口、清理及失败回滚 |
| WS 与身份迁移 | `sb.sh`、`serv00_runtime.py` 分开生成路径与节点身份，按 tag 读取既有路径，显式迁移会中断旧身份 | 生成新装、带或不带斜杠的旧默认值、自定义路径。比对服务端、JSON、Mihomo 与适用的分享链接，检查候选失败及旧身份被拒 |
| GitLab 权限 | `sb.sh` 仅向私有仓库发布，客户端、二维码和 Telegram 使用独立 HTTPS 地址 | 回环 Git HTTP 服务验证首次和再次推送、只读拒绝、匿名可见项目拒绝、三个文件的内容及旧 remote 清理 |
| Serv00 管理入口 | `app.js` 仅提供只读健康页。安装与保活使用同一份 `serv00_runtime.py` 和受审面板 | 从部署函数生成应用，启动后检查健康页、旧 GET 和未授权 POST，模拟重启及改端口调用数为零 |
| 客户端 TLS | `sb.sh`、`serv00_runtime.py` 为完整订阅生成公证书或叶证书指纹。缺少可靠自签信任字段的单节点格式使用完整订阅 | sing-box 1.10.7、1.14.3 与 Mihomo 1.19.33 原生回环。检验正确证书、同名替代证书和旧凭据；1.10 系列不生成 AnyTLS。[Mihomo TLS 字段](https://wiki.metacubex.one/en/config/proxies/tls/) |
| 公网订阅 | `sb.sh` 由 Caddy 读取三个白名单订阅文件，验证三份内容后才停旧 HTTP 入口 | 真实 Caddy + 私有 CA 核对 HTTPS 内容、拒绝旧令牌、拒绝非白名单文件。故障注入验证第三份损坏及切换中断 |
| Mihomo 局域网共享 | 两套生成器默认 `allow-lan: false`。VPS 与 Serv00 的客户端 DNS 均监听回环 | Mihomo 1.19.33 实测代理和 DNS 监听。显式共享时，指定源地址需认证，其他来源即使密码正确也被拒。[Mihomo 入站访问控制](https://wiki.metacubex.one/en/config/general/) |
| 安装与更新 | `README.md`、`sb.sh`、`tools/releases.sha256` 和 Serv00 安装器校验受审提交或固定 SHA-256。系统辅助工具优先用发行版签名软件源 | 离线发布物覆盖成功、篡改、缺摘要。失败候选未执行，旧版仍可用。内核不兼容、重启失败及终止信号均有回归 |
| SSH 自动化与日志 | `kp.sh`、`SSH.yml`、`serv00.yml` 使用预核实的 known_hosts；`workers_keep.js` 与健康工作流只检查 HTTPS | 真实 OpenSSH 对两把测试主机密钥进行匹配、未知和变更验证，操作日志仅含账户序号与状态 |
| 本地权限与 WARP | VPS 私有目录仅 root 可访问。域名站点由 Caddy 读取只读快照，本机 HTTP 使用独立账户。Serv00 使用账户专用工作目录与单独的 Web 读取组。WARP 注册或导入独立配置，保留依赖它的分流 | 注册失败、原配置保留、停用时拒绝匹配路由、重新启用以及权限隔离均有回归入口 |

回归用例随本次变更交付。在包含本次修复的干净检出中运行 `python -m unittest discover -s tests -v`。生成器用例需要 Bash、jq、OpenSSL 和 PyYAML，SSH 主机身份用例需要 OpenSSH 和 Paramiko。原生客户端及 Caddy 路由用例需要对应的本机程序。按测试输出查看平台跳过项。Linux 防火墙用例仅在新建的网络命名空间中写规则，两账户权限用例应在一次性 Linux 环境中以 root 运行。

原生测试可用 `SING_BOX_BIN`、`SING_BOX_110_BIN`、`MIHOMO_BIN` 指定已核实的本地二进制。Serv00 使用 `SERV00_TEST_SB` 与 `SERV00_TEST_MH`；切换前者可分别复测新版和 1.10.7。Caddy 放在 PATH。Linux 中的 `test_security_publication_lock.py` 使用真实 `flock` 验证并行刷新时 GitLab 读取的三文件属于同一次发布。

以下为上一轮安全改造的历史记录。该轮在临时独立 Git 检出中发现 81 项测试。Windows 通过 77 项，4 项要求 Linux 的网络命名空间、POSIX 账户权限或原生 `flock`。已实测的防火墙环境为隔离 Debian 12.15、Linux 6.1.0-56、iptables / ip6tables 1.8.9（nf_tables）和原生 nftables 1.0.6。VPS 发布权限用例运行了实际 BusyBox 1.35.0 和两个不同的 UID。Serv00 的文件权限也在该虚拟机模拟身份下实读。UFW 和 firewalld 的真实服务重载、Alpine OpenRC、FreeBSD 宿主权限及公网 IPv6 仍由部署者在目标环境核实。回环 HTTPS 路由使用 Windows Caddy 2.11.7 和私有 CA；发布清单中的 Linux Caddy 为 2.11.9。 该 Debian 虚拟机中的 14 项内核、权限、并发与回滚检查均通过，包括 HUP/TERM 和真实文件锁。

原工作目录还保留了基线 `.gitignore` 忽略的旧测试。另行复跑的 23 个历史模块中，10 个通过，13 个仍有失败或错误。已确认的差异包括期望从可变分支升级、期望无 TLS 的公网 VMess、缺少自签公证书，以及抽取旧 Bash 函数时遗漏新依赖。历史测试记录和随本次交付的验收用例应分别解读。

## 从 2026-11-19 审计基线升级

1. **记录运行状态。** 备份三份服务端 JSON、额外协议配置、证书、Caddy 片段、订阅快照及 root crontab。记录 sing-box 当前是否运行、已加入哪些 systemd/OpenRC 自启项。受审提交需包含本次修改；基线 `b0a8951` 只用于对照。
2. **更新入口和出站规则（1、10、11）。** 依下节从新发布目录运行菜单 **7**。原本停用的节点在运行迁移命令前，用新版 **6 → 2** 确认停服、移除调度并落盘停服标记。对云平台额外的元数据地址，把精确 CIDR 字符串数组写入 `/etc/s-box/restricted-cidrs.json`。运行 `sing_box --secure-routes` 迁移运行配置、两份内核模板及额外入站。1.10.7 的全局单地址族模式会同时限制分流域名的解析结果；若分流需同时使用两族，选择 IPv4 或 IPv6 优先模式。活动实例会重启，已有连接可能中断。用两族客户端复核分流和地址族优先级。共用配置锁先于订阅锁，服务运行期间避免直接编辑 JSON。停用后检查 `systemctl is-enabled sing-box` 或 `rc-update show` 以及 `crontab -l`；要重新提供服务用 **6 → 1**。
3. **切换 HTTPS 订阅（8）。** 从 **3 → 10** 重新应用域名。项目 Caddy 用 `sboxcaddy` 组，共享 Caddy 用其实际服务读取组，确认该组仅含受信账户。Caddy 的工作进程只需读取 `/var/lib/sbox-subscription-https/www/<令牌的 SHA-256>` 下三个文件。脚本先验证三份 HTTPS 内容，再停旧监听、清理开机任务。共享 Caddy 改由系统服务管理器重启，需为已有网站安排短时中断。多用户主机请在共享 Caddyfile 的全局块设置 `admin off`，或按下方配置仅服务账户可访问的 Unix 管理端点。失败后检查原站点、令牌、快照和监听是否恢复，修正权限或证书后重试。用新令牌逐一比对三份内容，轮换后检查旧路径和额外文件为 404。仅本机 HTTP 继续使用独立快照与回环端口。
4. **恢复推送及辅助服务（3–7）。** 使用过旧 TG 脚本时，通过 **3 → 5** 用新 Bot Token 和正确 chat ID 重建推送，或明确关闭。曾暴露的节点密码、订阅令牌和客户端 WARP 私钥分别轮换。域名及 CDN 优选值通过相应菜单重新核对。菜单 **14** 在重启前重新设置或停用，以替换旧启动任务。输入有效端口及适用的两位大写国家代码。安装、卸载及服务管理会核对同名服务的可执行文件和配置路径。如果主机已有发行版提供的 sing-box 服务，先核对两个实例的归属。WARP 直连探测可能在安装期间暂时停止活动隧道，保留独立管理通道。仅 IPv6 主机确需系统 DNS64 时输入 `YES`。恢复 DNS 时用 `cp -a --remove-destination /etc/s-box/resolver-backup.<实际后缀>/original /etc/resolv.conf`，备份中的 `contents` 可与链接目标核对。敏感网络采集参数通过 `network_probe.py --private-stdin` 输入 JSON 对象（`url`、可选 `proxy`），可重定向自 `0600` 文件。
5. **检查端口放行与转发（9、13）。** 先核实两族所有现有来源封禁、接口限制、限速及最终拒绝位置。仅在链尾放行对本机有效时确认 `--input-position append`。多个同族 nft input 链或链尾已有最终拒绝时，由管理员在现有防火墙中选择放行位置。升级首次同步可能撤销旧版置于链首的项目放行，先安排替代放行再切换。为同一 UDP 节点分别测试允许与受限来源。添加跳跃范围前核对 Hysteria2、TUIC、宿主 socket 与已有转发，按冲突提示缩小或另选区间。
6. **更新客户端（12、分流来源）。** 重新生成并拉取订阅。核对设备最终配置的监听和分流来源，按需固定已审查的规则集版本。设备面板的独立密钥只保存在使用面板的设备。[客户端规则与面板操作](README.md#客户端地域规则与本地面板)

**共享 Caddy 的管理端。** 在系统服务实际加载的 `/etc/caddy/Caddyfile` 中选择 `admin off`，或使用 `admin unix//var/lib/caddy/admin/admin.sock`。后者可先运行 `install -d -o caddy -g caddy -m 0700 /var/lib/caddy/admin`，须替换成该服务的实际用户和组。父路径须由 root 或该账户持有，其他用户不可写，路径不经过符号链接。核对读取组的主组用户及附加成员，避免普通用户通过同组权限读取订阅。仅供单用户使用的主机可在设置或轮换时显式使用 `SBOX_CADDY_SINGLE_USER=1 sing_box` 保留回环 TCP 管理端点。重新加载时脚本由服务管理器读取本地配置。先核对迁移前有效的服务设置，失败时恢复原文件并检查所有共享站点。[Caddy 管理端点](https://caddyserver.com/docs/caddyfile/options#admin)

### Serv00 回源与旧 WARP 策略（2、14）

优先使用本账户已登记、空闲的额外 TCP 端口并设置 `SERV00_ARGO_PORT`。只能复用 VMess 端口时，先向宿主确认 UID 限制覆盖 IPv4 回环，并在该账户验证具体公网单播地址和 `127.0.0.1` 能分别绑定同一端口，再设置 `SERV00_LOOPBACK_PORT_CONFIRMED=1`。两项前提需要在目标机验收。部署额外受限地址用 `~/.sing-box-serv00/restricted-cidrs.json`。

临时隧道由脚本向连接器传入 HTTPS 回源地址、证书名称和信任根。固定隧道先准备源站证书，根据脚本提示到 Cloudflare 的隧道发布路由，将 Service 设为 `https://127.0.0.1:<已分配端口>`，Origin Server Name 设为 `argo-origin.invalid`，CA Pool 填连接器可读、由当前命令打印的证书绝对路径，关闭 No TLS Verify。保存控制台配置后，按提示输入确认值。非交互方式通过 `SERV00_FIXED_ORIGIN_CONFIRMED` 提供同一值，已有固定隧道换证时包含新证书摘要。安排维护窗口，旧版明文源站与新连接器配置的切换会造成短时中断。对新装用 `--install`，旧目录用 `--migrate`，已有活动目录用 `--update`。[Cloudflare 回源参数](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/origin-parameters/)

从曾可被其他账号写入的旧目录迁移时，优先指定 `SERV00_WARP_POLICY_BACKUP` 为该账户私有、位于旧目录外的可信备份绝对路径。人工迁移时阅读完整的拟继承匹配条件、WARP 出站和远程规则集 URL；确认来源后再输入 `IMPORT-WARP`。来源不明时暂停，原规则涉及的域名仍按原部署处理。UUID 轮换与策略审批独立。对两种隧道分别检查正确源站证书可用、错误证书被拒，实测重启、更新、改端口及轮换。临时域名可能随重连改变，重新获取订阅；固定隧道改端口后重新核对控制台。失败恢复使用当前有效身份并保留 TLS 验证。

## b0a8951 审计修复的历史回归入口

2026-11-19 在 Windows 的干净交付副本上验证了 15 个模块、114 项测试，110 项通过，4 项因平台条件跳过。跳过项是 Linux 网络命名空间内的防火墙收发、原生 flock 发布并发、VPS 与 Serv00 的跨 UID 文件权限。另已通过四个入口脚本的 Bash 语法检查、Python 编译及 Git 差异检查。此次未部署到真实 VPS 或 Serv00。

在包含本次改动的干净工作副本中运行 `python -m unittest discover -s tests -v`。下表的模块均位于 `tests/`，可用 `python -m unittest discover -s tests -p 'test_security_server_routes.py' -v` 这样的命令单独复测。原生用例使用前文列出的内核环境变量；请留意测试输出中的跳过原因。

| 审计项 | 变更入口 | 本地验证及平台条件 |
| --- | --- | --- |
| 1 出站目的地址 | `sb.sh` 的 `secure_server_routes`、`ip_priority_patch`，Serv00 的 `server_config` | `test_security_server_routes` 用真实 1.10.7、1.14.3，各跑 4 种选族策略和 10 个目标。受控 DNS 返回允许、受限和混合地址，本地 SOCKS 记录实际目的 IP。两版 Serv00 配置分别对启用和停用 WARP 做内核检查。公网 WireGuard 对端及业务 UDP 链路待测。 |
| 2 Serv00 Argo | `acquire_state`、`prepare_origin`、`ensure_running`、`configure` | `test_security_serv00` 检查端口登记、TLS 信任、固定模式确认、先停连接器再换源站的顺序。`test_serv00_recovery` 覆盖失败恢复。实际 FreeBSD 的 UID 约束、固定隧道控制台下发及公网请求需两种隧道分别验收。 |
| 3 WARP、DNS | `warpcheck`、`warp_unrouted_ips`、`v4orv6`、`ipuuid` | `test_security_shell` 用 curl 与服务替身验证超时、活动服务恢复和管理员拒绝。临时 resolv.conf 覆盖符号链接的保存、替换和恢复。目标主机仍需验证实际 DNS64 与管理通道。 |
| 4 TG、二维码、订阅凭据 | `tgsbshow`、`sub_migrate_tg`、`sbshare`、`sub_https_ready` | `test_subscription_ui`、`test_security_identity`、`test_security_distribution` 覆盖非法输入、含尖括号的正文，以及开启和关闭推送时真实刷新流程的参数记录。Telegram 投递使用替身。 |
| 5 菜单 14、停止进程 | `inssbwpph`、`warp_socks_boot`、`warp_socks_probe`、`project_process` | `test_security_shell` 执行生成的 cron/OpenRC 启动入口，核对参数、错误响应和相近端口的进程选择。宿主开机和 WARP 出口待测。 |
| 6 域名和 Argo | `changeym`、`cfargoym`、`vmesscfadd`、`quick_tunnel_hostname`、`sb_client` | `test_security_shell`、`test_security_identity` 检查临时域名完整后缀、已落盘非法值及合法 Host/SNI 在 YAML、JSON、VMess URI 中的实际值。公网 CDN 和临时隧道域名待测。 |
| 7 临时凭据、敏感探测 | `kp.sh` 本机和远端 staging，`gitlabsub`、`changewg`、`tools/network_probe.py` | `test_security_shell` 验证正常退出及 HUP/INT/TERM 清理、Python 与 curl 参数，并在回环 TLS 重定向下检查明文端没有收到请求。 |
| 8 订阅站点 | `sub_caddy_site`、`sub_apply_locked`、`sub_publish_current` | `test_https_live` 使用真实 Caddy 2.11.7 和私有 CA，检查白名单、大小写、轮换和快照缺失。`test_security_subscription_rollback`、`test_subscription` 检查旧入口、磁盘文件和快照回滚。`test_security_caddy_admin` 检查共享管理端。目标主机复核 Caddy 服务身份及共享站点重启。 |
| 9 防火墙限制顺序 | `tools/firewall.py` 的 `manage` 与各后端规则写入 | `test_security_firewall_release` 用两族规则替身检查同一端口的受限和允许来源、旧链首条目迁移和失败恢复。真实内核收发用例需 Linux 网络命名空间，UFW/firewalld 重载还需相应服务。 |
| 10 并发回滚 | `config_locked`、`config_apply`、`core_update`、`egress_apply`、额外入站和订阅入口 | `test_security_node_lifecycle` 以两种排序重叠 UUID 轮换和端口事务，在服务重启后令防火墙同步失败，检查新 UUID、旧端口及运行服务替身。`test_core_update` 覆盖内核回滚。Windows 用目录互斥替身，Linux 使用原生 flock。 |
| 11 管理员停服 | `stclre`、`singbox_stop`、`singbox_enable`、`singbox_maintenance`、`cronsb` | `test_security_node_lifecycle` 用 systemd 和 OpenRC 替身验证停服、下一次调度、旧启动项、显式启用和 HUP/INT/TERM。实际重启后应再次检查监听和服务状态。 |
| 12 客户端管理接口 | VPS `sb_client`、Serv00 `emit_clients` | 生成用例核对默认配置；`test_security_server_routes` 在两版真实客户端上开启本机面板，无密钥、其他设备密钥、本设备密钥分别返回 401、401、200。 |
| 13 UDP 端口跳跃 | `tools/firewall.py` 的 `check_hops` 与 NAT/socket 清单读取 | `test_security_firewall_release` 覆盖双栈、不同目的地址的可共存区间、端点/部分重叠、通配监听、已有 DNAT 和 nft interval map。拒绝冲突时比较持久状态和规则替身。 |
| 14 旧 WARP 策略来源 | Serv00 的 `legacy_warp_policy`、`import_old`、`configure` 和恢复入口 | `test_security_serv00` 检查私有可信备份、交互批准、来源不明时暂停，以及未批准状态不能经轮换或恢复启动。实际导入前仍需管理员核对匹配范围和规则集所有者。 |

## 单机 VPS

1. **保住管理通道。** 保留当前 SSH 会话，并确认云控制台能够登录。记录 SSH 实际监听地址和端口、云安全组、`ss -lntup` 结果，以及当前使用的防火墙后端。分别用 `iptables-save`、`ip6tables-save` 或 `nft list ruleset` 留存与本机对应的规则，同时备份 firewalld / UFW 的持久配置。若旧安装已经清空策略，须从此前的可信备份恢复。没有备份时，先按实际端口和管理员来源放行 SSH，另开会话验证 IPv4、IPv6，再按需恢复节点、其他应用和默认策略。云厂商救援控制台应在收紧策略前可用。
2. **备份并切换程序。** 保存 `/etc/s-box`、Caddy 配置和证书数据、自定义 service、root crontab 及现有程序版本。旧主机菜单会读取已安装内核的版本。先从可信安装记录确定旧内核版本和架构，核对相应官方归档的签名或受审 SHA-256，再提取归档中的 sing-box，用 `cmp` 与 `/etc/s-box/sing-box` 比较。来源不明或内容不符时，先从已验证归档恢复程序；共享的系统 Caddy 由发行版软件包或现有管理员的受信来源核对。按仓库 README 将受审提交提取到 `/etc/s-box/releases/<提交号>`，从该目录运行 `bash sb.sh`，选择 **7**。已安装的机器不必重新安装。默认由原防火墙管理方维护规则。需要项目自动放行时，先检查链顺序。iptables、UFW、原生 nftables 仅在可安全追加时运行 `sing_box --firewall-enable --input-position append`。firewalld 用 `sing_box --firewall-enable --input-position append`，多 zone 主机同时指定 `--zone <公网接口所属区域>`。手动重载主机防火墙后，用 `sing_box --firewall-sync` 对账。受审目录中的 `python3 tools/firewall.py remove` 可撤销本项目条目。核对脚本列出的旧 DNAT 规则或两族 `-t nat -S PREROUTING` 输出，只按明确归属逐条处理。请勿清空链。
3. **先迁移订阅入口。** 对外域名设置正确的 A/AAAA 和 TCP 80/443。旧公网 HTTP 运行期间，直接用 **3 → 10** 输入域名和新随机路径令牌。此时新文件目录与旧 Web 根独立。Caddy 通过可信 HTTPS 读取 `sbox.json`、`clmi.yaml`、`jhsub.txt` 并逐一比对本机文件后，脚本提交配置、停旧监听并移除已知旧令牌目录。失败时保留旧入口，按报错排查。仅需本机 HTTP 的新部署可用 **3 → 8 → 1**。 共用已有 Caddy 时，站点片段对实际服务组只读。无法自动识别该组时，核实服务身份后用 `SBOX_CADDY_GROUP=<组名> sing_box` 设置。
4. **准备证书。** 可信域名证书需包含客户端 SNI 对应的 DNS SAN。已有自签证书可用 `openssl x509 -in /etc/s-box/cert.pem -noout -ext subjectAltName` 核对。若旧证书仅有 CN，应安排维护窗口，备份证书和密钥。使用原密钥重新签发 `www.bing.com` 的自签公证书可运行下列命令。自定义 SNI 或怀疑私钥泄露时，应使用相应名称及新的独立私钥，并同步更新客户端信任。

   ```bash
   (
     set -eu
     umask 077
     cd /etc/s-box
     candidate=$(mktemp .cert.XXXXXX)
     trap 'rm -f -- "$candidate"' EXIT
     openssl req -new -x509 -days 3650 -key private.key -out "$candidate" \
       -subj /CN=www.bing.com -addext subjectAltName=DNS:www.bing.com -addext basicConstraints=CA:FALSE
     openssl verify -partial_chain -trusted "$candidate" -verify_hostname www.bing.com "$candidate"
     mv -f "$candidate" cert.pem
   )
   ```

5. **在维护窗口轮换凭据。** 旧 WS 路径包含共享 UUID 时选择 **3 → 2 → 3**。若曾通过不可信 HTTP、共享 GitLab 令牌或公共日志分发订阅，即使已有独立路径，也应通过 **3 → 2 → 1** 随机生成新 UUID。此操作重启节点，旧客户端失去认证。通过 **3 → 11** 轮换另设的 Trojan 密码、Snell PSK；订阅含客户端 WARP 私钥时，用 **3 → 12 → 3** 为该客户端重新申请或导入独立身份。自签证书变更后通过服务管理菜单重启节点。使用过历史共享 WARP 服务端密钥的实例，应通过 **3 → 6** 注册或导入独立配置。注册失败时检查依赖该出站的域名仍受原有策略约束。
6. **分发并验收。** 执行 **9 → 2** 重新生成三份文件，通过独立可信渠道交付新 HTTPS 地址。客户端刷新完整订阅并重载；静态导入的 URI 需重新导入，自签 TUIC、AnyTLS、Trojan 和自签公网 VMess 使用完整订阅。用实际客户端验证新身份可连接、旧身份不能重新连接。用 `ss -lntp` 检查旧 BusyBox 监听已停止。仅在单独配置本机 HTTP 时才应存在其回环端口。从另一台机器分别测试 IPv4/IPv6 的 HTTPS、旧 HTTP 端口与旧令牌。验证通过后删除云安全组中旧 HTTP 端口的放行项。

**客户端共享。** 默认 Mihomo 仅供本机使用。主线 DNS 启用并监听 `127.0.0.1:1053`，Serv00 默认关闭 DNS。确需局域网共享时，在自己维护的客户端覆写中显式启用 `allow-lan`，用 `bind-address` 限制网卡、`lan-allowed-ips` 限制允许的 CIDR，并设置非空 `authentication` 用户名和独立密码。DNS 保持回环可避免向局域网开放解析服务。应从允许和未允许的地址分别测试代理认证和访问结果。

## GitLab 订阅发布

1. 盘点所有使用旧令牌的 VPS、分支、定时任务、通知和客户端。准备专用私有项目，为每个发布端创建独立、限期有效的项目令牌，授予向指定分支推送所需的角色，以及 `read_api`、`write_repository` 两项范围。受保护分支还需允许对应身份推送。客户端使用上一节的新 Caddy 令牌。[GitLab 项目访问令牌](https://docs.gitlab.com/user/project/settings/project_access_tokens/)
2. 每台 VPS 依次运行 **3 → 7 → 1**，填写项目路径和分支，在隐藏输入框中输入新令牌；不要把令牌写进 shell 历史。首次设置要成功发布三份文件，随后才替换本机仅 root 可读的 `gitlab-publisher`。再次发布用 **3 → 7 → 2** 或 **9 → 2**。发生并发推送冲突时重试，以远端最新提交为基础。脚本只更新三个订阅文件。
3. 检查远程分支内容与本地相符，通知和二维码显示 Caddy URL。成功设置会删除旧脚本、分享文件和旧仓库的 GitLab origin。不再使用 GitLab 的机器运行 **3 → 7 → 3** 清理旧版痕迹。检查自建定时任务、其他凭据管理器、旧日志和备份是否保存旧值；处置时避免将令牌打印到工单或日志中。
4. **先替换全部发布端，再撤销旧令牌。** 在令牌所属的 GitLab 项目、群组或个人设置中撤销相应令牌，验证旧身份拉取、推送均被拒绝。通知客户端重新导入 HTTPS 地址。历史 Git 提交、聊天或截图中的节点密码仍可能已泄露，需按 VPS 步骤轮换节点身份。[GitLab 令牌管理](https://docs.gitlab.com/security/tokens/)

## Serv00 / Hostuno

FreeBSD 二进制 `sb` 和 `server` 的 SHA-256 固定在 `serv00_runtime.py` 的 `ASSETS`。摘要来自本次核对的上游发行资产（ID 212842014、212841973），上游未提供独立作者签名。维护者换版时需核实新资产、复算摘要，再将常量与安装器一起提交审查。

1. 在 SSH 中保存旧配置、证书、原端口分配和固定 Argo 隧道设置。停止指向旧 GET 操作 URL 或远程下载旧脚本的 cron、Worker 与工作流。保留 SSH 管理入口并核实宿主机的 SSH 主机密钥。
2. 将同一受审提交的完整发布目录放到自己的账户下。从该目录运行 `bash serv00.sh --migrate`。新装使用 `--install`。迁移会轮换节点身份、生成独立订阅令牌并切换进程，原客户端会断开。固定隧道的 HTTPS 回源、证书名称及 CA Pool 必须按前文一起配置。旧进程参数或进程页曾暴露固定隧道令牌时，还需轮换 Cloudflare 隧道令牌。
3. 通过 `~/bin/sb --show` 获取三份新 HTTPS 地址（`/sing-box-sub/<令牌>/` 下的三个文件），验证实际客户端能连接。运行 `~/bin/sb --retire-legacy`，程序通过 HTTPS 逐一比对三份新内容后才移除旧订阅。再实测旧 URL 和旧凭据失效。检查静态 Web 服务身份能读取发布副本，其他租户无法读取私有工作目录 `~/.sing-box-serv00`。需要指定 Web 服务组时使用 `SERV00_WEB_GROUP` 指向已核实的宿主读取组。使用 sing-box 1.10 客户端时可运行 `SERV00_CLIENT_SERIES=1.10 ~/bin/sb --refresh` 生成对应订阅，切回新版使用 `modern`。
4. 保活调用本机已安装的 `bash "$HOME/.sing-box-serv00/release/serv00keep.sh"`。SSH 批量调度使用 `kp.sh --keep`。为调度器设置 `ACCOUNTS` JSON 数组（字段 `SSH_USER`、`HOST`，口令模式另含 `SSH_PASS`）及 `SSH_KNOWN_HOSTS_FILE`。主机密钥须与宿主控制台或其他受信记录核对。可选 GitHub Actions 模板位于根目录 `SSH.yml`、`serv00.yml`，对应 Secrets 为 `SERV00_ACCOUNTS`、`SERV00_KNOWN_HOSTS`、可选 `SERV00_SSH_PRIVATE_KEY`。Worker 和健康工作流的 `HEALTH_URLS` 只填 HTTPS 的 `/health` 地址，响应为 `ok`。管理操作通过 SSH 执行。将仓库变量 `SERV00_REVIEWED_COMMIT` 设为已核实的完整 40 位提交号。自动化安装或更新应从该提交的 checkout 运行。

若首次 Serv00 迁移在切换进程后失败，可在 SSH 运行 `~/bin/sb --recover`，使用保存在私有目录的候选配置及新身份恢复服务，再检查端口和订阅。已有新版部署的更新失败会尝试恢复先前的 active 配置。

## 回滚与线上验收

保留迁移前程序和私有配置快照至验收完成。若候选内核或配置检查失败，先检查当前进程和原订阅仍可用。程序版本回退可重新选择已保存的受审发布目录，再按该版本检查配置并重启。若新身份已经生效、旧身份已泄露或撤销，恢复服务时须将新的认证值、WS 路径、证书及订阅读取凭据应用到恢复配置，并重新生成订阅。管理员设置过停服时，恢复后保留 `admin-stopped`，核实服务仍已停止且调度和自启均已撤销。仅在明确重新启用时启动节点。

| 部署者检查 | 预期结果 |
| --- | --- |
| 云安全组、VPS 两族防火墙、SSH 独立新会话 | 实际 SSH 通道畅通，IPv4 与 IPv6 的启用节点端口分别可达，其他业务转发与默认策略符合主机基线 |
| 域名和真实 ACME 签发、续期 | 正式客户端信任证书链，名称匹配，三份 HTTPS 内容与当前 VPS 一致，旧 HTTP 及旧令牌已失效 |
| GitLab 实际项目与令牌撤销 | 新身份能推目标分支，客户端无发布权限，撤销后所有旧发布端和旧地址不能再认证 |
| Serv00 的宿主身份、文件权限、实际部署版本 | 安装的面板与受审 checkout 相同，宿主通过 HTTPS 提供新订阅，旧 GET 管理路径返回 404，SSH 保活只运行本机受审版本 |
| 客户端和业务链路 | 独立客户端分别验证 TCP/UDP、Argo、证书拒绝、旧身份失效及 WARP 出口。确认旧的 HTTP/日志泄露路径已关闭 |
