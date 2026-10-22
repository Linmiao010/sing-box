# sing-box

面向 VPS 的 sing-box 多协议部署与管理脚本，提供节点配置生成、订阅分享和自定义域名 HTTPS 订阅。主入口为 [`sb.sh`](sb.sh)，安装后通过 `sing_box` 命令管理。

## 主要功能

- 部署 VLESS Reality Vision、VMess WebSocket（可选 TLS / Argo）、Hysteria2、TUIC v5 和 AnyTLS。
- 生成 sing-box、Clash/Mihomo 客户端配置及聚合节点链接，订阅内容在服务器本地生成。
- 提供本地 IP HTTP 订阅、GitLab 订阅和自定义域名 HTTPS 订阅；域名模式支持令牌轮换和后台端口管理。
- 管理协议端口、证书、Argo 隧道、域名分流、Telegram 通知以及 sing-box 内核版本。
- 适配 IPv4、IPv6 和双栈 VPS，包含 Debian、Ubuntu、CentOS、Alpine 安装分支，以及 amd64、arm64、armv7 架构识别。

IP 检测通过 HTTPS 查询公网地址，主查询站失败时尝试备用站。若本机网卡已配置 IPv6，但公网查询未成功，界面会显示本机地址及“公网探测未成功”，不再直接显示“无 IPv6”。本机地址仅用于状态提示，不会自动替代节点中的公网地址；刷新时两种地址族均探测失败会保留原节点地址。公网查询成功也不代表节点入站端口已经可达。

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

## 本地验证范围

在仓库目录运行离线行为测试：

```bash
python -m unittest discover -s tests -v
```

测试需要 Python 3、Bash、jq 和 Caddy；Windows 可使用 Git Bash。额外的 YAML 解析检查可使用 PyYAML，它仅用于测试。测试抽取相关 Bash 函数，在临时目录中检查安装、订阅生成、失败处理和通知内容，TG 请求由测试替身拦截。真实 Caddy 的 localhost 路由检查也只验证本机访问行为；这些结果不代表真实 VPS 的 systemd/OpenRC 服务、域名 DNS、公网 HTTPS 证书签发或客户端联网更新已经验证通过。

## 附带脚本

仓库还包含 [`serv00.sh`](serv00.sh) 和 [`serv00keep.sh`](serv00keep.sh)，用于 Serv00/Hostuno 环境下的部署与保活，支持 VLESS Reality、VMess WS/Argo 和 Hysteria2。这些脚本有各自的运行环境和外部资源依赖，不适用上面的 VPS 安装与更新流程。

## 许可证

本项目采用 [GNU GPL v3](LICENSE) 许可证。
