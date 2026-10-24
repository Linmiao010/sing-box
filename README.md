# sing-box

面向 VPS 的 sing-box 多协议部署与管理脚本，提供节点配置生成、订阅分享和自定义域名 HTTPS 订阅。主入口为 [`sb.sh`](sb.sh)，安装后通过 `sing_box` 命令管理。

## 主要功能

- 部署 VLESS Reality Vision、VMess WebSocket（可选 TLS / Argo）、Hysteria2、TUIC v5、AnyTLS、Trojan + TLS 和 Snell。
- 生成 sing-box、Clash/Mihomo 客户端配置及聚合节点链接，订阅内容在服务器本地生成。
- 提供本地 IP HTTP 订阅、GitLab 订阅和自定义域名 HTTPS 订阅；域名模式支持令牌轮换和后台端口管理。
- 管理协议端口、证书、Argo 隧道、域名分流、Telegram 通知以及 sing-box 内核版本。
- 适配 IPv4、IPv6 和双栈 VPS，包含 Debian、Ubuntu、CentOS、Alpine 安装分支，以及 amd64、arm64、armv7 架构识别。

普通菜单只读取本地信息，公网 IP、地区和出站连通性显示“未探测”。选择 **17 → 1** 后通过 HTTPS 查询公网地址，主查询站失败时尝试备用站。若本机网卡已配置 IPv6，但公网查询未成功，会区分显示本机配置与公网探测结果。本机地址仅用于状态提示，不会自动替代节点中的公网地址；使用菜单 **15** 刷新地址时，两种地址族均探测失败会保留原节点地址。公网查询成功也不代表节点入站端口已经可达。

## 安装与更新

在 VPS 上以 root 身份运行以下任一命令，需要 Bash 及对应的下载工具：

```bash
bash <(wget -qO- https://raw.githubusercontent.com/Linmiao010/sing-box/main/sb.sh)
```

或：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Linmiao010/sing-box/main/sb.sh)
```

进入菜单后选择 **1** 安装。安装完成后运行 `sing_box` 打开管理菜单。已有安装升级后也使用该命令；旧 `sb` 入口作为兼容链接保留，供已有定时任务调用。

首次安装、`sing_box` 快捷命令的菜单 **7** 升级、版本检查和重新安装均使用本仓库 `Linmiao010/sing-box` 的 `main` 分支。本地修改必须先推送到该分支，远程安装或升级命令才能取得新版本。

要在已安装的 VPS 上先验证未推送的改动，可将当前仓库的同一份 `sb.sh` 上传到 VPS，在该文件所在目录以 root 执行：

```bash
bash -n ./sb.sh && install -m 755 ./sb.sh /usr/bin/sing_box && {
  if [ -f /usr/bin/sb ]; then
    ln -sf /usr/bin/sing_box /usr/bin/sb || exit 1
  fi
  sing_box
}
```

这会把当前文件安装为同一个 `sing_box` 快捷命令。之后菜单 **7** 仍从本仓库 `main` 下载；本地改动推送前执行升级，会被远程版本覆盖。

## 常用快捷操作

以下命令用于已完成安装的 VPS。

创建本地 IP HTTP 订阅，将示例令牌替换为自己的令牌：

```bash
printf '3\n8\n1\nmy_subscription_token_123\n\n' | sing_box
```

启用 Argo 临时隧道：

```bash
printf '3\n3\n1\n1' | sing_box
```

启用 Argo 固定隧道，替换示例中的密钥与域名：

```bash
printf '3\n3\n2\n1\n固定密钥\n固定域名' | sing_box
```

设置域名分流，替换示例中的域名后缀：

```bash
printf '5\n2\n1\n后缀域名1 后缀域名2' | sing_box
```

## Trojan + TLS 与 Snell（VPS）

新安装会同时配置 Trojan + TLS；使用 sing-box 1.14.0 或更新的正式版内核时，也会配置 Snell。旧安装更新脚本后，进入 **3 → 11「管理 Trojan / Snell」**，可分别启用、设置端口和密码、切换 Trojan 证书或停用协议，无需重装。Trojan 密码和 Snell PSK 在这个菜单中独立管理。

Clash Verge Rev 使用现有的 **`clmi.yaml` Mihomo 订阅**。启用后刷新订阅，两个节点会加入手动选择、自动选择和负载均衡组。Snell 使用 sing-box 服务端 `version: 5`、Mihomo / sing-box 客户端 `version: 4`；sing-box 的这个服务端模式兼容 v4，不启用 v5 的 QUIC 代理模式。客户端需要支持 Snell v4，sing-box 客户端还需要 1.14.0 或更新版本。

Trojan 默认沿用安装时选用的证书，也可在 **3 → 11** 中选择已有 ACME 证书。使用脚本自签证书时，生成的客户端配置沿用现有自签模式，跳过证书校验；使用 ACME 证书时启用证书校验。使用自有域名时，确认 SNI 与证书名称匹配。两个协议的服务端监听均使用 TCP，需要放行所选 TCP 端口。

Trojan 会生成 `trojan://` 分享链接并加入 `jhsub.txt`。Snell 通过完整 YAML / JSON 配置分发，不生成不通用的单节点 URI；在 Clash Verge 中使用 `clmi.yaml` 即可。

额外入站保存在 `/etc/s-box/sb_extra.json`，服务与原有 `sb.json` 一起加载。设置新协议前会校验合并配置，失败时保留原配置；内核切换也会先用候选内核检查配置，不兼容时保留当前内核。原有五类协议仍使用原管理菜单。

## 自定义域名 HTTPS 订阅（VPS）

运行 `sing_box`，进入 **3 → 10「设置自定义域名分发」**，可直接启用或更换域名；**3 → 8** 用于管理订阅入口。域名订阅通过 Caddy 提供 HTTPS，订阅后台仅监听 `127.0.0.1`；节点协议及其端口沿用原配置。

### 启用域名订阅

1. 准备独立的订阅域名，例如 `sub.example.com`。将其 A/AAAA 记录指向当前 VPS；初次配置使用 DNS-only（仅 DNS 解析），并确保填写的 IPv4/IPv6 地址可达。
2. 放行 VPS 防火墙与云平台安全组的 **TCP 80、443**。使用独立订阅后台端口，无需更改节点端口。
3. 在 `sing_box` 的 **3 → 10** 中输入域名（原 **3 → 8 → 5** 入口仍可用），仅填 `sub.example.com`，不带 `https://`、端口或路径。
4. 输入至少 **16 个字符**的订阅令牌，只能使用字母、数字、`_`、`-`。直接回车会通过 `openssl rand -hex 24` 生成令牌，即 24 个随机字节编码为 48 个十六进制字符。
5. 输入一个未占用的本机后台端口，或回车随机选择。脚本配置入口并检查 HTTPS；成功后显示订阅链接。

首次使用且没有可复用的 Caddy 时，脚本会提示当前下载阶段。官网下载连续 20 秒低于 1 KiB/s 或下载校验失败后，会自动尝试 GitHub 官方发布包，并校验 SHA-256；每个二进制下载最多 120 秒，不对同一来源反复重试。两种来源均失败时保留现有 Caddy 文件并清理临时下载；这一步尚未开始申请订阅域名证书。已有可执行的 `/etc/s-box/caddy` 或可共享的系统 Caddy 时跳过下载。

三个订阅地址分别为：

```text
https://sub.example.com/<token>/sbox.json
https://sub.example.com/<token>/clmi.yaml
https://sub.example.com/<token>/jhsub.txt
```

`sbox.json` 用于 Sing-box 客户端，`clmi.yaml` 用于 Clash/Mihomo，`jhsub.txt` 为聚合节点。将 `<token>` 换成设置的令牌，或直接复制脚本显示的完整地址。持有链接即可下载订阅内容，请按凭据保管；泄露后通过菜单 **3 → 8 → 2** 轮换令牌，并更新客户端中的链接。

### 日常操作

| 菜单 | 操作 |
| --- | --- |
| `3 → 10`（或 `3 → 8 → 5`） | 启用域名订阅或更换域名 |
| `3 → 8 → 2` | 轮换订阅令牌，回车随机生成 |
| `3 → 8 → 3` | 更换订阅后台端口，自动更新 Caddy 转发配置 |
| `3 → 8 → 6` | 显示当前订阅链接及二维码 |
| `3 → 8 → 4` | 停用并卸载订阅入口，保留 GitLab 配置 |
| 主菜单 `9` | 刷新节点和订阅文件，显示当前域名订阅链接 |

刷新时保留现有文件格式，并逐个文件原子替换；生成或校验失败会保留原订阅文件。客户端需要手动刷新或按自身设置定时刷新订阅，服务端更新文件不会主动推送到客户端。

域名模式下，主菜单 **9** 的刷新操作跳过自动 GitLab 推送；原来的 **3 → 7** GitLab 设置入口仍保留。TG 通知中的订阅链接也优先使用域名地址，已有通知脚本会在下次发送前迁移，保留其他通知内容和机器人凭据。

停用订阅不会自动开放公网 HTTP。确需使用原 IP 订阅时，先选择 **3 → 8 → 4** 停用域名订阅，再选择 **3 → 8 → 1** 启用 HTTP 模式。

### 与现有网站共用 VPS

脚本自行管理的 Caddy 仅启用 HTTP/1.1、HTTP/2，避免占用 UDP 443。若系统已有 `/etc/caddy/Caddyfile` 且能运行 `caddy` 命令，脚本会加入一条带 `# s-box subscription` 标记的独立订阅配置 `import`，共享现有 Caddy。配置校验后先尝试热加载；热加载失败（例如已有配置设置了 `admin off`）时，可能重启现有 Caddy 服务，现有网站连接可能短暂中断。

若 TCP 80/443 被 Nginx、非标准安装的 Caddy 或其他入口占用，且脚本无法接入标准 Caddy 配置，脚本会报告冲突。需要在现有站点服务中自行接入订阅路由，或先调整入口配置；脚本不会停止这些服务来抢占端口。

## 性能排查与本次修复（2026-10-24）

普通打开菜单不查询远端版本、公网 IP 或 SOCKS，也不安装软件包、创建 TUN 设备或修改 crontab。主菜单 **7/8** 更新时查询版本，**17 → 1** 主动刷新版本和联网状态，**17 → 2** 显式修复依赖及 OpenVZ TUN。原有操作编号与规则代理功能保留。

依赖检查只在安装/修复动作中进行；状态记录使用 `/etc/s-box/dependencies.ready`。旧的 `sbyg_update` / `sing_box_update` 相对目录标记不再控制启动：已有安装无论标记是否丢失，必要命令齐全就不运行包管理器，安装缺失依赖且复查成功后才写新标记。不再运行全系统 `yum/dnf update`，也不自动重写 CentOS 8 软件源；软件源失效时需先修复源，再执行 **17 → 2**。RPM 系统保留 iptables 的开机启用，修复动作不重载正在工作的防火墙。

主动状态刷新各网络请求预算为：脚本版本 6 秒、内核版本两个来源合计 6 秒、公网地址及地区最多 30 秒、SOCKS 两种解析方式合计 6 秒，总网络预算不超过 48 秒，另加本地进程执行时间。失败会明确显示查询失败；状态探测不写节点地址。脚本更新与首次内核安装的版本元数据也有连接/总超时，内核更新复用本次已取得的版本。二进制下载不套用这些短预算。

重定向输出的节点/订阅生成跳过展示用 `sleep 2`；交互展示保留停顿，Argo 等真实异步等待保留。

### 证据与边界

已重新核对 fork `a1507538d12b3b95299fff8377f0ac35f7d0c1d4`、上游 `b13f9435c4792fccf04d4fbc73e24a1948abff1b`，共同祖先 `5923edcc5d38654d6153f5f8795c4169f8014ed6`，fork ahead 11 / behind 2。用户曾经测速更快的部署版本尚未取得，不能用本次上游快照代替它。

| 问题 | 证据与归属 | 本次处理 / 验证状态 |
| --- | --- | --- |
| 首屏等待远端服务 | 版本、IP、SOCKS 串行请求及重复探测主要继承上游；fork 的双源 IP 回退增加部分路径的等待 | 改成按需探测，普通首屏请求数为 0；隔离测试覆盖正常、超时、DNS 失败和无 IPv6 |
| 换目录启动触发依赖初始化 | 相对标记、全系统更新与顶层 TUN/ping 逻辑继承上游 | 移至安装/显式修复；已测标记丢失、旧标记、不同目录及安装失败 |
| 静默订阅刷新等待 | 上游已有展示 sleep，fork 新动作也调用这条链路 | 输出重定向时跳过展示等待；保留发布失败回滚测试 |
| 网页慢 / 吞吐慢 | 首轮菜单修复前的 8 组原有节点 TLS/Argo 对照中，Mihomo YAML 与上游结构完全一致；JSON 唯一结构差异为 fork 显式 `stack: gvisor` | 后续确认国内域名冷解析依赖默认海外 DNS，补丁与实验见下节；真实客户端性能仍需复测 |
| 自动组可能选择新协议 | fork 将 Trojan/Snell 加入共享候选 | 未取得实际选组/出口记录，不能确认性能因果；保留协议可用性 |

本次真实内核校验使用 **sing-box 1.14.3** 与 **Mihomo v1.19.33**：分别检查客户端 5 个有效模板分支，sing-box 服务端检查 TLS 开/关和额外协议的 8 种组合；另有 32 组 JSON/YAML 结构与节点引用检查。校验不启动代理服务，不证明规则集下载、真实 TUN、Linux 服务、网络连通性或吞吐；legacy 1.10.7 未做真实内核校验。

### FlClash 国内网页首访 DNS 修复

后续复现目标为 Android FlClash 导入 `clmi.yaml`，首次访问 `zhihu.com` 慢；对照使用同一协议、两台不同 VPS。复核最新上游仍为 `b13f9435…`，其 DNS 模板也存在下面的路径，不能将此认定为 fork 独有回归。

原规则第一条 `GEOIP,LAN,DIRECT` 在判断地址是否属于私网之前会触发域名解析，而默认 `nameserver` 只有 `1.1.1.1` / `8.8.8.8` 的 DoH。配合 `respect-rules: true`，这些海外 DNS 的请求按规则经代理出口。即使知乎业务随后判定为直连，首次请求也已经等待这段解析；缓存命中时则可能看不出问题。VPS 的常规测速接近，并不能排除这条 DNS 链路的延迟或重试不同。[Mihomo GEOIP 实现](https://github.com/MetaCubeX/mihomo/blob/v1.19.33/rules/common/geoip.go)、[DNS 配置说明](https://wiki.metacubex.one/en/config/dns/)

修复只在 `clall()` 增加以下 DNS 策略，使已有 CN 域名集合使用脚本原本已使用的阿里/腾讯 DoH。普通 VMess、TLS 和所有 Argo 生成分支共用这处模板。

```yaml
dns:
  nameserver-policy:
    "geosite:cn":
      - "https://223.5.5.5/dns-query"
      - "https://doh.pub/dns-query"
```

默认海外 DNS、节点域名解析、IPv6、fake-IP、原分流规则及节点选择均保留。没有给 LAN 加 `no-resolve`，避免改变非 CN 域名解析到私网地址时的处理；也不需要同时增加 `direct-nameserver`。

用真实 **Mihomo v1.19.33 Windows 核心**做了回环单变量实验：默认 DNS 人工延迟 600 ms，国内策略 DNS 无延迟，均返回回环地址，HTTP 内容固定 34 字节；每次冷请求用独立核心进程，随后再发一次热请求。

| 配置 / 域名 | 冷请求完成时间 | 实际收到查询的 DNS |
| --- | --- | --- |
| 原配置，`zhihu.com`，n=3 | p50 605.373 ms，范围 605.225–615.984 | 默认慢 DNS |
| 仅增加策略，`zhihu.com`，n=3 | p50 4.053 ms，范围 3.867–4.607 | 国内策略 DNS |
| 仅增加 `direct-nameserver`，n=1 | 606.904 ms | 先默认慢 DNS，再直连 DNS |
| 仅前移 CN 规则，n=1 | 604.426 ms | 默认慢 DNS |
| 仅 LAN 加 `no-resolve`，n=1 | 605.472 ms | 默认慢 DNS |
| `www.zhihu.com`，各 n=1 | 604.669 → 3.932 ms | 默认 → 国内 |
| `picx.zhimg.com`，各 n=1 | 605.153 → 4.832 ms | 默认 → 国内 |

共 17 个独立核心进程、34 次目标 HTTP 请求，全部 200、34 字节、0 失败；热请求均没有新增 DNS 查询。`example.org` 仍使用默认 DNS，`lab.internal` 解析后仍按 LAN 直连。由于这些目标都返回 `127.0.0.1`，它们的最终连接均为 LAN/DIRECT，**该实验不证明公网目标的实际出口或 Android TUN 行为**。600 ms 是测试注入值，不能将表格当成知乎真实提速或两台 VPS 差异的定案。

原始证据保存在本地 `.test-tools/protocol-validation/dns-policy-experiment/results.json` 及对应核心日志。另有生成回归覆盖 TLS 开/关 × Argo 固定/临时状态，检查 DNS 策略、原规则、IPv6 和其他 DNS 设置。

要让手机实际使用修复，必须完成这条链路：

1. 将修复后的 `sb.sh` 安装到 VPS；本地代码未推送前，菜单 **7** 取不到此补丁，使用下文的手动部署方式。
2. 在 VPS 选择 **9 → 2** 重新生成 `clmi.yaml`，确认文件中存在上述 `nameserver-policy`。
3. 在 FlClash 更新这个订阅并重新应用配置。若使用文件导入，需要重新导入；仅更新 VPS 脚本不会更新手机已有配置。
4. 核对 FlClash 最终生效配置仍有这项策略。若设置了 DNS 覆写，让此字段跟随订阅，或在覆写中加入同一策略；应用会合并覆写，订阅原文不等于最终配置。[FlClash 配置合并实现](https://github.com/chen08209/FlClash/blob/main/lib/common/task.dart)
5. 固定原有同一节点，在相同网络下重新测试知乎及其图片资源的冷/热加载。这里尚未访问用户 Android 或两台 VPS，真实改善幅度待这一步确认。

### 菜单离线计时

在同一 Windows 开发机的 Git Bash 下，抽取真实菜单与显示函数，重定向系统路径；JSON/进程状态使用 fixture，每次模拟网络请求只等待 10 ms。计时从启动隔离进程到实际 `readp` 输入点，随后发送 `0` 并确认退出。顶层 OS/OpenVZ 初始化另做隔离行为检查，未执行完整安装器。

| 模拟场景 | 样本数（前 / 后） | 修改前首输入 p50 / p95 | 修改后首输入 p50 / p95 | 每次公网请求（前 → 后） | 失败数 |
| --- | --- | --- | --- | --- | --- |
| 正常网络 | 30 / 30 | 3.872 / 5.566 秒 | 2.301 / 2.470 秒 | 12 → 0 | 0 / 0 |
| 请求超时 | 30 / 30 | 4.068 / 4.846 秒 | 2.271 / 2.659 秒 | 14 → 0 | 0 / 0 |

修改后各场景包管理器、ping、mknod、crontab 调用也为 0；DNS 失败和无 IPv6 的行为测试通过。旧版正常场景中各阶段耗时中位数为：`lapre` 0.411 秒、两次 `v4v6` 合计 0.873 秒、`sbymfl` 1.318 秒、`showprotocol` 2.349 秒；后三项有嵌套，不能相加。

连续 20 次 `3 → 0` 往返可正常退出：子菜单进入 p50 为 0.018 秒（范围 0.015–0.030），返回 p50 为 1.886 秒（范围 1.774–3.440），无公网或包管理器调用。返回测试以同进程 `main_menu` 模拟 `sing_box` 重入，不包含真实新解释器启动，n=20 不报告 p95。

上述计时不是 Linux/VPS 验收：Windows/Git Bash 的本地管道开销较大，基线采样期间穿插了少量其他测试，修改后串行测量；不据此计算严格性能增益。该模型首屏 p95 仍高于 2 秒，**VPS 首屏 ≤2 秒、往返 ≤1 秒的目标尚未实测确认**。已证实的是普通展示消除了隐式联网、包安装和 TUN/crontab 修改。

原始分段日志保存于本地 ignored 的 `.test-tools/menu-before.json` / `menu-after.json`。可重新采集；比较前后时固定机器负载，旧源码使用明确的提交，不要在提交补丁后将 `HEAD` 误作旧版：

```bash
python tests/test_menu_performance.py --benchmark --samples 30 --scenarios online timeout --source-ref a1507538d12b3b95299fff8377f0ac35f7d0c1d4 --output .test-tools/menu-before.json
python tests/test_menu_performance.py --benchmark --samples 30 --scenarios online timeout --switch-cycles 20 --output .test-tools/menu-after.json
```

### 可重复的配置与网络采集

先备份客户端当前配置和持久化策略选择。分别准备上游生成配置、fork 生成配置及客户端合并/覆写后实际配置，使用脱敏标签对照：

```bash
python tools/compare_client_configs.py upstream.json fork.json
python tools/compare_client_configs.py downloaded.yaml effective.yaml
```

工具仅输出变化路径与类型，保留规则/节点列表顺序，不打印配置值；YAML 解析需要测试环境已有的 PyYAML。凭据不同也会报告其路径，勿把凭据差异误认为性能因素。订阅链接和配置原件仍须私下保管。

固定同一个原有节点、VPS、客户端/服务端内核及测试目标。按 **A/B/A/B** 手动切换配置，每段独立调用采集工具，结束后恢复原策略：

- A：上游规则配置，固定原有节点；B：fork 规则配置，同一节点。
- C：fork 全局模式只作诊断对照；D：fork 自动组/负载均衡，逐次记录实际最终节点。
- E：仅实际使用 sing-box TUN 时，在 B 基础上单独改变该客户端版本支持的 stack，记录 CPU、TCP/UDP 与吞吐。Mihomo 不做这个 JSON 栈实验；单节点链接应检查客户端自带规则与覆写。

```bash
# TARGET 为实际慢网页或关键资源；每段至少10次，冷/热缓存分别记录。
python tools/network_probe.py --label A1-fixed-warm --url "$TARGET" --output web.csv --count 10 --system-path
# 手动切换为 B 后执行；随后恢复 A2，再切换 B2，沿用同一输出文件。
python tools/network_probe.py --label B1-fixed-warm --url "$TARGET" --output web.csv --count 10 --system-path

# 固定大小的同一下载对象；BYTES 填实际大小，不用204或错误页测吞吐。
python tools/network_probe.py --label B-single --url "$DOWNLOAD_TARGET" --output throughput.csv --count 5 --concurrency 1 --expected-bytes "$BYTES" --system-path
python tools/network_probe.py --label B-multi --url "$DOWNLOAD_TARGET" --output throughput.csv --count 5 --concurrency 4 --expected-bytes "$BYTES" --system-path

# 隔离显式代理链路时，先核实监听类型和端口。
python tools/network_probe.py --label B-http-proxy --url "$TARGET" --output proxy.csv --count 10 --proxy http://127.0.0.1:7890
```

当前 YAML 的 `7890` 是 HTTP 端口；JSON 的 TUN 配置不因此具备 SOCKS 入口。`--system-path` 禁用 curl 的环境代理，仍服从系统路由/TUN；显式代理实验不能替代原 TUN 场景复验。`--family 4` / `6` 只用于单变量对照，不修改系统 IPv6。

CSV 记录 `time_namelookup/connect/appconnect/starttransfer/total`、`speed_download`、`size_download`、HTTP 状态、curl 退出码、重定向、并发及 walltime。时间多为累计值，不能直接相加；代理下连接时间可能只到本地代理，`socks5h` 的 DNS 时间不表示完整目标解析耗时。[curl 官方计时说明](https://curl.se/docs/manpage.html)

速度单位为 B/s，除以 `1e6` 得 MB/s，乘以 `8/1e6` 得 Mbps。并发吞吐按每批总字节 / 实际 walltime 计算，CSV 中重复的 `batch_*` 列按 `(run_id, batch)` 去重。汇总报告失败次数、成功样本 p50/范围，仅样本数至少 30 时报告 p95；失败批次不进入成功吞吐统计。结果不记录 URL、query、代理凭据或响应体，人工 label 也不要包含敏感信息。每次 curl 为新进程，不等于系统 DNS 冷缓存。

curl 只能测请求，网页整体加载还需浏览器 HAR；删除 cookie、认证头、令牌和敏感 URL 后再分享。另记录如下表格，至少覆盖预期直连、代理、私网和自定义例外；缺失项填“未测”，不能用 URL 延迟代替吞吐：

| 标签 / 时间 / 冷热缓存 | 目标代号 | 预期出口 | 实际最终节点与协议 | 命中规则 / DNS 出口 | HTTP/退出码、失败数 | TTFB / 页面完成 | 单/多连接吞吐、总字节 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A1 / 待填 | 慢页、直连或代理目标 | 待填 | 待填 | 待填 | 见 CSV | CSV / HAR | 见 CSV |

真实网页 TTFB、加载完成时间、单/多连接吞吐当前均为**未测**，尚不能判定达到上游 90% 吞吐或无超过 10% 的网页回退。继续验证最少需要：客户端应用/内核/OS与TUN或系统代理模式、导入格式及脱敏生效配置、实际选中节点与是否经CDN/Argo/WARP、VPS与管理脚本版本/哈希、原始慢网页和测速目标、用户曾认为更快的部署版本。

### 部署与回滚

本补丁未推送或部署。先将工作树 `sb.sh` 上传到 VPS；在服务器执行以下步骤（不是在开发机运行安装器）：

```bash
backup="/root/sing-box-backup-$(date +%Y%m%d-%H%M%S)"
install -d -m 700 "$backup" || exit 1
manager=$(command -v sing_box || command -v sb) || exit 1
cp -L "$manager" "$backup/manager.sh" || exit 1
cp -a /etc/s-box "$backup/s-box" || exit 1
bash -n ./sb.sh && install -m 755 ./sb.sh /usr/bin/sing_box || exit 1
if [ -e /usr/bin/sb ]; then ln -sfn /usr/bin/sing_box /usr/bin/sb; fi
sha256sum ./sb.sh /usr/bin/sing_box
readlink -f /usr/bin/sb
sing_box
```

确认两份 SHA-256 相同，普通首屏可接受输入，选择 **0** 正常退出；断网复验应使用可恢复的测试环境，勿断开生产 SSH。菜单 **17 → 1** 只查看主动探测结果，菜单 **15** 才会刷新节点地址。

后续 DNS 修复修改了 Mihomo 客户端模板，未修改服务端配置，部署脚本不需要重启 VPS 代理服务。部署后必须选择 **9 → 2** 重新生成订阅，再让客户端刷新并核对下载文件、覆写及实际选组；随后按上述矩阵复测。对生效配置使用对应客户端版本的 `sing-box check -c …` / `mihomo -t -f …`，服务端单独校验 `sb.json` 和存在时的 `sb_extra.json`，不能只检查 JSON/YAML 语法。

回滚脚本：`install -m 755 "$backup/manager.sh" /usr/bin/sing_box`。若本次还刷新过订阅，需要恢复时只从备份取回 `sbox.json`、`clmi.yaml`、`jhsub.txt` 等实际改动文件，再刷新客户端；保留令牌、凭据、节点地址与自定义规则，不卸载重装。菜单 **7** 始终下载远端 `main`，未推送的本地修复可能被覆盖。

## 本地验证范围

在仓库目录运行离线行为测试：

```bash
bash -n sb.sh
python -m unittest discover -s tests -v
git diff --check
```

测试需要 Python 3、Bash、jq 和 Caddy；Windows 可使用 Git Bash。额外的 YAML 解析检查可使用 PyYAML，它仅用于测试。测试抽取相关 Bash 函数，在临时目录中检查安装、订阅生成、失败处理和通知内容，TG 请求由测试替身拦截。真实 Caddy 的 localhost 路由检查也只验证本机访问行为；这些结果不代表真实 VPS 的 systemd/OpenRC 服务、域名 DNS、公网 HTTPS 证书签发或客户端联网更新已经验证通过。

`tests/test_config_matrix.py` 的真实内核检查需指定 `SING_BOX_BIN`、`MIHOMO_BIN`、`MIHOMO_DATA_DIR`（已下载 GeoSite/GeoIP 数据）、`SB_TEST_CERT` 和 `SB_TEST_KEY`（测试证书/私钥）。未提供时明确跳过相关检查，不能把跳过计为通过。新采集工具的集成测试仅向本机 HTTP 服务发送请求。

后续 DNS 修复启用上述 native 检查后，全套 **136 项通过，0 失败、0 跳过**（215.515 秒）；`bash -n sb.sh` 与 `git diff --check` 通过。测试日志在本地 `.test-tools/dns-policy-final-tests.log`。软件包安装、TUN 初始化和 Linux 服务管理使用测试替身，尚未在真实 Debian/Alpine/CentOS VPS 执行安装或重启验证。

## 附带脚本

仓库还包含 [`serv00.sh`](serv00.sh) 和 [`serv00keep.sh`](serv00keep.sh)，用于 Serv00/Hostuno 环境下的部署与保活，支持 VLESS Reality、VMess WS/Argo 和 Hysteria2。这些脚本有各自的运行环境和外部资源依赖，不适用上面的 VPS 安装与更新流程。

## 许可证

本项目采用 [GNU GPL v3](LICENSE) 许可证。
