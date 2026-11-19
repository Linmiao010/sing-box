#!/usr/bin/env bash
# Run from a reviewed checkout. The installed command resolves from its private release.
set -eu
umask 077
bundle=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
if [[ ! -r "$bundle/serv00_runtime.py" ]]; then
    echo "请从完整的受审版本目录运行 serv00.sh" >&2
    exit 1
fi
if [[ $# == 0 ]]; then
    printf '%s\n' '1 安装  2 卸载  3 重启  4 更换 Argo 隧道' \
        '5 从此版本更新  6 查看分享与订阅  7 查看配置  8 更换或随机重置项目端口  9 迁移旧部署  0 退出'
    read -r -p '选择：' choice
    case "$choice" in
        1) set -- --install ;; 2) set -- --uninstall ;; 3) set -- --restart ;;
        4) set -- --argo ;; 5) set -- --update ;; 6) set -- --show ;;
        7) set -- --configs ;; 8) set -- --ports ;; 9) set -- --migrate ;;
        0) exit ;; *) exit 2 ;;
    esac
fi
exec python3 "$bundle/serv00_runtime.py" "$@"
