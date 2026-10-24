# sing-box

面向 VPS 的 sing-box 多协议部署与管理脚本，提供节点配置生成、订阅分享和自定义域名 HTTPS 订阅。主入口为 [`sb.sh`](sb.sh)，安装后通过 `sing_box` 命令管理。

详细操作说明见 [操作手册](docs/README.md)。

## 主要功能

- 部署 VLESS Reality Vision、VMess WebSocket（可选 TLS / Argo）、Hysteria2、TUIC v5、AnyTLS、Trojan + TLS 和 Snell。
- 生成 sing-box、Clash/Mihomo 客户端配置及聚合节点链接，订阅内容在服务器本地生成。
- 提供本地 IP HTTP 订阅、GitLab 订阅和自定义域名 HTTPS 订阅；域名模式支持令牌轮换和后台端口管理。
- 管理协议端口、证书、Argo 隧道、域名分流、Telegram 通知以及 sing-box 内核版本。
- 适配 IPv4、IPv6 和双栈 VPS，包含 Debian、Ubuntu、CentOS、Alpine 安装分支，以及 amd64、arm64、armv7 架构识别。

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

## 附带脚本

仓库还包含 [`serv00.sh`](serv00.sh) 和 [`serv00keep.sh`](serv00keep.sh)，用于 Serv00/Hostuno 环境下的部署与保活，支持 VLESS Reality、VMess WS/Argo 和 Hysteria2。这些脚本有各自的运行环境和外部资源依赖，不适用上面的 VPS 安装与更新流程。

## 许可证

本项目采用 [GNU GPL v3](LICENSE) 许可证。
