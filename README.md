### 一、Sing-box-yg精装桶一键五协议共存脚本（VPS专用）
### 二、Serv00/Hostuno-sb-yg多平台一键三协议共存脚本（Serv00/Hostuno专用）

### 注：本项目分享订阅节点都为本地化生成，不使用节点转换、订阅器等第三方外链引用，无需担心节点订阅被外链作者查看

### 交流平台：[甬哥博客地址](https://ygkkk.blogspot.com)、[甬哥YouTube频道](https://www.youtube.com/@ygkkk)、[甬哥TG电报群组](https://t.me/+jZHc6-A-1QQ5ZGVl)、[甬哥TG电报频道](https://t.me/+DkC9ZZUgEFQzMTZl)

----------------------------------------------------------------
#### 推荐推广：极简 + 轻量 + 快速的多协议的ArgoSBX脚本，请移步到[ArgoSBX脚本项目](https://github.com/yonggekkk/argosbx)

--------------------------------------------------------------

### 一、Sing-box-yg精装桶小白专享一键五协议共存脚本（VPS专用）

* 支持人气最高的五大协议：Vless-reality-vision、Vmess-ws(tls)/Argo、Hysteria-2、Tuic-v5、Anytls

* 支持纯IPV6、纯IPV4、双栈VPS，支持amd与arm架构，支持alpine系统，推荐使用最新的Ubuntu系统

* 小白简单模式：无需域名证书，回车三次就安装完成，复制、扫描你要的节点配置

#### 相关说明及注意点请查看[甬哥博客说明与Sing-box视频教程](https://ygkkk.blogspot.com/2023/10/sing-box-yg.html)

#### 视频教程：
[SSH连不上？使用VPS内置SSH，配合一键功能化脚本命令，小白也能快速搭节点！（Racknerd等所有VPS通用）](https://youtu.be/vhqPG9h8PB4)

[Racknerd VPS：小白自建最强翻墙代理协议组合方案；高速、稳定、无视IP被封；解决Google gemini无法使用问题](https://youtu.be/aGEmCu503V8)

[🥇搭建代理9大问题排行榜：第4名全网99%的人被误导！第1名每个人都被折腾到爆！](https://youtu.be/pJwJBqBkcfw)

[🥇2025年度代理协议"拉到夯"综合排名](https://youtu.be/IoFtykGXDao)

[Sing-box精装桶小白一键脚本（一）：配置文件通吃SFA/SFI/SFW三平台客户端，Argo隧道、双证书切换、域名分流](https://youtu.be/QwTapeVPeB0)

[Sing-box精装桶小白一键脚本（二）：纯IPV6 VPS搭建，CDN优选IP设置汇总，全平台多种客户端一个脚本全套带走](https://youtu.be/kmTgj1DundU)

[Sing-box精装桶小白一键脚本（三）：自建gitlab私有订阅链接一键同步推送全平台，WARP分流ChatGPT，SFW电脑客户端支持订阅链接](https://youtu.be/by7C2HU6-fU)

[Sing-box精装桶小白一键脚本（四）：vmess协议CDN优选IP多形态设置(详见说明图)](https://youtu.be/Qfm8DbLeb6w)

[Sing-box精装桶小白一键脚本（五）：集成oblivion warp免费vpn功能，本地WARP+赛风VPN切换分流(30个国家IP)](https://youtu.be/5Y6NPsYPws0)

[Sing-box精装桶五合一脚本重磅更新（六）：新增AnyTLS协议；本地IP订阅自动同步更新，通吃Clash/Mihomo、Sing-box与聚合节点](https://youtu.be/LF0-n6-Z6kI)

### VPS专用一键脚本如下：快捷方式：```sb```

```
bash <(wget -qO- https://raw.githubusercontent.com/Linmiao010/sing-box/main/sb.sh)
```
或者
```
bash <(curl -fsSL https://raw.githubusercontent.com/Linmiao010/sing-box/main/sb.sh)
```

首次安装、`sb` 快捷命令的菜单 7 升级、版本检查和重新安装均使用本仓库 `Linmiao010/sing-box` 的 `main` 分支。

一键创建本地 IP 订阅（将示例令牌换成自己的字母数字密码）：```printf '3\n8\n1\nmy_subscription_token_123\n\n' | sb```

一键快捷命令现实Argo临时隧道：```printf '3\n3\n1\n1' | sb```

一键快捷命令现实Argo固定隧道：```printf '3\n3\n2\n1\n固定密钥\n固定域名' | sb```

一键快捷命令现实域名分流：```printf '5\n2\n1\n后缀域名1 后缀域名2' | sb```

### 自定义域名 HTTPS 订阅（VPS）

运行 `sb`，进入 **3 → 8** 管理订阅入口。域名订阅通过 Caddy 提供 HTTPS，订阅后台仅监听 `127.0.0.1`；节点协议及其端口沿用原配置。

#### 安装与更新来源

上面的 curl/wget 一键安装命令、`sb` 快捷命令及菜单 **7** 升级使用同一份 `Linmiao010/sing-box/main/sb.sh`。本地修改必须先推送到本仓库 `main`，远程安装或升级命令才能取得新版本。

要在已安装的 VPS 上先验证未推送的改动，可将当前仓库的同一份 `sb.sh` 上传到 VPS，在该文件所在目录以 root 执行：

```bash
bash -n ./sb.sh && install -m 755 ./sb.sh /usr/bin/sb && sb
```

这会把当前文件安装为同一个 `sb` 快捷命令。之后菜单 **7** 仍从本仓库 `main` 下载；本地改动推送前执行升级，会被远程版本覆盖。

#### 启用域名订阅

1. 准备独立的订阅域名，例如 `sub.example.com`。将其 A/AAAA 记录指向当前 VPS；初次配置使用 DNS-only（仅 DNS 解析），并确保填写的 IPv4/IPv6 地址可达。
2. 放行 VPS 防火墙与云平台安全组的 **TCP 80、443**。使用独立订阅后台端口，无需更改节点端口。
3. 在 `sb` 的 **3 → 8 → 5** 中输入域名，仅填 `sub.example.com`，不带 `https://`、端口或路径。
4. 输入至少 **16 个字符**的订阅令牌，只能使用字母、数字、`_`、`-`。直接回车会通过 `openssl rand -hex 24` 生成令牌，即 24 个随机字节编码为 48 个十六进制字符。
5. 输入一个未占用的本机后台端口，或回车随机选择。脚本配置入口并检查 HTTPS；成功后显示订阅链接。

三个订阅地址分别为：

```text
https://sub.example.com/<token>/sbox.json
https://sub.example.com/<token>/clmi.yaml
https://sub.example.com/<token>/jhsub.txt
```

`sbox.json` 用于 Sing-box 客户端，`clmi.yaml` 用于 Clash/Mihomo，`jhsub.txt` 为聚合节点。将 `<token>` 换成设置的令牌，或直接复制脚本显示的完整地址。持有链接即可下载订阅内容，请按凭据保管；泄露后通过菜单 **3 → 8 → 2** 轮换令牌，并更新客户端中的链接。

#### 日常操作

| 菜单 | 操作 |
| --- | --- |
| `3 → 8 → 5` | 启用域名订阅或更换域名 |
| `3 → 8 → 2` | 轮换订阅令牌，回车随机生成 |
| `3 → 8 → 3` | 更换订阅后台端口，自动更新 Caddy 转发配置 |
| `3 → 8 → 6` | 显示当前订阅链接及二维码 |
| `3 → 8 → 4` | 停用并卸载订阅入口，保留 GitLab 配置 |
| 主菜单 `9` | 刷新节点和订阅文件，显示当前域名订阅链接 |

刷新时保留现有文件格式，并逐个文件原子替换；生成或校验失败会保留原订阅文件。客户端需要手动刷新或按自身设置定时刷新订阅，服务端更新文件不会主动推送到客户端。

域名模式下，主菜单 **9** 的刷新操作跳过自动 GitLab 推送；原来的 **3 → 7** GitLab 设置入口仍保留。TG 通知中的订阅链接也优先使用域名地址，已有通知脚本会在下次发送前迁移，保留其他通知内容和机器人凭据。

停用订阅不会自动开放公网 HTTP。确需使用原 IP 订阅时，先选择 **3 → 8 → 4** 停用域名订阅，再选择 **3 → 8 → 1** 启用 HTTP 模式。

#### 与现有网站共用 VPS

脚本自行管理的 Caddy 仅启用 HTTP/1.1、HTTP/2，避免占用 UDP 443。若系统已有 `/etc/caddy/Caddyfile` 且能运行 `caddy` 命令，脚本会加入一条带 `# s-box subscription` 标记的独立订阅配置 `import`，共享现有 Caddy。配置校验后先尝试热加载；热加载失败（例如已有配置设置了 `admin off`）时，可能重启现有 Caddy 服务，现有网站连接可能短暂中断。

若 TCP 80/443 被 Nginx、非标准安装的 Caddy 或其他入口占用，且脚本无法接入标准 Caddy 配置，脚本会报告冲突。需要在现有站点服务中自行接入订阅路由，或先调整入口配置；脚本不会停止这些服务来抢占端口。

#### 本地验证范围

在仓库目录运行离线行为测试：

```bash
python -m unittest discover -s tests -v
```

测试需要 Python 3、Bash、jq 和 Caddy；Windows 可使用 Git Bash。额外的 YAML 解析检查可使用 PyYAML，它仅用于测试。测试抽取相关 Bash 函数，在临时目录中检查安装、订阅生成、失败处理和通知内容，TG 请求由测试替身拦截。真实 Caddy 的 localhost 路由检查也只验证本机访问行为；这些结果不代表真实 VPS 的 systemd/OpenRC 服务、域名 DNS、公网 HTTPS 证书签发或客户端联网更新已经验证通过。


### Sing-box-yg脚本界面预览图（注：相关参数随意填写，仅供围观）

![1d5425c093618313888fe41a55f493f](https://github.com/user-attachments/assets/2b4b04a6-2de4-499a-afa1-ed78bccc50a8)

-----------------------------------------------------

### 二、Serv00/Hostuno一键三协议共存脚本（Serv00/Hostuno专用）：

* 目前免费Serv00使用代理脚本有被封账号的风险，收费版Hostuno不受影响，可正常使用

* 切勿与其他Serv00脚本混用！！！

* 引用[老王eooce](https://github.com/eooce/Sing-box/blob/test/sb_00.sh)、[frankiejun](https://github.com/frankiejun/serv00-play/blob/main/start.sh)相关功能，支持一键三协议：vless-reality、vmess-ws(argo)、hysteria2

* 主要增加reality协议默认支持 CF vless/trojan 节点的proxyip以及非标端口的优选反代IP功能

* 聚合通用节点分享，支持到22个节点：三协议各自三个IP，argo全覆盖13个端口节点，已添加不死优选IP

#### 相关说明及注意点请查看[甬哥博客说明与Serv00视频教程](https://ygkkk.blogspot.com/2025/01/serv00.html)

#### 视频教程：

[Serv00免费代理脚本最终教程（一）：独家支持三个IP自定义安装，支持Proxyip+反代IP、支持Argo临时/固定隧道+CDN回源；支持五个节点的Sing-box与Clash订阅配置输出](https://youtu.be/2VF9D6z2z7w)

[Serv00免费代理脚本最终教程（二）：Serv00不必再登录SSH了，部署保活融为一体，独家支持Github、VPS、软路由多平台多账户通用部署，四大方案总有一款适合你](https://youtu.be/rYeX1iU_iZ0)

[Serv00免费代理脚本最终教程（三）：多功能网页生成【保活+重启+重置端口+查看订阅节点】、随意重置端口功能；Github+Workers自动执行保活功能任你选！](https://youtu.be/9uCfFNnjNc0)

[Serv00免费代理脚本最终教程（四）：重大更新！支持Argo临时/固定隧道相互切换，实时更新节点信息；完美适配Serv00收费版Hostuno.com](https://youtu.be/XN6_vpz1NhE)

[Serv00免费代理脚本最终教程（五）：Github、VPS、软路由多平台脚本大更新！支持多功能网页，Cron内射保活+网页外射保活，任你选](https://youtu.be/tKaBdbU4G4s)

### Serv00/Hostuno-sb-yg一键脚本 

* Argo高度自定义：可以重置临时隧道; 可以继续使用上回的固定隧道; 也可以更换固定隧道的域名或token

```
bash <(curl -Ls https://raw.githubusercontent.com/yonggekkk/sing-box-yg/main/serv00.sh)
```

#### Serv00/Hostuno-sb-yg脚本界面预览图，仅限方案一的SSH端安装脚本（注：仅供围观）
![a6b776a094566ab14e88fdcd70ba9e9](https://github.com/user-attachments/assets/90a918ed-aec7-4a1f-8159-97f3acfd0092)


-----------------------------------------------------
### 感谢支持！微信打赏甬哥侃侃侃ygkkk
![41440820a366deeb8109db5610313a1](https://github.com/user-attachments/assets/5cd2d891-ae54-4397-8211-ac4c6d1099c9)

---------------------------------------
### 感谢你右上角的star🌟
[![Stargazers over time](https://starchart.cc/yonggekkk/sing-box-yg.svg)](https://starchart.cc/yonggekkk/sing-box-yg)

---------------------------------------
#### 声明：所有代码来源于Github社区与ChatGPT的整合
