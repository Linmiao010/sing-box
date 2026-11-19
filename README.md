# sing-box

面向 VPS 的 sing-box 多协议部署与管理脚本，提供节点配置生成、订阅分享和自定义域名 HTTPS 订阅。主入口为 [`sb.sh`](sb.sh)，安装后通过 `sing_box` 命令管理。

详细操作说明见 [操作手册](docs/README.md)。

## 主要功能

- 部署 VLESS Reality Vision、VMess 公网 WSS 或 Argo 回环源站、Hysteria2、TUIC v5、AnyTLS、Trojan + TLS 和 Snell。
- 生成 sing-box、Clash/Mihomo 客户端配置及聚合节点链接，订阅内容在服务器本地生成。
- 公网客户端通过 Caddy HTTPS 读取快照中的三个白名单订阅文件。本机 HTTP 可独立启用。GitLab 发布凭据只供服务器更新仓库。
- 管理协议端口、证书、Argo 隧道、域名分流、Telegram 通知以及 sing-box 内核版本。
- 适配 IPv4、IPv6 和双栈 VPS，包含 Debian、Ubuntu、CentOS、Alpine 安装分支，以及 amd64、arm64、armv7 架构识别。
- 支持逐地址探测与 IPv4 / IPv6 出站源地址选择，入口为 **3 → 4**；绑定作用于脚本探测和 sing-box 原生直连，与地址族优先级、节点公布地址分别设置。
- 支持单客户端 WARP 前置，入口为 **3 → 12**；为 `clmi.yaml` 的代理节点生成 `客户端 → WARP → VPS` 链路，客户端使用 Mihomo 内置 WireGuard，服务端出口另行设置。

## 安装与更新

从维护者或自己的代码审查渠道取得包含本次修复的 **40 位 Git 提交号**。在 VPS 的 root Bash 会话中运行下列命令。第一次安装选择菜单 **1**。从旧版迁移时，从新脚本进入菜单 **7**，输入同一提交号切换快捷命令。

```bash
(
  set -eu
  umask 077
  read -r -p '已核实的完整提交号：' REV
  [[ "$REV" =~ ^[0-9a-f]{40}$ ]]
  install -d -m 700 /etc/s-box/releases
  tmp=$(mktemp -d /etc/s-box/releases/.stage.XXXXXX)
  trap 'rm -rf -- "$tmp"' EXIT
  git -C "$tmp" init -q
  git -C "$tmp" fetch -q --depth=1 https://github.com/Linmiao010/sing-box.git "$REV"
  test "$(git -C "$tmp" rev-parse 'FETCH_HEAD^{commit}')" = "$REV"
  git -C "$tmp" fsck --strict --no-reflogs "$REV"
  git -C "$tmp" archive -o "$tmp/release.tar" "$REV"
  mkdir "$tmp/tree"
  tar xf "$tmp/release.tar" -C "$tmp/tree"
  chmod 700 "$tmp/tree/sb.sh"
  release="/etc/s-box/releases/$REV"
  if [[ ! -d "$release" ]]; then mv -T "$tmp/tree" "$release"; fi
  bash "$release/sb.sh"
)
```

每次更新先按同样方式安装已核实的新提交，菜单 **7** 原子切换 `/usr/bin/sing_box`。切换前保留旧发布目录，回滚时可重新选择其提交号。运行用户提供的未提交补丁时，应先审查并生成自己的提交，从可信仓库地址获取。订阅迁移和凭据轮换见 [操作手册](docs/README.md)。

安装时防火墙默认沿用原策略。使用 iptables、UFW 或原生 nftables 时，先核对两族输入链的来源封禁、接口、限速和最终拒绝位置。确认可以在既有规则之后追加时，运行 `sing_box --firewall-enable --input-position append`。若已有末尾拒绝规则或多个同族 nftables input 基础链，请在主机防火墙中手动放行。firewalld 同样需要 `--input-position append`，多个活动 zone 需指定公网入口所属的 `--zone <zone>`。该区域启用通配监听的 UDP 跳跃还需核对 DNAT 对来源封禁的影响，再显式传入 `--confirm-forward-ports`。管理员重新加载规则后，可运行 `sing_box --firewall-sync` 对账。添加 UDP 跳跃前，工具会核对已配置的节点、跳跃范围、宿主 UDP socket 和活动 NAT 转发。历史无标记的 DNAT 需对照原配置人工辨认。

`tools/releases.sha256` 与脚本一起由该提交固定，覆盖内核、Caddy、cloudflared、旧版地理数据库和随库的 sbwpph。内核支持清单中的 1.10.7 和 1.14.3。Caddy 采用清单中的官方发布归档。维护者更新清单时，从上游对应版本的 GitHub Releases 资产核实 SHA-256 或发布签名；1.10.7 发布页未提供资产摘要，本清单使用该官方固定标签对应三个归档的实测 SHA-256。仓库内的 sbwpph 直接核对同一受审树中的文件。新版本号、URL 与摘要一并审查提交，菜单中查询到更新版本后仍需等待清单覆盖。

菜单 **12** 可通过发行版签名软件源安装 Certbot，支持独立 HTTP-01 和已安装的认证插件。也可自行准备 `/root/ygkkkca/{ca.log,cert.crt,private.key}`。菜单 **13** 可从已配置的受信软件源安装系统 WARP 客户端，并通过 warp-cli 注册、连接、断开或查看状态。菜单 **11** 为当前已支持 BBR 的内核启用该算法。[Cloudflare 官方软件源](https://pkg.cloudflareclient.com/) · [Linux WARP 命令](https://developers.cloudflare.com/warp-client/get-started/linux/)

## 常用快捷操作

以下命令用于已完成安装的 VPS。

在 **3 → 10** 输入订阅域名，完成 Caddy HTTPS 配置。在 **3 → 8** 管理订阅令牌和仅供本机使用的 HTTP 后端。

启用 Argo 前，先在 **3 → 1 → 2** 将 VMess 切换为回环源站。再进入 **3 → 3** 选择临时或固定隧道；固定隧道在交互菜单中输入 Cloudflare 提供的隧道令牌和域名。

设置域名分流，替换示例中的域名后缀：

```bash
printf '5\n2\n1\n后缀域名1 后缀域名2' | sing_box
```

## 附带脚本

Serv00/Hostuno 使用同一受审提交的完整检出。在自己的 SSH 账户下从该检出运行 `bash serv00.sh`，并按操作手册设置保活。

## 许可证

本项目采用 [GNU GPL v3](LICENSE) 许可证。
