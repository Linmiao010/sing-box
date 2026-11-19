# 旧部署安全迁移与验收

适用于从 `4a8e812` 及相同布局的部署升级到包含本次修复的提交。先取得受信提交号和独立 SSH 或控制台访问。备份含私钥的配置时使用 root 或账户专用的 `0700` 目录，并将备份存放在订阅站点之外。

## 修复范围与验证入口

| 项目 | 实现与默认行为 | 本地验收 |
| --- | --- | --- |
| 防火墙与 SSH 通道 | `sb.sh`、`tools/firewall.py` 默认保留主机策略，可显式按实际入站放行或配置 UDP 跳跃 | `test_security_firewall_release.py` 对比两族的 SSH 放行、其他应用 NAT 和默认策略，检查重复放行、改端口、清理及失败回滚 |
| WS 与身份迁移 | `sb.sh`、`serv00_runtime.py` 分开生成路径与节点身份，按 tag 读取既有路径，显式迁移会中断旧身份 | 生成新装、带或不带斜杠的旧默认值、自定义路径。比对服务端、JSON、Mihomo 与适用的分享链接，检查候选失败及旧身份被拒 |
| GitLab 权限 | `sb.sh` 仅向私有仓库发布，客户端、二维码和 Telegram 使用独立 HTTPS 地址 | 回环 Git HTTP 服务验证首次和再次推送、只读拒绝、匿名可见项目拒绝、三个文件的内容及旧 remote 清理 |
| Serv00 管理入口 | `app.js` 仅提供只读健康页。安装与保活使用同一份 `serv00_runtime.py` 和受审面板 | 从部署函数生成应用，启动后检查健康页、旧 GET 和未授权 POST，模拟重启及改端口调用数为零 |
| 客户端 TLS | `sb.sh`、`serv00_runtime.py` 为完整订阅生成公证书或叶证书指纹。缺少可靠自签信任字段的单节点格式使用完整订阅 | sing-box 1.10.7、1.14.3 与 Mihomo 1.19.33 原生回环。检验正确证书、同名替代证书和旧凭据；1.10 系列不生成 AnyTLS。[Mihomo TLS 字段](https://wiki.metacubex.one/en/config/proxies/tls/) |
| 公网订阅 | `sb.sh` 分开管理回环后端和 Caddy，验证三份内容后才停旧入口 | 真实 Caddy + 私有 CA 核对 HTTPS 内容、拒绝旧令牌、拒绝非白名单文件。故障注入验证第三份损坏及切换中断 |
| Mihomo 局域网共享 | 两套生成器默认 `allow-lan: false`。VPS DNS 监听回环，Serv00 DNS 默认关闭 | Mihomo 1.19.33 实测代理和 DNS 监听。显式共享时，指定源地址需认证，其他来源即使密码正确也被拒。[Mihomo 入站访问控制](https://wiki.metacubex.one/en/config/general/) |
| 安装与更新 | `README.md`、`sb.sh`、`tools/releases.sha256` 和 Serv00 安装器校验受审提交或固定 SHA-256。系统辅助工具优先用发行版签名软件源 | 离线发布物覆盖成功、篡改、缺摘要。失败候选未执行，旧版仍可用。内核不兼容、重启失败及终止信号均有回归 |
| SSH 自动化与日志 | `kp.sh`、`SSH.yml`、`serv00.yml` 使用预核实的 known_hosts；`workers_keep.js` 与健康工作流只检查 HTTPS | 真实 OpenSSH 对两把测试主机密钥进行匹配、未知和变更验证，操作日志仅含账户序号与状态 |
| 本地权限与 WARP | VPS 私有目录仅 root 可访问，BusyBox 使用专用账户读取发布副本。Serv00 使用账户专用工作目录与单独的 Web 读取组。WARP 注册或导入独立配置，保留依赖它的分流 | 注册失败、原配置保留、停用时拒绝匹配路由、重新启用以及权限隔离均有回归入口 |

回归用例随本次变更交付。在包含本次修复的干净检出中运行 `python -m unittest discover -s tests -v`。生成器用例需要 Bash、jq、OpenSSL 和 PyYAML，SSH 主机身份用例需要 OpenSSH 和 Paramiko。原生客户端及 Caddy 路由用例需要对应的本机程序。按测试输出查看平台跳过项。Linux 防火墙用例仅在新建的网络命名空间中写规则，两账户权限用例应在一次性 Linux 环境中以 root 运行。

原生测试可用 `SING_BOX_BIN`、`SING_BOX_110_BIN`、`MIHOMO_BIN` 指定已核实的本地二进制。Serv00 使用 `SERV00_TEST_SB` 与 `SERV00_TEST_MH`；切换前者可分别复测新版和 1.10.7。Caddy 放在 PATH。Linux 中的 `test_security_publication_lock.py` 使用真实 `flock` 验证并行刷新时 GitLab 读取的三文件属于同一次发布。

本次交付文件在临时独立 Git 检出中发现 81 项测试。Windows 通过 77 项，4 项要求 Linux 的网络命名空间、POSIX 账户权限或原生 `flock`。已实测的防火墙环境为隔离 Debian 12.15、Linux 6.1.0-56、iptables / ip6tables 1.8.9（nf_tables）和原生 nftables 1.0.6。VPS 发布权限用例运行了实际 BusyBox 1.35.0 和两个不同的 UID。Serv00 的文件权限也在该虚拟机模拟身份下实读。UFW 和 firewalld 的真实服务重载、Alpine OpenRC、FreeBSD 宿主权限及公网 IPv6 仍由部署者在目标环境核实。回环 HTTPS 路由使用 Windows Caddy 2.11.7 和私有 CA；发布清单中的 Linux Caddy 为 2.11.9。 该 Debian 虚拟机中的 14 项内核、权限、并发与回滚检查均通过，包括 HUP/TERM 和真实文件锁。

原工作目录还保留了基线 `.gitignore` 忽略的旧测试。另行复跑的 23 个历史模块中，10 个通过，13 个仍有失败或错误。已确认的差异包括期望从可变分支升级、期望无 TLS 的公网 VMess、缺少自签公证书，以及抽取旧 Bash 函数时遗漏新依赖。历史测试记录和随本次交付的验收用例应分别解读。

## 单机 VPS

1. **保住管理通道。** 保留当前 SSH 会话，并确认云控制台能够登录。记录 SSH 实际监听地址和端口、云安全组、`ss -lntup` 结果，以及当前使用的防火墙后端。分别用 `iptables-save`、`ip6tables-save` 或 `nft list ruleset` 留存与本机对应的规则，同时备份 firewalld / UFW 的持久配置。若旧安装已经清空策略，须从此前的可信备份恢复。没有备份时，先按实际端口和管理员来源放行 SSH，另开会话验证 IPv4、IPv6，再按需恢复节点、其他应用和默认策略。云厂商救援控制台应在收紧策略前可用。
2. **备份并切换程序。** 保存 `/etc/s-box`、Caddy 配置和证书数据、自定义 service、root crontab 及现有程序版本。旧主机菜单会读取已安装内核的版本。先从可信安装记录确定旧内核版本和架构，核对相应官方归档的签名或受审 SHA-256，再提取归档中的 sing-box，用 `cmp` 与 `/etc/s-box/sing-box` 比较。来源不明或内容不符时，先从已验证归档恢复程序；共享的系统 Caddy 由发行版软件包或现有管理员的受信来源核对。按仓库 README 将受审提交提取到 `/etc/s-box/releases/<提交号>`，从该目录运行 `bash sb.sh`，选择 **7**。已安装的机器不必重新安装。默认由原防火墙管理方维护规则。需要项目自动放行时，运行 `sing_box --firewall-enable`，多 zone 主机用 `sing_box --firewall-enable --zone <公网接口所属区域>`。手动重载主机防火墙后，用 `sing_box --firewall-sync` 对账。受审目录中的 `python3 tools/firewall.py remove` 可撤销本项目条目。核对脚本列出的旧 DNAT 规则或两族 `-t nat -S PREROUTING` 输出，只按明确归属逐条处理。请勿清空链。
3. **先迁移订阅入口。** 对外域名设置正确的 A/AAAA 和 TCP 80/443。旧公网 HTTP 运行期间，直接用 **3 → 10** 输入新随机路径令牌和另一个空闲后台端口。此时新文件目录与旧 Web 根独立。Caddy 通过可信 HTTPS 读取 `sbox.json`、`clmi.yaml`、`jhsub.txt` 并逐一比对本机文件后，脚本提交配置、停旧监听并移除已知旧令牌目录。失败时保留旧入口，按报错排查。仅需本机 HTTP 的新部署可用 **3 → 8 → 1**。 共用已有 Caddy 时，站点片段对实际服务组只读。无法自动识别该组时，核实服务身份后用 `SBOX_CADDY_GROUP=<组名> sing_box` 设置。
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
6. **分发并验收。** 执行 **9 → 2** 重新生成三份文件，通过独立可信渠道交付新 HTTPS 地址。客户端刷新完整订阅并重载；静态导入的 URI 需重新导入，自签 TUIC、AnyTLS、Trojan 和自签公网 VMess 使用完整订阅。用实际客户端验证新身份可连接、旧身份不能重新连接。检查 `ss -lntp`，BusyBox 应仅绑定 `127.0.0.1:<新端口>`。从另一台机器分别测试 IPv4/IPv6 的 HTTPS、旧 HTTP 端口与旧令牌。验证通过后删除云安全组中旧 HTTP 端口的放行项。

**客户端共享。** 默认 Mihomo 仅供本机使用。主线 DNS 启用并监听 `127.0.0.1:1053`，Serv00 默认关闭 DNS。确需局域网共享时，在自己维护的客户端覆写中显式启用 `allow-lan`，用 `bind-address` 限制网卡、`lan-allowed-ips` 限制允许的 CIDR，并设置非空 `authentication` 用户名和独立密码。DNS 保持回环可避免向局域网开放解析服务。应从允许和未允许的地址分别测试代理认证和访问结果。

## GitLab 订阅发布

1. 盘点所有使用旧令牌的 VPS、分支、定时任务、通知和客户端。准备专用私有项目，为每个发布端创建独立、限期有效的项目令牌，授予向指定分支推送所需的角色和 `write_repository` 范围。受保护分支还需允许对应身份推送。客户端使用上一节的新 Caddy 令牌。[GitLab 项目访问令牌](https://docs.gitlab.com/user/project/settings/project_access_tokens/)
2. 每台 VPS 依次运行 **3 → 7 → 1**，填写项目路径和分支，在隐藏输入框中输入新令牌；不要把令牌写进 shell 历史。首次设置要成功发布三份文件，随后才替换本机仅 root 可读的 `gitlab-publisher`。再次发布用 **3 → 7 → 2** 或 **9 → 2**。发生并发推送冲突时重试，以远端最新提交为基础。脚本只更新三个订阅文件。
3. 检查远程分支内容与本地相符，通知和二维码显示 Caddy URL。成功设置会删除旧脚本、分享文件和旧仓库的 GitLab origin。不再使用 GitLab 的机器运行 **3 → 7 → 3** 清理旧版痕迹。检查自建定时任务、其他凭据管理器、旧日志和备份是否保存旧值；处置时避免将令牌打印到工单或日志中。
4. **先替换全部发布端，再撤销旧令牌。** 在令牌所属的 GitLab 项目、群组或个人设置中撤销相应令牌，验证旧身份拉取、推送均被拒绝。通知客户端重新导入 HTTPS 地址。历史 Git 提交、聊天或截图中的节点密码仍可能已泄露，需按 VPS 步骤轮换节点身份。[GitLab 令牌管理](https://docs.gitlab.com/security/tokens/)

## Serv00 / Hostuno

FreeBSD 二进制 `sb` 和 `server` 的 SHA-256 固定在 `serv00_runtime.py` 的 `ASSETS`。摘要来自本次核对的上游发行资产（ID 212842014、212841973），上游未提供独立作者签名。维护者换版时需核实新资产、复算摘要，再将常量与安装器一起提交审查。

1. 在 SSH 中保存旧配置、证书、原端口分配和固定 Argo 隧道设置。停止指向旧 GET 操作 URL 或远程下载旧脚本的 cron、Worker 与工作流。保留 SSH 管理入口并核实宿主机的 SSH 主机密钥。
2. 将同一受审提交的完整发布目录放到自己的账户下。从该目录运行 `bash serv00.sh --migrate`。新装使用 `--install`。迁移会轮换节点身份、生成独立订阅令牌并切换进程，原客户端会断开。固定 Argo 在 Cloudflare 控制台将 HTTP 回源改成命令输出的 `127.0.0.1:<端口>`。旧进程参数或进程页曾暴露固定隧道令牌时，还需轮换 Cloudflare 隧道令牌。
3. 通过 `~/bin/sb --show` 获取三份新 HTTPS 地址（`/sing-box-sub/<令牌>/` 下的三个文件），验证实际客户端能连接。运行 `~/bin/sb --retire-legacy`，程序通过 HTTPS 逐一比对三份新内容后才移除旧订阅。再实测旧 URL 和旧凭据失效。检查静态 Web 服务身份能读取发布副本，其他租户无法读取私有工作目录 `~/.sing-box-serv00`。需要指定 Web 服务组时使用 `SERV00_WEB_GROUP` 指向已核实的宿主读取组。使用 sing-box 1.10 客户端时可运行 `SERV00_CLIENT_SERIES=1.10 ~/bin/sb --refresh` 生成对应订阅，切回新版使用 `modern`。
4. 保活调用本机已安装的 `bash "$HOME/.sing-box-serv00/release/serv00keep.sh"`。SSH 批量调度使用 `kp.sh --keep`。为调度器设置 `ACCOUNTS` JSON 数组（字段 `SSH_USER`、`HOST`，口令模式另含 `SSH_PASS`）及 `SSH_KNOWN_HOSTS_FILE`。主机密钥须与宿主控制台或其他受信记录核对。可选 GitHub Actions 模板位于根目录 `SSH.yml`、`serv00.yml`，对应 Secrets 为 `SERV00_ACCOUNTS`、`SERV00_KNOWN_HOSTS`、可选 `SERV00_SSH_PRIVATE_KEY`。Worker 和健康工作流的 `HEALTH_URLS` 只填 HTTPS 的 `/health` 地址，响应为 `ok`。管理操作通过 SSH 执行。将仓库变量 `SERV00_REVIEWED_COMMIT` 设为已核实的完整 40 位提交号。自动化安装或更新应从该提交的 checkout 运行。

若首次 Serv00 迁移在切换进程后失败，可在 SSH 运行 `~/bin/sb --recover`，使用保存在私有目录的候选配置及新身份恢复服务，再检查端口和订阅。已有新版部署的更新失败会尝试恢复先前的 active 配置。

## 回滚与线上验收

保留迁移前程序和私有配置快照至验收完成。若候选内核或配置检查失败，先检查当前进程和原订阅仍可用。程序版本回退可重新选择已保存的受审发布目录，再按该版本检查配置并重启。若新身份已经生效、旧身份已泄露或撤销，恢复服务时须将新的认证值、WS 路径、证书及订阅读取凭据应用到恢复配置，并重新生成订阅。旧版脚本的防火墙清空和令牌分享路径不适用于回滚。

| 部署者检查 | 预期结果 |
| --- | --- |
| 云安全组、VPS 两族防火墙、SSH 独立新会话 | 实际 SSH 通道畅通，IPv4 与 IPv6 的启用节点端口分别可达，其他业务转发与默认策略符合主机基线 |
| 域名和真实 ACME 签发、续期 | 正式客户端信任证书链，名称匹配，三份 HTTPS 内容与当前 VPS 一致，旧 HTTP 及旧令牌已失效 |
| GitLab 实际项目与令牌撤销 | 新身份能推目标分支，客户端无发布权限，撤销后所有旧发布端和旧地址不能再认证 |
| Serv00 的宿主身份、文件权限、实际部署版本 | 安装的面板与受审 checkout 相同，宿主通过 HTTPS 提供新订阅，旧 GET 管理路径返回 404，SSH 保活只运行本机受审版本 |
| 客户端和业务链路 | 独立客户端分别验证 TCP/UDP、Argo、证书拒绝、旧身份失效及 WARP 出口。确认旧的 HTTP/日志泄露路径已关闭 |
