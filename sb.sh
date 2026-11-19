#!/bin/bash
SB_REPO="Linmiao010/sing-box"
SB_BRANCH="main"
SB_RAW_BASE="https://raw.githubusercontent.com/${SB_REPO}/${SB_BRANCH}"
SB_RELEASE_DIR=$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")
if [[ "${1:-}" == --firewall-sync ]]; then exec python3 "$SB_RELEASE_DIR/tools/firewall.py" sync; fi
export LANG=en_US.UTF-8
red='\033[0;31m'
green='\033[0;32m'
yellow='\033[0;33m'
blue='\033[0;36m'
bblue='\033[0;34m'
plain='\033[0m'
red(){ echo -e "\033[31m\033[01m$1\033[0m";}
green(){ echo -e "\033[32m\033[01m$1\033[0m";}
yellow(){ echo -e "\033[33m\033[01m$1\033[0m";}
blue(){ echo -e "\033[36m\033[01m$1\033[0m";}
white(){ echo -e "\033[37m\033[01m$1\033[0m";}
readp(){
local prompt
# Mark color sequences as nonprinting so Readline keeps the cursor aligned.
prompt=$(printf '\001\033[33m\033[01m\002%b\001\033[0m\002' "$1")
read -e -r -p "$prompt" "$2"
}
[[ $EUID -ne 0 ]] && yellow "请以root模式运行脚本" && exit 1
umask 077
unset config_lock_held
if [[ -d /etc/s-box ]]; then
    chmod 700 /etc/s-box || exit 1
    find /etc/s-box -maxdepth 1 -type f -exec chmod go-rwx {} + || exit 1
fi
#[[ -e /etc/hosts ]] && grep -qE '^ *172.65.251.78 gitlab.com' /etc/hosts || echo -e '\n172.65.251.78 gitlab.com' >> /etc/hosts
if [[ -f /etc/redhat-release ]]; then
release="Centos"
elif cat /etc/issue | grep -q -E -i "alpine"; then
release="alpine"
elif cat /etc/issue | grep -q -E -i "debian"; then
release="Debian"
elif cat /etc/issue | grep -q -E -i "ubuntu"; then
release="Ubuntu"
elif cat /etc/issue | grep -q -E -i "centos|red hat|redhat"; then
release="Centos"
elif cat /proc/version | grep -q -E -i "debian"; then
release="Debian"
elif cat /proc/version | grep -q -E -i "ubuntu"; then
release="Ubuntu"
elif cat /proc/version | grep -q -E -i "centos|red hat|redhat"; then
release="Centos"
else 
red "脚本不支持当前的系统，请选择使用Ubuntu,Debian,Centos系统。" && exit
fi
export sbnh=$(/etc/s-box/sing-box version 2>/dev/null | awk '/version/{print $NF}' 2>/dev/null | cut -d '.' -f 1,2)
vsid=$(grep -i version_id /etc/os-release | cut -d \" -f2 | cut -d . -f1)
op=$(cat /etc/redhat-release 2>/dev/null || cat /etc/os-release 2>/dev/null | grep -i pretty_name | cut -d \" -f2)
#if [[ $(echo "$op" | grep -i -E "arch|alpine") ]]; then
if [[ $(echo "$op" | grep -i -E "arch") ]]; then
red "脚本不支持当前的 $op 系统，请选择使用Ubuntu,Debian,Centos系统。" && exit
fi
version=$(uname -r | cut -d "-" -f1)
[[ -z $(systemd-detect-virt 2>/dev/null) ]] && vi=$(virt-what 2>/dev/null) || vi=$(systemd-detect-virt 2>/dev/null)
case $(uname -m) in
armv7l) cpu=armv7;;
aarch64) cpu=arm64;;
x86_64) cpu=amd64;;
*) red "目前脚本不支持$(uname -m)架构" && exit;;
esac
if [[ -n $(sysctl net.ipv4.tcp_congestion_control 2>/dev/null | awk -F ' ' '{print $3}') ]]; then
bbr=`sysctl net.ipv4.tcp_congestion_control | awk -F ' ' '{print $3}'`
else
bbr="未知（本机未提供）"
fi
hostname=$(hostname)

# Called only by installation or an explicit repair, never menu display.
ensure_dependencies(){
local dependency missing=0 manager
local -a required=(curl jq openssl ip ss crontab socat busybox tar xxd python3 qrencode git sysctl sha256sum flock pgrep)
for dependency in "${required[@]}"; do
command -v "$dependency" >/dev/null 2>&1 || missing=1
done
(( missing )) || return 0
green "安装/修复sing-box脚本必要的依赖……"
if command -v apk >/dev/null 2>&1; then
apk add bash libc6-compat curl jq openssl procps busybox busybox-extras iproute2 iputils coreutils git socat grep tar tzdata util-linux xxd python3 libqrencode-tools virt-what || return 1
elif command -v apt-get >/dev/null 2>&1; then
apt-get update && apt-get install -y curl jq cron socat busybox coreutils util-linux openssl tar xxd python3 qrencode git iproute2 iputils-ping procps || return 1
elif command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1; then
command -v dnf >/dev/null 2>&1 && manager=dnf || manager=yum
"$manager" install -y epel-release && "$manager" install -y curl jq cronie socat busybox coreutils util-linux openssl tar vim-common python3 qrencode git iproute iputils procps-ng || return 1
else
red "未找到受支持的包管理器，请手动安装缺失依赖"
return 1
fi
for dependency in "${required[@]}"; do
command -v "$dependency" >/dev/null 2>&1 || { red "依赖仍缺失：$dependency"; return 1; }
done
return 0
}

setup_tun(){
[[ $vi == openvz ]] || return 0
local tun_result
tun_result=$(LC_ALL=C cat /dev/net/tun 2>&1)
[[ $tun_result == *"File descriptor in bad state"* ]] && return 0
red "检测到未开启TUN，现尝试添加TUN支持"
if [[ ! -e /dev/net/tun ]]; then
mkdir -p /dev/net && mknod /dev/net/tun c 10 200 && chmod 0666 /dev/net/tun || return 1
fi
tun_result=$(LC_ALL=C cat /dev/net/tun 2>&1)
if [[ $tun_result != *"File descriptor in bad state"* ]]; then
red "添加TUN支持失败，请联系VPS厂商开启"; return 1
fi
printf '%s\n' '#!/bin/bash' 'mkdir -p /dev/net; [ -c /dev/net/tun ] || { mknod /dev/net/tun c 10 200 && chmod 0666 /dev/net/tun; }' > /root/tun.sh && chmod +x /root/tun.sh || return 1
grep -qE "^ *@reboot root bash /root/tun.sh >/dev/null 2>&1" /etc/crontab || echo "@reboot root bash /root/tun.sh >/dev/null 2>&1" >> /etc/crontab || return 1
green "TUN守护功能已启动"
return 0
}
v4v6(){
v4=$(public_ip 4)
v6=$(public_ip 6)
# Interface addresses show configuration, not proof of Internet connectivity.
v6_local=$(ip -o -6 addr show scope global up 2>/dev/null | awk '
$3 == "inet6" && / scope global / && !/ (tentative|dadfailed|deprecated)( |$)/ {
    split($4, address, "/"); print address[1]; exit
}')
if [[ -n $v6 ]]; then
v6_display="$v6"
elif [[ -n $v6_local ]]; then
v6_display="$v6_local（本机已配置；公网探测未成功）"
else
v6_display="未检测到全局IPv6地址；公网探测未成功"
fi
v4dq= v6dq=
[[ -z $v4 ]] || v4dq=$(curl -fsS -4 --noproxy '*' --connect-timeout 2 --max-time 5 https://ip.fm 2>/dev/null | sed -n 's/.*Location: //p')
[[ -z $v6 ]] || v6dq=$(curl -fsS -6 --noproxy '*' --connect-timeout 2 --max-time 5 https://ip.fm 2>/dev/null | sed -n 's/.*Location: //p')
return 0
}

ip_literal_valid(){
local family=$1 address=$2 part compressed=0
local -a parts
if [[ $family == 4 ]]; then
[[ $address =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || return 1
IFS=. read -r -a parts <<< "$address"
for part in "${parts[@]}"; do
(( 10#$part <= 255 )) || return 1
done
else
[[ $address =~ ^[0-9a-fA-F:]+$ && $address != *:::* ]] || return 1
[[ $address != :* || $address == ::* ]] || return 1
[[ $address != *: || $address == *:: ]] || return 1
if [[ $address == *::* ]]; then
compressed=1
address=${address/::/:}
[[ $address != *::* ]] || return 1
address=${address#:}
address=${address%:}
else
[[ $address != :* && $address != *: ]] || return 1
fi
IFS=: read -r -a parts <<< "$address"
for part in "${parts[@]}"; do
[[ $part =~ ^[0-9a-fA-F]{1,4}$ ]] || return 1
done
(( (compressed && ${#parts[@]} < 8) || (!compressed && ${#parts[@]} == 8) )) || return 1
fi
}

read_node_addresses(){
node_ipv4= node_ipv6=
[[ -e /etc/s-box/node-addresses ]] || return 0
if ! { IFS= read -r node_ipv4 && IFS= read -r node_ipv6; } < /etc/s-box/node-addresses ||
   { [[ -n $node_ipv4 ]] && ! ip_literal_valid 4 "$node_ipv4"; } ||
   { [[ -n $node_ipv6 ]] && ! ip_literal_valid 6 "$node_ipv6"; }; then
    red "节点地址记录无效，已保留原配置"; return 1
fi
}

# Called under the configuration lock, only with WARP-off, unbound node observations.
save_node_addresses(){
local node_ipv4 node_ipv6 candidate
[[ -n $1 || -n $2 ]] || return 0
read_node_addresses || return 1
[[ ${1:-$node_ipv4} != "$node_ipv4" || ${2:-$node_ipv6} != "$node_ipv6" ]] || return 0
candidate=$(mktemp /etc/s-box/.node-addresses.XXXXXX) || return 1
if ! printf '%s\n' "${1:-$node_ipv4}" "${2:-$node_ipv6}" > "$candidate" ||
   ! mv -f -- "$candidate" /etc/s-box/node-addresses; then
    rm -f -- "$candidate"; return 1
fi
}

# Admit a newly observed node address only after the running profile blocks it.
sync_node_addresses(){
local node_ipv4 node_ipv6
[[ -n $1 || -n $2 ]] || return 0
save_node_addresses "$@" || return 1
[[ -s /etc/s-box/sb.json ]] || return 0
read_node_addresses || return 1
if python3 - /etc/s-box/sb.json "$node_ipv4" "$node_ipv6" <<'PY'
import ipaddress, json, sys
with open(sys.argv[1], encoding="utf-8") as source:
    rules = json.load(source)["route"]["rules"]
addresses = [ipaddress.ip_address(value) for value in sys.argv[2:] if value]
for rule in rules:
    cidrs = rule.get("ip_cidr", [])
    if "100.64.0.0/10" in cidrs and "168.63.129.16/32" in cidrs and (
        rule == {"ip_cidr": cidrs, "action": "reject"} or rule == {"ip_cidr": cidrs, "outbound": "block"}
    ):
        networks = [ipaddress.ip_network(cidr, strict=False) for cidr in cidrs]
        if all(any(address in network for network in networks) for address in addresses):
            sys.exit(0)
        break
    if rule.get("action") not in ("sniff", "resolve"):
        break
sys.exit(1)
PY
then return 0; fi
config_apply secure_server_routes
}

public_ip(){
local family=$1 url address source=${4:-}
local -a urls=(https://icanhazip.com https://api64.ipify.org)
local -a binding=()
if [[ -n $source ]]; then
ip_literal_valid "$family" "$source" || return 1
binding=(--interface "$source")
fi
[[ -z ${3:-} ]] || urls=("$3")
for url in "${urls[@]}"; do
if address=$(curl -q -fsS "-$family" --noproxy '*' --retry 0 --connect-timeout "${2:-2}" --max-time "${2:-5}" "${binding[@]}" "$url" 2>/dev/null); then
address=${address%$'\r'}
if ip_literal_valid "$family" "$address"; then
printf '%s\n' "$address"
return 0
fi
fi
done
return 1
}

egress_read(){
local values
egress_ipv4= egress_ipv6=
[[ -e /etc/s-box/egress.json ]] || return 0
values=$(jq -er '
  if type == "object" and (.ipv4_source | type) == "string" and (.ipv6_source | type) == "string"
    and (.ipv4_source | test("^[0-9.]*$")) and (.ipv6_source | test("^[0-9a-fA-F:]*$"))
  then [.ipv4_source, .ipv6_source] | map(if . == "" then "-" else . end) | join(" ")
  else error("invalid egress settings") end
' /etc/s-box/egress.json 2>/dev/null) || return 1
values=${values%$'\r'}
read -r egress_ipv4 egress_ipv6 <<< "$values"
[[ $egress_ipv4 != - ]] || egress_ipv4=
[[ $egress_ipv6 != - ]] || egress_ipv6=
[[ -z $egress_ipv4 ]] || ip_literal_valid 4 "$egress_ipv4" || return 1
[[ -z $egress_ipv6 ]] || ip_literal_valid 6 "$egress_ipv6" || return 1
}

egress_public_ip(){
local egress_ipv4 egress_ipv6 source
egress_read || return 1
case "$1" in
4) source=$egress_ipv4 ;;
6) source=$egress_ipv6 ;;
*) return 1 ;;
esac
public_ip "$1" "${2:-}" "${3:-}" "$source"
}

egress_ip_key(){
# Compare equivalent compressed/expanded IPv6 literals without another dependency.
local family=$1 address=$2 part missing
local -a left=() right=()
ip_literal_valid "$family" "$address" || return 1
if [[ $family == 4 ]]; then
IFS=. read -r -a left <<< "$address"
for part in "${left[@]}"; do printf '%d.' "$((10#$part))"; done
else
if [[ $address == *::* ]]; then
IFS=: read -r -a left <<< "${address%%::*}"
IFS=: read -r -a right <<< "${address#*::}"
missing=$((8 - ${#left[@]} - ${#right[@]}))
while (( missing-- > 0 )); do left+=(0); done
left+=("${right[@]}")
else
IFS=: read -r -a left <<< "$address"
fi
for part in "${left[@]}"; do printf '%04x' "$((16#${part:-0}))"; done
fi
}

egress_addresses(){
local family=$1 addresses links
[[ $family == 4 || $family == 6 ]] || return 1
links=$(ip -o link show up 2>/dev/null) || return 1
addresses=$(ip -o "-$family" addr show 2>/dev/null) || return 1
printf '%s\n' "$addresses" | awk -v links="$links" -v family="$family" '
BEGIN {
  OFS="\t"; split(links, rows, "\n")
  for (i in rows) { split(rows[i], fields, " "); name=fields[2]; sub(/:$/, "", name); sub(/@.*/, "", name); up[name]=1 }
}
$3 == (family == 6 ? "inet6" : "inet") {
  iface=$2; sub(/@.*/, "", iface); split($4, cidr, "/")
  scope="-"; valid="-"; preferred="-"
  for (i=5; i<=NF; i++) {
    if ($i == "scope") scope=$(i+1)
    if ($i == "valid_lft") valid=$(i+1)
    if ($i == "preferred_lft") preferred=$(i+1)
  }
  row=" " $0 " "; state="ready"
  if (scope != "global") state="not_global"
  else if (!up[iface]) state="down"
  else if (row ~ / tentative /) state="tentative"
  else if (row ~ / dadfailed /) state="dadfailed"
  else if (row ~ / deprecated / || preferred == "0sec") state="deprecated"
  print cidr[1],iface,scope,state,(row ~ / temporary / ? "true" : "false"),valid,preferred,(row ~ / dynamic / ? "true" : "false")
}' | jq -Rn '[inputs | select(length > 0) | split("\t") |
  {address:.[0],interface:.[1],scope:.[2],state:.[3],temporary:(.[4] == "true"),
   valid_lft:.[5],preferred_lft:.[6],dynamic:(.[7] == "true")} ]'
}

egress_candidate_usable(){
local family=$1 source=$2 addresses address key
key=$(egress_ip_key "$family" "$source") || return 1
addresses=$(egress_addresses "$family") || return 1
while IFS= read -r address; do
address=${address%$'\r'}
[[ $(egress_ip_key "$family" "$address") != "$key" ]] || return 0
done < <(jq -r '.[] | select(.state == "ready") | .address' <<< "$addresses")
return 1
}

egress_probe(){
local family=$1 source=$2 url=$3 limit=${4:-5} result code=0 meta address= local_ip= remote= elapsed= status
local connect=2
local -a binding=()
[[ $family == 4 || $family == 6 ]] || return 1
if [[ -n $source ]]; then
ip_literal_valid "$family" "$source" || return 1
binding=(--interface "$source")
fi
(( limit >= 2 )) || connect=$limit
result=$(curl -q -fsS "-$family" --noproxy '*' --retry 0 --connect-timeout "$connect" --max-time "$limit" \
  "${binding[@]}" --write-out $'\n%{local_ip}\t%{remote_ip}\t%{time_total}\n' "$url" 2>/dev/null) || code=$?
if [[ $result == *$'\n'* ]]; then
meta=${result##*$'\n'}; address=${result%$'\n'*}
# IP echo services may terminate the body with LF or CRLF before curl metadata.
while [[ $address == *$'\n' || $address == *$'\r' ]]; do address=${address%?}; done
local_ip=${meta%%$'\t'*}; meta=${meta#*$'\t'}
remote=${meta%%$'\t'*}; elapsed=${meta#*$'\t'}; elapsed=${elapsed%$'\r'}
fi
case "$code" in
0) status=ok ;;
6) status=dns ;;
45) status=bind ;;
28) status=timeout ;;
7) status=connect ;;
22) status=http ;;
35|51|58|60|77|83|90|91) status=tls ;;
*) status=error ;;
esac
if [[ $status == ok ]]; then
if ! ip_literal_valid "$family" "$address" || ! ip_literal_valid "$family" "$local_ip"; then
status=invalid_response
elif [[ -n $source && $(egress_ip_key "$family" "$source") != "$(egress_ip_key "$family" "$local_ip")" ]]; then
status=source_mismatch
fi
fi
[[ $status == ok ]] || address=
jq -cn --arg status "$status" --arg local "$local_ip" --arg public "$address" --arg remote "$remote" \
  --arg seconds "$elapsed" '{status:$status,local:$local,public:$public,remote:$remote,seconds:$seconds}' || return 1
[[ $status == ok ]]
}

egress_scan(){
(
local family=$1 budget=${2:-30} work addresses index source state url remaining limit started=$SECONDS serial=0 pid
local -a pids=()
[[ $family == 4 || $family == 6 ]] && [[ $budget =~ ^[1-9][0-9]*$ ]] || exit 1
work=$(mktemp -d) || exit 1
trap 'for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || :; done; rm -rf -- "$work"' EXIT
trap 'exit 1' HUP INT TERM
addresses=$(egress_addresses "$family") || exit 1
jq '[{address:"",interface:"系统自动",state:"ready",temporary:false}] + .' <<< "$addresses" > "$work/rows.json" || exit 1
: > "$work/result-empty.json"
while IFS=$'\t' read -r index source state; do
state=${state%$'\r'}
[[ $state == ready ]] || continue
[[ $source != - ]] || source=
for url in https://icanhazip.com https://api64.ipify.org; do
if (( ${#pids[@]} >= 4 )); then
for pid in "${pids[@]}"; do wait "$pid" || :; done
pids=()
fi
remaining=$((budget - SECONDS + started))
(( remaining > 0 )) || break 2
limit=5; (( remaining >= limit )) || limit=$remaining
serial=$((serial + 1))
(
result=$(egress_probe "$family" "$source" "$url" "$limit") || :
[[ -n $result ]] || result='{"status":"error"}'
jq --argjson row "$index" --arg endpoint "$url" '. + {row:$row,endpoint:$endpoint}' <<< "$result"
) > "$work/result-$serial.json" &
pids+=("$!")
done
done < <(jq -r 'to_entries[] | [.key, (if .value.address == "" then "-" else .value.address end), .value.state] | @tsv' "$work/rows.json")
for pid in "${pids[@]}"; do wait "$pid" || :; done
pids=()
jq -s --slurpfile rows "$work/rows.json" '. as $results | $rows[0] | to_entries |
  map(.key as $id | .value + {results:[$results[] | select(.row == $id) | del(.row)]})' "$work"/result-*.json
)
}

menu_ip_status(){
local family address url source detected_v4= detected_v6=
local egress_ipv4 egress_ipv6
v4= v6=
if ! egress_read; then
vps_ipv4="出站源地址设置无效"; vps_ipv6=$vps_ipv4
showv4=$vps_ipv4 showv6=$vps_ipv6
return 1
fi
# Race both HTTPS sources for both families, with no sequential fallback wait.
# Each request has 2.6s, leaving time for process/validation overhead within 3s.
while read -r family address; do
case "$family" in
4) [[ -n $detected_v4 ]] || detected_v4=$address ;;
6) [[ -n $detected_v6 ]] || detected_v6=$address ;;
esac
done < <(
for family in 4 6; do
if [[ $family == 4 ]]; then source=$egress_ipv4; else source=$egress_ipv6; fi
for url in https://icanhazip.com https://api64.ipify.org; do
(
address=$(public_ip "$family" 2.6 "$url" "$source") || address=
printf '%s %s\n' "$family" "$address"
) &
done
done
wait
)
vps_ipv4=${detected_v4:-公网IPv4探测未成功}
vps_ipv6=${detected_v6:-公网IPv6探测未成功}
# Only unbound observations may be reused by the separate node-address menu.
[[ -n $egress_ipv4 ]] || v4=$detected_v4
[[ -n $egress_ipv6 ]] || v6=$detected_v6
showv4=$vps_ipv4 showv6=$vps_ipv6
}

warpcheck(){
local family trace status
for family in 4 6; do
    status=unknown
    if trace=$(curl -q -fsS "-$family" --noproxy '*' --proto '=https' --connect-timeout 2 --max-time 5 \
        https://www.cloudflare.com/cdn-cgi/trace 2>/dev/null); then
        trace=$(sed -n 's/^warp=//p' <<< "$trace")
        case "$trace" in off|on|plus) status=$trace ;; esac
    fi
    printf -v "wgcfv$family" '%s' "$status"
done
}

warp_unrouted_ips(){
(
local service family status address
local -a stopped=() active=()
warp_unit(){
    local action=$1 unit=$2
    if command -v rc-service >/dev/null 2>&1; then
        if [[ $unit == wg-quick@wgcf && -e /etc/init.d/wg-quick.wgcf ]]; then unit=wg-quick.wgcf; fi
        rc-service "$unit" "$action" 8>&- 9>&-
    elif [[ $action == status ]]; then systemctl is-active --quiet "$unit"
    else systemctl "$action" "$unit"; fi
}
for service in wg-quick@wgcf warp-go; do
    if warp_unit status "$service" >/dev/null 2>&1; then active+=("$service"); fi
done
restore_warp(){
    local rc=$? unit
    trap - EXIT
    for unit in "${stopped[@]}"; do warp_unit start "$unit" >&2 || rc=1; done
    exit "$rc"
}
trap restore_warp EXIT
trap 'exit 1' HUP INT TERM
for service in "${active[@]}"; do
    stopped+=("$service")
    warp_unit stop "$service" >&2 || exit 1
done
warpcheck
for family in 4 6; do
    status=wgcfv$family
    address=
    if [[ ${!status} == off ]]; then
        if [[ $1 == node ]]; then address=$(public_ip "$family") || address=
        else address=$(egress_public_ip "$family") || address=; fi
    fi
    printf '%s\n' "${address:--}"
done
)
}

v4orv6(){
local detected_v4=$1 detected_v6=$2 local_v4 answer backup candidate
if [[ -z $detected_v4 && -z $detected_v6 ]]; then
    red "公网地址或 WARP 状态探测失败，已保留 DNS"; return 1
fi
ipv=prefer_ipv4
if [[ -z $detected_v4 ]]; then
    ipv=prefer_ipv6
    if local_v4=$(ip -o -4 addr show scope global) && [[ -z $local_v4 ]]; then
        yellow "仅检测到 IPv6。使用第三方 DNS64 会更改整机 DNS 并通过其网关访问 IPv4。"
        readp "是否替换 /etc/resolv.conf？输入 YES 确认，回车保留：" answer || answer=
        if [[ $answer == YES ]]; then
            backup=$(mktemp -d /etc/s-box/resolver-backup.XXXXXX) || return 1
            cp -Pp -- /etc/resolv.conf "$backup/original" &&
                cp -Lp -- /etc/resolv.conf "$backup/contents" || return 1
            candidate=$(mktemp /etc/.sbox-resolv.XXXXXX) || return 1
            if ! printf '%s\n' 'nameserver 2a00:1098:2b::1' 'nameserver 2a00:1098:2c::1' > "$candidate" ||
               ! chmod 644 "$candidate" || ! mv -fT -- "$candidate" /etc/resolv.conf; then
                rm -f -- "$candidate"; return 1
            fi
            green "已备份原内容与链接。恢复命令：cp -a --remove-destination '$backup/original' /etc/resolv.conf"
        fi
    fi
fi
if [[ -n $detected_v6 ]]; then endip="2606:4700:d0::a29f:c001"; else endip="162.159.192.1"; fi
}

v6(){
local addresses first second
addresses=$(warp_unrouted_ips node) || return 1
first=${addresses%%$'\n'*}; second=${addresses#*$'\n'}
v4orv6 "${first#-}" "${second#-}" && save_node_addresses "${first#-}" "${second#-}"
}

firewall_sync(){
[[ -f /etc/s-box/firewall.json ]] || return 0
python3 "$SB_RELEASE_DIR/tools/firewall.py" sync
}

firewall_hops(){
[[ -f /etc/s-box/firewall.json ]] || return 0
python3 "$SB_RELEASE_DIR/tools/firewall.py" list "$1"
}

firewall_enable(){
if [[ -f /etc/systemd/system/sing-box.service || -f /etc/init.d/sing-box ]]; then
    [[ $(singbox_unit_state) == project ]] || { red "同名服务归属未确认，已保留防火墙与启动配置"; return 1; }
fi
python3 "$SB_RELEASE_DIR/tools/firewall.py" enable "$@" || return 1
# Older installations predate the service startup hook. Leave their main unit intact.
if [[ -f /etc/systemd/system/sing-box.service ]] && ! grep -q -- --firewall-sync /etc/systemd/system/sing-box.service; then
    local dropdir=/etc/systemd/system/sing-box.service.d
    mkdir -p "$dropdir" || return 1
    [[ ! -e "$dropdir/51-sing-box-managed-firewall.conf" ]] || grep -Fxq '# sing-box:managed firewall' "$dropdir/51-sing-box-managed-firewall.conf" || return 1
    printf '%s\n' '# sing-box:managed firewall' '[Unit]' 'After=firewalld.service nftables.service ufw.service' '[Service]' \
        'ExecStartPre=/usr/bin/sing_box --firewall-sync' > "$dropdir/51-sing-box-managed-firewall.conf" &&
        systemctl daemon-reload || return 1
elif [[ -f /etc/init.d/sing-box ]] && ! grep -q -- --firewall-sync /etc/init.d/sing-box; then
    [[ ! -e /etc/init.d/sing-box-managed-firewall ]] || grep -Fxq '# sing-box:managed firewall' /etc/init.d/sing-box-managed-firewall || return 1
    cat > /etc/init.d/sing-box-managed-firewall <<'EOF'
#!/sbin/openrc-run
# sing-box:managed firewall
depend(){ after firewall nftables iptables ip6tables; before sing-box; }
start(){ /usr/bin/sing_box --firewall-sync; }
EOF
    chmod 755 /etc/init.d/sing-box-managed-firewall && rc-update add sing-box-managed-firewall default || return 1
fi
}

firewall_legacy(){
local tool
for tool in iptables ip6tables; do
command -v "$tool" >/dev/null || continue
"$tool" -t nat -S PREROUTING 2>/dev/null | awk -v family="$tool" '/ -j DNAT / && !/sing-box:managed/ {print family " 旧版或其他服务的未标记规则（请管理员核对）: " $0}'
done
}

openyn(){
local action zone hopping
local -a options=(--input-position append)
firewall_legacy
readp "回车沿用主机防火墙，输入 1 在现有过滤规则之后放行节点。请先核对来源、接口和限速规则：" action || return 1
case "$action" in
1) if command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
       firewall-cmd --get-active-zones
       readp "输入节点公网接口所属 zone（仅有一个时可回车）：" zone || return 1
       [[ -z $zone ]] || options+=(--zone "$zone")
       readp "firewalld 的 UDP 跳跃可能因 DNAT 先于来源封禁放行。已核对 StrictForwardPorts 和该 zone 的来源策略并接受此影响才输入 1，回车禁用跳跃：" hopping || return 1
       case "$hopping" in 1) options+=(--confirm-forward-ports) ;; "") ;; *) return 1 ;; esac
   fi
   firewall_enable "${options[@]}" ;;
"") return 0 ;;
*) red "选项无效"; return 1 ;;
esac
}

release_fetch(){
(
local name=$1 dst=$2 row digest asset url part checksum
row=$(awk -v name="$name" '$2 == name {row=$0; n++} END {if(n!=1) exit 1; print row}' "$SB_RELEASE_DIR/tools/releases.sha256") ||
{ red "受审发布清单没有唯一条目：$name" >&2; exit 1; }
read -r digest asset url <<< "$row"
[[ "$digest" =~ ^[0-9a-f]{64}$ ]] || exit 1
part=$(mktemp "${dst}.part.XXXXXX") || exit 1
trap 'rm -f -- "$part"' EXIT
case "$url" in
"bundle:$name") cp -- "$SB_RELEASE_DIR/$name" "$part" || exit 1 ;;
https://*) curl -q -fLsS --proto '=https' --proto-redir '=https' --connect-timeout 10 --max-time 180 --retry 1 \
  -H 'Accept: application/octet-stream' -o "$part" "$url" || exit 1 ;;
*) exit 1 ;;
esac
checksum=$(sha256sum < "$part") && [[ "${checksum%% *}" == "$digest" ]] ||
{ red "发布物 SHA-256 不符：$name" >&2; exit 1; }
mv -f -- "$part" "$dst"
)
}

release_matches(){
local expected actual
[[ -f "$2" && -x "$2" ]] || return 1
expected=$(awk -v name="$1" '$2 == name {hash=$1; n++} END {if(n!=1) exit 1; print hash}' "$SB_RELEASE_DIR/tools/releases.sha256") &&
[[ "$expected" =~ ^[0-9a-f]{64}$ ]] &&
actual=$(sha256sum < "$2") && [[ "${actual%% *}" == "$expected" ]]
}

inssb(){
local menu name work actual
yellow "1：受审正式版 1.14.3（默认）  2：1.10.7（旧版 geosite）"
readp "请选择【1-2】：" menu || return 1
case "$menu" in ""|1) sbcore=1.14.3 ;; 2) sbcore=1.10.7 ;; *) return 1 ;; esac
name=sing-box-$sbcore-linux-$cpu
work=$(mktemp -d /etc/s-box/.install-core.XXXXXX) || return 1
if release_fetch "$name.tar.gz" "$work/core.tar.gz" &&
   tar -xzf "$work/core.tar.gz" -O "$name/sing-box" > "$work/sing-box" &&
   chmod 755 "$work/sing-box" && actual=$("$work/sing-box" version) &&
   [[ $(awk '/^sing-box version /{print $3; exit}' <<< "$actual") == "$sbcore" ]] &&
   mv -f -- "$work/sing-box" /etc/s-box/sing-box; then
  rm -rf -- "$work"
  sbnh=${sbcore%.*}
  blue "已安装 Sing-box $sbcore"
else
  rm -rf -- "$work"
  red "内核未通过下载、校验或安装检查"; return 1
fi
}

inscertificate(){
local existing_domain
ymzs(){
ym_vl_re=apple.com
echo
blue "Vless-reality的SNI域名默认为 apple.com"
tlsyn=true
ym_vm_ws=$(acme_hostname) || return 1
certificatec_vmess_ws='/root/ygkkkca/cert.crt'
certificatep_vmess_ws='/root/ygkkkca/private.key'
certificatec_hy2='/root/ygkkkca/cert.crt'
certificatep_hy2='/root/ygkkkca/private.key'
certificatec_tuic='/root/ygkkkca/cert.crt'
certificatep_tuic='/root/ygkkkca/private.key'
certificatec_an='/root/ygkkkca/cert.crt'
certificatep_an='/root/ygkkkca/private.key'
}

zqzs(){
ym_vl_re=apple.com
echo
blue "Vless-reality的SNI域名默认为 apple.com"
tlsyn=true
ym_vm_ws=www.bing.com
certificatec_vmess_ws='/etc/s-box/cert.pem'
certificatep_vmess_ws='/etc/s-box/private.key'
certificatec_hy2='/etc/s-box/cert.pem'
certificatep_hy2='/etc/s-box/private.key'
certificatec_tuic='/etc/s-box/cert.pem'
certificatep_tuic='/etc/s-box/private.key'
certificatec_an='/etc/s-box/cert.pem'
certificatep_an='/etc/s-box/private.key'
}

red "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
green "二、生成并设置相关证书"
echo
blue "自动生成bing自签证书中……" && sleep 2
if [[ ! -e /etc/s-box/cert.pem && ! -e /etc/s-box/private.key ]]; then
(umask 077; openssl ecparam -genkey -name prime256v1 -out /etc/s-box/private.key &&
 openssl req -new -x509 -days 3650 -key /etc/s-box/private.key -out /etc/s-box/cert.pem \
   -subj "/CN=www.bing.com" -addext "subjectAltName=DNS:www.bing.com" -addext "basicConstraints=CA:FALSE") || return 1
fi
echo
if [[ ! -s /etc/s-box/cert.pem || ! -s /etc/s-box/private.key ]]; then
    red "自签证书或私钥缺失，请恢复证书对后重试"; return 1
fi
echo
if [[ -f /root/ygkkkca/cert.crt && -f /root/ygkkkca/private.key && -s /root/ygkkkca/cert.crt && -s /root/ygkkkca/private.key ]]; then
existing_domain=$(acme_hostname) || return 1
yellow "检测到 ACME 域名证书：$existing_domain"
green "是否使用 $existing_domain 域名IP证书？"
yellow "1：否！使用自签的证书 (回车默认)"
yellow "2：是！使用 $existing_domain 域名IP证书"
readp "请选择【1-2】：" menu
if [ -z "$menu" ] || [ "$menu" = "1" ] ; then
zqzs
else
ymzs || return 1
fi
else
green "是否申请一个Acme域名IP证书？"
yellow "1：否！继续使用自签的证书 (回车默认)"
yellow "2：是！使用ACME 脚本申请Acme证书 (支持80端口域名IP证书模式与Dns API域名模式)"
readp "请选择【1-2】：" menu
if [ -z "$menu" ] || [ "$menu" = "1" ] ; then
zqzs
else
acme || return 1
if [[ ! -s /root/ygkkkca/cert.crt || ! -s /root/ygkkkca/private.key ]]; then
red "Acme证书申请失败，继续使用自签证书" 
zqzs
else
ymzs || return 1
fi
fi
fi
readp "VMess 模式：1 公网 WSS 直连（默认），2 仅回环 WS 源站供 Argo 连接：" menu || return 1
case "$menu" in ""|1) tlsyn=true ;; 2) tlsyn=false ;; *) return 1 ;; esac
}

chooseport(){
if [[ -z $port ]]; then
port=$(shuf -i 10000-65535 -n 1)
until [[ -z $(ss -tunlp | grep -w udp | awk '{print $5}' | sed 's/.*://g' | grep -w "$port") && -z $(ss -tunlp | grep -w tcp | awk '{print $5}' | sed 's/.*://g' | grep -w "$port") ]] 
do
[[ -n $(ss -tunlp | grep -w udp | awk '{print $5}' | sed 's/.*://g' | grep -w "$port") || -n $(ss -tunlp | grep -w tcp | awk '{print $5}' | sed 's/.*://g' | grep -w "$port") ]] && yellow "\n端口被占用，请重新输入端口" && readp "自定义端口:" port
done
else
until [[ -z $(ss -tunlp | grep -w udp | awk '{print $5}' | sed 's/.*://g' | grep -w "$port") && -z $(ss -tunlp | grep -w tcp | awk '{print $5}' | sed 's/.*://g' | grep -w "$port") ]]
do
[[ -n $(ss -tunlp | grep -w udp | awk '{print $5}' | sed 's/.*://g' | grep -w "$port") || -n $(ss -tunlp | grep -w tcp | awk '{print $5}' | sed 's/.*://g' | grep -w "$port") ]] && yellow "\n端口被占用，请重新输入端口" && readp "自定义端口:" port
done
fi
blue "确认的端口：$port" && sleep 2
}

vlport(){
readp "\n设置Vless-reality端口 (回车跳过为10000-65535之间的随机端口)：" port
chooseport
port_vl_re=$port
}
vmport(){
readp "\n设置Vmess-ws端口 (回车跳过为10000-65535之间的随机端口)：" port
chooseport
port_vm_ws=$port
}
hy2port(){
readp "\n设置Hysteria2主端口 (回车跳过为10000-65535之间的随机端口)：" port
chooseport
port_hy2=$port
}
tu5port(){
readp "\n设置Tuic5主端口 (回车跳过为10000-65535之间的随机端口)：" port
chooseport
port_tu=$port
}
anport(){
readp "\n设置Anytls主端口，最新内核时可用 (回车跳过为10000-65535之间的随机端口)：" port
chooseport
port_an=$port
}

insport(){
red "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
green "三、设置各个协议端口"
yellow "1：自动选择协议端口（默认）。选定后仅在云安全组放行所需公网端口"
yellow "2：自定义每个协议端口。请确保VPS后台已开放指定的端口"
readp "请输入【1-2】：" port
if [ -z "$port" ] || [ "$port" = "1" ] ; then
ports=()
for i in {1..7}; do
while true; do
port=$(shuf -i 10000-65535 -n 1)
if ! [[ " ${ports[@]} " =~ " $port " ]] && \
[[ -z $(ss -tunlp | grep -w tcp | awk '{print $5}' | sed 's/.*://g' | grep -w "$port") ]] && \
[[ -z $(ss -tunlp | grep -w udp | awk '{print $5}' | sed 's/.*://g' | grep -w "$port") ]]; then
ports+=($port)
break
fi
done
done
port_vm_ws=${ports[0]}
port_vl_re=${ports[1]}
port_hy2=${ports[2]}
port_tu=${ports[3]}
port_an=${ports[4]}
port_tr=${ports[5]}
port_sn=${ports[6]}
if [[ $tlsyn == "true" ]]; then
numbers=("2053" "2083" "2087" "2096" "8443")
else
numbers=("8080" "8880" "2052" "2082" "2086" "2095")
fi
port_vm_ws=${numbers[$RANDOM % ${#numbers[@]}]}
until [[ -z $(ss -tunlp | grep -w tcp | awk '{print $5}' | sed 's/.*://g' | grep -w "$port_vm_ws") ]]
do
if [[ $tlsyn == "true" ]]; then
numbers=("2053" "2083" "2087" "2096" "8443")
else
numbers=("8080" "8880" "2052" "2082" "2086" "2095")
fi
port_vm_ws=${numbers[$RANDOM % ${#numbers[@]}]}
done
echo
blue "根据Vmess-ws协议是否启用TLS，随机指定支持CDN优选IP的标准端口：$port_vm_ws"
else
vlport && vmport && hy2port && tu5port
if [[ "$sbnh" != "1.10" ]]; then
anport
fi
readp "\n设置 Trojan + TLS 端口（回车随机）：" port
chooseport
port_tr=$port
if snell_supported "$sbnh"; then
readp "\n设置 Snell TCP 端口（回车随机）：" port
chooseport
port_sn=$port
fi
fi
echo
blue "各协议端口确认如下"
blue "Vless-reality端口：$port_vl_re"
blue "Vmess-ws端口：$port_vm_ws"
blue "Hysteria-2端口：$port_hy2"
blue "Tuic-v5端口：$port_tu"
if [[ "$sbnh" != "1.10" ]]; then
blue "Anytls端口：$port_an"
fi
blue "Trojan + TLS 端口：$port_tr"
snell_supported "$sbnh" && blue "Snell TCP 端口：$port_sn"
red "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
green "四、自动生成各个协议统一的uuid (密码)"
uuid=$(/etc/s-box/sing-box generate uuid) || return 1
ws_path=$(openssl rand -hex 24) || return 1
ws_path=/$ws_path
blue "节点凭据与 WebSocket 路径已独立生成"
}

valid_node_address(){
sub_valid_domain "$1" || ip_literal_valid 4 "$1" || ip_literal_valid 6 "$1"
}

acme_hostname(){
local value
value=$(cat /root/ygkkkca/ca.log) && sub_valid_domain "$value" || {
    red "ACME 记录缺少合法域名" >&2; return 1
}
client_tls_material /root/ygkkkca/cert.crt /root/ygkkkca/private.key "$value" >/dev/null || return 1
printf '%s\n' "$value"
}

quick_tunnel_hostname(){
local host
host=$(awk '{
    for (i=1; i<=NF; i++) {
        if ($i !~ /^https:\/\/[a-z0-9-]+\.trycloudflare\.com(\/[^[:space:]]*)?$/) continue
        name=$i; sub(/^https:\/\//,"",name); sub(/\/.*/,"",name)
        split(name,labels,"."); if (length(labels[1]) <= 63 && labels[1] !~ /^-|-$/) last=name
    }
} END {if(last) print last}' /etc/s-box/argo.log 2>/dev/null)
[[ -n $host ]] && printf '%s\n' "$host"
}

project_process(){
local kind=$1 port=$2 action=$3 exe pid actual attempt pids found=1
local -a argv
case $kind in
    temporary) sub_valid_port "$port" || return 1; exe=/etc/s-box/cloudflared ;;
    temporary-all) [[ $action == stop ]] || return 1; exe=/etc/s-box/cloudflared ;;
    fixed) exe=/etc/s-box/cloudflared ;;
    socks) exe=/etc/s-box/sbwpph ;;
    *) return 1 ;;
esac
if pids=$(pgrep -x "${exe##*/}" 2>/dev/null); then :
elif [[ $? != 1 ]]; then
    red "无法核对项目进程，已停止操作" >&2; return 2
fi
while IFS= read -r pid; do
    [[ -n $pid ]] || continue
    actual=$(readlink "/proc/$pid/exe") || continue
    [[ $actual == "$exe" || $actual == "$exe (deleted)" ]] || continue
    mapfile -d '' -t argv < "/proc/$pid/cmdline" 2>/dev/null || continue
    [[ ${argv[0]} == "$exe" ]] || continue
    case $kind in
        temporary)
            [[ ${argv[1]} == tunnel && ${argv[2]} == --url &&
                ( ${argv[3]} == "http://127.0.0.1:$port" || ${argv[3]} == "http://localhost:$port" ) ]] || continue ;;
        temporary-all)
            [[ ${argv[1]} == tunnel && ${argv[2]} == --url && ${argv[3]} =~ ^http://(127\.0\.0\.1|localhost):[0-9]+$ ]] || continue ;;
        fixed)
            [[ ${argv[1]} == tunnel && " ${argv[*]} " == *" run "* ]] || continue ;;
    esac
    found=0
    if [[ $action == stop ]]; then
        kill -TERM "$pid" || return 1
        for attempt in {1..30}; do
            kill -0 "$pid" 2>/dev/null && [[ $(readlink "/proc/$pid/exe") == "$actual" ]] || break
            sleep .1
        done
        if kill -0 "$pid" 2>/dev/null && [[ $(readlink "/proc/$pid/exe") == "$actual" ]]; then return 1; fi
    fi
done <<< "$pids"
[[ $action == stop ]] || return "$found"
}

config_jq(){
local filter=$1 source=${!#}
shift
jq "${@:1:$#-1}" '
  def one($name): if length == 1 then .[0] else error($name + " must be unique") end;
  def inbound($tag): [.inbounds[]? | select(.tag == $tag)] | one("inbound " + $tag);
  def outbound($tag): [.outbounds[]? | select(.tag == $tag)] | one("outbound " + $tag);
  def endpoint($tag): [.endpoints[]? | select(.tag == $tag)] | one("endpoint " + $tag);
'"$filter" "$source"
}

config_get(){
config_jq 'inbound($tag) | '"$2" -r --arg tag "$1" "${3:-/etc/s-box/sb.json}"
}

selfsigned_material(){
local cert=$1 name=$2 pem digest
pem=$(openssl x509 -in "$cert" -outform PEM) || return 1
pem=${pem//$'\r'/}
openssl x509 -in "$cert" -noout -ext subjectAltName 2>/dev/null | grep -q 'DNS:' &&
    ! openssl x509 -in "$cert" -noout -ext basicConstraints 2>/dev/null | grep -q 'CA:TRUE' &&
    openssl verify -partial_chain -CAfile "$cert" -purpose sslserver -verify_hostname "$name" "$cert" >/dev/null || {
    printf '%s\n' "自签证书与 $name 的 DNS SAN 不匹配，请重新签发并重新分发订阅" >&2
    return 1
}
digest=$(set -o pipefail; openssl x509 -in "$cert" -outform DER | openssl dgst -sha256 -binary | xxd -p -c 32) || return 1
[[ $digest =~ ^[[:xdigit:]]{64}$ ]] || return 1
jq -cn --arg pem "$pem" --arg fingerprint "$digest" '{certificate:$pem, fingerprint:$fingerprint}'
}

client_tls_material(){
local cert=$1 key=$2 name=$3 cert_pub key_pub mode
if [[ $key == /root/ygkkkca/private.key ]]; then
    chmod 700 /root/ygkkkca || return 1
    if [[ -L $key ]]; then
        mode=$(stat -Lc '%a' "$key") && (( (8#$mode & 0037) == 0 )) || {
            red "外部私钥权限须限于所有者或受信组只读" >&2; return 1
        }
    else chmod 600 "$key" || return 1; fi
fi
cert_pub=$(openssl x509 -in "$cert" -noout -pubkey) &&
    key_pub=$(openssl pkey -passin pass: -in "$key" -pubout) && [[ -n $cert_pub && $cert_pub == "$key_pub" ]] || {
    red "节点证书与私钥不匹配或无法读取" >&2; return 1
}
# The supplied bundle may provide intermediates. Only the system store supplies trust anchors.
if [[ $cert != /etc/s-box/cert.pem ]] &&
    openssl x509 -in "$cert" -noout -ext subjectAltName | grep -q 'DNS:' &&
    openssl verify -purpose sslserver -verify_hostname "$name" -untrusted "$cert" "$cert" >/dev/null 2>&1; then
    printf '{}\n'
else
    selfsigned_material "$cert" "$name"
fi
}

load_node_trust(){
local file=$1 rows tag cert key name prefix identity material
local -A checked=()
rows=$(jq -r '.inbounds[] | select(.tls.enabled and (.tls.certificate_path != null)) |
    [.tag,.tls.certificate_path,.tls.key_path,.tls.server_name] | @tsv' "$file") || return 1
while IFS=$'\t' read -r tag cert key name; do
    case $tag in vmess-sb) prefix=vm ;; hy2-sb) prefix=hy2 ;; tuic5-sb) prefix=tu5 ;;
        anytls-sb) prefix=an ;; trojan-sb) prefix=trojan ;; *) continue ;; esac
    if [[ -z $name ]]; then
        case $cert in
            /etc/s-box/cert.pem) name=www.bing.com ;;
            /root/ygkkkca/cert.crt) name=$(acme_hostname) || return 1 ;;
            *) red "入站 $tag 缺少证书域名" >&2; return 1 ;;
        esac
    fi
    identity="$cert/$key/$name"
    if [[ ! -v 'checked[$identity]' ]]; then
        checked[$identity]=$(client_tls_material "$cert" "$key" "$name") || return 1
    fi
    material=${checked[$identity]}
    printf -v "${prefix}_material" '%s' "$material"
    if [[ $material == '{}' ]]; then printf -v "${prefix}_self" false; else printf -v "${prefix}_self" true; fi
    printf -v "${prefix}_name" '%s' "$name"
done <<< "$rows"
}

config_patch_inbound(){
if [[ $3 == 1.10 && $4 == anytls-sb ]]; then cp -p "$1" "$2"; return; fi
config_jq 'inbound($tag) as $old |
  .inbounds |= map(if .tag == $tag then . * $patch else . end)' \
  --arg tag "$4" --argjson patch "$5" "$1" > "$2"
}

config_patch_acme(){
jq -e --arg name "$3" --arg cert /root/ygkkkca/cert.crt '
    (.inbounds[]? | select(.tls.certificate_path == $cert) | .tls.server_name) = $name' "$1" > "$2"
}

config_patch_identity(){
printf '%s\0%s' "$4" "${5:-}" | config_jq '
  split("\u0000") as [$uuid,$path] | $server[0] |
  (["vless-sb","vmess-sb","hy2-sb","tuic5-sb"] +
    if $series == "1.10" then [] else ["anytls-sb"] end) as $tags |
  reduce $tags[] as $tag (.;
    inbound($tag) as $node | ($node.users | one("single user in " + $tag)) as $user |
    .inbounds |= map(if .tag == $tag then
      (if $tag == "vmess-sb" then
         (if (.transport.path | ascii_downcase | (contains($user.uuid | ascii_downcase) or contains($uuid | ascii_downcase)))
          then if $path == "" then error("independent WS path required") else .transport.path=$path end
          else . end) | .listen=(if .tls.enabled then .listen else "127.0.0.1" end)
       else . end) |
      .users[0] |= (if has("uuid") then .uuid=$uuid else . end |
                    if has("password") then .password=$uuid else . end)
      else . end))' -Rs --slurpfile server "$1" --arg series "$3" - > "$2"
}

# A disabled modern WARP rule keeps its original match and explicitly drops it.
config_patch_warp_profile(){
local src=$1 dst=$2 series=$3 profile=$4
config_jq '
  $profile[0] as $p |
  def renew_address($address; $bits; $family):
    ([.[] | select(contains(":") == ($family==6))]) as $matches |
    if ($matches|length)>1 then error("ambiguous WARP interface addresses")
    elif ($matches|length)==0 then if $address=="" then . else . + [$address + "/" + $bits] end
    elif $address=="" then error("new profile lacks existing address family")
    else $matches[0] as $old |
      map(if .==$old then $address + "/" + (($old|split("/")[1]) // $bits) else . end)
    end;
  def addresses($old): ($old // []) | renew_address($p.ip;"32";4) | renew_address(($p.ipv6 // "");"128";6);
  if $series=="1.10" then
    outbound("wireguard-out") as $old |
    .outbounds |= map(if .tag=="wireguard-out" then
      . + {type:"wireguard",tag:.tag,server:$p.server,server_port:$p.port,local_address:addresses($old.local_address),
       private_key:$p.private_key,peer_public_key:$p.public_key,reserved:$p.reserved,mtu:$p.mtu}
      else . end)
  else
    ([.endpoints[]? | select(.tag=="warp-out")]) as $nodes |
    (if ($nodes|length)==0 then {} else ($nodes|one("WARP endpoint")) end) as $node |
    (if ($nodes|length)>0 then ($node.peers|one("WARP peer")) else {} end) as $peer |
    (if ($nodes|length)>0 then .
     else ([.route.rules|to_entries[] | select(.value.action=="reject" and .value.method=="drop"
           and .value.domain_suffix!=null)] | one("disabled WARP route") | .key) as $i |
           .route.rules[$i] |= (del(.action,.method)+{outbound:"warp-out"})
     end) |
    .endpoints=([.endpoints[]? | select(.tag!="warp-out")] +
      [($node + {type:"wireguard",tag:"warp-out",address:addresses($node.address),private_key:$p.private_key,mtu:$p.mtu,
        peers:[($peer + {address:$p.server,port:$p.port,public_key:$p.public_key,reserved:$p.reserved,
                allowed_ips:($peer.allowed_ips // ["0.0.0.0/0","::/0"])})]})])
  end' --arg series "$series" --slurpfile profile "$profile" "$src" > "$dst"
}

config_patch_socks_port(){
config_jq 'outbound("socks-out") as $node |
  .outbounds |= map(if .tag == "socks-out" then .server_port=$port else . end)' \
  --argjson port "$4" "$1" > "$2"
}

config_locked(){
if [[ ${config_lock_held:-0} == 1 ]]; then "$@"; return; fi
(
    flock -x 8 || exit 1
    local config_lock_held=1 installed
    if [[ -x /etc/s-box/sing-box ]]; then
        installed=$(/etc/s-box/sing-box version 2>/dev/null)
        if [[ $installed =~ ^sing-box\ version\ ([0-9]+\.[0-9]+)\. ]]; then local sbnh=${BASH_REMATCH[1]}; fi
    fi
    # Keep the lock in the writer and its rollback even if the menu process exits.
    # Detached services close the lock descriptors at their launch sites.
    "$@"
) 8>/run/s-box-config.lock
}

extra_for_series(){
if jq -e 'all(.inbounds[]?; .type != "trojan" and (has("domain_strategy") | not))' "$1" >/dev/null; then
    cp -p -- "$1" "$2"
    return $?
fi
jq -e --arg series "$3" --slurpfile main "$4" '
    if $series=="1.10" then
        (($main[0].outbounds[]? | select(.tag=="direct") | .domain_strategy) // "prefer_ipv4") as $s |
        .inbounds |= map(.domain_strategy=$s)
    else .inbounds |= map(del(.domain_strategy)) end' "$1" > "$2"
}

config_apply(){
config_locked config_apply_locked "$@"
}

config_apply_locked(){
(
local patch=$1 work file series restore=0 failed=0
local -a check_args files=(sb10.json sb11.json sb.json)
local extra=/etc/s-box/sb_extra.json
shift
if [[ -f $extra && ( $patch == secure_server_routes || $patch == ip_priority_patch || $patch == config_patch_identity ) ]]; then
    files+=(sb_extra.json)
fi
work=$(mktemp -d /etc/s-box/.config.XXXXXX) || exit 1
config_cleanup(){
local status=$?
trap - EXIT
trap '' HUP INT TERM
if (( restore )); then
for file in "${files[@]}"; do
mv -f -- "$work/old-$file" "/etc/s-box/$file" || failed=1
done
if (( failed )); then
red "恢复配置失败，剩余备份保留在 $work"
return 1
fi
ip_priority_restart >/dev/null 2>&1 || red "原配置已恢复，但服务启动失败，请查看运行日志"
[[ ! -f /etc/s-box/firewall.json ]] || firewall_sync || red "恢复原配置后的防火墙同步失败"
red "服务端配置修改失败，已恢复原配置"
fi
rm -rf -- "$work"
return "$status"
}
trap config_cleanup EXIT
trap 'exit 1' HUP INT TERM
for file in "${files[@]}"; do
cp -p "/etc/s-box/$file" "$work/old-$file" && cp -p "/etc/s-box/$file" "$work/$file" || exit 1
if [[ $file == sb_extra.json ]]; then
    if [[ $patch == config_patch_identity ]]; then
        jq -e --slurpfile before "$work/old-sb.json" --slurpfile after "$work/sb.json" '
            def vm_uuid: .inbounds[] | select(.tag=="vmess-sb") | .users[0].uuid;
            ($before[0] | vm_uuid) as $old | ($after[0] | vm_uuid) as $new |
            (.inbounds[] | select(.tag=="trojan-sb") | .users[] | select(.password==$old).password)=$new' \
            "$extra" > "$work/$file" || exit 1
    else
        extra_for_series "$extra" "$work/$file" "$sbnh" "$work/sb.json" || exit 1
    fi
    extra=$work/$file
    continue
fi
case "$file" in
sb10.json) series=1.10 ;;
sb11.json) series=1.11 ;;
sb.json) series=$sbnh ;;
esac
"$patch" "/etc/s-box/$file" "$work/$file" "$series" "$@" || exit 1
done
check_args=(-c "$work/sb.json")
[[ ! -f $extra ]] || check_args+=(-c "$extra")
if ! /etc/s-box/sing-box check "${check_args[@]}"; then
red "候选配置校验失败，已保留原配置"
exit 1
fi
if [[ $patch == config_patch_inbound && $1 == vmess-sb ]] &&
    jq -e --slurpfile candidate "$work/sb.json" '
      def vm: .inbounds[] | select(.tag=="vmess-sb");
      (vm) as $old | ($candidate[0] | vm) as $new |
      $old.tls.enabled==false and ([$old.listen,$old.listen_port,$old.tls.enabled] !=
                                   [$new.listen,$new.listen_port,$new.tls.enabled])' "$work/old-sb.json" >/dev/null; then
    argo_stop_for_origin || exit 1
fi
restore=1
for file in "${files[@]}"; do
mv -f -- "$work/$file" "/etc/s-box/$file" || exit 1
done
ip_priority_restart checked || exit 1
[[ ! -f /etc/s-box/firewall.json ]] || firewall_sync || exit 1
restore=0
)
}

config_refresh(){
sbshare >/dev/null 2>&1 || {
red "配置已生效，但订阅刷新失败，请通过菜单9重试"
return 1
}
}

config_rule_get(){
local file=$1 series=$2 channel=$3 field=$4 tag
case "$field" in domain_suffix|geosite) ;; *) return 1 ;; esac
if [[ "$series" == 1.10 ]]; then
case "$channel" in
warp4) tag=warp-IPv4-out ;;
warp6) tag=warp-IPv6-out ;;
socks4) tag=socks-IPv4-out ;;
socks6) tag=socks-IPv6-out ;;
direct4) tag=vps-outbound-v4 ;;
direct6) tag=vps-outbound-v6 ;;
*) return 1 ;;
esac
else
case "$channel:$field" in
warp6:domain_suffix) tag=warp-out ;;
socks4:domain_suffix) tag=socks-out ;;
warp4:*|warp6:geosite|socks4:geosite|socks6:*|direct4:*|direct6:*) printf 'yg_kkk\n'; return 0 ;;
*) return 1 ;;
esac
fi
jq -er --arg tag "$tag" --arg field "$field" '
  (any(.endpoints[]?; .tag=="warp-out") | not) as $disabled |
  [.route.rules[]? | select(.outbound == $tag or ($tag=="warp-out" and $disabled and
     .action=="reject" and .method=="drop" and .domain_suffix!=null))] |
  if length == 1 then .[0][$field] | join(" ")
  else error("缺少唯一的分流路由: " + $tag) end
' "$file"
}

config_patch_route(){
local src=$1 dst=$2 series=$3 channel=$4 field=$5 domains=$6 tag strategy=
case "$field" in domain_suffix|geosite) ;; *) return 1 ;; esac
if [[ "$series" == 1.10 ]]; then
case "$channel" in
warp4) tag=warp-IPv4-out ;;
warp6) tag=warp-IPv6-out ;;
socks4) tag=socks-IPv4-out ;;
socks6) tag=socks-IPv6-out ;;
direct4) tag=vps-outbound-v4 ;;
direct6) tag=vps-outbound-v6 ;;
*) return 1 ;;
esac
else
case "$channel:$field" in
warp6:domain_suffix) tag=warp-out; strategy=prefer_ipv6 ;;
socks4:domain_suffix) tag=socks-out; strategy=prefer_ipv4 ;;
warp4:*|warp6:geosite|socks4:geosite|socks6:*|direct4:*|direct6:*) cp -- "$src" "$dst"; return $? ;;
*) return 1 ;;
esac
fi
jq -e --arg tag "$tag" --arg field "$field" --arg series "$series" \
  --arg strategy "$strategy" --argjson domains "$domains" '
  def one($description):
    if length == 1 then .[0] else error("缺少唯一的" + $description) end;
  (if ($domains | type) != "array" then error("分流域名必须为数组")
   elif any($domains[]; type != "string") then error("分流域名必须为字符串")
   elif ($domains | length) == 0 then ["yg_kkk"] else $domains end) as $values |
  (any(.endpoints[]?; .tag=="warp-out") | not) as $disabled |
  ([.route.rules | to_entries[] | select(.value.outbound == $tag or ($tag=="warp-out" and $disabled and
     .value.action=="reject" and .value.method=="drop" and .value.domain_suffix!=null)) | .key] |
    one("分流路由: " + $tag)) as $route |
  if $series == "1.10" then .route.rules[$route][$field] = $values
  else
    .route.rules[$route].domain_suffix as $old |
    if ($old | type) != "array" then error("分流路由缺少域名数组") else
      ([.route.rules | to_entries[] |
        select(.value.action == "resolve" and .value.strategy == $strategy and
               .value.domain_suffix == $old) | .key] | one("分流解析规则: " + $tag)) as $resolve |
      if $resolve >= $route then error("分流解析规则必须位于对应路由之前") else
        .route.rules[$route].domain_suffix = $values |
        .route.rules[$resolve].domain_suffix = $values
      end
    end
  end
' "$src" > "$dst"
}

config_set_port(){
local patch
patch=$(jq -cn --argjson port "$2" '{listen_port:$port}') || return 1
config_apply config_patch_inbound "$1" "$patch" && config_refresh
}

inssbjsonser(){
local egress_ipv4= egress_ipv6= egress_bind_fields= vm_listen=127.0.0.1 ws_json
[[ $tlsyn != true ]] || vm_listen=::
if [[ -z ${ws_path:-} ]]; then
    if [[ -s /etc/s-box/sb.json ]]; then ws_path=$(config_get vmess-sb '.transport.path') || return 1
    else ws_path=$(openssl rand -hex 24) || return 1; ws_path=/$ws_path; fi
fi
ws_json=$(jq -cn --arg path "$ws_path" '$path') || return 1
if [[ -e /etc/s-box/egress.json ]]; then
egress_read || { red "出站源地址设置无效，未重新生成配置"; return 1; }
[[ -z $egress_ipv4 ]] || egress_bind_fields+=",\"inet4_bind_address\":\"$egress_ipv4\""
[[ -z $egress_ipv6 ]] || egress_bind_fields+=",\"inet6_bind_address\":\"$egress_ipv6\""
fi
cat > /etc/s-box/sb10.json <<EOF
{
"log": {
    "disabled": false,
    "level": "info",
    "timestamp": true
  },
  "inbounds": [
    {
      "type": "vless",
      "sniff": true,
      "sniff_override_destination": true,
      "tag": "vless-sb",
      "listen": "::",
      "listen_port": ${port_vl_re},
      "users": [
        {
          "uuid": "${uuid}",
          "flow": "xtls-rprx-vision"
        }
      ],
      "tls": {
        "enabled": true,
        "server_name": "${ym_vl_re}",
          "reality": {
          "enabled": true,
          "handshake": {
            "server": "${ym_vl_re}",
            "server_port": 443
          },
          "private_key": "$private_key",
          "short_id": ["$short_id"]
        }
      }
    },
{
        "type": "vmess",
        "sniff": true,
        "sniff_override_destination": true,
        "tag": "vmess-sb",
        "listen": "$vm_listen",
        "listen_port": ${port_vm_ws},
        "users": [
            {
                "uuid": "${uuid}",
                "alterId": 0
            }
        ],
        "transport": {
            "type": "ws",
            "path": $ws_json,
            "max_early_data":2048,
            "early_data_header_name": "Sec-WebSocket-Protocol"    
        },
        "tls":{
                "enabled": ${tlsyn},
                "server_name": "${ym_vm_ws}",
                "certificate_path": "$certificatec_vmess_ws",
                "key_path": "$certificatep_vmess_ws"
            }
    }, 
    {
        "type": "hysteria2",
        "sniff": true,
        "sniff_override_destination": true,
        "tag": "hy2-sb",
        "listen": "::",
        "listen_port": ${port_hy2},
        "users": [
            {
                "password": "${uuid}"
            }
        ],
        "ignore_client_bandwidth":false,
        "tls": {
            "enabled": true,
            "alpn": [
                "h3"
            ],
            "server_name": "$ym_vm_ws",
            "certificate_path": "$certificatec_hy2",
            "key_path": "$certificatep_hy2"
        }
    },
        {
            "type":"tuic",
            "sniff": true,
            "sniff_override_destination": true,
            "tag": "tuic5-sb",
            "listen": "::",
            "listen_port": ${port_tu},
            "users": [
                {
                    "uuid": "${uuid}",
                    "password": "${uuid}"
                }
            ],
            "congestion_control": "bbr",
            "tls":{
                "enabled": true,
                "alpn": [
                    "h3"
                ],
                "server_name": "$ym_vm_ws",
                "certificate_path": "$certificatec_tuic",
                "key_path": "$certificatep_tuic"
            }
        }
],
"outbounds": [
{
"type":"direct",
"tag":"direct",
"domain_strategy": "$ipv"$egress_bind_fields
},
{
"type":"direct",
"tag": "vps-outbound-v4", 
"domain_strategy":"prefer_ipv4"$egress_bind_fields
},
{
"type":"direct",
"tag": "vps-outbound-v6",
"domain_strategy":"prefer_ipv6"$egress_bind_fields
},
{
"type": "socks",
"tag": "socks-out",
"server": "127.0.0.1",
"server_port": 40000,
"version": "5"
},
{
"type":"direct",
"tag":"socks-IPv4-out",
"detour":"socks-out",
"domain_strategy":"prefer_ipv4"
},
{
"type":"direct",
"tag":"socks-IPv6-out",
"detour":"socks-out",
"domain_strategy":"prefer_ipv6"
},
{
"type":"direct",
"tag":"warp-IPv4-out",
"detour":"wireguard-out",
"domain_strategy":"prefer_ipv4"
},
{
"type":"direct",
"tag":"warp-IPv6-out",
"detour":"wireguard-out",
"domain_strategy":"prefer_ipv6"
},
{"type":"block","tag":"wireguard-out"},
{
"type": "block",
"tag": "block"
}
],
"route":{
"rules":[
{
"protocol": [
"quic",
"stun"
],
"outbound": "block"
},
{
"outbound":"warp-IPv4-out",
"domain_suffix": [
"yg_kkk"
]
,"geosite": [
"yg_kkk"
]
},
{
"outbound":"warp-IPv6-out",
"domain_suffix": [
"yg_kkk"
]
,"geosite": [
"yg_kkk"
]
},
{
"outbound":"socks-IPv4-out",
"domain_suffix": [
"yg_kkk"
]
,"geosite": [
"yg_kkk"
]
},
{
"outbound":"socks-IPv6-out",
"domain_suffix": [
"yg_kkk"
]
,"geosite": [
"yg_kkk"
]
},
{
"outbound":"vps-outbound-v4",
"domain_suffix": [
"yg_kkk"
]
,"geosite": [
"yg_kkk"
]
},
{
"outbound":"vps-outbound-v6",
"domain_suffix": [
"yg_kkk"
]
,"geosite": [
"yg_kkk"
]
},
{
"outbound": "direct",
"network": "udp,tcp"
}
]
}
}
EOF

cat > /etc/s-box/sb11.json <<EOF
{
"log": {
    "disabled": false,
    "level": "info",
    "timestamp": true
  },
  "inbounds": [
    {
      "type": "vless",

      
      "tag": "vless-sb",
      "listen": "::",
      "listen_port": ${port_vl_re},
      "users": [
        {
          "uuid": "${uuid}",
          "flow": "xtls-rprx-vision"
        }
      ],
      "tls": {
        "enabled": true,
        "server_name": "${ym_vl_re}",
          "reality": {
          "enabled": true,
          "handshake": {
            "server": "${ym_vl_re}",
            "server_port": 443
          },
          "private_key": "$private_key",
          "short_id": ["$short_id"]
        }
      }
    },
{
        "type": "vmess",

 
        "tag": "vmess-sb",
        "listen": "$vm_listen",
        "listen_port": ${port_vm_ws},
        "users": [
            {
                "uuid": "${uuid}",
                "alterId": 0
            }
        ],
        "transport": {
            "type": "ws",
            "path": $ws_json,
            "max_early_data":2048,
            "early_data_header_name": "Sec-WebSocket-Protocol"    
        },
        "tls":{
                "enabled": ${tlsyn},
                "server_name": "${ym_vm_ws}",
                "certificate_path": "$certificatec_vmess_ws",
                "key_path": "$certificatep_vmess_ws"
            }
    }, 
    {
        "type": "hysteria2",

 
        "tag": "hy2-sb",
        "listen": "::",
        "listen_port": ${port_hy2},
        "users": [
            {
                "password": "${uuid}"
            }
        ],
        "ignore_client_bandwidth":false,
        "tls": {
            "enabled": true,
            "alpn": [
                "h3"
            ],
            "server_name": "$ym_vm_ws",
            "certificate_path": "$certificatec_hy2",
            "key_path": "$certificatep_hy2"
        }
    },
        {
            "type":"tuic",

     
            "tag": "tuic5-sb",
            "listen": "::",
            "listen_port": ${port_tu},
            "users": [
                {
                    "uuid": "${uuid}",
                    "password": "${uuid}"
                }
            ],
            "congestion_control": "bbr",
            "tls":{
                "enabled": true,
                "alpn": [
                    "h3"
                ],
                "server_name": "$ym_vm_ws",
                "certificate_path": "$certificatec_tuic",
                "key_path": "$certificatep_tuic"
            }
        },
        {
            "type":"anytls",
            "tag":"anytls-sb",
            "listen":"::",
            "listen_port":${port_an},
            "users":[
                {
                  "password":"${uuid}"
                }
            ],
            "padding_scheme":[],
            "tls":{
                "enabled": true,
                "server_name": "$ym_vm_ws",
                "certificate_path": "$certificatec_an",
                "key_path": "$certificatep_an"
            }
        }
],
"endpoints":[],









"outbounds": [
{
"type":"direct",
"tag":"direct"$egress_bind_fields
},
{
"type": "socks",
"tag": "socks-out",
"server": "127.0.0.1",
"server_port": 40000,
"version": "5"
}
],
"route":{
"rules":[
{
 "action": "sniff"
},
{
"action": "resolve",
"domain_suffix":[
"yg_kkk"
],
"strategy": "prefer_ipv4"
},
{
"action": "resolve",
"domain_suffix":[
"yg_kkk"
],
"strategy": "prefer_ipv6"
},
{
"domain_suffix":[
"yg_kkk"
],
"outbound":"socks-out"
},
{
"domain_suffix":[
"yg_kkk"
],
"action":"reject","method":"drop"
},
{"action":"resolve","strategy":"$ipv"},
{
"outbound": "direct",
"network": "udp,tcp"
}
]
}
}
EOF
local file candidate series
for file in sb10.json sb11.json; do
    series=1.11; [[ $file != sb10.json ]] || series=1.10
    candidate=$(mktemp /etc/s-box/.route.XXXXXX) || return 1
    if ! secure_server_routes "/etc/s-box/$file" "$candidate" "$series" ||
       ! mv -f -- "$candidate" "/etc/s-box/$file"; then rm -f -- "$candidate"; return 1; fi
done
if [[ -s /etc/s-box/server_warp.json ]]; then
    local file series candidate
    for file in sb10.json sb11.json; do
        series=1.11; [[ $file != sb10.json ]] || series=1.10
        candidate=$(mktemp /etc/s-box/.warp-config.XXXXXX) || return 1
        if ! config_patch_warp_profile "/etc/s-box/$file" "$candidate" "$series" /etc/s-box/server_warp.json ||
            ! mv -f -- "$candidate" "/etc/s-box/$file"; then rm -f -- "$candidate"; return 1; fi
    done
fi
[[ "$sbnh" == "1.10" ]] && num=10 || num=11
cp /etc/s-box/sb${num}.json /etc/s-box/sb.json
}

# Shared by installation and the explicit migration of existing profiles.
secure_server_routes(){
local extra='[]' interfaces local_cidrs advertised= ip node_ipv4 node_ipv6
read_node_addresses || return 1
interfaces=$(ip -o addr show scope global) || { red "无法枚举本机地址，已中止路由生成"; return 1; }
[[ ! -s /etc/s-box/server_ipcl.log ]] || advertised=$(< /etc/s-box/server_ipcl.log)
# Include all interface aliases, and the known public node addresses on NAT hosts.
local_cidrs=$(
    awk '$3=="inet" || $3=="inet6" {split($4, a, "/"); print a[1] ($3=="inet" ? "/32" : "/128")}' <<< "$interfaces"
    for ip in "$node_ipv4" "$node_ipv6" "$advertised"; do
        if ip_literal_valid 4 "$ip"; then printf '%s/32\n' "$ip"
        elif ip_literal_valid 6 "$ip"; then printf '%s/128\n' "$ip"; fi
    done
)
if [[ -e /etc/s-box/restricted-cidrs.json ]]; then
    extra=$(jq -ce 'if type=="array" and all(.[]; type=="string") then . else error("expected CIDR array") end' /etc/s-box/restricted-cidrs.json) || return 1
fi
jq -e --arg series "$3" --argjson extra "$extra" --arg local_cidrs "$local_cidrs" '
  (["0.0.0.0/8","10.0.0.0/8","100.64.0.0/10","127.0.0.0/8","169.254.0.0/16",
    "172.16.0.0/12","192.168.0.0/16","224.0.0.0/4","240.0.0.0/4","::/96",
    "::ffff:0:0/96","fc00::/7","fe80::/10","ff00::/8","168.63.129.16/32",
    "64:ff9b::/96","64:ff9b:1::/48","::ffff:0:0:0/96","2002::/16","100::/64",
    "192.0.0.0/24","198.18.0.0/15"] + $extra + ($local_cidrs | split("\n") - [""]) | unique) as $restricted |
  {ip_cidr:["0.0.0.0/0","::/0"],invert:true} as $missing |
  (if $series == "1.10" then {outbound:"block"} else {action:"reject"} end) as $deny |
  (if $series == "1.10" then {network:"udp",outbound:"block"}
   else {network:"udp",action:"route-options",udp_connect:true} end) as $udp |
  .route.rules |= map(select((. == $udp or . == ($missing + $deny) or
      (((.ip_cidr // []) | index("100.64.0.0/10")) != null and
       ((.ip_cidr // []) | index("168.63.129.16/32")) != null and del(.ip_cidr) == $deny)) | not)) |
  # Reject unresolved destinations before any outbound, including SOCKS5. The core dials
  # the resolved IPs for TCP and connected UDP. Legacy has no connected-UDP route option.
  [{ip_cidr:$restricted} + $deny,$missing + $deny,$udp] as $guards |
  if any(.outbounds[]?; has("override_address")) or any(.route.rules[]?; has("override_address"))
  then error("请先人工复核目标地址改写规则") else . end |
  if $series == "1.10" then
    ((.outbounds[] | select(.tag=="direct") | .domain_strategy) // "prefer_ipv4") as $strategy |
    .inbounds |= map(.domain_strategy=(if (["prefer_ipv4","prefer_ipv6","ipv4_only","ipv6_only"] | index($strategy)) != null
        then $strategy else "prefer_ipv4" end) | del(.sniff_override_destination)) |
    .route.rules = $guards + .route.rules
  else
    [.route.rules[] | select(.action=="resolve" and
       (del(.strategy,.action)=={} or del(.strategy,.action)==$missing))] as $defaults |
    if ($defaults | length)>1 then error("multiple default resolvers") else . end |
    [.route.rules[] | select(.action=="resolve" and has("domain_suffix"))] as $split |
    if any((.route.rules - $defaults - $split)[]; .action=="resolve")
    then error("请先人工复核自定义解析规则") else . end |
    [.route.rules[] | select(.action=="sniff")] as $sniff |
    .route.rules = $sniff + $split + [($missing + {action:"resolve",strategy:($defaults[0].strategy // "prefer_ipv4")})] +
      $guards + (.route.rules - $sniff - $split - $defaults)
  end' "$1" > "$2"
}

snell_supported(){
local version=${1#v} major minor
[[ "$version" =~ ^([0-9]+)\.([0-9]+)(\.|$) ]] || return 1
major=${BASH_REMATCH[1]} minor=${BASH_REMATCH[2]}
(( major > 1 || (major == 1 && minor >= 14) )) || return 1
[[ "$version" != 1.14.0-* ]]
}

extra_config_init(){
if [[ ! -e /etc/s-box/sb_extra.json ]]; then
(umask 077; printf '{"inbounds":[]}\n' > /etc/s-box/sb_extra.json) || return 1
fi
}

extra_inbound(){
local protocol=$1 port=$2 password=$3 strategy='{}'
[[ "$port" =~ ^[1-9][0-9]{0,4}$ ]] && (( port <= 65535 )) && [[ -n "$password" ]] || return 1
case "$protocol" in
trojan)
if [[ $sbnh == 1.10 ]]; then
    strategy=$(jq -ce '{domain_strategy:((.outbounds[] | select(.tag=="direct") | .domain_strategy) // "prefer_ipv4")}' /etc/s-box/sb.json) || return 1
fi
jq -n --argjson port "$port" --argjson strategy "$strategy" --arg password "$password" \
    --arg cert "$4" --arg key "$5" --arg sni "$6" '
    {type:"trojan",tag:"trojan-sb",listen:"::",listen_port:$port,
     users:[{password:$password}],tls:{enabled:true,server_name:$sni,
     certificate_path:$cert,key_path:$key}} + $strategy'
;;
snell)
# sing-box v5 inbound implements the TCP wire protocol used by v4 clients.
jq -n --argjson port "$port" --arg psk "$password" '
    {type:"snell",tag:"snell-sb",listen:"::",listen_port:$port,version:5,psk:$psk}'
;;
*) return 1 ;;
esac
}

extra_port_available(){
local protocol=$1 port=$2 current sockets
# 0: available; 1: invalid/occupied; 2: configuration or socket inspection failed.
[[ "$port" =~ ^[1-9][0-9]{0,4}$ ]] && (( port <= 65535 )) || return 1
current=$(jq -er --argjson port "$port" \
    '[.inbounds[]? | select(.listen_port == $port)] | length' /etc/s-box/sb.json) || return 2
(( current == 0 )) || return 1
if [[ -e /etc/s-box/sb_extra.json ]]; then
current=$(jq -er --arg protocol "$protocol" --argjson port "$port" \
    '[.inbounds[]? | select(.type != $protocol and .listen_port == $port)] | length' \
    /etc/s-box/sb_extra.json) || return 2
(( current == 0 )) || return 1
current=$(jq -r --arg protocol "$protocol" '.inbounds[]? | select(.type == $protocol) | .listen_port' /etc/s-box/sb_extra.json) || return 2
[[ "$current" != "$port" ]] || return 0
fi
sockets=$(ss -ltnH) || return 2
! awk '{print $4}' <<< "$sockets" | grep -Eq ":${port}$"
}

extra_random_port(){
local candidate attempt status
for attempt in {1..10}; do
candidate=$(shuf -i 10000-65535 -n 1) || return 1
if extra_port_available "$1" "$candidate"; then
printf '%s\n' "$candidate"
return 0
else
status=$?
if (( status != 1 )); then
red "读取配置或检查监听端口失败，已停止随机端口选择" >&2
return 1
fi
fi
done
red "尝试10次仍未找到可用端口，请手动指定" >&2
return 1
}

extra_apply(){
(
local candidate=$1 backup restore=0 prepared unit_state
unit_state=$(singbox_unit_state)
[[ $unit_state == absent || $unit_state == project ]] || { red "同名服务归属未确认，已保留协议配置"; exit 1; }
if [[ $sbnh == 1.10 ]]; then
    prepared=$(mktemp /etc/s-box/.extra-legacy.XXXXXX) || exit 1
    if ! extra_for_series "$candidate" "$prepared" "$sbnh" /etc/s-box/sb.json ||
       ! mv -f -- "$prepared" "$candidate"; then rm -f -- "$prepared"; exit 1; fi
fi
if ! /etc/s-box/sing-box check -c /etc/s-box/sb.json -c "$candidate"; then
red "新协议配置校验失败，原配置未更改"
exit 1
fi
backup=$(mktemp /etc/s-box/.extra-backup.XXXXXX) || exit 1
extra_cleanup(){
local status=$?
trap - EXIT
trap '' HUP INT TERM
if (( restore )); then
if ! mv -f -- "$backup" /etc/s-box/sb_extra.json; then
red "恢复原配置失败，备份保留在 $backup"
return 1
fi
restartsb >/dev/null 2>&1 || red "原配置已恢复，但服务重启失败，请查看运行日志"
[[ ! -f /etc/s-box/firewall.json ]] || firewall_sync || red "恢复原配置后的防火墙同步失败"
red "新协议应用未完成，已恢复原配置"
fi
rm -f -- "$backup"
return "$status"
}
trap extra_cleanup EXIT
trap 'exit 1' HUP INT TERM
if [[ -f /etc/s-box/sb_extra.json ]]; then
cp -p /etc/s-box/sb_extra.json "$backup" || exit 1
else
printf '{"inbounds":[]}\n' > "$backup" || exit 1
fi
chmod 600 "$candidate" || exit 1
restore=1
mv -f -- "$candidate" /etc/s-box/sb_extra.json || exit 1
# Rewrite our service once here so an existing installation also loads the extra file.
sbservice checked || exit 1
[[ ! -f /etc/s-box/firewall.json ]] || firewall_sync || exit 1
restore=0
)
}

extra_set(){
config_locked extra_set_locked "$@"
}

extra_set_locked(){
local protocol=$1 port=$2 password=$3 node candidate
if [[ "$protocol" == snell ]] && ! snell_supported "$sbnh"; then
red "Snell 需要 sing-box 1.14.0 或更新的正式版内核，请先通过菜单8升级"
return 1
fi
if ! extra_port_available "$protocol" "$port"; then
red "端口无效或已被占用，请选择其他 TCP 端口"
return 1
fi
node=$(extra_inbound "$@") || return 1
candidate=$(mktemp /etc/s-box/.extra.XXXXXX) || return 1
if [[ -f /etc/s-box/sb_extra.json ]]; then
jq --argjson node "$node" '.inbounds = ([.inbounds[] | select(.tag != $node.tag)] + [$node])' \
    /etc/s-box/sb_extra.json > "$candidate" || { rm -f -- "$candidate"; return 1; }
else
jq -n --argjson node "$node" '{inbounds:[$node]}' > "$candidate" || { rm -f -- "$candidate"; return 1; }
fi
if extra_apply "$candidate"; then
return 0
fi
rm -f -- "$candidate"
return 1
}

extra_remove(){
config_locked extra_remove_locked "$@"
}

extra_remove_locked(){
local candidate
[[ "$1" == trojan || "$1" == snell ]] || return 1
[[ -f /etc/s-box/sb_extra.json ]] || return 0
candidate=$(mktemp /etc/s-box/.extra.XXXXXX) || return 1
if jq --arg protocol "$1" '.inbounds |= map(select(.type != $protocol))' \
    /etc/s-box/sb_extra.json > "$candidate" && extra_apply "$candidate"; then
return 0
fi
rm -f -- "$candidate"
return 1
}

extra_install(){
local trojan snell='null' sni psk candidate
extra_port_available trojan "$port_tr" || { red "Trojan端口与其他协议冲突"; return 1; }
if [[ "$certificatec_hy2" == /etc/s-box/cert.pem ]]; then
sni=www.bing.com
else
sni=$(acme_hostname) || return 1
fi
trojan=$(extra_inbound trojan "$port_tr" "$uuid" "$certificatec_hy2" "$certificatep_hy2" "$sni") || return 1
if snell_supported "$sbnh"; then
[[ "$port_sn" != "$port_tr" ]] && extra_port_available snell "$port_sn" || { red "Snell端口与其他协议冲突"; return 1; }
psk=$(/etc/s-box/sing-box generate rand --base64 32) || return 1
snell=$(extra_inbound snell "$port_sn" "$psk") || return 1
else
yellow "当前内核不支持 Snell；升级至 1.14.0 或更新正式版后可在 3 → 11 启用"
fi
candidate=$(mktemp /etc/s-box/.extra.XXXXXX) || return 1
if jq -n --argjson trojan "$trojan" --argjson snell "$snell" \
    '{inbounds:([$trojan,$snell] | map(select(. != null)))}' > "$candidate" &&
    /etc/s-box/sing-box check -c /etc/s-box/sb.json -c "$candidate" &&
    chmod 600 "$candidate" && mv -f -- "$candidate" /etc/s-box/sb_extra.json; then
return 0
fi
rm -f -- "$candidate"
return 1
}

extra_client_data(){
local trojan_material='{}' trojan_self trojan_name
load_node_trust /etc/s-box/sb_extra.json || return 1
jq --argjson material "$trojan_material" --arg server "$server_ipcl" --arg host "$hostname" '
  [.inbounds[] | select(.type == "trojan" or .type == "snell") |
    . as $in | (.type + "-" + $host) as $name |
    if .type == "trojan" then
      ($material.certificate != null) as $self |
      {singbox:{type:"trojan",tag:$name,server:$server,server_port:.listen_port,
        password:.users[0].password,tls:({enabled:true,server_name:.tls.server_name,insecure:false} +
          (if $self then {certificate:$material.certificate} else {} end))},
       mihomo:({type:"trojan",name:$name,server:$server,port:.listen_port,password:.users[0].password,
        udp:true,sni:.tls.server_name,"skip-cert-verify":false,"client-fingerprint":"chrome"} +
          (if $self then {fingerprint:$material.fingerprint} else {} end))}
    else
      {singbox:{type:"snell",tag:$name,server:$server,server_port:.listen_port,version:4,psk:.psk},
       mihomo:{type:"snell",name:$name,server:$server,port:.listen_port,version:4,psk:.psk,udp:true}}
    end] | {singbox:map(.singbox),mihomo:map(.mihomo)}' /etc/s-box/sb_extra.json
}

extra_share(){
local data link
data=$(extra_client_data) || return 1
link=$(printf '%s' "$data" | jq -r '.singbox[] | select(.type == "trojan" and .tls.certificate == null) |
    "trojan://" + (.password | @uri) + "@" +
    (if (.server | contains(":")) then "[" + .server + "]" else .server end) +
    ":" + (.server_port|tostring) + "?security=tls&type=tcp&sni=" + (.tls.server_name|@uri) +
    "&allowInsecure=0#" + (.tag|@uri)') || return 1
if [[ -n "$link" ]]; then
printf '%s\n' "$link" > /etc/s-box/trojan.txt || return 1
red "🚀【 Trojan + TLS 】分享链接"
printf '%s\n' "$link"
printf '%s' "$link" | qrencode -o - -t ANSIUTF8
fi
if printf '%s' "$data" | jq -e '.mihomo[] | select(.type == "snell")' >/dev/null; then
red "🚀【 Snell 】请通过 clmi.yaml 订阅导入 Clash Verge，单节点 YAML 如下："
printf '%s' "$data" | jq -cr '.mihomo[] | select(.type == "snell") | "- " + tojson' || return 1
fi
return 0
}

extra_menu(){
config_locked extra_menu_locked "$@"
}

extra_menu_locked(){
local choice protocol old port password cert key sni cert_choice
[[ -s /etc/s-box/sb.json ]] || { red "请先安装 sing-box"; return 1; }
readp "1：启用/设置 Trojan + TLS（端口、密码、证书）\n2：启用/设置 Snell（端口、PSK）\n3：停用 Trojan\n4：停用 Snell\n0：返回\n请选择【0-4】：" choice || return 1
case "$choice" in
1|2)
[[ "$choice" == 1 ]] && protocol=trojan || protocol=snell
if [[ "$protocol" == snell ]] && ! snell_supported "$sbnh"; then
red "Snell 需要 sing-box 1.14.0 或更新的正式版内核，请先通过菜单8升级"
return 1
fi
old='{}'
if [[ -s /etc/s-box/sb_extra.json ]]; then
old=$(jq -c --arg protocol "$protocol" '[.inbounds[] | select(.type == $protocol)] | first // {}' /etc/s-box/sb_extra.json) || return 1
fi
port=$(printf '%s' "$old" | jq -r '.listen_port // empty') || return 1
readp "TCP端口（回车保留 ${port:-随机端口}）：" choice || return 1
port=${choice:-$port}
[[ -n "$port" ]] || port=$(extra_random_port "$protocol") || return 1
password=$(printf '%s' "$old" | jq -r '.users[0].password // .psk // empty') || return 1
readp "密码/PSK（回车保留，首次设置则随机生成）：" choice || return 1
password=${choice:-$password}
[[ -n "$password" ]] || password=$(/etc/s-box/sing-box generate rand --base64 32) || return 1
if [[ "$protocol" == trojan ]]; then
cert=$(printf '%s' "$old" | jq -r '.tls.certificate_path // empty') || return 1
key=$(printf '%s' "$old" | jq -r '.tls.key_path // empty') || return 1
sni=$(printf '%s' "$old" | jq -r '.tls.server_name // empty') || return 1
readp "Trojan证书：1 自签；2 已有ACME域名证书；回车保留当前，首次默认自签：" cert_choice || return 1
case "$cert_choice" in
1) cert=/etc/s-box/cert.pem; key=/etc/s-box/private.key; sni=www.bing.com ;;
2) cert=/root/ygkkkca/cert.crt; key=/root/ygkkkca/private.key; sni=$(acme_hostname) || return 1 ;;
"") if [[ -z "$cert" ]]; then cert=/etc/s-box/cert.pem; key=/etc/s-box/private.key; sni=www.bing.com; fi ;;
*) red "证书选项无效"; return 1 ;;
esac
[[ -s "$cert" && -s "$key" && -n "$sni" ]] || { red "证书、私钥或证书域名缺失，请先通过菜单12申请证书"; return 1; }
fi
extra_set "$protocol" "$port" "$password" "$cert" "$key" "$sni" || return 1
;;
3) extra_remove trojan || return 1 ;;
4) extra_remove snell || return 1 ;;
0) return 0 ;;
*) red "选项无效"; return 1 ;;
esac
if ! sbshare; then
red "协议配置已应用，但订阅刷新失败，请通过菜单9重试"
return 1
fi
green "Trojan / Snell 配置已应用，请在 Clash Verge 中刷新 clmi.yaml 订阅"
}

singbox_unit_state(){
local file line state fragment effective executable=/etc/s-box/sing-box
local args='run -c /etc/s-box/sb.json' extra=' -c /etc/s-box/sb_extra.json'
if command -v apk >/dev/null 2>&1; then
    file=/etc/init.d/sing-box
    if [[ ! -e $file && ! -L $file ]]; then
        if rc-service -e sing-box >/dev/null 2>&1; then echo foreign; else echo absent; fi
        return
    fi
    if [[ -f $file && ! -L $file ]] &&
       [[ $(grep '^command=' "$file") == "command=\"$executable\"" ]] &&
       line=$(grep '^command_args=' "$file") &&
       [[ $line == "command_args=\"$args\"" || $line == "command_args=\"$args$extra\"" ]]; then
        echo project
    else echo foreign; fi
    return
fi
file=/etc/systemd/system/sing-box.service
state=$(systemctl show -p LoadState --value sing-box.service 2>/dev/null)
if [[ $state == not-found && ! -e $file && ! -L $file ]]; then
    local vendor
    for vendor in /run/systemd/system/sing-box.service /usr/local/lib/systemd/system/sing-box.service \
                  /usr/lib/systemd/system/sing-box.service /lib/systemd/system/sing-box.service; do
        if [[ -e $vendor || -L $vendor ]] || compgen -G "$vendor.d/*.conf" >/dev/null; then echo foreign; return; fi
    done
    if compgen -G "$file.d/*.conf" >/dev/null; then echo foreign; else echo absent; fi
    return
fi
if [[ $state == loaded && -f $file && ! -L $file ]] &&
   line=$(grep '^ExecStart=' "$file") &&
   [[ $line == "ExecStart=$executable $args" || $line == "ExecStart=$executable $args$extra" ]] &&
   fragment=$(systemctl show -p FragmentPath --value sing-box.service) && [[ $fragment == "$file" ]] &&
   effective=$(systemctl show -p ExecStart --value sing-box.service) &&
   [[ $effective != *'}'*'{'* &&
      ( $effective == "{ path=$executable ; argv[]=$executable $args ;"* ||
        $effective == "{ path=$executable ; argv[]=$executable $args$extra ;"* ) ]]; then
    echo project
else echo foreign; fi
}

singbox_require_owned(){
[[ $(singbox_unit_state) == project ]] || { red "同名 sing-box 服务归属未确认"; return 1; }
}

sbservice(){
local unit_state
unit_state=$(singbox_unit_state)
[[ $unit_state == absent || $unit_state == project ]] || { red "同名 sing-box 服务归属未确认，保留原服务"; return 1; }
extra_config_init || return 1
if [[ ${1:-} != checked ]]; then
/etc/s-box/sing-box check -c /etc/s-box/sb.json -c /etc/s-box/sb_extra.json || return 1
fi
if command -v apk >/dev/null 2>&1; then
echo '#!/sbin/openrc-run
description="sing-box service"
command="/etc/s-box/sing-box"
command_args="run -c /etc/s-box/sb.json -c /etc/s-box/sb_extra.json"
command_background=true
depend(){ after firewall nftables iptables ip6tables; }
start_pre(){ [ ! -f /etc/s-box/admin-stopped ] || return 1; if [ -f /etc/s-box/firewall.json ]; then /usr/bin/sing_box --firewall-sync; fi; }
pidfile="/var/run/sing-box.pid"' > /etc/init.d/sing-box || return 1
chmod +x /etc/init.d/sing-box && singbox_require_owned || return 1
[[ ! -e /etc/s-box/admin-stopped ]] || return 0
rc-update add sing-box default || return 1
rc-service sing-box restart 8>&- 9>&-
else
cat > /etc/systemd/system/sing-box.service <<EOF || return 1
[Unit]
ConditionPathExists=!/etc/s-box/admin-stopped
After=network.target nss-lookup.target firewalld.service nftables.service ufw.service
[Service]
User=root
WorkingDirectory=/root
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_BIND_SERVICE CAP_NET_RAW
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE CAP_NET_RAW
ExecStartPre=/bin/sh -c 'if test -f /etc/s-box/firewall.json; then /usr/bin/sing_box --firewall-sync; fi'
ExecStart=/etc/s-box/sing-box run -c /etc/s-box/sb.json -c /etc/s-box/sb_extra.json
ExecReload=/bin/kill -HUP \$MAINPID
Restart=on-failure
RestartSec=10
LimitNOFILE=infinity
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && singbox_require_owned || return 1
[[ ! -e /etc/s-box/admin-stopped ]] || return 0
systemctl enable sing-box >/dev/null 2>&1 || return 1
systemctl restart sing-box
fi
}

ipuuid(){
local selected_ip
if command -v apk >/dev/null 2>&1; then
status_cmd="rc-service sing-box status"
status_pattern="started"
else
status_cmd="systemctl is-active sing-box"
status_pattern="active"
fi
if [[ -n $($status_cmd 2>/dev/null | grep -w "$status_pattern") && -f '/etc/s-box/sb.json' ]]; then
if [[ ${1:-} == select ]] && {
ip -o link show dev wgcf 2>/dev/null | grep -qE '<([^>]*,)?UP[,>]' || pgrep -x warp-go >/dev/null 2>&1
}; then
red "检测到活动的系统级WARP，公网探测可能为WARP出口，已保留原节点地址；请关闭全局WARP后重新进入主菜单再选择配置输出"
return 1
fi
# Selected egress observations are not evidence for the server advertised to clients.
if [[ ${1:-} != verified && ( ${1:-} != select || -e /etc/s-box/egress.json ) ]]; then v4v6; fi
if [[ ${1:-} == select ]]; then
    warpcheck
    [[ $wgcfv4 == off ]] || v4=
    [[ $wgcfv6 == off ]] || v6=
fi
if [[ -z $v4 && -z $v6 ]]; then
red "公网IP探测未成功，已保留原节点地址"
return 1
fi
if [[ ${1:-} == select || ( -n $v4 && -n $v6 ) ]]; then
green "调整IPv4/IPv6配置输出"
yellow "1：使用IPv4配置输出 (回车默认)"
yellow "2：使用IPv6配置输出"
readp "请选择【1-2】：" menu || return 1
case "$menu" in
""|1) selected_ip=$v4 ;;
2) selected_ip=$v6 ;;
*) red "输入无效，请选择1或2；已保留原节点地址"; return 1 ;;
esac
[[ -n $selected_ip ]] || { red "所选地址族的公网IP探测未成功，已保留原节点地址"; return 1; }
else
yellow "仅确认一个地址族的公网IP，使用已探测到的地址生成配置"
selected_ip=${v4:-$v6}
fi
if [[ ${1:-} == select ]]; then
    config_locked sync_node_addresses "$v4" "$v6" || return 1
fi
server_ip=$selected_ip
[[ $selected_ip != *:* ]] || server_ip="[$selected_ip]"
server_ipcl=$selected_ip
printf '%s\n' "$server_ip" > /etc/s-box/server_ip.log
printf '%s\n' "$server_ipcl" > /etc/s-box/server_ipcl.log
else
red "Sing-box服务未运行" && exit
fi
}

wgcfgo(){
local addresses v4 v6
addresses=$(warp_unrouted_ips node) || return 1
v4=${addresses%%$'\n'*}; v6=${addresses#*$'\n'}; v4=${v4#-}; v6=${v6#-}
config_locked sync_node_addresses "$v4" "$v6" || return 1
if [[ -s /etc/s-box/server_ipcl.log ]]; then
    local previous
    previous=$(< /etc/s-box/server_ipcl.log)
    if [[ ( $previous == *:* && -z $v6 ) || ( $previous != *:* && -z $v4 ) ]]; then
        red "原节点地址族探测失败，已保留节点地址"; return 1
    fi
fi
ipuuid verified
}

result_vl_vm_hy_tu(){

server_ip=$(cat /etc/s-box/server_ip.log)
server_ipcl=$(cat /etc/s-box/server_ipcl.log)
uuid=$(config_get vless-sb '.users[0].uuid') || return 1
vl_port=$(config_get vless-sb '.listen_port') || return 1
vl_name=$(config_get vless-sb '.tls.server_name') && sub_valid_domain "$vl_name" || return 1
public_key=$(cat /etc/s-box/public.key)
short_id=$(config_get vless-sb '.tls.reality.short_id[0]') || return 1
argo=$(quick_tunnel_hostname)
argogd=
if [[ -e /etc/s-box/sbargoym.log ]]; then
    argogd=$(cat /etc/s-box/sbargoym.log) && sub_valid_domain "$argogd" || return 1
fi
ws_path=$(config_get vmess-sb '.transport.path') || return 1
if [[ ${ws_path,,} == *"${uuid,,}"* ]]; then yellow "旧 WS 路径含凭据；通过 3 → 2 → 3 迁移并刷新所有客户端" >&2; fi
vm_port=$(config_get vmess-sb '.listen_port') || return 1
tls=$(config_get vmess-sb '.tls.enabled') || return 1
vm_name=$(config_get vmess-sb '.tls.server_name') && sub_valid_domain "$vm_name" || return 1
vm_self=false; vm_material=; hy2_self=; tu5_self=; an_self=false; an_name=
load_node_trust /etc/s-box/sb.json || return 1
[[ -n $hy2_self && -n $tu5_self ]] || return 1
[[ $tls != true || -n $vm_material ]] || return 1
if [[ $tls == true && $vm_self == true ]]; then
    vmadd_local=$server_ipcl; vmadd_are_local=$server_ip
elif [[ -e /etc/s-box/cfvmadd_local.txt ]]; then
    vmadd_local=$(cat /etc/s-box/cfvmadd_local.txt) || return 1
    valid_node_address "$vmadd_local" || return 1
    vmadd_are_local=$vmadd_local
else
    vmadd_local=$vm_name; vmadd_are_local=$vm_name
fi
if [[ -e /etc/s-box/cfymjx.txt ]]; then
    local host_override
    host_override=$(cat /etc/s-box/cfymjx.txt) && sub_valid_domain "$host_override" || return 1
    [[ $vm_self == true ]] || vm_name=$host_override
fi
if [[ -f /etc/s-box/cfvmadd_argo.txt ]]; then
vmadd_argo=$(cat /etc/s-box/cfvmadd_argo.txt) && valid_node_address "$vmadd_argo" || return 1
else
vmadd_argo=cloudflare-ech.com
fi
hy2_port=$(config_get hy2-sb '.listen_port') || return 1
hy2_ports=$(firewall_hops hy2-sb) || return 1
if [[ -n $hy2_ports ]]; then
cmhy2pt=$(echo $hy2_ports | tr ':' '-')
hyps="&mport=$cmhy2pt"
sbhy2pt=$(echo "$hy2_ports" | grep -o '[0-9]\+:[0-9]\+' | sed 's/.*/"&"/' | paste -sd,)
else
hyps=
fi
sb_hy2_ip=$server_ip; cl_hy2_ip=$server_ipcl; SHA256=
if [[ $hy2_self == true ]]; then
    SHA256=$(jq -er .fingerprint <<< "$hy2_material") || return 1
    [[ ${1:-} == client-only ]] || printf '%s\n' "$SHA256" > /etc/s-box/SHA256.txt
else
    sb_hy2_ip=$hy2_name; cl_hy2_ip=$hy2_name
fi
tu5_port=$(config_get tuic5-sb '.listen_port') || return 1
sb_tu5_ip=$server_ip; cl_tu5_ip=$server_ipcl
if [[ $tu5_self == false ]]; then sb_tu5_ip=$tu5_name; cl_tu5_ip=$tu5_name; fi
an_port=
if [[ $sbnh != 1.10 ]]; then
    [[ -n $an_name ]] && an_port=$(config_get anytls-sb '.listen_port') || return 1
    sb_an_ip=$server_ip; cl_an_ip=$server_ipcl
    if [[ $an_self == false ]]; then sb_an_ip=$an_name; cl_an_ip=$an_name; fi
fi
}

resvless(){
echo
white "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
vl_link="vless://$uuid@$server_ip:$vl_port?encryption=none&flow=xtls-rprx-vision&security=reality&sni=$vl_name&fp=chrome&pbk=$public_key&sid=$short_id&type=tcp&headerType=none#vl-reality-$hostname"
echo "$vl_link" > /etc/s-box/vl_reality.txt || return 1
red "🚀【 vless-reality-vision 】节点信息如下：" && { [[ ! -t 1 ]] || sleep 2; }
echo
echo "分享链接【v2ran(切换singbox内核)、nekobox、小火箭shadowrocket】"
echo -e "${yellow}$vl_link${plain}"
echo
echo "二维码【v2ran(切换singbox内核)、nekobox、小火箭shadowrocket】"
printf '%s' "$vl_link" | qrencode -o - -t ANSIUTF8
white "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
echo
}

resvmess(){
if [[ $vm_self == true ]]; then blue "自签 WSS 请导入含证书的完整订阅"; return 0; fi
local vm_link
vmess_encode(){
(
set -o pipefail
printf '%s\0%s' "$uuid" "$ws_path" | jq -Rsc --arg add "$1" --arg host "$2" --arg port "$3" --arg name "$4" --arg tls "$5" \
  '
  split("\u0000") as [$uuid,$path] |
  {add:$add,aid:"0",host:$host,id:$uuid,net:"ws",path:$path,port:$port,
   ps:$name,tls:$tls,type:"none",v:"2"} +
  (if $tls == "tls" then {sni:$host,fp:"chrome"} else {} end)
' | base64 -w 0
)
}
if [[ "$tls" = "false" ]]; then
if [[ -n $argo ]] && project_process temporary "$vm_port" status; then
echo
white "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
red "🚀【 vmess-ws(tls)+Argo 】临时节点信息如下(可选择3-8-3，自定义CDN优选地址)：" && { [[ ! -t 1 ]] || sleep 2; }
echo
echo "分享链接【v2rayn、v2rayng、nekobox、小火箭shadowrocket】"
vm_link=$(vmess_encode "$vmadd_argo" "$argo" 443 "vm-argo-$hostname" tls) || return 1
echo -e "${yellow}vmess://$vm_link${plain}"
echo
echo "二维码【v2rayn、v2rayng、nekobox、小火箭shadowrocket】"
echo "vmess://$vm_link" > /etc/s-box/vm_ws_argols.txt || return 1
printf '%s' "vmess://$vm_link" | qrencode -o - -t ANSIUTF8
fi
if [[ -n $argogd ]] && project_process fixed '' status; then
echo
white "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
red "🚀【 vmess-ws(tls)+Argo 】固定节点信息如下 (可选择3-8-3，自定义CDN优选地址)：" && { [[ ! -t 1 ]] || sleep 2; }
echo
echo "分享链接【v2rayn、v2rayng、nekobox、小火箭shadowrocket】"
vm_link=$(vmess_encode "$vmadd_argo" "$argogd" 443 "vm-argo-$hostname" tls) || return 1
echo -e "${yellow}vmess://$vm_link${plain}"
echo
echo "二维码【v2rayn、v2rayng、nekobox、小火箭shadowrocket】"
echo "vmess://$vm_link" > /etc/s-box/vm_ws_argogd.txt || return 1
printf '%s' "vmess://$vm_link" | qrencode -o - -t ANSIUTF8
fi
blue "明文 WS 仅用于回环隧道后端；启用 Argo 后分发公网 WSS 节点"
else
echo
white "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
red "🚀【 vmess-ws-tls 】节点信息如下 (建议选择3-8-1，设置为CDN优选节点)：" && { [[ ! -t 1 ]] || sleep 2; }
echo
echo "分享链接【v2rayn、v2rayng、nekobox、小火箭shadowrocket】"
vm_link=$(vmess_encode "$vmadd_are_local" "$vm_name" "$vm_port" "vm-ws-tls-$hostname" tls) || return 1
echo -e "${yellow}vmess://$vm_link${plain}"
echo
echo "二维码【v2rayn、v2rayng、nekobox、小火箭shadowrocket】"
echo "vmess://$vm_link" > /etc/s-box/vm_ws_tls.txt || return 1
printf '%s' "vmess://$vm_link" | qrencode -o - -t ANSIUTF8
fi
white "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
echo
}


reshy2(){
echo
white "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
if [[ $hy2_self == true ]]; then blue "Hysteria2 单节点客户端须支持 pinSHA256；也可使用携带节点证书的完整订阅。"; fi
hy2_link="hysteria2://$uuid@$sb_hy2_ip:$hy2_port?security=tls&alpn=h3&insecure=0$hyps&sni=$hy2_name${SHA256:+&pinSHA256=$SHA256}#hy2-$hostname"
echo "$hy2_link" > /etc/s-box/hy2.txt || return 1
red "🚀【 Hysteria-2 】节点信息如下：" && { [[ ! -t 1 ]] || sleep 2; }
echo
echo "分享链接【v2rayn、v2rayng、nekobox、小火箭shadowrocket】"
echo -e "${yellow}$hy2_link${plain}"
echo
echo "二维码【v2rayn、v2rayng、nekobox、小火箭shadowrocket】"
printf '%s' "$hy2_link" | qrencode -o - -t ANSIUTF8
white "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
echo
}

restu5(){
if [[ $tu5_self == true ]]; then blue "自签 TUIC 请导入含证书的完整订阅"; return 0; fi
echo
white "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
tuic5_link="tuic://$uuid:$uuid@$sb_tu5_ip:$tu5_port?congestion_control=bbr&udp_relay_mode=native&alpn=h3&sni=$tu5_name&insecure=0&allowInsecure=0#tu5-$hostname"
echo "$tuic5_link" > /etc/s-box/tuic5.txt || return 1
red "🚀【 Tuic-v5 】节点信息如下：" && { [[ ! -t 1 ]] || sleep 2; }
echo
echo "分享链接【v2rayn、nekobox、小火箭shadowrocket】"
echo -e "${yellow}$tuic5_link${plain}"
echo
echo "二维码【v2rayn、nekobox、小火箭shadowrocket】"
printf '%s' "$tuic5_link" | qrencode -o - -t ANSIUTF8
white "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
echo
}

resan(){
if [[ $an_self == true ]]; then blue "自签 AnyTLS 请导入含证书的完整订阅"; return 0; fi
echo
white "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
an_link="anytls://$uuid@$sb_an_ip:$an_port?&sni=$an_name&allowInsecure=0&insecure=0#anytls-$hostname"
echo "$an_link" > /etc/s-box/an.txt || return 1
red "🚀【 Anytls】节点信息如下：" && { [[ ! -t 1 ]] || sleep 2; }
echo
echo "分享链接【v2rayn、小火箭shadowrocket】"
echo -e "${yellow}$an_link${plain}"
echo
echo "二维码【v2rayn、nekobox、小火箭shadowrocket】"
printf '%s' "$an_link" | qrencode -o - -t ANSIUTF8
white "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
echo
}

client_warp_validate(){
python3 - "$1" <<'PY'
import base64, ipaddress, json, re, sys
try:
    with open(sys.argv[1], encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict) or type(data.get("enabled")) is not bool:
        raise ValueError("enabled 必须为 true 或 false")
    fields = ("enabled", "server", "port", "ip", "ipv6", "private_key", "public_key", "reserved", "mtu")
    data = {key: data[key] for key in fields if key in data}
    if data["enabled"]:
        server = data.get("server", "")
        if not isinstance(server, str) or not server or len(server) > 253:
            raise ValueError("WARP 入口地址无效")
        try:
            ipaddress.ip_address(server)
        except ValueError:
            if ":" in server or re.fullmatch(r"[0-9.]+", server) or not all(
                re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", x)
                for x in server.split(".")
            ):
                raise ValueError("WARP 入口必须为 IP 或域名，不含端口")
        if type(data.get("port")) is not int or not 1 <= data["port"] <= 65535:
            raise ValueError("WARP 端口无效")
        if not isinstance(data.get("ip"), str):
            raise ValueError("缺少隧道 IPv4")
        ipaddress.IPv4Address(data["ip"])
        if "ipv6" in data and not isinstance(data["ipv6"], str):
            raise ValueError("隧道 IPv6 必须为字符串")
        if data.get("ipv6"):
            ipaddress.IPv6Address(data["ipv6"])
        else:
            data.pop("ipv6", None)
        for key in ("private_key", "public_key"):
            if not isinstance(data.get(key), str) or len(base64.b64decode(data[key], validate=True)) != 32:
                raise ValueError("WireGuard 密钥必须为 base64 编码的 32 字节")
        reserved = data.setdefault("reserved", [0, 0, 0])
        if not isinstance(reserved, list) or len(reserved) != 3 or any(type(x) is not int or not 0 <= x <= 255 for x in reserved):
            raise ValueError("reserved 必须为三个 0 到 255 的整数")
        mtu = data.setdefault("mtu", 1280)
        if type(mtu) is not int or not 1280 <= mtu <= 1420:
            raise ValueError("MTU 必须在 1280 到 1420 之间")
    print(json.dumps(data, separators=(",", ":")))
except (OSError, ValueError, TypeError) as e:
    print("客户端 WARP 参数无效：" + str(e), file=sys.stderr)
    sys.exit(1)
PY
}

client_warp_read(){
local config=${client_warp_config_file:-/etc/s-box/client_warp.json}
client_warp_json='{"enabled":false}'
client_warp_dialer= client_warp_node=
if [[ -e $config ]]; then
    client_warp_json=$(client_warp_validate "$config") || return 1
elif grep -Fq '# client-warp: enabled' /etc/s-box/clmi.yaml 2>/dev/null; then
    printf '%s\n' '客户端 WARP 状态文件缺失，保留原订阅；请通过3→12导入参数或明确关闭。' >&2
    return 1
fi
if [[ $(jq -r '.enabled' <<< "$client_warp_json") == true ]]; then
    client_warp_dialer='  dialer-proxy: WARP-前置'
    client_warp_node=$(jq -cr '
        {name:"WARP-前置",type:"wireguard",server:.server,port:.port,ip:.ip,
         "private-key":.private_key,"public-key":.public_key,reserved:.reserved,
         mtu:.mtu,udp:true,"allowed-ips":["0.0.0.0/0","::/0"],
         "remote-dns-resolve":true,dns:["1.1.1.1","8.8.8.8"]}
         + (if .ipv6 then {ipv6:.ipv6} else {} end) | "- " + tojson
    ' <<< "$client_warp_json") || return 1
    client_warp_node=$'# client-warp: enabled\n'"$client_warp_node"
fi
}

client_warp_register(){
(
local destination=$1 endpoint_family=${2:-4} register_dir public_key
umask 077
register_dir=$(mktemp -d /etc/s-box/.client-warp-register.XXXXXX) || exit 1
trap 'rm -rf -- "$register_dir"' EXIT
trap 'exit 1' HUP INT TERM
openssl genpkey -algorithm X25519 -out "$register_dir/key.pem" 2>/dev/null || exit 1
(set -o pipefail; openssl pkey -in "$register_dir/key.pem" -outform DER 2>/dev/null | tail -c 32 | base64 > "$register_dir/private.b64") || exit 1
public_key=$(set -o pipefail; openssl pkey -in "$register_dir/key.pem" -pubout -outform DER 2>/dev/null | tail -c 32 | base64 | tr -d '\r\n') || exit 1
jq -n --arg key "$public_key" --arg tos "$(date -u +'%Y-%m-%dT%H:%M:%S.000Z')" \
    '{key:$key,tos:$tos,key_type:"curve25519",tunnel_type:"wireguard",locale:"en_US",
      fcm_token:"",install_id:"",serial_number:"",os_version:"16.0.0",model:"PC"}' > "$register_dir/request.json" || exit 1
curl --noproxy '*' -fsS --connect-timeout 3 --max-time 15 \
    -H 'CF-Client-Version: a-6.38.9-5641' \
    -H 'User-Agent: 1.1.1.1/6.38.9-5641 (Android 16.0.0)' \
    -H 'Content-Type: application/json' --data-binary "@$register_dir/request.json" \
    'https://api.cloudflareclient.com/v0a5641/reg' -o "$register_dir/response.json" || exit 1
python3 - "$register_dir/response.json" "$register_dir/private.b64" "$endpoint_family" > "$register_dir/candidate.json" <<'PY'
import base64, json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        response = json.load(f)
    response = response.get("result", response)
    if response.get("tunnel_type", "wireguard") != "wireguard":
        raise ValueError("注册响应不是 WireGuard 配置")
    config = response["config"]
    addresses = config["interface"]["addresses"]
    peer = config["peers"][0]
    endpoint = peer["endpoint"]
    if sys.argv[3] == "6":
        server = endpoint.get("v6") or endpoint.get("host")
    else:
        server = endpoint.get("v4") or endpoint.get("host") or endpoint.get("v6")
    port = (endpoint.get("ports") or [2408])[0]
    if not isinstance(server, str) or not server:
        raise ValueError("注册响应缺少入口")
    if server.startswith("["):
        server, suffix = server[1:].split("]", 1)
        if suffix:
            port = int(suffix[1:] if suffix.startswith(":") else suffix)
    elif server.count(":") == 1:
        server, port = server.rsplit(":", 1)
        port = int(port)
    with open(sys.argv[2], encoding="ascii") as f:
        private_key = f.read().strip()
    reserved = list(base64.b64decode(config["client_id"], validate=True))
    print(json.dumps({"enabled":True,"server":server,"port":port,
        "ip":addresses["v4"],"ipv6":addresses.get("v6", ""),
        "private_key":private_key,"public_key":peer["public_key"],"reserved":reserved,"mtu":1280}))
except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
    print("WARP 注册响应不完整；未使用共享凭据，请重试或导入有效参数。", file=sys.stderr)
    sys.exit(1)
PY
[[ $? == 0 ]] || exit 1
client_warp_validate "$register_dir/candidate.json" > "$register_dir/validated.json" || exit 1
chmod 600 "$register_dir/validated.json" && mv -f -- "$register_dir/validated.json" "$destination"
)
}

client_warp_apply(){
config_locked client_warp_apply_locked "$@"
}

client_warp_apply_locked(){
(
local candidate=$1 apply_dir client_warp_config_file sub_switching=0
umask 077
exec 9>/etc/s-box/subscription.lock || exit 1
flock -x 9 || exit 1
apply_dir=$(mktemp -d /etc/s-box/.client-warp.XXXXXX) || exit 1
trap 'rm -rf -- "$apply_dir"' EXIT
trap 'exit 1' HUP INT TERM
client_warp_config_file=$apply_dir/client_warp.json
client_warp_validate "$candidate" > "$client_warp_config_file" || exit 1
result_vl_vm_hy_tu client-only && sb_client "$apply_dir" || exit 1
chmod 600 "$client_warp_config_file" || exit 1
sub_publish_current "$apply_dir" client_warp.json clmi.yaml
)
}

client_warp_egress_status(){
local family source trace detected local_ip rules
rules=$(jq -r '
    [.route.rules[]? | select((.outbound // "") | test("warp|wireguard|socks")) |
      ((.domain_suffix // []) + (.geosite // []))[] | select(. != "yg_kkk")] | length' /etc/s-box/sb.json 2>/dev/null)
if [[ $rules =~ ^[0-9]+$ && $rules != 0 ]]; then
    yellow "服务端仍有WARP/SOCKS域名分流：前置不会改变它，原生出口需在菜单5清理对应规则。"
elif [[ ! $rules =~ ^[0-9]+$ ]]; then
    yellow "无法读取服务端分流规则，原生出口状态未知。"
fi
for family in 4 6; do
    source=$(jq -r --arg key "inet${family}_bind_address" \
        '.outbounds[]? | select(.tag == "direct" and .type == "direct" and (.detour // "") == "") | .[$key] // ""' /etc/s-box/sb.json 2>/dev/null)
    local -a bind=()
    [[ -z $source ]] || bind=(--interface "$source")
    trace=$(curl -q -"$family" --noproxy '*' -fsS --connect-timeout 2 --max-time 4 \
        "${bind[@]}" -w '\nlocal_ip=%{local_ip}\n' https://www.cloudflare.com/cdn-cgi/trace 2>/dev/null) || trace=
    detected=$(printf '%s\n' "$trace" | sed -n 's/^warp=//p' | tr -d '\r')
    local_ip=$(printf '%s\n' "$trace" | sed -n 's/^local_ip=//p' | tr -d '\r')
    if [[ -n $source && $local_ip != "$source" ]]; then detected=; fi
    case "$detected" in
    on|plus) yellow "IPv${family}：检测到WARP出口；如需原生出口，请通过菜单13取消系统全局接管。" ;;
    off) green "IPv${family}：本次直连探测未经过WARP（仍需验证实际代理出口）。" ;;
    *) yellow "IPv${family}：出口探测未成功，状态未知。" ;;
    esac
done
}

client_warp_menu(){
(
local choice action import_file endpoint port candidate current
local client_warp_json client_warp_node client_warp_dialer
candidate=$(mktemp /etc/s-box/.client-warp-menu.XXXXXX) || exit 1
chmod 600 "$candidate" || exit 1
trap 'rm -f -- "$candidate"' EXIT
trap 'exit 1' HUP INT TERM
while true; do
    if client_warp_read; then
        current=$(jq -r 'if .enabled then "开启" else "关闭" end' <<< "$client_warp_json")
    else
        current=参数损坏或缺失
    fi
    green "客户端 WARP 前置（单客户端 / Clash-Mihomo）：$current"
    yellow "只作用于clmi.yaml完整订阅；二维码、jhsub.txt、sbox.json不含前置。"
    readp "1：启用WARP前置\n2：关闭WARP前置\n3：重新申请 / 导入WARP参数\n4：修改WARP入口IP或域名及端口\n0：返回\n请选择【0-4】：" choice || exit 1
    case "$choice" in
    0) exit 0 ;;
    1)
        if [[ -s /etc/s-box/client_warp.json ]] && ! jq -e '.enabled == false and .private_key == null' /etc/s-box/client_warp.json >/dev/null 2>&1; then
            jq '.enabled=true' /etc/s-box/client_warp.json > "$candidate" || continue
        else
            yellow "申请客户端专用WARP参数，单次请求限时15秒……"
            client_warp_register "$candidate" || { red "注册失败，未启用；可选择3导入有效参数。"; continue; }
        fi ;;
    2)
        if ! jq '.enabled=false' /etc/s-box/client_warp.json > "$candidate" 2>/dev/null; then
            printf '%s\n' '{"enabled":false}' > "$candidate"
        fi ;;
    3)
        readp "1：重新申请独立参数\n2：导入client_warp.json文件\n请选择【1-2】：" action || exit 1
        case "$action" in
        1) client_warp_register "$candidate" || { red "注册失败，已保留旧配置。"; continue; } ;;
        2)
            readp "请输入参数JSON文件的绝对路径：" import_file || exit 1
            [[ $import_file == /* && -f $import_file ]] || { red "文件不存在或不是绝对路径。"; continue; }
            jq '.enabled=true' "$import_file" > "$candidate" || continue ;;
        *) continue ;;
        esac ;;
    4)
        [[ -s /etc/s-box/client_warp.json ]] || { red "请先申请或导入参数。"; continue; }
        readp "WARP入口IP或域名（不含端口）：" endpoint || exit 1
        readp "WARP入口端口：" port || exit 1
        [[ $port =~ ^[1-9][0-9]{0,4}$ ]] && (( port <= 65535 )) || { red "端口无效。"; continue; }
        jq --arg server "$endpoint" --argjson port "$port" '.server=$server | .port=$port | .enabled=true' \
            /etc/s-box/client_warp.json > "$candidate" || continue ;;
    *) red "输入无效，请选择0到4。"; continue ;;
    esac
    if client_warp_apply "$candidate"; then
        green "本地Mihomo配置已更新。请刷新并重载clmi.yaml订阅，关闭旧连接后验证。"
        yellow "已配置的本地HTTP/HTTPS订阅随文件更新生效；二维码和聚合链接不含前置。"
        if [[ -s /etc/s-box/gitlab-publisher ]]; then
            yellow "如已配置 GitLab 发布，请用 9 → 2 同步远端。"
        fi
        if [[ $(jq -r '.enabled' "$candidate") == true ]]; then
            yellow "客户端WARP连通性尚未验证；检查服务端出口（最多8秒）……"
            client_warp_egress_status
        fi
    else
        red "配置生成或保存失败，已保留旧状态及订阅。"
    fi
done
)
}

sb_client(){
(
local publish_dir sb_all sb_any1 sb_any2 cl_all cl_any1 cl_any2 sb_hy2ports vm_listen_port extra_data extra_piece
local client_destination=${1:-/etc/s-box} rules_revision=sing
if [[ -e /etc/s-box/client-rules-revision ]]; then
    rules_revision=$(cat /etc/s-box/client-rules-revision) || exit 1
    [[ $rules_revision =~ ^[0-9a-f]{40}$ ]] || { red "地域规则版本必须为已审查的 40 位小写提交号"; exit 1; }
fi
local cert_json fingerprint hy2_sb_cert= tu5_sb_cert= an_sb_cert= hy2_cl_pin= tu5_cl_pin= an_cl_pin=
local proto material_var self_var pem pin
for proto in hy2 tu5 an; do
    self_var=${proto}_self; material_var=${proto}_material
    [[ ${!self_var} == true ]] || continue
    pem=$(jq -ce .certificate <<< "${!material_var}") && pin=$(jq -er .fingerprint <<< "${!material_var}") || exit 1
    printf -v "${proto}_sb_cert" '"certificate":%s,' "$pem"
    printf -v "${proto}_cl_pin" '  fingerprint: "%s"' "$pin"
done
if [[ $vm_self == true ]]; then
    cert_json=$(jq -ce .certificate <<< "$vm_material") && fingerprint=$(jq -er .fingerprint <<< "$vm_material") || exit 1
fi
local client_warp_json client_warp_dialer client_warp_node
client_warp_read || exit 1
publish_dir=$(mktemp -d /etc/s-box/.client.XXXXXX) || exit 1
trap 'rm -rf -- "$publish_dir"' EXIT
trap 'exit 1' HUP INT TERM

sbhy2ports(){
if [[ -n $hy2_ports ]]; then
    cat <<EOF
  "server_ports": [ $sbhy2pt ],
EOF
fi
}

sbany1(){
  if [[ "$sbnh" != "1.10" ]]; then
    echo "\"anytls-$hostname\","
  fi
}
clany1(){
  if [[ "$sbnh" != "1.10" ]]; then
    echo "- anytls-$hostname"
  fi
}
sbany2(){
  if [[ "$sbnh" != "1.10" ]]; then
    cat <<EOF
         {
            "type": "anytls",
            "tag": "anytls-$hostname",
            "server": "$sb_an_ip",
            "server_port": $an_port,
            "password": "$uuid",
            "idle_session_check_interval": "30s",
            "idle_session_timeout": "30s",
            "min_idle_session": 5,
            "tls": {
                "enabled": true,
                $an_sb_cert
                "insecure": false,
                "server_name": "$an_name"
            }
         },
EOF
  fi
}
clany2(){
  if [[ "$sbnh" != "1.10" ]]; then
    cat <<EOF
- name: anytls-$hostname
$client_warp_dialer
  type: anytls
  server: $cl_an_ip
  port: $an_port
  password: $uuid
  client-fingerprint: chrome
  udp: true
  idle-session-check-interval: 30
  idle-session-timeout: 30
  sni: $an_name
  skip-cert-verify: false
$an_cl_pin
EOF
  fi
}

sball(){
cat <<EOF
{
  "log": {
    "level": "info",
    "timestamp": true
  },
  "http_clients": [
    {
      "tag": "rule-set-direct"
    }
  ],
  "dns": {
    "servers": [
      {
        "type": "fakeip",
        "tag": "fakeip",
        "inet4_range": "198.18.0.0/15",
        "inet6_range": "fc00::/18"
      },
      {
        "type": "https",
        "tag": "dns-cn",
        "server": "223.5.5.5"
      },
      {
        "type": "https",
        "tag": "dns-proxy",
        "server": "dns.google",
        "domain_resolver": "dns-cn",
        "detour": "proxy"
      }
    ],
    "rules": [
      {
        "rule_set": [
          "geosite-cn"
        ],
        "action": "route",
        "server": "dns-cn"
      },
      {
        "query_type": [
          "A",
          "AAAA"
        ],
        "action": "route",
        "server": "fakeip"
      }
    ],
    "final": "dns-proxy",
    "strategy": "prefer_ipv4",
    "cache_capacity": 8192,
    "optimistic": {
      "enabled": true,
      "timeout": "1h"
    },
    "timeout": "10s",
    "reverse_mapping": true
  },
  "inbounds": [
    {
      "type": "tun",
      "tag": "tun-in",
      "address": [
        "172.19.0.1/30",
        "fdfe:dcba:9876::1/126"
      ],
      "auto_route": true,
      "strict_route": true,
      "mtu": 1420
    }
  ],
  "route": {
    "default_http_client": "rule-set-direct",
    "default_domain_resolver": "dns-cn",
    "auto_detect_interface": true,
    "rule_set": [
      {
        "tag": "geosite-cn",
        "type": "remote",
        "format": "binary",
        "update_interval": "1d",
        "url": "https://cdn.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@$rules_revision/geo/geosite/cn.srs"
      },
      {
        "tag": "geoip-cn",
        "type": "remote",
        "format": "binary",
        "update_interval": "1d",
        "url": "https://cdn.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@$rules_revision/geo/geoip/cn.srs"
      }
    ],
    "rules": [
      {"inbound": ["tun-in"], "action": "sniff"},
      {"inbound": ["tun-in"], "port": 53, "action": "hijack-dns"},
      {
        "inbound": [
          "tun-in"
        ],
        "protocol": [
          "dns"
        ],
        "action": "hijack-dns"
      },
      {
        "clash_mode": "Global",
        "action": "route",
        "outbound": "proxy"
      },
      {
        "ip_is_private": true,
        "action": "route",
        "outbound": "direct"
      },
      {
        "rule_set": [
          "geosite-cn"
        ],
        "action": "route",
        "outbound": "direct"
      },
      {
        "rule_set": [
          "geoip-cn"
        ],
        "action": "route",
        "outbound": "direct"
      },
      {
        "clash_mode": "Direct",
        "action": "route",
        "outbound": "direct"
      },
      {
        "network": [
          "tcp",
          "udp"
        ],
        "port": 853,
        "action": "reject"
      }
    ],
    "final": "proxy"
  },
  "experimental": {
    "cache_file": {
      "enabled": true,
      "store_dns": true
    }
  },
  "outbounds": [
    {
      "type": "vless",
      "tag": "vless-$hostname",
      "server": "$server_ipcl",
      "server_port": $vl_port,
      "uuid": "$uuid",
      "flow": "xtls-rprx-vision",
      "tls": {
        "enabled": true,
        "server_name": "$vl_name",
        "utls": {
          "enabled": true,
          "fingerprint": "chrome"
        },
      "reality": {
          "enabled": true,
          "public_key": "$public_key",
          "short_id": "$short_id"
        }
      }
    },


    {
        "type": "hysteria2",
        "tag": "hy2-$hostname",
        "server": "$cl_hy2_ip",
        "server_port": $hy2_port,
$sb_hy2ports
        "password": "$uuid",
        "tls": {
            "enabled": true,
            "server_name": "$hy2_name",
            $hy2_sb_cert
                "insecure": false,
            "alpn": [
                "h3"
            ]
        }
    },
        {
            "type":"tuic",
            "tag": "tuic5-$hostname",
            "server": "$cl_tu5_ip",
            "server_port": $tu5_port,
            "uuid": "$uuid",
            "password": "$uuid",
            "congestion_control": "bbr",
            "udp_relay_mode": "native",
            "udp_over_stream": false,
            "zero_rtt_handshake": false,
            "heartbeat": "10s",
            "tls":{
                "enabled": true,
                "server_name": "$tu5_name",
                $tu5_sb_cert
                "insecure": false,
                "alpn": [
                    "h3"
                ]
            }
        },
EOF
}

clall(){
cat <<EOF
port: 7890
allow-lan: false
lan-allowed-ips: ["127.0.0.1/32", "::1/128"]
mode: rule
log-level: info
unified-delay: true
dns:
  enable: true 
  listen: "127.0.0.1:1053"
  ipv6: true
  prefer-h3: false
  respect-rules: true
  use-system-hosts: false
  cache-algorithm: "arc"
  enhanced-mode: "fake-ip"
  fake-ip-range: "198.18.0.1/16"
  fake-ip-filter:
    - "+.lan"
    - "+.local"
    - "+.msftconnecttest.com"
    - "+.msftncsi.com"
    - "localhost.ptlogin2.qq.com"
    - "localhost.sec.qq.com"
    - "+.in-addr.arpa"
    - "+.ip6.arpa"
    - "time.*.com"
    - "time.*.gov"
    - "pool.ntp.org"
    - "localhost.work.weixin.qq.com"
  default-nameserver: ["https://223.5.5.5/dns-query", "https://1.1.1.1/dns-query"]
  nameserver:
    - "https://1.1.1.1/dns-query"
    - "https://8.8.8.8/dns-query"
  nameserver-policy:
    "geosite:cn":
      - "https://223.5.5.5/dns-query"
      - "https://doh.pub/dns-query"
  proxy-server-nameserver:
    - "https://223.5.5.5/dns-query"
    - "https://doh.pub/dns-query"

proxies:
$client_warp_node
- name: vless-reality-vision-$hostname               
$client_warp_dialer
  type: vless
  server: $server_ipcl                           
  port: $vl_port                                
  uuid: $uuid   
  network: tcp
  udp: true
  tls: true
  flow: xtls-rprx-vision
  servername: $vl_name                 
  reality-opts: 
    public-key: $public_key    
    short-id: $short_id                      
  client-fingerprint: chrome                  

- name: hysteria2-$hostname                            
$client_warp_dialer
  type: hysteria2                                      
  server: $cl_hy2_ip                               
  port: $hy2_port
  ports: $cmhy2pt
  password: $uuid                          
  alpn:
    - h3
  sni: $hy2_name                               
  skip-cert-verify: false
$hy2_cl_pin
  fast-open: true

- name: tuic5-$hostname                            
$client_warp_dialer
  server: $cl_tu5_ip                      
  port: $tu5_port                                    
  type: tuic
  uuid: $uuid       
  password: $uuid   
  alpn: [h3]
  disable-sni: false
  reduce-rtt: true
  udp-relay-mode: native
  congestion-controller: bbr
  sni: $tu5_name                                
  skip-cert-verify: false
$tu5_cl_pin
EOF
}

tls=$(set -o pipefail; config_get vmess-sb '.tls.enabled') || exit 1
vm_listen_port=$(set -o pipefail; config_get vmess-sb '.listen_port') || exit 1
sb_hy2ports=$(sbhy2ports) || exit 1
sb_all=$(sball) || exit 1
sb_any1=$(sbany1) || exit 1
sb_any2=$(sbany2) || exit 1
cl_all=$(clall) || exit 1
cl_any1=$(clany1) || exit 1
cl_any2=$(clany2) || exit 1
if [[ -s /etc/s-box/sb_extra.json ]]; then
extra_data=$(extra_client_data) || exit 1
extra_piece=$(printf '%s' "$extra_data" | jq -r '.singbox[] | (.tag|tojson) + ","') || exit 1
sb_any1+=$'\n'"$extra_piece"
extra_piece=$(printf '%s' "$extra_data" | jq -cr '.singbox[] | tojson + ","') || exit 1
sb_any2+=$'\n'"$extra_piece"
extra_piece=$(printf '%s' "$extra_data" | jq -r '.mihomo[] | "    - " + (.name|tojson)') || exit 1
cl_any1+=$'\n'"$extra_piece"
extra_piece=$(printf '%s' "$extra_data" | jq -cr --arg dialer "$client_warp_dialer" \
    '.mihomo[] | if $dialer != "" then . + {"dialer-proxy":"WARP-前置"} else . end | "- " + tojson') || exit 1
cl_any2+=$'\n'"$extra_piece"
fi
# Public client nodes always use WSS. Plain WS is only the local tunnel origin.
local vm_server vm_host vm_tag vm_cl_name vm_node
local -a vm_nodes=()
if [[ $tls == true ]]; then
    vm_nodes+=(direct)
else
    if [[ -n $argo ]] && project_process temporary "$vm_listen_port" status; then vm_nodes+=(temporary); fi
    if [[ -n $argogd ]] && project_process fixed '' status; then vm_nodes+=(fixed); fi
fi
for vm_node in "${vm_nodes[@]}"; do
    case "$vm_node" in
        direct) vm_server=$vmadd_local; vm_host=$vm_name; vm_tag=vmess-$hostname; vm_cl_name=vmess-ws-$hostname ;;
        temporary) vm_server=$vmadd_argo; vm_host=$argo; vm_tag=vmess-tls-argo临时-$hostname; vm_cl_name=$vm_tag ;;
        fixed) vm_server=$vmadd_argo; vm_host=$argogd; vm_tag=vmess-tls-argo固定-$hostname; vm_cl_name=$vm_tag ;;
    esac
    local node_port=443
    [[ $vm_node != direct ]] || node_port=$vm_port
    extra_piece=$(printf '%s\0%s' "$uuid" "$ws_path" | jq -Rsc --arg server "$vm_server" --arg host "$vm_host" --arg name "$vm_tag" \
        --argjson port "$node_port" --argjson trust "${cert_json:-null}" --arg self "$vm_self" \
        'split("\u0000") as [$uuid,$path] |
         {type:"vmess",tag:$name,server:$server,server_port:$port,uuid:$uuid,security:"auto",
          tls:({enabled:true,server_name:$host,utls:{enabled:true,fingerprint:"chrome"}} +
               (if $self=="true" then {certificate:$trust} else {} end)),
          transport:{type:"ws",path:$path,headers:{Host:[$host]}}} | tojson + ","' -r) || exit 1
    sb_any2+=$'\n'"$extra_piece"
    extra_piece=$(jq -nr --arg name "$vm_tag" '$name|tojson + ","') || exit 1
    sb_any1+=$'\n'"$extra_piece"
    extra_piece=$(printf '%s\0%s' "$uuid" "$ws_path" | jq -Rsc --arg server "$vm_server" --arg host "$vm_host" --arg name "$vm_cl_name" \
        --argjson port "$node_port" --arg dialer "$client_warp_dialer" --arg self "$vm_self" --arg pin "$fingerprint" \
        'split("\u0000") as [$uuid,$path] |
         {type:"vmess",name:$name,server:$server,port:$port,uuid:$uuid,alterId:0,cipher:"auto",udp:true,
          tls:true,network:"ws",servername:$host,"ws-opts":{path:$path,headers:{Host:$host}}}
          + (if $self=="true" then {fingerprint:$pin} else {} end)
          + (if $dialer!="" then {"dialer-proxy":"WARP-前置"} else {} end) | "- " + tojson' -r) || exit 1
    cl_any2+=$'\n'"$extra_piece"
    extra_piece=$(jq -nr --arg name "$vm_cl_name" '"    - " + ($name|tojson)') || exit 1
    cl_any1+=$'\n'"$extra_piece"
done
cat > "$publish_dir/sbox.json" <<EOF || exit 1
$sb_all
$sb_any2
        {
            "tag": "proxy",
            "type": "selector",
			"default": "auto",
            "outbounds": [
        "auto",
        "vless-$hostname",
$sb_any1
        "hy2-$hostname",
        "tuic5-$hostname"
            ]
        },
        {
            "tag": "auto",
            "type": "urltest",
            "outbounds": [
        "vless-$hostname",
$sb_any1
        "hy2-$hostname",
        "tuic5-$hostname"
            ],
            "url": "https://www.gstatic.com/generate_204",
            "interval": "10m",
            "tolerance": 30,
            "idle_timeout": "30m"
        },
        {
            "type": "direct",
            "tag": "direct"
        }
    ]
}
EOF

cat > "$publish_dir/clmi.yaml" <<EOF || exit 1
$cl_all

$cl_any2

proxy-groups:
- name: 负载均衡
  type: load-balance
  url: https://www.gstatic.com/generate_204
  interval: 300
  strategy: round-robin
  proxies:
    - vless-reality-vision-$hostname                              
    - hysteria2-$hostname
    - tuic5-$hostname
    $cl_any1

- name: 自动选择
  type: url-test
  url: https://www.gstatic.com/generate_204
  interval: 300
  tolerance: 50
  proxies:
    - vless-reality-vision-$hostname                              
    - hysteria2-$hostname
    - tuic5-$hostname
    $cl_any1
    
- name: 🌍选择代理节点
  type: select
  proxies:
    - 负载均衡                                         
    - 自动选择
    - DIRECT
    - vless-reality-vision-$hostname                              
    - hysteria2-$hostname
    - tuic5-$hostname
    $cl_any1
rules:
  - GEOIP,LAN,DIRECT
  - GEOSITE,CN,DIRECT
  - GEOIP,CN,DIRECT
  - MATCH,🌍选择代理节点
EOF
if [[ $sbnh == 1.10 ]]; then
    jq '
      del(.http_clients,.route.default_http_client,.route.default_domain_resolver,.experimental.cache_file.store_dns) |
      .experimental.cache_file.store_fakeip=true |
      .dns |= (del(.optimistic,.timeout,.cache_capacity) |
        .servers=[{tag:"fakeip",address:"fakeip"}, {tag:"dns-cn",address:"https://223.5.5.5/dns-query",detour:"direct"},
                  {tag:"dns-proxy",address:"https://dns.google/dns-query",address_resolver:"dns-cn",detour:"proxy"}] |
        .fakeip={enabled:true,inet4_range:"198.18.0.0/15",inet6_range:"fc00::/18"} |
        .rules |= [{outbound:"any",server:"dns-cn"}] + map(del(.action))) |
      .inbounds |= map(.sniff=true) |
      .route.rules |= map(select(.action!="sniff") | if .action=="hijack-dns" then .outbound="dns-out"
                          elif .action=="reject" then .outbound="block" else . end | del(.action)) |
      .outbounds |= (map(del(.server_ports)) + [{type:"dns",tag:"dns-out"},{type:"block",tag:"block"}])
    ' "$publish_dir/sbox.json" > "$publish_dir/legacy.json" &&
        mv -f -- "$publish_dir/legacy.json" "$publish_dir/sbox.json" || exit 1
fi
[ -s "$publish_dir/sbox.json" ] && [ -s "$publish_dir/clmi.yaml" ] || exit 1
jq -e . "$publish_dir/sbox.json" >/dev/null || exit 1
chmod 600 "$publish_dir/sbox.json" "$publish_dir/clmi.yaml" || exit 1
mv -f -- "$publish_dir/sbox.json" "$client_destination/sbox.json" || exit 1
mv -f -- "$publish_dir/clmi.yaml" "$client_destination/clmi.yaml" || exit 1
)
}

cfargo_ym(){
tls=$(config_get vmess-sb '.tls.enabled') || return 1
if [[ "$tls" = "false" ]]; then
echo
yellow "1：添加或者删除Argo临时隧道"
yellow "2：添加或者删除Argo固定隧道"
yellow "0：返回上层"
readp "请选择【0-2】：" menu
if [ "$menu" = "1" ]; then
config_locked cfargo
elif [ "$menu" = "2" ]; then
config_locked cfargoym
else
changeserv
fi
else
yellow "因vmess开启了tls，Argo隧道功能不可用" && sleep 2
fi
}

cloudflaredargo(){
local arch=$cpu
[[ "$arch" != armv7 ]] || arch=arm
if ! release_matches "cloudflared-linux-$arch" /etc/s-box/cloudflared; then
release_fetch "cloudflared-linux-$arch" /etc/s-box/cloudflared && chmod 755 /etc/s-box/cloudflared || return 1
fi
/etc/s-box/cloudflared version
}

argo_unit_owned(){
local unit=/etc/systemd/system/argo.service
if command -v rc-service >/dev/null 2>&1; then
    grep -Eq '^command="/etc/s-box/cloudflared( tunnel)?"$' /etc/init.d/argo 2>/dev/null
else
    grep -Fq 'ExecStart=/etc/s-box/cloudflared tunnel ' "$unit" &&
        [[ $(systemctl show -p FragmentPath --value argo) == "$unit" ]] &&
        [[ $(systemctl show -p ExecStart --value argo) == *"path=/etc/s-box/cloudflared ;"* ]]
fi
}

argo_origin_ready(){
[[ ! -e /etc/s-box/admin-stopped ]] && sub_service status sing-box >/dev/null &&
    [[ $(config_get vmess-sb '.listen') == 127.0.0.1 && $(config_get vmess-sb '.tls.enabled') == false ]] || {
    red "请先启动项目 sing-box 并选择回环 VMess 源站"; return 1
}
}

argo_start_temporary(){
local port
argo_origin_ready && port=$(config_get vmess-sb '.listen_port') && sub_valid_port "$port" &&
    project_process temporary-all '' stop || return 1
nohup /etc/s-box/cloudflared tunnel --url "http://127.0.0.1:$port" --edge-ip-version auto --no-autoupdate --protocol http2 8>&- 9>&- > /etc/s-box/argo.log 2>&1 &
}

cfargoym(){
local fixed_host
echo
if [[ -f /etc/s-box/sbargotoken.log && -f /etc/s-box/sbargoym.log ]]; then
fixed_host=$(cat /etc/s-box/sbargoym.log)
if sub_valid_domain "$fixed_host"; then green "当前 Argo 固定隧道域名：$fixed_host"
else yellow "现有固定隧道域名无效，请重新设置"; fi
green "固定隧道凭据已配置"
fi
echo
green "请进入Cloudflare官网 --- Zero Trust --- 网络 --- 连接器，创建固定隧道"
yellow "1：重置/设置Argo固定隧道域名"
yellow "2：停止Argo固定隧道"
yellow "0：返回上层"
readp "请选择【0-2】：" menu
if [ "$menu" = "1" ]; then
IFS= read -rs -p "输入 Argo 固定隧道 Token：" argotoken || return 1
printf '\n'
readp "输入Argo固定隧道域名: " argoym || return 1
sub_valid_domain "$argoym" && [[ -n $argotoken ]] || { red "固定隧道域名或 Token 无效"; return 1; }
vm_port=$(config_get vmess-sb '.listen_port') || return 1
argo_origin_ready || return 1
echo
yellow "Zero Trust 的固定隧道源站请填写 http://127.0.0.1:$vm_port"
if [[ -e /etc/init.d/argo || -e /etc/systemd/system/argo.service || -n $(systemctl show -p FragmentPath --value argo 2>/dev/null) ]]; then
    argo_unit_owned || { red "已存在其他 argo 服务，请先解决服务名冲突"; return 1; }
fi
cloudflaredargo || return 1
echo
project_process fixed '' stop || return 1
echo
if [[ -n "${argotoken}" && -n "${argoym}" ]]; then
(umask 077; printf '%s' "$argotoken" > /etc/s-box/sbargotoken.log) || return 1
chmod 600 /etc/s-box/sbargotoken.log || return 1
if pidof systemd >/dev/null 2>&1; then
cat > /etc/systemd/system/argo.service <<EOF
[Unit]
Description=argo service
After=network.target sing-box.service
BindsTo=sing-box.service
PartOf=sing-box.service
ConditionPathExists=!/etc/s-box/admin-stopped
[Service]
Type=simple
NoNewPrivileges=yes
TimeoutStartSec=0
ExecStart=/etc/s-box/cloudflared tunnel --no-autoupdate --edge-ip-version auto --protocol http2 run --token-file /etc/s-box/sbargotoken.log
Restart=on-failure
RestartSec=5s
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload >/dev/null 2>&1
systemctl enable argo >/dev/null 2>&1
systemctl restart argo >/dev/null 2>&1 || return 1
elif command -v rc-service >/dev/null 2>&1; then
cat > /etc/init.d/argo <<EOF
#!/sbin/openrc-run
description="argo service"
command="/etc/s-box/cloudflared"
command_args="tunnel --no-autoupdate --edge-ip-version auto --protocol http2 run --token-file /etc/s-box/sbargotoken.log"
pidfile="/run/argo.pid"
command_background="yes"
depend() {
need net sing-box
}
start_pre(){ [ ! -e /etc/s-box/admin-stopped ]; }
EOF
chmod +x /etc/init.d/argo >/dev/null 2>&1
rc-update add argo default >/dev/null 2>&1
rc-service argo restart 8>&- 9>&- >/dev/null 2>&1 || return 1
fi
fi
printf '%s\n' "$argoym" > /etc/s-box/sbargoym.log || return 1

argosh=$(cat /etc/s-box/sbargoym.log 2>/dev/null)
sbshare > /dev/null 2>&1
blue "Argo固定隧道设置完成，固定域名：$argosh"
elif [ "$menu" = "2" ]; then
argo_unit_owned || { red "未识别项目管理的 argo 服务"; return 1; }
argo_stop_fixed || return 1
if command -v rc-service >/dev/null 2>&1; then rm -f /etc/init.d/argo || return 1
else rm -f /etc/systemd/system/argo.service && systemctl daemon-reload || return 1; fi
rm -f -- /etc/s-box/vm_ws_argogd.txt /etc/s-box/sbargoym.log /etc/s-box/sbargotoken.log
sbshare > /dev/null 2>&1
green "Argo固定隧道已停止"
else
return 0
fi
}

cfargo(){
echo
yellow "1：重置Argo临时隧道域名"
yellow "2：停止Argo临时隧道"
yellow "0：返回上层"
readp "请选择【0-2】：" menu
if [ "$menu" = "1" ]; then
green "请稍等……"
cloudflaredargo || return 1
argo_start_temporary || return 1
sleep 20
argo=$(quick_tunnel_hostname)
if [[ -n $argo ]] && status=$(curl -q -sIL --proto '=https' --proto-redir '=https' --max-time 8 -o /dev/null -w '%{http_code}' "https://$argo/") && [[ $status =~ ^(400|404|503)$ ]]; then
sbshare > /dev/null 2>&1
blue "Argo临时隧道申请成功，域名验证有效：$argo" && sleep 2
if command -v apk >/dev/null 2>&1; then
cat > /etc/local.d/alpineargo.start <<'EOF'
#!/bin/bash
sleep 10
exec /usr/bin/sing_box --argo-boot
EOF
chmod +x /etc/local.d/alpineargo.start
rc-update add local default >/dev/null 2>&1
else
local cron_tmp
cron_tmp=$(mktemp) || return 1
crontab -l 2>/dev/null > "$cron_tmp"
sed -i -e '/^@reboot .*\/etc\/s-box\/cloudflared tunnel --url /d' -e '\|^@reboot sleep 10 && /usr/bin/sing_box --argo-boot$|d' "$cron_tmp" || return 1
cat >> "$cron_tmp" <<'ARGO_CRON'
@reboot sleep 10 && /usr/bin/sing_box --argo-boot
ARGO_CRON
crontab "$cron_tmp" >/dev/null 2>&1
rm "$cron_tmp"
fi
else
yellow "Argo临时域名验证暂不可用，请稍后再试"
fi
elif [ "$menu" = "2" ]; then
argo_stop_temporary || return 1
rm -f -- /etc/s-box/vm_ws_argols.txt /etc/s-box/argo.log
sbshare > /dev/null 2>&1
green "Argo临时隧道已停止"
else
return 0
fi
}

instsllsingbox(){
config_locked instsllsingbox_locked "$@"
}

instsllsingbox_locked(){
if [[ -e /etc/s-box/sb.json || $(singbox_unit_state) != absent ]]; then
red "已存在 sing-box 配置、同名服务或无法确认服务状态，已保留原部署"; return 1
fi
ensure_dependencies && setup_tun || return 1
install -d -m 700 /etc/s-box || return 1
v6 || return 1
inssb || return 1
inscertificate && insport || return 1
sleep 2
echo
blue "Vless-reality相关key与id将自动生成……"
key_pair=$(/etc/s-box/sing-box generate reality-keypair)
private_key=$(echo "$key_pair" | awk '/PrivateKey/ {print $2}' | tr -d '"')
public_key=$(echo "$key_pair" | awk '/PublicKey/ {print $2}' | tr -d '"')
echo "$public_key" > /etc/s-box/public.key
short_id=$(/etc/s-box/sing-box generate rand --hex 4)
if [[ "$sbnh" == 1.10 ]]; then
release_fetch geoip.db /root/geoip.db && release_fetch geosite.db /root/geosite.db || return 1
fi
red "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
green "五、自动生成warp-wireguard出站账户" && sleep 2
warpwg && inssbjsonser || return 1
extra_install || return 1
lnsb || return 1
sbservice checked || return 1
openyn || return 1
sbactive
red "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
awk -F '更新内容' 'NR==1 {print $1}' "$SB_RELEASE_DIR/version" > /etc/s-box/v
blue "sing-box脚本安装成功，脚本快捷方式：sing_box" && cronsb
echo
wgcfgo && sbshare || return 1
red "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
blue "可选择9，刷新并显示所有协议配置及分享链接"
red "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
echo
}

changeym(){
local result
config_locked changeym_locked "$@"; result=$?
if [[ $result == 10 ]]; then sing_box; else return "$result"; fi
}

changeym_locked(){
local menu tag domain current patch cert key enabled
green "请选择要切换域名/证书模式的协议"
green "1：Vless Reality 域名：$(config_get vless-sb '.tls.server_name')"
green "2：Vmess 公网 WSS / Argo 回环源站（当前 TLS：$(config_get vmess-sb '.tls.enabled')）"
if [[ -f /root/ygkkkca/ca.log ]]; then
green "3：Hysteria2 证书"
green "4：TUIC 证书"
[[ "$sbnh" == 1.10 ]] || green "5：AnyTLS 证书"
else
yellow "申请 ACME 证书后可切换其他协议的证书模式"
fi
green "0：返回上层"
readp "请选择：" menu || return 1
case "$menu" in
1)
readp "请输入vless-reality域名 (回车使用apple.com)：" domain || return 1
domain=${domain:-apple.com}
sub_valid_domain "$domain" || { red "Reality 域名无效"; return 1; }
tag=vless-sb
patch=$(jq -cn --arg domain "$domain" '{tls:{server_name:$domain,reality:{handshake:{server:$domain}}}}') || return 1
;;
2)
tag=vmess-sb
readp "1：自签 WSS 直连；2：ACME WSS 直连；3：仅回环 WS（Argo）：" menu || return 1
case "$menu" in
1|3) cert=/etc/s-box/cert.pem; key=/etc/s-box/private.key; domain=www.bing.com ;;
2) cert=/root/ygkkkca/cert.crt; key=/root/ygkkkca/private.key; domain=$(acme_hostname) || return 1 ;;
*) return 1 ;;
esac
enabled=true; [[ $menu != 3 ]] || enabled=false
[[ $menu != 1 ]] || selfsigned_material "$cert" "$domain" >/dev/null || return 1
patch=$(jq -cn --arg cert "$cert" --arg key "$key" --arg domain "$domain" --argjson enabled "$enabled" \
    '{listen:(if $enabled then "::" else "127.0.0.1" end),tls:{server_name:$domain,enabled:$enabled,certificate_path:$cert,key_path:$key}}') || return 1
;;
3|4|5)
[[ -f /root/ygkkkca/ca.log ]] || { red "请先通过菜单12申请域名证书"; return 1; }
case "$menu" in
3) tag=hy2-sb ;;
4) tag=tuic5-sb ;;
5) [[ "$sbnh" != 1.10 ]] || return 1; tag=anytls-sb ;;
esac
current=$(config_get "$tag" '.tls') || return 1
key=$(jq -r '.key_path' <<< "$current") || return 1
if [[ "$key" == /etc/s-box/private.key ]]; then
domain=$(acme_hostname) || return 1
cert=/root/ygkkkca/cert.crt; key=/root/ygkkkca/private.key
else
cert=/etc/s-box/cert.pem; key=/etc/s-box/private.key; domain=www.bing.com
fi
patch=$(jq -cn --arg cert "$cert" --arg key "$key" --arg name "$domain" \
    '{tls:{certificate_path:$cert,key_path:$key,server_name:$name}}') || return 1

;;
*) return 10 ;;
esac
config_apply config_patch_inbound "$tag" "$patch" && config_refresh || return 1
blue "协议域名/证书设置已更新"
if [[ "$tag" == vmess-sb ]]; then
[[ "$enabled" == false ]] && blue "TLS已关闭，可设置Argo隧道" || blue "TLS已开启，Argo隧道不支持此模式"
fi
return 0
}

allports(){
vl_port=$(config_get vless-sb '.listen_port') || return 1
vm_port=$(config_get vmess-sb '.listen_port') || return 1
hy2_port=$(config_get hy2-sb '.listen_port') || return 1
tu5_port=$(config_get tuic5-sb '.listen_port') || return 1
an_port=; [[ "$sbnh" == "1.10" ]] || an_port=$(config_get anytls-sb '.listen_port') || return 1
hy2_ports=$(firewall_hops hy2-sb) || return 1
tu5_ports=$(firewall_hops tuic5-sb) || return 1
[[ -n $hy2_ports ]] && hy2zfport="$hy2_ports" || hy2zfport="未添加"
[[ -n $tu5_ports ]] && tu5zfport="$tu5_ports" || tu5zfport="未添加"
}

changeport(){
sbactive
allports
yellow "Trojan / Snell 的端口在 3 → 11 中设置"
fports(){
local rangeport
readp "请输入 UDP 转发范围（格式 1000:2000）：" rangeport || return 1
python3 "$SB_RELEASE_DIR/tools/firewall.py" add "$hop_tag" "$rangeport"
}
fport(){
local onlyport
readp "请输入 UDP 转发端口：" onlyport || return 1
[[ "$onlyport" =~ ^[1-9][0-9]{0,4}$ ]] || return 1
python3 "$SB_RELEASE_DIR/tools/firewall.py" add "$hop_tag" "$onlyport"
}
hy2deports(){ python3 "$SB_RELEASE_DIR/tools/firewall.py" clear hy2-sb; }
tu5deports(){ python3 "$SB_RELEASE_DIR/tools/firewall.py" clear tuic5-sb; }

allports
green "Vless-reality、Vmess-ws、Anytls仅能更改唯一的端口，vmess-ws注意Argo端口重置"
green "Hysteria2与Tuic5支持更改主端口，也支持增删多个转发端口"
green "Hysteria2支持端口跳跃，且与Tuic5都支持多端口复用"
echo
green "1：Vless-reality协议 ${yellow}端口:$vl_port${plain}"
green "2：Vmess-ws协议 ${yellow}端口:$vm_port${plain}"
green "3：Hysteria2协议 ${yellow}端口:$hy2_port  转发多端口: $hy2zfport${plain}"
green "4：Tuic5协议 ${yellow}端口:$tu5_port  转发多端口: $tu5zfport${plain}"
if [[ "$sbnh" != "1.10" ]]; then
green "5：Anytls协议 ${yellow}端口:$an_port${plain}"
fi
green "0：返回上层"
readp "请选择要变更端口的协议：" menu
if [ "$menu" = "1" ]; then
vlport
config_set_port vless-sb "$port_vl_re" || return 1
blue "Vless-reality端口更改完成"
echo
elif [ "$menu" = "5" ]; then
anport
config_set_port anytls-sb "$port_an" || return 1
blue "Anytls端口更改完成"
echo
elif [ "$menu" = "2" ]; then
vmport
config_set_port vmess-sb "$port_vm_ws" || return 1
blue "Vmess-ws端口更改完成"
tls=$(config_get vmess-sb '.tls.enabled') || return 1
if [[ "$tls" = "false" ]]; then
blue "切记：如果Argo使用中，临时隧道必须重置，固定隧道的CF设置界面端口必须修改为$port_vm_ws"
else
blue "因TLS已开启，当前Argo隧道已不支持开启"
fi
echo
elif [ "$menu" = "3" ]; then
green "1：更换Hysteria2主端口（已管理的跳跃端口随之更新）"
green "2：添加Hysteria2多端口"
green "3：重置删除Hysteria2多端口"
green "0：返回上层"
readp "请选择【0-3】：" menu
if [ "$menu" = "1" ]; then
hy2port
config_set_port hy2-sb "$port_hy2" || return 1
blue "Hysteria2端口更改完成"
elif [ "$menu" = "2" ]; then
green "1：添加Hysteria2范围端口"
green "2：添加Hysteria2单端口"
green "0：返回上层"
readp "请选择【0-2】：" menu
local hop_tag=hy2-sb
if [ "$menu" = "1" ]; then
fports && sbshare > /dev/null 2>&1 && changeport
elif [ "$menu" = "2" ]; then
fport && sbshare > /dev/null 2>&1 && changeport
else
changeport
fi
elif [ "$menu" = "3" ]; then
if [ -n "$hy2_ports" ]; then
hy2deports && sbshare > /dev/null 2>&1 && yellow "Hysteria2多端口已删除" && changeport
else
sbshare > /dev/null 2>&1 && yellow "Hysteria2未设置多端口" && changeport
fi
else
changeport
fi

elif [ "$menu" = "4" ]; then
green "1：更换Tuic5主端口（已管理的跳跃端口随之更新）"
green "2：添加Tuic5多端口"
green "3：重置删除Tuic5多端口"
green "0：返回上层"
readp "请选择【0-3】：" menu
if [ "$menu" = "1" ]; then
tu5port
config_set_port tuic5-sb "$port_tu" || return 1
blue "Tuic5端口更改完成"
elif [ "$menu" = "2" ]; then
green "1：添加Tuic5范围端口"
green "2：添加Tuic5单端口"
green "0：返回上层"
readp "请选择【0-2】：" menu
local hop_tag=tuic5-sb
if [ "$menu" = "1" ]; then
fports && sbshare > /dev/null 2>&1 && changeport
elif [ "$menu" = "2" ]; then
fport && sbshare > /dev/null 2>&1 && changeport
else
changeport
fi
elif [ "$menu" = "3" ]; then
if [ -n "$tu5_ports" ]; then
tu5deports && sbshare > /dev/null 2>&1 && yellow "Tuic5多端口已删除" && changeport
else
sbshare > /dev/null 2>&1 && yellow "Tuic5未设置多端口" && changeport
fi
else
changeport
fi
else
sing_box
fi
}

changeuuid(){
local result
config_locked changeuuid_locked "$@"; result=$?
if [[ $result == 10 ]]; then changeserv; else return "$result"; fi
}

changeuuid_locked(){
local menu uuid path old
old=$(config_get vless-sb '.users[0].uuid') || return 1
path=$(config_get vmess-sb '.transport.path') || return 1
if [[ ${path,,} == *"${old,,}"* ]]; then yellow "当前 WS 路径含节点凭据，建议使用选项 3 迁移"; fi
yellow "1：自定义原有协议的uuid (密码)"
yellow "2：自定义Vmess的path路径"
yellow "3：迁移旧 WS 路径，轮换共享 UUID（旧连接会中断）"
yellow "0：返回上层"
readp "请选择【0-3】：" menu || return 1
case "$menu" in
1|3)
if [[ $menu == 3 ]]; then
    [[ ${path,,} == *"${old,,}"* ]] || { green "当前路径已独立，其他凭据轮换请用选项 1"; return 0; }
    uuid=
else
    readp "输入uuid，回车随机生成：" uuid || return 1
fi
[[ -n "$uuid" ]] || uuid=$(/etc/s-box/sing-box generate uuid) || return 1
[[ "$uuid" =~ ^[[:xdigit:]]{8}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{12}$ ]] || { red "UUID格式无效"; return 1; }
[[ ${uuid,,} != "${old,,}" ]] || { red "新旧 UUID 必须不同"; return 1; }
local random_path
random_path=$(openssl rand -hex 24) || return 1
config_apply config_patch_identity "$uuid" "/$random_path" && config_refresh || return 1
blue "节点凭据已轮换，请刷新订阅或重新导入静态链接。如启用 GitLab，请重新发布。"
;;
2)
[[ ${path,,} != *"${old,,}"* ]] || { red "旧路径含凭据，请先使用选项 3 迁移"; return 1; }
readp "输入Vmess的path路径，回车表示不变：" path || return 1
[[ -n "$path" ]] || return 0
[[ $path == /* && ${path,,} != *"${old,,}"* && $path != *\?ed=* && $path != *\&ed=* ]] || { red "请输入以 / 开头且不含凭据或客户端 early-data 参数的路径"; return 1; }
local patch
patch=$(jq -cn --arg path "$path" '{transport:{path:$path}}') || return 1
config_apply config_patch_inbound vmess-sb "$patch" && config_refresh || return 1
;;
*) return 10 ;;
esac
blue "已确认Vmess的path路径：$(config_get vmess-sb '.transport.path')"
}

ip_priority_get(){
jq -er '
  def one($description):
    if length == 1 then .[0] else error("缺少唯一的" + $description) end;
  ([.outbounds[]? | select(.type == "direct" and .tag == "direct")] |
    one("默认 direct 出站")) as $direct |
  if $direct | has("domain_strategy") then $direct.domain_strategy // "default"
  else
    ([.route.rules | to_entries[] |
      select(.value.outbound == "direct" and .value.network == "udp,tcp" and
             ((.value | keys) - ["action", "outbound", "network"] | length) == 0 and
             (.value.action == null or .value.action == "route")) | .key] |
      one("默认 direct 路由")) as $route |
    [.route.rules | to_entries[] |
      select(.value.action == "resolve" and
             (((.value | keys) - ["action", "strategy"] | length) == 0 or
               (.value | del(.action,.strategy)) == {ip_cidr:["0.0.0.0/0","::/0"],invert:true}))] as $resolve |
    if ($resolve | length) == 0 then "default"
    elif ($resolve | length) == 1 and ($resolve[0].key == ($route - 1) or ($resolve[0].key < $route and $resolve[0].value.invert == true))
    then $resolve[0].value.strategy // "default"
    else error("默认解析规则位置不明确") end
  end
' "${1:-/etc/s-box/sb.json}"
}

ip_priority_label(){
case "$1" in
prefer_ipv4) printf 'IPv4优先' ;;
prefer_ipv6) printf 'IPv6优先' ;;
ipv4_only) printf '仅解析IPv4' ;;
ipv6_only) printf '仅解析IPv6' ;;
default) printf '默认（未显式设置）' ;;
*) printf '无法读取' ;;
esac
}

ip_priority_patch(){
jq -e --arg series "$3" --arg strategy "$4" '
  def one($description):
    if length == 1 then .[0] else error("缺少唯一的" + $description) end;
  if $series == "1.10" then
    ([.outbounds | to_entries[] |
      select(.value.type == "direct" and .value.tag == "direct") | .key] |
      one("默认 direct 出站")) as $direct |
    .outbounds[$direct].domain_strategy = $strategy |
    (.inbounds[]? | select(has("domain_strategy")).domain_strategy) = $strategy
  else
    ([.route.rules | to_entries[] |
      select(.value.outbound == "direct" and .value.network == "udp,tcp" and
             ((.value | keys) - ["action", "outbound", "network"] | length) == 0 and
             (.value.action == null or .value.action == "route")) | .key] |
      one("默认 direct 路由")) as $route |
    [.route.rules | to_entries[] |
      select(.value.action == "resolve" and
             (((.value | keys) - ["action", "strategy"] | length) == 0 or
               (.value | del(.action,.strategy)) == {ip_cidr:["0.0.0.0/0","::/0"],invert:true})) | .key] as $resolve |
    if ($resolve | length) == 0 and any(.route.rules[]; .invert==true and .action=="reject" and .ip_cidr==["0.0.0.0/0","::/0"]) then
      ([.route.rules | to_entries[] | select(.value.action=="reject" and
          (.value.ip_cidr // [] | index("100.64.0.0/10")) != null) | .key] | one("目标地址保护规则")) as $guard |
      .route.rules |= .[:$guard] + [{action:"resolve",strategy:$strategy,invert:true,ip_cidr:["0.0.0.0/0","::/0"]}] + .[$guard:]
    elif ($resolve | length) == 0 then
      .route.rules |= .[:$route] + [{"action":"resolve", "strategy":$strategy}] + .[$route:]
    elif ($resolve | length) == 1 and ($resolve[0] == ($route - 1) or ($resolve[0] < $route and .route.rules[$resolve[0]].invert == true))
    then .route.rules[$resolve[0]].strategy = $strategy
    else error("默认解析规则位置不明确") end
  end
' "$1" > "$2"
}

ip_priority_restart(){
[[ ! -e /etc/s-box/admin-stopped ]] || return 0
restartsb "$@" || return 1
# Type=simple can acknowledge restart before the process fails during startup.
sleep 1
if command -v apk >/dev/null 2>&1; then
rc-service sing-box status 2>/dev/null | grep -qw started
else
systemctl is-active --quiet sing-box
fi
}

ip_priority_apply(){
case "$1" in
prefer_ipv4|prefer_ipv6|ipv4_only|ipv6_only) ;;
*) red "无效的IP优先级"; return 1 ;;
esac
config_apply ip_priority_patch "$1"
}

egress_config_patch(){
# egress_apply validates the selected addresses before generating candidates.
jq -e --arg ipv4 "$3" --arg ipv6 "$4" '
  def native:
    .type == "direct" and (.tag == "direct" or .tag == "vps-outbound-v4" or .tag == "vps-outbound-v6")
    and (has("detour") | not);
  if ([.outbounds[]? | select(native and .tag == "direct")] | length) != 1
  then error("缺少唯一的原生 direct 出站，未修改配置") else
    .outbounds |= map(if native then
      del(.inet4_bind_address, .inet6_bind_address) |
      (if $ipv4 != "" then .inet4_bind_address=$ipv4 else . end) |
      (if $ipv6 != "" then .inet6_bind_address=$ipv6 else . end)
      else . end)
  end
' "$1" > "$2"
}

egress_apply(){
config_locked egress_apply_locked "$@"
}

egress_apply_locked(){
(
local family=$1 source=$2 egress_ipv4 egress_ipv6 work file url verified=0 restore=0 had_state=0 failed=0
local -a check_args
[[ $family == 4 || $family == 6 ]] || { red "无效的地址族"; exit 1; }
egress_read || { red "出站源地址设置无效，已保留原配置"; exit 1; }
if [[ -n $source ]]; then
egress_candidate_usable "$family" "$source" || {
red "所选地址已不存在或尚不可用，已保留原配置"; exit 1;
}
for url in https://icanhazip.com https://api64.ipify.org; do
if egress_probe "$family" "$source" "$url" >/dev/null; then
verified=1; break
fi
done
(( verified )) || { red "所选源地址复测未成功，已保留原配置"; exit 1; }
fi
if [[ $family == 4 ]]; then egress_ipv4=$source; else egress_ipv6=$source; fi
work=$(mktemp -d /etc/s-box/.egress.XXXXXX) || exit 1
egress_cleanup(){
local status=$?
trap - EXIT
trap '' HUP INT TERM
if (( restore )); then
for file in sb10.json sb11.json sb.json; do
mv -f -- "$work/old-$file" "/etc/s-box/$file" || failed=1
done
if (( had_state )); then
mv -f -- "$work/old-egress.json" /etc/s-box/egress.json || failed=1
else
rm -f -- /etc/s-box/egress.json || failed=1
fi
if (( failed )); then
red "恢复配置失败，剩余备份保留在 $work"
return 1
fi
ip_priority_restart >/dev/null 2>&1 || red "原配置已恢复，但服务启动失败，请查看运行日志"
[[ ! -f /etc/s-box/firewall.json ]] || firewall_sync || red "恢复原配置后的防火墙同步失败"
red "出站源地址切换失败，已恢复原配置和地址设置"
fi
rm -rf -- "$work"
return "$status"
}
trap egress_cleanup EXIT
trap 'exit 1' HUP INT TERM
if [[ -e /etc/s-box/egress.json ]]; then
had_state=1
cp -p /etc/s-box/egress.json "$work/old-egress.json" || exit 1
fi
for file in sb10.json sb11.json sb.json; do
cp -p "/etc/s-box/$file" "$work/old-$file" && cp -p "/etc/s-box/$file" "$work/$file" || exit 1
egress_config_patch "/etc/s-box/$file" "$work/$file" "$egress_ipv4" "$egress_ipv6" || exit 1
done
jq -n --arg ipv4 "$egress_ipv4" --arg ipv6 "$egress_ipv6" \
  '{ipv4_source:$ipv4,ipv6_source:$ipv6}' > "$work/egress.json" || exit 1
check_args=(-c "$work/sb.json")
[[ ! -f /etc/s-box/sb_extra.json ]] || check_args+=(-c /etc/s-box/sb_extra.json)
if ! /etc/s-box/sing-box check "${check_args[@]}"; then
red "候选配置校验失败，已保留原配置和地址设置"
exit 1
fi
restore=1
for file in sb10.json sb11.json sb.json egress.json; do
mv -f -- "$work/$file" "/etc/s-box/$file" || exit 1
done
ip_priority_restart checked || exit 1
[[ ! -f /etc/s-box/firewall.json ]] || firewall_sync || exit 1
restore=0
)
}

changeip(){
local choose strategy family address
yellow "设置VPS默认出站的域名解析优先级；已有WARP/SOCKS分流保持各自策略"
yellow "仅解析模式只限制域名解析结果，IP地址请求仍可能使用另一地址族"
while true; do
readp "1. IPv4优先\n2. IPv6优先\n3. 仅解析IPv4\n4. 仅解析IPv6\n0. 返回上层\n请选择：" choose || return 1
case "$choose" in
1) strategy=prefer_ipv4; family=4 ;;
2) strategy=prefer_ipv6; family=6 ;;
3) strategy=ipv4_only; family=4 ;;
4) strategy=ipv6_only; family=6 ;;
0) [[ ${1:-} == egress ]] || changeserv; return ;;
*) red "输入无效，请选择0-4"; continue ;;
esac
address=$(egress_public_ip "$family") || address=
if [[ -z $address ]]; then
yellow "IPv${family}公网探测未成功，无法确认可用性"
if [[ $strategy == *_only ]]; then
red "仅解析模式未应用，已保留原配置"
return 1
fi
fi
ip_priority_apply "$strategy" || return 1
blue "当前已更换的IP优先级：$(ip_priority_label "$strategy")"
[[ ${1:-} == egress ]] || sing_box
return
done
}

egress_status(){
local egress_ipv4 egress_ipv6 family addresses count
if ! egress_read; then
yellow "出站源地址设置无效，请检查 /etc/s-box/egress.json；未回退系统自动选择"
return 1
fi
for family in 4 6; do
if addresses=$(ip -o "-$family" addr show scope global 2>/dev/null); then
count=$(printf '%s\n' "$addresses" | awk -v kind="$( [[ $family == 4 ]] && printf inet || printf inet6 )" '$3 == kind {n++} END {print n+0}')
else
count=未知
fi
if [[ $family == 4 ]]; then
printf '本机IPv4：%s 个；出站源：%s\n' "$count" "${egress_ipv4:-系统自动}"
else
printf '本机IPv6：%s 个；出站源：%s\n' "$count" "${egress_ipv6:-系统自动}"
fi
done
}

egress_select(){
local family=$1 data choice row source state refresh=1
[[ $family == 4 || $family == 6 ]] || return 1
while true; do
if (( refresh )); then
yellow "检测 IPv${family} 出站：最多4个并发请求，完整扫描限时30秒；超时未开始的项目显示未检测"
if ip -o link show dev wgcf 2>/dev/null | grep -qE '<([^>]*,)?UP[,>]' || pgrep -x warp-go >/dev/null 2>&1; then
yellow "检测到系统级WARP：绑定源地址不会绕过系统路由，本次结果不能证明原生出口"
fi
data=$(egress_scan "$family") || { red "无法读取本机地址或完成扫描，未修改设置"; return 1; }
refresh=0
jq -r '
  def reason:
    {ready:"可选",down:"网卡未启用",not_global:"非全局地址",tentative:"地址尚未就绪",dadfailed:"地址重复检测失败",
     deprecated:"地址已弃用",ok:"通过",dns:"DNS失败",bind:"绑定失败",timeout:"超时",connect:"连接失败",
     http:"HTTP失败",tls:"TLS失败",invalid_response:"返回无效",source_mismatch:"源地址不符",error:"请求失败"}[.] // .;
  to_entries[] | .key as $n | .value |
  "\($n)：\(if .address == "" then "系统自动（恢复内核选择）" else .address end)  [\(.interface)]  \(.state | reason)\(if .temporary then "；临时地址，不建议长期固定" else "" end)",
  (if .address != "" then "   有效期：\(.valid_lft // "未知")；首选有效期：\(.preferred_lft // "未知")" else empty end),
  "   本轮：\([.results[] | select(.status == "ok")] | length)/2 通过\(if (.results|length) < 2 then "；其余未检测" else "" end)\(if .address != "" and .state == "ready" and (.temporary|not) and ([.results[] | select(.status == "ok")]|length) == 2 then "；本轮推荐" else "" end)",
  (.results[] | "   \(.endpoint // "探测")：\(.status | reason)；本地源=\(.local // "-")；公网出口=\(.public // "-")；目标=\(.remote // "-")；耗时=\(.seconds // "-")s")
' <<< "$data"
yellow "只影响脚本探测和sing-box原生直连；不修改节点公布地址。DNS和系统路由仍由现有配置处理。"
fi
readp "输入地址序号，0恢复系统自动，r重新检测，b返回：" choice || return 1
case "$choice" in
b|B) return 0 ;;
r|R) refresh=1; continue ;;
esac
if [[ ! $choice =~ ^[0-9]{1,6}$ ]]; then
red "请输入列表中的地址序号"; continue
fi
row=$(jq -ce --arg index "$choice" '.[$index | tonumber] // empty' <<< "$data") || {
red "地址序号不存在"; continue;
}
source=$(jq -r '.address' <<< "$row")
state=$(jq -r '.state' <<< "$row")
if [[ -n $source && $state != ready ]]; then
red "该地址当前不可用于固定出站，请重新检测后选择"; continue
fi
if egress_apply "$family" "$source"; then
blue "IPv${family} 出站源已设为：${source:-系统自动}"
return 0
fi
return 1
done
}

egress_menu(){
local choice
while true; do
egress_status
yellow "地址族优先级决定连接目标IPv4还是IPv6；源地址决定使用本机哪个IP建立连接"
readp "1：设置IPv4/IPv6优先级\n2：选择IPv4源地址\n3：选择IPv6源地址\n4：刷新当前出站连通性\n0：返回上层\n请选择：" choice || return 1
case "$choice" in
1) changeip egress ;;
2) egress_select 4 ;;
3) egress_select 6 ;;
4) menu_ip_status; printf '公网探测 IPv4：%s  IPv6：%s\n' "$vps_ipv4" "$vps_ipv6"
   yellow "完整的逐地址扫描请进入2或3；选择页支持r重新检测" ;;
0) changeserv; return ;;
*) red "输入无效，请选择0-4" ;;
esac
done
}

tgsbshow(){
local menu token userid candidate
yellow "1：设置 Telegram 凭据    2：关闭推送并删除本机凭据    0：返回"
readp "请选择【0-2】：" menu || return 1
case "$menu" in
1)
    IFS= read -rs -p "输入 Telegram Bot Token：" token || return 1
    printf '\n'
    IFS= read -rs -p "输入数字聊天 ID 或 @用户名：" userid || return 1
    printf '\n'
    [[ $token =~ ^[0-9]+:[A-Za-z0-9_-]+$ && ( $userid =~ ^-?[0-9]+$ || $userid =~ ^@[A-Za-z0-9_]{5,}$ ) ]] ||
        { red "Telegram 凭据格式无效"; return 1; }
    (umask 077
     candidate=$(mktemp /etc/s-box/.telegram.XXXXXX) || exit 1
     trap 'rm -f -- "$candidate"' EXIT
     trap 'exit 1' HUP INT TERM
     printf '%s\n' "$token" "$userid" > "$candidate" &&
         chmod 600 "$candidate" && mv -f -- "$candidate" /etc/s-box/telegram.credentials
    ) || return 1
    unset token userid
    tgnotice ;;
2) rm -f -- /etc/s-box/sbtg.sh /etc/s-box/telegram.credentials ;;
*) changeserv ;;
esac
}

sub_migrate_tg(){
(
umask 077
[[ -s /etc/s-box/telegram.credentials ]] || {
    red "旧 Telegram 配置须通过 3 → 5 重新输入凭据"; exit 1
}
local candidate
candidate=$(mktemp /etc/s-box/.sbtg.XXXXXX) || exit 1
trap 'rm -f -- "$candidate"' EXIT
trap 'exit 1' HUP INT TERM
cat > "$candidate" <<'TG_SCRIPT' || exit 1
#!/bin/bash
set +x
set -o pipefail
cd /etc/s-box || exit 1
{ IFS= read -r token && IFS= read -r chat; } < telegram.credentials || exit 1
[[ $token =~ ^[0-9]+:[A-Za-z0-9_-]+$ && ( $chat =~ ^-?[0-9]+$ || $chat =~ ^@[A-Za-z0-9_]{5,}$ ) ]] || exit 1
send_text(){
    local message=$1
    while [[ -n $message ]]; do
        printf '%s' "${message:0:3500}" |
            curl -q -fs --proto '=https' --connect-timeout 5 --max-time 20 \
                --data-urlencode 'text@-' --config /dev/fd/3 3<<TG_REQUEST |
url = "https://api.telegram.org/bot$token/sendMessage"
data-urlencode = "chat_id=$chat"
TG_REQUEST
            jq -e '.ok == true' >/dev/null || return 1
        message=${message:3500}
    done
}
for file in vl_reality.txt vm_ws_argols.txt vm_ws_argogd.txt vm_ws_tls.txt hy2.txt tuic5.txt an.txt trojan.txt jhsub.txt; do
    [[ ! -s $file ]] || send_text "$file"$'\n'"$(< "$file")" || exit 1
done
for file in sbox.json clmi.yaml; do
    if [[ -s subdomain.log && -s subtoken.log ]]; then
        send_text "https://$(< subdomain.log)/$(< subtoken.log)/$file" || exit 1
    elif [[ -s $file ]]; then
        send_text "$file"$'\n'"$(< "$file")" || exit 1
    fi
done
TG_SCRIPT
chmod 700 "$candidate" && mv -f -- "$candidate" /etc/s-box/sbtg.sh
)
}

tgnotice(){
if [[ -s /etc/s-box/telegram.credentials ]]; then
    sub_migrate_tg || return 1
    sbshare >/dev/null 2>&1 || { red "节点配置刷新失败"; return 1; }
    bash /etc/s-box/sbtg.sh || { red "Telegram 推送失败"; return 1; }
    green "Telegram 推送成功"
elif [[ -f /etc/s-box/sbtg.sh ]]; then
    red "请先在 Telegram 设置中重新输入凭据或关闭推送"; return 1
else
    yellow "未设置 TG 通知"
fi
}

changeserv(){
sbactive
echo
green "Sing-box配置变更选择如下:"
readp "1：更换Reality域名伪装地址、切换自签证书与Acme域名证书、开关TLS\n2：更换原有协议UUID(密码)、Vmess-Path路径\n3：设置Argo临时隧道、固定隧道\n4：IP出站设置（地址族优先级/源地址）\n5：设置Telegram推送节点通知\n6：更换Warp-wireguard出站账户\n7：管理私有 GitLab 项目的订阅发布\n8：管理订阅分享链接（仅本机 HTTP / 自定义域名 HTTPS）\n9：设置所有Vmess节点的CDN优选地址\n10：设置自定义域名分发（HTTPS订阅）\n11：管理Trojan / Snell（启停、端口、密码、证书）\n12：客户端WARP前置（Clash/Mihomo）\n0：返回上层\n请选择【0-12】：" menu
if [ "$menu" = "1" ];then
changeym
elif [ "$menu" = "2" ];then
changeuuid
elif [ "$menu" = "3" ];then
cfargo_ym
elif [ "$menu" = "4" ];then
egress_menu
elif [ "$menu" = "5" ];then
tgsbshow
elif [ "$menu" = "6" ];then
changewg
elif [ "$menu" = "7" ];then
gitlabsub
elif [ "$menu" = "8" ];then
ipsub
elif [ "$menu" = "9" ];then
vmesscfadd
elif [ "$menu" = "10" ];then
ipsub 5
elif [ "$menu" = "11" ];then
extra_menu
elif [ "$menu" = "12" ];then
client_warp_menu
else 
sing_box
fi
}

sub_valid_domain(){
local label
[[ ${#1} -le 253 && "$1" =~ ^[a-z0-9.-]+$ && "$1" == *.* && "$1" != *..* && "$1" != *. && ! "${1##*.}" =~ ^[0-9]+$ ]] || return 1
local -a labels
IFS=. read -r -a labels <<< "$1"
for label in "${labels[@]}"; do
[[ ${#label} -le 63 && "$label" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] || return 1
done
}

sub_valid_token(){
[[ "$1" =~ ^[A-Za-z0-9_-]{1,128}$ ]]
}

sub_valid_port(){
[[ "$1" =~ ^[1-9][0-9]{0,4}$ ]] && (( $1 <= 65535 ))
}

sub_service(){
local action=$1 name=$2
if command -v rc-service >/dev/null 2>&1; then
case "$action" in
enable) rc-update add "$name" default ;;
disable) rc-update del "$name" default ;;
enabled) rc-update show default | grep -qw "$name" ;;
*) rc-service "$name" "$action" 8>&- 9>&- ;;
esac
else
case "$action" in
status) systemctl is-active --quiet "$name" ;;
enabled) systemctl is-enabled --quiet "$name" ;;
*) systemctl "$action" "$name" ;;
esac
fi
}

sub_caddy_site(){
local key
key=$(sub_https_key "$2") || return 1
cat <<EOF
$1 {
    @subscription path_regexp subscription ^/$2/(sbox[.]json|clmi[.]yaml|jhsub[.]txt)$
    handle @subscription {
        header Cache-Control "no-store"
        uri strip_prefix /$2
        root * /var/lib/sbox-subscription-https/www/$key
        file_server
    }
    handle {
        respond 404
    }
}
EOF
}

sub_https_key(){
local digest
digest=$(printf '%s' "$1" | sha256sum) || return 1
digest=${digest%% *}
[[ $digest =~ ^[0-9a-f]{64}$ ]] && printf '%s\n' "$digest"
}

sub_caddy_download(){
(
local work asset="caddy_2.11.9_linux_$1.tar.gz"
work=$(mktemp -d /etc/s-box/.caddy.XXXXXX) || exit 1
trap 'rm -rf -- "$work"' EXIT
release_fetch "$asset" "$work/archive" &&
tar -xzf "$work/archive" -O caddy > "$work/caddy" &&
chmod 755 "$work/caddy" && "$work/caddy" version >/dev/null &&
mv -f -- "$work/caddy" /etc/s-box/caddy
)
}

sub_caddy_admin_uid(){
local pid user pidfile
if command -v rc-service >/dev/null 2>&1; then
    for pidfile in /run/caddy/caddy.pid /run/caddy.pid; do
        [[ -r $pidfile ]] && { read -r pid < "$pidfile"; break; }
    done
else
    pid=$(systemctl show -p MainPID --value caddy 2>/dev/null)
fi
if [[ $pid =~ ^[1-9][0-9]*$ && -r /proc/$pid/status &&
      $(readlink -f "/proc/$pid/exe") == "$(readlink -f "$(command -v caddy)")" ]]; then
    awk '$1=="Uid:" {print $3; found=1} END {exit !found}' "/proc/$pid/status"; return
fi
if ! command -v rc-service >/dev/null 2>&1 &&
   [[ $(systemctl show -p LoadState --value caddy 2>/dev/null) == loaded ]]; then
    user=$(systemctl show -p User --value caddy) && id -u "${user:-root}"; return
fi
return 1
}

sub_caddy_admin_safe(){
local address path directory uid metadata owner mode first=1
address=$(set -o pipefail; caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile 2>/dev/null |
    jq -er 'if .admin.disabled == true then "off" else .admin.listen // "localhost:2019" end') || {
    red "无法核对共享 Caddy 的管理端点" >&2; return 1
}
[[ $address != off ]] || return 0
if [[ $address != unix//* ]]; then
    # A local TCP API can also be reached by other users on the host.
    if [[ ${SBOX_CADDY_SINGLE_USER:-} == 1 && $address =~ ^(tcp[46]?/)?(localhost|127\.0\.0\.1|\[::1\]):[0-9]+$ ]]; then return 0; fi
    red "共享 Caddy 请配置 admin off 或受保护目录内的 Unix 管理套接字；单用户回环 TCP 需显式设置 SBOX_CADDY_SINGLE_USER=1" >&2
    return 1
fi
path=${address#unix/}; path=${path%%|*}
[[ $path =~ ^(/[[:alnum:]_.~-]+)+$ && /${path#/}/ != */../* && /${path#/}/ != */./* && ! -L $path ]] || return 1
uid=$(sub_caddy_admin_uid) && [[ $uid =~ ^[0-9]+$ ]] || {
    red "无法确认 Caddy 服务 UID，请使用 admin off 或核对服务用户" >&2; return 1
}
directory=${path%/*}
while [[ -n $directory ]]; do
    [[ -d $directory && ! -L $directory ]] || return 1
    metadata=$(stat -c '%u %a' -- "$directory") || return 1
    read -r owner mode <<< "$metadata"
    [[ $owner == 0 || $owner == "$uid" ]] && (( (8#$mode & 0022) == 0 )) || return 1
    if (( first )); then
        [[ $owner == "$uid" ]] && (( (8#$mode & 0777) == 0700 )) || {
            red "管理套接字目录须归 Caddy 服务 UID 所有，权限为 0700" >&2; return 1
        }
        first=0
    fi
    directory=${directory%/*}
done
(( ! first ))
}

sub_caddy_prepare(){
local mode arch
mode=$(cat /etc/s-box/sub-caddy-mode 2>/dev/null)
if [[ "$mode" == shared || ( -z "$mode" && -f /etc/caddy/Caddyfile ) ]]; then
command -v caddy >/dev/null 2>&1 || { red "已有 /etc/caddy/Caddyfile，但未找到 caddy 命令" >&2; return 1; }
sub_caddy_admin_safe || return 1
echo shared
return
fi
# 不抢占其它网站或节点正在使用的 TCP 端口。
if ! sub_service status sbox-sub-caddy >/dev/null 2>&1; then
if ss -Hltn 2>/dev/null | awk '{print $4}' | grep -Eq ':(80|443)$'; then
red "TCP 80/443 已被占用。请在现有 HTTPS 服务接入订阅，或先调整端口；未停止已有服务。" >&2
return 1
fi
fi
case "$(uname -m)" in
x86_64) arch=amd64 ;;
aarch64) arch=arm64 ;;
armv7l) arch=armv7 ;;
*) red "Caddy 不支持当前架构" >&2; return 1 ;;
esac
if ! release_matches "caddy-linux-$arch" /etc/s-box/caddy; then
sub_caddy_download "$arch" || return 1
fi
echo managed
}

sub_caddy_reload(){
if [[ "$1" == shared ]]; then
    if grep -Fxq 'import /etc/caddy/s-box-subscription.caddy # s-box subscription' /etc/caddy/Caddyfile; then
        sub_caddy_admin_safe || return 1
    fi
    # The running daemon may still use its previous admin address. Apply the checked file
    # via the service manager without transmitting a token-bearing config to that endpoint.
    sub_service restart caddy
else
    sub_service restart sbox-sub-caddy
fi
}

sub_caddy_group(){
local pid group user
if [[ -n ${SBOX_CADDY_GROUP:-} ]]; then
    printf '%s\n' "$SBOX_CADDY_GROUP"; return
fi
if command -v rc-service >/dev/null 2>&1; then
    pid=$(pgrep -x caddy)
else
    pid=$(systemctl show -p MainPID --value caddy 2>/dev/null)
fi
if [[ "$pid" =~ ^[1-9][0-9]*$ && -r /proc/$pid/status ]]; then
    # Use the effective group of the process that must read the import on restart.
    awk '$1=="Gid:" {print $3; found=1} END {exit !found}' "/proc/$pid/status"; return
fi
if ! command -v rc-service >/dev/null 2>&1 &&
   [[ $(systemctl show -p LoadState --value caddy 2>/dev/null) == loaded ]]; then
    group=$(systemctl show -p Group --value caddy) || return 1
    if [[ -n "$group" ]]; then printf '%s\n' "$group"; return; fi
    user=$(systemctl show -p User --value caddy) || return 1
    id -gn "${user:-root}"; return
fi
red "无法确定共享 Caddy 的读取组。请核对服务身份，用 SBOX_CADDY_GROUP 指定该组。" >&2
return 1
}

sub_caddy_apply(){
local domain=$1 token=$2 port=$3 mode=$4
[[ -d /etc/caddy ]] || install -d -m 755 /etc/caddy || return 1
local caddy_group=sboxcaddy snippet
if [[ "$mode" == shared ]]; then
    caddy_group=$(sub_caddy_group) || return 1
else
    sub_service_user sboxcaddy || return 1
fi
snippet=$(mktemp /etc/caddy/.sbox-site.XXXXXX) || return 1
if ! sub_caddy_site "$domain" "$token" "$port" > "$snippet" ||
   ! chgrp "$caddy_group" "$snippet" || ! chmod 640 "$snippet" ||
   ! mv -f "$snippet" /etc/caddy/s-box-subscription.caddy; then
    rm -f "$snippet"; return 1
fi
if [[ "$mode" == shared ]]; then
grep -qxF 'import /etc/caddy/s-box-subscription.caddy # s-box subscription' /etc/caddy/Caddyfile ||
printf '\nimport /etc/caddy/s-box-subscription.caddy # s-box subscription\n' >> /etc/caddy/Caddyfile || return 1
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile || return 1
else
cat > /etc/s-box/sub-Caddyfile <<'EOF'
{
    admin off
    servers {
        protocols h1 h2
    }
}
import /etc/caddy/s-box-subscription.caddy
EOF
[[ $? == 0 ]] || return 1
/etc/s-box/caddy validate --config /etc/s-box/sub-Caddyfile --adapter caddyfile || return 1
local runtime=/var/lib/sbox-caddy
install -d -m 750 -g sboxcaddy "$runtime" &&
install -m 640 -g sboxcaddy /etc/s-box/sub-Caddyfile "$runtime/Caddyfile" &&
install -m 750 -g sboxcaddy /etc/s-box/caddy "$runtime/caddy.next" || return 1
# The certificate store belongs to the web frontend, separate from node credentials.
if [[ ! -d "$runtime/data" ]]; then
    local data_stage
    data_stage=$(mktemp -d "$runtime/.data.XXXXXX") || return 1
    if [[ -d /etc/s-box/sub-caddy-data ]]; then cp -a /etc/s-box/sub-caddy-data/. "$data_stage/" || return 1; fi
    chown -R sboxcaddy:sboxcaddy "$data_stage" && mv -T "$data_stage" "$runtime/data" || return 1
fi
if command -v rc-service >/dev/null 2>&1; then
    if ! command -v setcap >/dev/null && command -v apk >/dev/null; then
        apk add libcap-setcap || return 1
    fi
    setcap cap_net_bind_service=ep "$runtime/caddy.next" || { red "OpenRC 需要 setcap 授予 Caddy 低端口绑定权限"; return 1; }
fi
mv -f "$runtime/caddy.next" "$runtime/caddy" || return 1
if command -v rc-service >/dev/null 2>&1; then
cat > /etc/init.d/sbox-sub-caddy <<'EOF'
#!/sbin/openrc-run
description="Sing-box subscription HTTPS"
command="/var/lib/sbox-caddy/caddy"
command_user="sboxcaddy:sboxcaddy"
command_args="run --config /var/lib/sbox-caddy/Caddyfile --adapter caddyfile"
command_background=true
pidfile="/run/sbox-sub-caddy.pid"
output_log="/var/log/sbox-sub-caddy.log"
error_log="/var/log/sbox-sub-caddy.log"
export XDG_DATA_HOME=/var/lib/sbox-caddy/data
export XDG_CONFIG_HOME=/var/lib/sbox-caddy/data
depend() { need net; }
EOF
[[ $? == 0 ]] && chmod 755 /etc/init.d/sbox-sub-caddy || return 1
else
cat > /etc/systemd/system/sbox-sub-caddy.service <<'EOF'
[Unit]
Description=Sing-box subscription HTTPS
After=network-online.target
Wants=network-online.target
[Service]
User=sboxcaddy
Group=sboxcaddy
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
Environment=XDG_DATA_HOME=/var/lib/sbox-caddy/data
Environment=XDG_CONFIG_HOME=/var/lib/sbox-caddy/data
ExecStart=/var/lib/sbox-caddy/caddy run --config /var/lib/sbox-caddy/Caddyfile --adapter caddyfile
Restart=on-failure
RestartSec=3
[Install]
WantedBy=multi-user.target
EOF
[[ $? == 0 ]] && systemctl daemon-reload || return 1
fi
fi
sub_caddy_reload "$mode"
}

sub_httpd_pids(){
local root=${1:-/var/lib/sbox-subscription/www}
pgrep -f "(busybox|busybox-extras) httpd .* -h ${root}([[:space:]]|$)"
}

sub_httpd_running(){
sub_httpd_pids "${1:-/var/lib/sbox-subscription/www}" >/dev/null
}

sub_httpd_stop(){
local root=${1:-/var/lib/sbox-subscription/www} pids attempt
pids=$(sub_httpd_pids "$root") || return 0
kill $pids 2>/dev/null || { sub_httpd_running "$root" && return 1; }
for attempt in {1..20}; do
    sub_httpd_running "$root" || return 0
    sleep 0.1
done
return 1
}

sub_httpd_write(){
local binary=busybox
sub_valid_port "$2" || return 1
command -v busybox-extras >/dev/null 2>&1 && binary=busybox-extras
cat > /etc/s-box/sub-httpd.sh <<EOF
#!/bin/sh
exec $binary httpd -f -p 127.0.0.1:$2 -u sboxsub:sboxsub -h /var/lib/sbox-subscription/www
EOF
[[ $? == 0 ]] && chmod 700 /etc/s-box/sub-httpd.sh
}

sub_httpd_start(){
sub_valid_port "$1" && sub_valid_token "$2" || return 1
sub_httpd_stop || return 1
nohup /bin/bash /etc/s-box/sub-httpd.sh 8>&- 9>&- > /etc/s-box/sub-httpd.log 2>&1 &
local pid=$!
sleep 1
kill -0 "$pid" 2>/dev/null || { red "订阅后台启动失败，请查看 /etc/s-box/sub-httpd.log"; return 1; }
curl -q --noproxy '*' -fs --max-time 5 --config - -o /dev/null <<< "url = \"http://127.0.0.1:$1/$2/sbox.json\""
}

sub_httpd_boot(){
local cronfile
if command -v rc-service >/dev/null 2>&1; then
if [[ ${1:-} == remove ]]; then rm -f /etc/local.d/alpinesub.start; return; fi
mkdir -p /etc/local.d || return 1
printf '#!/bin/bash\nsleep 10\n/bin/bash /etc/s-box/sub-httpd.sh >/dev/null 2>&1 &\n' > /etc/local.d/alpinesub.start || return 1
chmod 755 /etc/local.d/alpinesub.start && rc-update add local default
else
cronfile=$(mktemp) || return 1
crontab -l > "$cronfile" 2>/dev/null
sed -i -e '\@/root/websbox\([^[:alnum:]_.-]\|$\)@d' -e '\@/etc/s-box/sub-httpd\.sh\([^[:alnum:]_.-]\|$\)@d' "$cronfile" || { rm -f "$cronfile"; return 1; }
[[ ${1:-} == remove ]] || echo '@reboot sleep 10 && /bin/bash /etc/s-box/sub-httpd.sh >/dev/null 2>&1 &' >> "$cronfile"
crontab "$cronfile"
local status=$?
rm -f "$cronfile"
return "$status"
fi
}

sub_https_ready(){
local domain=$1 token=$2 probe=$3 attempt name ok
sub_valid_domain "$domain" && sub_valid_token "$token" || return 1
for attempt in {1..12}; do
    ok=1
    for name in sbox.json clmi.yaml jhsub.txt; do
        if ! curl -q --noproxy '*' -fs --connect-timeout 3 --max-time 5 --resolve "$domain:443:127.0.0.1" \
            --config - -o "$probe" <<< "url = \"https://$domain/$token/$name\"" || ! cmp -s "$probe" "/etc/s-box/$name"; then
            ok=0; break
        fi
    done
    (( ok )) && return 0
    sleep 3
done
red "HTTPS 未能验证全部三份订阅。请核对证书、内容和 Caddy 配置。"
return 1
}

sub_service_user(){
local user=$1
if ! id "$user" >/dev/null 2>&1; then
    if command -v useradd >/dev/null 2>&1; then
        useradd --system --user-group --no-create-home --shell /bin/false "$user" || return 1
    else
        addgroup -S "$user" && adduser -S -D -H -G "$user" -s /bin/false "$user" || return 1
    fi
fi
[[ $(id -u "$user") != 0 && $(id -gn "$user") == "$user" ]]
}

sub_prepare_reader(){
local root=${sub_root:-/var/lib/sbox-subscription} group=${sub_group:-sboxsub}
case "$group" in sboxcaddy|sboxsub) sub_service_user "$group" || return 1 ;; esac
install -d -m 0750 -o root -g "$group" "$root" "$root/www"
}

sub_unlink(){
local root=${sub_root:-/var/lib/sbox-subscription} old
sub_valid_token "$1" || return 1
old=$(sub_snapshot_target "$root" "$1")
sub_snapshot_replace '' "$root" "$1" || return 1
if [[ "$old" =~ ^\.\./(release\.[a-zA-Z0-9]+)$ ]]; then rm -rf -- "$root/${BASH_REMATCH[1]}"; fi
}

sub_links(){
(
local root=${sub_root:-/var/lib/sbox-subscription} group=${sub_group:-sboxsub} snapshot link old name token=$1
sub_valid_token "$token" || exit 1
snapshot=$(mktemp -d "$root/release.XXXXXX") || exit 1
link="$root/.link.${snapshot##*.}"
trap 'rm -f -- "$link"; if [[ -n $snapshot && $(sub_snapshot_target "$root" "$token") != "../${snapshot##*/}" ]]; then rm -rf -- "$snapshot"; fi' EXIT
trap 'exit 1' HUP INT TERM
for name in sbox.json clmi.yaml jhsub.txt; do
    [[ -s "/etc/s-box/$name" ]] && install -m 640 -g "$group" "/etc/s-box/$name" "$snapshot/$name" || exit 1
done
chgrp "$group" "$snapshot" && chmod 750 "$snapshot" || exit 1
old=''
if [[ -L $root/www/$1 ]]; then old=$(sub_snapshot_target "$root" "$1") || exit 1; fi
ln -s "../${snapshot##*/}" "$link" && sub_snapshot_replace "$link" "$root" "$1" || exit 1
snapshot=''
[[ ${2:-} == keep-previous ]] || sub_drop_snapshot "$old"
)
}

sub_snapshot_target(){
local root target
[[ -L $1/www/$2 ]] || return 1
root=$(cd -P -- "$1" && pwd -P) && target=$(cd -P -- "$1/www/$2" 2>/dev/null && pwd -P) || return 1
[[ ${target%/*} == "$root" && ${target##*/} =~ ^release\.[a-zA-Z0-9]+$ ]] || return 1
printf '../%s\n' "${target##*/}"
}

sub_snapshot_replace(){
printf '%s' "$3" | python3 -c 'import os, sys
path = os.path.join(sys.argv[2], sys.stdin.read())
try:
    if sys.argv[1]:
        os.replace(sys.argv[1], path)
    else:
        os.unlink(path)
except FileNotFoundError:
    if sys.argv[1]:
        sys.exit("找不到候选快照链接")
except OSError:
    sys.exit("无法原子切换订阅快照")' "$1" "$2/www"
}

sub_drop_snapshot(){
if [[ $1 =~ ^\.\./(release\.[a-zA-Z0-9]+)$ ]]; then
    rm -rf -- "${sub_root:-/var/lib/sbox-subscription}/${BASH_REMATCH[1]}"
fi
}

sub_restore_snapshot(){
local root=${sub_root:-/var/lib/sbox-subscription} token=$1 previous=$2 link=$3 current
current=$(sub_snapshot_target "$root" "$token")
[[ $current != "$previous" ]] || return 0
if [[ -z $previous ]]; then
    sub_unlink "$token"
else
    [[ $previous =~ ^\.\./release\.[a-zA-Z0-9]+$ && -d $root/www/$previous ]] || return 1
    ln -s "$previous" "$link" && sub_snapshot_replace "$link" "$root" "$token" && sub_drop_snapshot "$current"
fi
}

sub_restore_legacy_httpd(){
local domain=$1 token=$2 port=$3 address=$3 binary=busybox pid
sub_httpd_running /root/websbox && return 0
sub_valid_port "$port" && sub_valid_token "$token" || return 1
[[ -z $domain ]] || address=127.0.0.1:$port
command -v busybox-extras >/dev/null 2>&1 && binary=busybox-extras
nohup "$binary" httpd -f -p "$address" -h /root/websbox 8>&- 9>&- > /etc/s-box/sub-httpd.log 2>&1 &
pid=$!
sleep 1
kill -0 "$pid" 2>/dev/null && curl -q --noproxy '*' -fs --max-time 5 --config - -o /dev/null <<< "url = \"http://127.0.0.1:$port/$token/sbox.json\""
}

# Callers hold subscription.lock from the first read through this commit.
sub_publish_current(){
(
local stage=$1 backup name mutating=0 committed=0 token='' previous='' restore_link
shift
local -a files=("$@")
local sub_root=/var/lib/sbox-subscription sub_group=sboxsub
if [[ -s /etc/s-box/subdomain.log && ${sub_switching:-0} != 1 ]]; then
    sub_root=/var/lib/sbox-subscription-https
    token=$(cat /etc/s-box/subtoken.log) && sub_valid_token "$token" && token=$(sub_https_key "$token") || exit 1
    [[ -L $sub_root/www/$token ]] || { red "请先通过菜单 3 → 10 将旧 HTTPS 订阅切换为直接读取快照" >&2; exit 1; }
    sub_group=$(stat -c %g "$sub_root") || exit 1
fi
local web=$sub_root/www
if [[ ${sub_switching:-0} != 1 && -d $web && -s /etc/s-box/subtoken.log ]]; then
    if [[ -z $token ]]; then token=$(cat /etc/s-box/subtoken.log) && sub_valid_token "$token" || exit 1; fi
    if [[ -L $web/$token ]]; then
        previous=$(sub_snapshot_target "$sub_root" "$token")
        [[ $previous =~ ^\.\./release\.[a-zA-Z0-9]+$ && -d $web/$previous ]] || exit 1
    else
        # A first migration may have prepared directories but rolled back to the legacy HTTP listener.
        token=''
    fi
fi
backup=$(mktemp -d /etc/s-box/.publish-backup.XXXXXX) || exit 1
restore_link="$sub_root/.restore.${backup##*.}"
sub_finish_publish(){
    local status=$? restored=1
    trap - EXIT
    trap '' HUP INT TERM
    if (( committed )); then
        status=0
        [[ -z $token ]] || sub_drop_snapshot "$previous" || status=1
        (( ! status )) || red "新订阅文件已发布。清理前一份快照失败，请检查发布目录权限。" >&2
    elif (( mutating )); then
        [[ -z $token ]] || sub_restore_snapshot "$token" "$previous" "$restore_link" || restored=0
        for name in "${files[@]}"; do
            if [[ -f $backup/$name ]]; then command mv -f -- "$backup/$name" "/etc/s-box/$name" || restored=0
            else rm -f -- "/etc/s-box/$name" || restored=0; fi
        done
        status=1
    fi
    if (( restored )); then rm -rf -- "$backup"
    else printf '订阅回滚失败，原文件备份保留在 %s\n' "$backup" >&2; fi
    [[ -z $token ]] || rm -f -- "$restore_link"
    exit "$status"
}
trap sub_finish_publish EXIT
trap 'exit 1' HUP INT TERM
for name in "${files[@]}"; do
    [[ -s $stage/$name ]] || exit 1
    if [[ -e /etc/s-box/$name ]]; then cp -p -- "/etc/s-box/$name" "$backup/$name" || exit 1; fi
done
mutating=1
for name in "${files[@]}"; do
    mv -f -- "$stage/$name" "/etc/s-box/$name" || exit 1
done
[[ -z $token ]] || sub_links "$token" keep-previous || exit 1
committed=1
)
}

sub_apply(){
config_locked sub_apply_serialized "$@"
}

sub_apply_serialized(){
( flock -x 9 && sub_apply_locked "$@" ) 9>/etc/s-box/subscription.lock
}

sub_apply_locked(){
(
local legacy=0 generate=1
local domain=$1 token=$2 port=$3 mode='' olddomain oldtoken oldport backup i name
local httpd_active=0 caddy_active=0 caddy_enabled=0 service='' committed=0 boot_changed=0
local sub_root=/var/lib/sbox-subscription sub_group=sboxsub snapshot_key=$token
local -a reader_groups=()
local previous restore_link legacy_stopping=0 httpd_touched=0 caddy_touched=0 local_enabled=0
sub_valid_token "$token" && { if [[ -n "$domain" ]]; then sub_valid_domain "$domain"; else sub_valid_port "$port"; fi; } || {
red "域名、订阅令牌或端口格式无效"; return 1;
}
olddomain=$(cat /etc/s-box/subdomain.log 2>/dev/null)
oldtoken=$(cat /etc/s-box/subtoken.log 2>/dev/null)
oldport=$(cat /etc/s-box/subport.log 2>/dev/null)
if [[ -z "$domain" && -n "$olddomain" ]]; then
    red "请先通过选项 4 停用域名订阅，再设置回环入口"; return 1
fi
if [[ -f /etc/s-box/gitlab-publisher && "$token" == "$(sed -n '3p' /etc/s-box/gitlab-publisher)" ]]; then
    red "订阅路径必须使用独立令牌"; return 1
fi
sub_httpd_running /root/websbox && legacy=1
if (( legacy )) && [[ -z $domain && "$port" == "$oldport" ]]; then
    red "旧订阅仍在运行，请为 HTTPS 验证选择另一个后台端口"; return 1
fi
if [[ -z "$olddomain" && -n "$oldtoken" && -d /root/websbox ]]; then
    [[ -n "$domain" && "$token" != "$oldtoken" ]] || {
        red "旧公网 HTTP 入口需用 3 → 10 迁移到新 HTTPS 令牌，或用 3 → 8 → 4 停用"; return 1;
    }
    # The legacy listener follows the files in /etc/s-box. Rotate node secrets after cutover.
    generate=0
fi
if [[ -n "$domain" ]]; then
mode=$(sub_caddy_prepare) || return 1
service=sbox-sub-caddy
sub_root=/var/lib/sbox-subscription-https
snapshot_key=$(sub_https_key "$token") || return 1
sub_group=sboxcaddy
if [[ $mode == shared ]]; then
    service=caddy
    sub_group=$(sub_caddy_group) || return 1
fi
sub_service status "$service" >/dev/null 2>&1 && caddy_active=1
sub_service enabled "$service" >/dev/null 2>&1 && caddy_enabled=1
fi
sub_httpd_running && httpd_active=1
backup=$(mktemp -d /etc/s-box/.subscription.XXXXXX) || return 1
local -a files=(/etc/s-box/subdomain.log /etc/s-box/subtoken.log /etc/s-box/subport.log
/etc/s-box/sbox.json /etc/s-box/clmi.yaml /etc/s-box/jhsub.txt
/etc/s-box/sub-caddy-mode /etc/s-box/sub-httpd.sh /etc/s-box/sub-Caddyfile
/etc/caddy/s-box-subscription.caddy /etc/local.d/alpinesub.start
/etc/init.d/sbox-sub-caddy /etc/systemd/system/sbox-sub-caddy.service)
[[ "$mode" != shared ]] || files+=(/etc/caddy/Caddyfile)
for i in "${!files[@]}"; do
if [[ -f "${files[$i]}" ]] && ! cp -p "${files[$i]}" "$backup/$i"; then
rm -rf "$backup"; return 1
fi
done
crontab -l > "$backup/cron" 2>/dev/null
if command -v rc-service >/dev/null && sub_service enabled local >/dev/null 2>&1; then local_enabled=1; fi
previous=''
if [[ -L $sub_root/www/$snapshot_key || -e $sub_root/www/$snapshot_key ]]; then
    previous=$(sub_snapshot_target "$sub_root" "$snapshot_key") || {
        red "现有订阅目录无法定位到项目快照，保留原配置"; rm -rf -- "$backup"; exit 1
    }
fi
printf '%s\n' "$previous" > "$backup/previous-snapshot" || { rm -rf -- "$backup"; exit 1; }
restore_link="$sub_root/.restore.${backup##*.}"
local -a reader_dirs=("$sub_root" "$sub_root/www")
for i in "${!reader_dirs[@]}"; do
    if [[ -d ${reader_dirs[$i]} ]]; then
        reader_groups[$i]=$(stat -c %g "${reader_dirs[$i]}") || exit 1
    fi
done
sub_finish_apply(){
    local status=$? rollback_ok=1
    trap - EXIT
    trap '' HUP INT TERM
    if (( committed )); then
        status=0
        sub_drop_snapshot "$previous" || status=1
        if sub_valid_token "$oldtoken"; then
            if [[ -n $domain ]]; then
                if [[ $oldtoken != "$token" && -n $olddomain ]]; then
                    local old_key
                    old_key=$(sub_https_key "$oldtoken") && sub_unlink "$old_key" || status=1
                fi
                ( sub_root=/var/lib/sbox-subscription; sub_unlink "$oldtoken" ) || status=1
            else
                [[ $oldtoken == "$token" ]] || sub_unlink "$oldtoken" || status=1
            fi
            if [[ -d /root/websbox/$oldtoken ]]; then rm -rf -- "/root/websbox/$oldtoken" || status=1; fi
        fi
        (( ! status )) || red "新订阅入口已生效。清理旧快照失败，请检查旧文件权限。"
    else
        (( ! httpd_touched )) || sub_httpd_stop || rollback_ok=0
        # OpenRC needs the candidate init script to stop a newly started service.
        if (( caddy_touched && ! caddy_active )) && sub_service status "$service" >/dev/null 2>&1; then
            sub_service stop "$service" >/dev/null 2>&1 || rollback_ok=0
        fi
        if (( caddy_touched && ! caddy_enabled )) && sub_service enabled "$service" >/dev/null 2>&1; then
            sub_service disable "$service" >/dev/null 2>&1 || rollback_ok=0
        fi
        sub_restore_snapshot "$snapshot_key" "$previous" "$restore_link" || rollback_ok=0
        for i in "${!reader_groups[@]}"; do
            if [[ $(stat -c %g "${reader_dirs[$i]}") != "${reader_groups[$i]}" ]]; then
                chgrp "${reader_groups[$i]}" "${reader_dirs[$i]}" || rollback_ok=0
            fi
        done
        for i in "${!files[@]}"; do
            if [[ -f "$backup/$i" ]]; then cp -p "$backup/$i" "${files[$i]}" || rollback_ok=0
            else rm -f "${files[$i]}" || rollback_ok=0; fi
        done
        if (( boot_changed )); then
            if command -v rc-service >/dev/null; then
                if (( ! local_enabled )) && sub_service enabled local >/dev/null 2>&1; then
                    rc-update del local default >/dev/null 2>&1 || rollback_ok=0
                fi
            else crontab "$backup/cron" || rollback_ok=0; fi
        fi
        if (( caddy_touched )); then
            command -v rc-service >/dev/null || systemctl daemon-reload || rollback_ok=0
            (( ! caddy_active )) || sub_caddy_reload "$mode" >/dev/null 2>&1 || rollback_ok=0
        fi
        if (( httpd_touched && httpd_active )); then
            if [[ ! -f /etc/s-box/sub-httpd.sh ]]; then
                sub_valid_port "$oldport" && sub_httpd_write "$olddomain" "$oldport" || rollback_ok=0
            fi
            sub_httpd_start "$oldport" "$oldtoken" >/dev/null 2>&1 || rollback_ok=0
        fi
        if (( legacy_stopping )); then
            sub_restore_legacy_httpd "$olddomain" "$oldtoken" "$oldport" || rollback_ok=0
        fi
        status=1
        (( ! rollback_ok )) || red "订阅入口设置失败，已恢复原配置"
    fi
    rm -f -- "$restore_link"
    if (( rollback_ok )); then rm -rf -- "$backup"
    else red "订阅回滚遇到错误，备份保留在 $backup，请核对服务和订阅快照"; fi
    exit "$status"
}
trap sub_finish_apply EXIT
trap 'exit 1' HUP INT TERM
if (( generate )); then
    sbshare subscription-switch >/dev/null 2>&1 || { red "订阅生成失败，保留现有入口"; exit 1; }
fi
sub_prepare_reader || { red "无法设置订阅快照读取组"; exit 1; }
sub_links "$snapshot_key" keep-previous || exit 1
if [[ -z $domain ]]; then
    sub_httpd_write "$domain" "$port" || exit 1
    httpd_touched=1
    sub_httpd_start "$port" "$token" || exit 1
fi
if [[ -n $domain ]]; then
    caddy_touched=1
    sub_caddy_apply "$domain" "$token" "$port" "$mode" &&
        sub_https_ready "$domain" "$token" "$backup/probe" || exit 1
fi
printf '%s\n' "$token" > /etc/s-box/subtoken.log &&
    printf '%s\n' "$port" > /etc/s-box/subport.log || exit 1
if [[ -n $domain ]]; then
    printf '%s\n' "$domain" > /etc/s-box/subdomain.log &&
        printf '%s\n' "$mode" > /etc/s-box/sub-caddy-mode || exit 1
fi
[[ -z $service ]] || sub_service enable "$service" || exit 1
boot_changed=1
sub_httpd_boot "${domain:+remove}" || exit 1
if [[ -n $domain ]]; then
    if (( httpd_active )); then httpd_touched=1; sub_httpd_stop || exit 1; fi
    rm -f /etc/s-box/sub-httpd.sh || exit 1
fi
if (( legacy )); then
    legacy_stopping=1
    sub_httpd_stop /root/websbox || exit 1
fi
committed=1
exit 0
)
}

sub_remove(){
[[ -d /etc/s-box ]] || return 0
( flock -x 9 && sub_remove_locked ) 9>/etc/s-box/subscription.lock
}

sub_remove_locked(){
local mode cronfile saved caddy_active=0
mode=$(cat /etc/s-box/sub-caddy-mode 2>/dev/null)
if [[ "$mode" == shared ]]; then
sub_service status caddy >/dev/null 2>&1 && caddy_active=1
saved=$(mktemp) || return 1
cp -p /etc/caddy/Caddyfile "$saved" || { rm -f "$saved"; return 1; }
sed -i '\|^import /etc/caddy/s-box-subscription.caddy # s-box subscription$|d' /etc/caddy/Caddyfile
if ! caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile ||
{ [[ "$caddy_active" == 1 ]] && ! sub_caddy_reload shared; }; then
cp -p "$saved" /etc/caddy/Caddyfile
[[ "$caddy_active" != 1 ]] || sub_caddy_reload shared >/dev/null 2>&1
rm -f "$saved"
red "无法停用 Caddy 订阅站点，已保留订阅配置"
return 1
fi
rm -f "$saved"
elif [[ "$mode" == managed ]]; then
if sub_service status sbox-sub-caddy >/dev/null 2>&1; then
sub_service stop sbox-sub-caddy >/dev/null 2>&1 || return 1
fi
sub_service disable sbox-sub-caddy >/dev/null 2>&1
fi
sub_httpd_stop && sub_httpd_stop /root/websbox || return 1
cronfile=$(mktemp) || return 1
crontab -l > "$cronfile" 2>/dev/null
sed -i -e '\@/root/websbox\([^[:alnum:]_.-]\|$\)@d' -e '\@/etc/s-box/sub-httpd\.sh\([^[:alnum:]_.-]\|$\)@d' "$cronfile"
crontab "$cronfile" >/dev/null 2>&1
rm -f "$cronfile" /etc/local.d/alpinesub.start
rm -f /etc/caddy/s-box-subscription.caddy /etc/s-box/sub-Caddyfile /etc/s-box/sub-httpd.sh
rm -f /etc/s-box/subdomain.log /etc/s-box/subtoken.log /etc/s-box/subport.log /etc/s-box/sub-caddy-mode
rm -rf /root/websbox /var/lib/sbox-subscription /var/lib/sbox-subscription-https
# Keep Caddy's certificates for reactivation under the same service account.
rm -f /etc/init.d/sbox-sub-caddy /etc/systemd/system/sbox-sub-caddy.service
command -v rc-service >/dev/null 2>&1 || systemctl daemon-reload
}

sub_show_urls(){
local domain token port base name
domain=$(cat /etc/s-box/subdomain.log 2>/dev/null)
token=$(cat /etc/s-box/subtoken.log 2>/dev/null)
port=$(cat /etc/s-box/subport.log 2>/dev/null)
sub_valid_token "$token" || { yellow "尚未设置订阅"; return 1; }
if [[ -n "$domain" ]]; then
base="https://$domain/$token"
else
if [[ ! -L /var/lib/sbox-subscription/www/$token ]]; then
    yellow "检测到旧 HTTP 配置。请通过 3 → 10 迁移。"; return 1
fi
base="http://127.0.0.1:$port/$token"
fi
for name in sbox.json clmi.yaml jhsub.txt; do
blue "$base/$name"
if [[ "$1" != noqr ]] && command -v qrencode >/dev/null 2>&1; then
printf '%s' "$base/$name" | qrencode -o - -t ANSIUTF8
fi
done
}

ipsub(){
local menu=${1:-} domain token port
domain=$(cat /etc/s-box/subdomain.log 2>/dev/null)
token=$(cat /etc/s-box/subtoken.log 2>/dev/null)
port=$(cat /etc/s-box/subport.log 2>/dev/null)
if [[ -z "$menu" ]]; then
yellow "1：设置仅本机可读的 HTTP 订阅（127.0.0.1）"
yellow "2：更换订阅链接路径密码"
yellow "3：更换订阅后台端口"
yellow "4：停用并卸载订阅入口（IP/域名）"
yellow "5：启用/更换自定义域名订阅（HTTPS）"
yellow "6：查看订阅链接及二维码"
yellow "0：返回上层"
readp "请选择【0-6】：" menu
fi
case "$menu" in
1)
[[ -z "$domain" ]] || { red "请先选择 4 停用域名订阅"; return 1; }
readp "输入订阅路径密码（仅字母数字、下划线、短横线；回车随机生成）：" token
readp "输入未占用的订阅端口（回车随机）：" port
;;
2)
[[ -n "$token" ]] || { red "请先启用订阅"; return 1; }
readp "输入新的订阅路径密码（回车随机生成）：" token
;;
3)
[[ -z "$domain" ]] || { red "HTTPS 订阅直接读取快照，无后台端口"; return 1; }
[[ -n "$token" ]] || { red "请先启用订阅"; return 1; }
readp "输入未占用的订阅后台端口（回车随机）：" port
;;
4) sub_remove && green "订阅入口已停用，GitLab 配置保留"; return ;;
5)
blue "请将独立订阅域名解析到本机，并放行 TCP 80/443。已有 Caddy 将增加独立站点配置。"
readp "输入订阅域名（例如 sub.example.com，不含 https:// 或路径）：" domain
domain=${domain,,}
sub_valid_domain "$domain" || { red "请输入有效的订阅域名"; return 1; }
readp "输入订阅令牌（至少16位字母数字、下划线或短横线；回车随机生成）：" token
[[ -z "$token" || ${#token} -ge 16 ]] || { red "域名订阅令牌至少16位"; return 1; }
port=
;;
6) sub_show_urls; return ;;
*) changeserv; return ;;
esac
[[ -n "$token" ]] || token=$(openssl rand -hex 24) || return 1
[[ -z "$domain" || ${#token} -ge 16 ]] || { red "域名订阅令牌至少16位"; return 1; }
if [[ -n $domain ]]; then port=; elif [[ -z $port ]]; then port=$(shuf -i 10000-65535 -n 1) || return 1; fi
if sub_apply "$domain" "$token" "$port"; then
green "订阅入口已更新；客户端刷新订阅后获取最新节点"
sub_show_urls
else
return 1
fi
}

vmesscfadd(){
local host= menu
echo
green "推荐使用稳定的世界大厂或组织的官方CDN域名作为CDN优选地址："
blue "cloudflare-ech.com"
blue "www.visa.com.sg"
blue "www.wto.org"
blue "www.shopify.com"
echo
yellow "恢复默认操作：选项1设置为VPS的IP或者解析的域名，选项2设置为www.bing.com或者解析的域名"
echo
yellow "1：自定义Vmess-ws(tls)主协议节点的CDN优选地址"
yellow "2：针对选项1，重置客户端host/sni域名(IP解析到CF上的域名)"
yellow "3：自定义Vmess-ws(tls)-Argo节点的CDN优选地址"
yellow "0：返回上层"
readp "请选择【0-3】：" menu
if [ "$menu" = "1" ]; then
echo
green "请确保VPS的IP已解析到Cloudflare的域名上"
if [[ ! -f /etc/s-box/cfymjx.txt ]] 2>/dev/null; then
readp "输入客户端host/sni域名(IP解析到CF上的域名)：" host || return 1
sub_valid_domain "$host" || { red "域名无效"; return 1; }
fi
echo
readp "输入自定义的优选IP/域名：" menu || return 1
valid_node_address "$menu" || { red "优选地址无效"; return 1; }
[[ -z $host ]] || printf '%s\n' "$host" > /etc/s-box/cfymjx.txt || return 1
printf '%s\n' "$menu" > /etc/s-box/cfvmadd_local.txt || return 1
sbshare > /dev/null 2>&1
green "设置成功，选择主菜单9进行节点配置更新" && sleep 2 && vmesscfadd
elif  [ "$menu" = "2" ]; then
rm -rf /etc/s-box/cfymjx.txt
sbshare > /dev/null 2>&1
green "重置成功，可选择1重新设置" && sleep 2 && vmesscfadd
elif  [ "$menu" = "3" ]; then
readp "输入自定义的优选IP/域名：" menu || return 1
valid_node_address "$menu" || { red "优选地址无效"; return 1; }
printf '%s\n' "$menu" > /etc/s-box/cfvmadd_argo.txt || return 1
sbshare > /dev/null 2>&1
green "设置成功，选择主菜单9进行节点配置更新" && sleep 2 && vmesscfadd
else
changeserv
fi
}

gitlab_settings_valid(){
local project=$1 branch=$2 segment
[[ "$project" == */* && "$project" != /* && "$project" != */ && "$project" != *//* && ${project,,} != *.git ]] || return 1
local -a segments
IFS=/ read -ra segments <<< "$project"
for segment in "${segments[@]}"; do
    [[ "$segment" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]*$ ]] || return 1
done
[[ "$branch" != -* ]] && git check-ref-format "refs/heads/$branch" >/dev/null
}

gitlab_copy_subscriptions(){
(
flock -s 9 || exit 1
local name
for name in sbox.json clmi.yaml jhsub.txt; do
    [[ -s /etc/s-box/$name ]] && cp "/etc/s-box/$name" "$1/$name" || exit 1
done
) 9>/etc/s-box/subscription.lock
}

gitlab_publish(){
(
set +x
umask 077
local settings=$1 verify_write=${2:-} project branch remote stage status token metadata
{ IFS= read -r project && IFS= read -r branch && IFS= read -r token; } < "$settings" || exit 1
gitlab_settings_valid "$project" "$branch" || exit 1
[[ -n $token && $token != *[[:cntrl:]]* ]] || exit 1
local -a probe=(-q --noproxy '*' --proto '=https' -sS --connect-timeout 5 --max-time 15)
metadata=$(printf 'PRIVATE-TOKEN: %s\n' "$token" | curl "${probe[@]}" --fail --header @- \
    "https://gitlab.com/api/v4/projects/${project//\//%2F}") || exit 1
unset token
jq -se --arg project "${project,,}" 'length==1 and (.[0] | .visibility=="private" and
    (.path_with_namespace | ascii_downcase)==$project)' <<< "$metadata" >/dev/null || exit 1
remote="https://gitlab.com/$project.git"
status=$(curl "${probe[@]}" --output /dev/null --write-out '%{http_code}' \
    "$remote/info/refs?service=git-upload-pack") || exit 1
[[ $status == 401 || $status == 404 ]] || exit 1
stage=$(mktemp -d /etc/s-box/.git-publish.XXXXXX) || exit 1
trap 'rm -rf -- "$stage"' EXIT
trap 'exit 1' HUP INT TERM
# Ignore interactive/global credential helpers; askpass supplies this project's secret only.
export GIT_TERMINAL_PROMPT=0 GIT_ASKPASS="$stage/askpass" SB_GIT_SETTINGS="$settings"
unset GIT_TRACE GIT_TRACE_PACKET GIT_TRACE_CURL GIT_CURL_VERBOSE GIT_TRACE2 GIT_TRACE2_EVENT GIT_TRACE2_PERF
unset GIT_CONFIG GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT GIT_SSL_NO_VERIFY GIT_DIR GIT_COMMON_DIR GIT_WORK_TREE GIT_INDEX_FILE
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
cat > "$GIT_ASKPASS" <<'ASKPASS' || exit 1
#!/bin/sh
case "$1" in
*Username*) printf '%s\n' oauth2 ;;
*Password*) sed -n '3p' "$SB_GIT_SETTINGS" ;;
*) exit 1 ;;
esac
ASKPASS
chmod 700 "$GIT_ASKPASS" || exit 1
export LC_ALL=C
cd "$stage" && git init -q --template= repo && cd repo || exit 1
git config credential.helper '' && git config http.followRedirects false && git config http.sslVerify true &&
git config core.hooksPath /dev/null && git config user.name 's-box publisher' &&
git config user.email 's-box@localhost' || exit 1
git ls-remote --exit-code "$remote" "refs/heads/$branch" >/dev/null
status=$?
case "$status" in
0) git fetch -q --depth=1 "$remote" "refs/heads/$branch" && git reset -q --mixed FETCH_HEAD || exit 1 ;;
2) ;; # First publication to a new branch.
*) exit 1 ;;
esac
gitlab_copy_subscriptions . || exit 1
git add -f -- sbox.json clmi.yaml jhsub.txt || exit 1
if ! git diff --cached --quiet; then
    git commit -qm 'Update subscription files' || exit 1
elif [[ $verify_write == verify-write ]]; then
    # A new credential must pass the target branch's actual update rules.
    git commit -q --allow-empty -m 'Verify subscription publisher' || exit 1
fi
git push -q "$remote" "HEAD:refs/heads/$branch"
) >/dev/null 2>&1
}

gitlab_clear_legacy(){
local url
if [[ -d /etc/s-box/.git ]]; then
    url=$(git -C /etc/s-box config --local --get remote.origin.url 2>/dev/null)
    case "$url" in https://*@gitlab.com/*|https://gitlab.com/*)
        git -C /etc/s-box remote remove origin >/dev/null 2>&1 || return 1 ;;
    esac
fi
rm -f /etc/s-box/{gitpush.sh,gitlabtoken.txt,gitlab_ml_ml,sing_box_gitlab.txt,clash_meta_gitlab.txt,jh_sub_gitlab.txt}
}

gitlabsub(){
(
set +x
umask 077
local menu project branch secret candidate
readp "1：配置私有项目发布  2：发布最新订阅  3：清理旧版凭据与链接  0：返回：" menu || exit 1
case "$menu" in
1)
    readp "GitLab 私有项目路径（例如 my-group/my-project）：" project || exit 1
    readp "发布分支（回车使用 main）：" branch || exit 1
    branch=${branch:-main}
    gitlab_settings_valid "$project" "$branch" || { red "项目或分支名称无效"; exit 1; }
    IFS= read -rs -p "输入独立的项目发布令牌（read_api 和 write_repository）：" secret || exit 1
    printf '\n'
    [[ -n "$secret" && "$secret" != "$(cat /etc/s-box/subtoken.log 2>/dev/null)" &&
       "$secret" != "$(cat /etc/s-box/gitlabtoken.txt 2>/dev/null)" ]] ||
        { red "请新建独立发布令牌"; exit 1; }
    candidate=$(mktemp /etc/s-box/.gitlab-settings.XXXXXX) || exit 1
    trap 'rm -f -- "$candidate"' EXIT
    trap 'exit 1' HUP INT TERM
    printf '%s\n' "$project" "$branch" "$secret" > "$candidate" || exit 1
    unset secret
    if ! sbshare >/dev/null 2>&1 || ! gitlab_publish "$candidate" verify-write; then
        red "发布失败，请核对私有项目可见性、分支权限和网络"; exit 1
    fi
    mv -f -- "$candidate" /etc/s-box/gitlab-publisher && gitlab_clear_legacy || exit 1
    green "三份文件已发布。通过 3 → 10 的 HTTPS 入口分发给客户端。"
    ;;
2) sbshare >/dev/null 2>&1 && gitlabsubgo ;;
3) gitlab_clear_legacy && green "本机旧凭据与链接已清理。请在 GitLab 撤销旧令牌。" ;;
esac
)
}

gitlabsubgo(){
[[ -s /etc/s-box/gitlab-publisher ]] || { yellow "尚未配置独立 GitLab 发布凭据"; return 1; }
gitlab_publish /etc/s-box/gitlab-publisher || { red "GitLab 发布失败，远程分支未确认更新"; return 1; }
green "GitLab 订阅已同步"
}

warpwg(){
local profile=/etc/s-box/server_warp.json family=4
[[ $endip != *:* ]] || family=6
if [[ -e $profile ]]; then
    client_warp_validate "$profile" | jq -e '.enabled' >/dev/null || { red "已有服务端 WARP 配置无效，已停止安装"; return 1; }
elif ! client_warp_register "$profile" "$family"; then
    yellow "WARP 注册失败，WARP 出站已停用，匹配其分流规则的流量将被拒绝。可在 3 → 6 重试或导入。"
fi
return 0
}

changewg(){
(
local choice source candidate
readp "1：重新注册独立 WARP 账户\n2：从本地文件导入独立服务端 WARP JSON\n0：返回\n请选择：" choice || exit 1
[[ $choice == 1 || $choice == 2 ]] || exit 0
candidate=$(mktemp /etc/s-box/.server-warp.XXXXXX) || exit 1
trap 'rm -f -- "$candidate"' EXIT
trap 'exit 1' HUP INT TERM
if [[ $choice == 1 ]]; then
    client_warp_register "$candidate" || { red "WARP 注册失败，保留现有出站与分流规则"; exit 1; }
else
    readp "输入服务端 WARP JSON 文件路径：" source || exit 1
    client_warp_validate "$source" > "$candidate" || exit 1
fi
jq -e '.enabled' "$candidate" >/dev/null || exit 1
config_apply config_patch_warp_profile "$candidate" || exit 1
mv -f -- "$candidate" /etc/s-box/server_warp.json || exit 1
green "已启用独立 WARP 账户，原 WARP 分流仍走该出站"
)
}

sbymfl(){
local channel domains groups text target label
warp_s4_ip=${warp_s4_ip:-Socks5-IPV4未探测}
warp_s6_ip=${warp_s6_ip:-Socks5-IPV6未探测}
vps_ipv4=${vps_ipv4:-公网IPv4未探测}
vps_ipv6=${vps_ipv6:-公网IPv6未探测}
for channel in warp4 warp6 socks4 socks6 direct4 direct6; do
case "$channel" in
warp4) target=wfl4; label=warp出站IPV4未探测 ;;
warp6) target=wfl6; label=warp出站IPV6未探测 ;;
socks4) target=sfl4; label=$warp_s4_ip ;;
socks6) target=sfl6; label=$warp_s6_ip ;;
direct4) target=adfl4; label=$vps_ipv4 ;;
direct6) target=adfl6; label=$vps_ipv6 ;;
esac
if [[ "$sbnh" != 1.10 && "$channel" != warp6 && "$channel" != socks4 ]]; then
printf -v "$target" '%s' '暂不支持'
continue
fi
domains=$(config_rule_get /etc/s-box/sb.json "$sbnh" "$channel" domain_suffix) || return 1
groups=$(config_rule_get /etc/s-box/sb.json "$sbnh" "$channel" geosite) || return 1
[[ "$domains" != yg_kkk ]] || domains=
[[ "$groups" != yg_kkk ]] || groups=
text=${domains}${groups:+ ${groups}}
if [[ -n "$text" ]]; then text="已分流：$text"; else text=未分流; fi
printf -v "$target" '%s' "${yellow}【$label】$text${plain}"
done
}

changefl(){
sbactive
blue "对所有协议进行统一的域名分流"
blue "为确保分流可用，双栈IP（IPV4/IPV6）分流模式为优先模式"
blue "warp-wireguard默认开启 (选项1与2)"
blue "socks5需要在VPS安装warp官方客户端或者WARP-plus-Socks5-赛风VPN (选项3与4)"
blue "VPS本地出站分流(选项5与6)"
echo
[[ "$sbnh" == "1.10" ]] && blue "当前Sing-box内核支持geosite分流方式" || blue "当前Sing-box内核不支持geosite分流方式，仅支持分流2、3选项"
echo
yellow "注意："
yellow "一、后缀域名方式只能填域名 (例：谷歌网站填写：google.com googleapis.com)"
yellow "二、geosite方式须填写geosite规则名 (例：奈飞填写netflix ；迪士尼填写disney ；ChatGPT填写openai ；全局且绕过中国填写geolocation-!cn)"
yellow "三、同一个完整域名或者geosite切勿重复分流"
yellow "四、如分流通道中有个别通道无网络，所填分流为黑名单模式，即屏蔽该网站访问"
changef
}

changef(){
local menu channel field domains input
while true; do
sbymfl || return 1
green "1：WARP IPv4 $wfl4"
green "2：WARP IPv6 $wfl6"
green "3：SOCKS IPv4 $sfl4"
green "4：SOCKS IPv6 $sfl6"
green "5：本机 IPv4 $adfl4"
green "6：本机 IPv6 $adfl6"
green "0：返回上层"
readp "请选择：" menu || return 1
case "$menu" in
1) channel=warp4 ;; 2) channel=warp6 ;;
3) channel=socks4 ;; 4) channel=socks6 ;;
5) channel=direct4 ;; 6) channel=direct6 ;;
*) sing_box; return ;;
esac
if [[ "$sbnh" != 1.10 && "$channel" != warp6 && "$channel" != socks4 ]]; then
yellow "当前内核不支持此通道，请使用1.10系列内核"
continue
fi
readp "1：后缀域名\n2：geosite\n3：返回\n请选择：" menu || return 1
case "$menu" in
1) field=domain_suffix ;;
2) [[ "$sbnh" == 1.10 ]] || { yellow "当前内核不支持geosite"; continue; }; field=geosite ;;
*) continue ;;
esac
readp "每项之间留空格，回车重置此分流通道：" input || return 1
domains=$(jq -cn --arg value "$input" '$value | [splits("\\s+") | select(length>0)]') || return 1
config_apply config_patch_route "$channel" "$field" "$domains" || return 1
green "分流配置已更新"
done
}

restartsb(){
[[ ! -e /etc/s-box/admin-stopped ]] || return 0
singbox_require_owned || return 1
# Only transaction callers that checked the candidate may pass "checked".
if [[ ${1:-} != checked ]]; then
local check_args=(-c /etc/s-box/sb.json)
[[ ! -f /etc/s-box/sb_extra.json ]] || check_args+=(-c /etc/s-box/sb_extra.json)
/etc/s-box/sing-box check "${check_args[@]}" || return 1
fi
if command -v apk >/dev/null 2>&1; then
rc-service sing-box restart 8>&- 9>&-
else
systemctl restart sing-box
fi
}

singbox_maintenance(){
[[ ! -e /etc/s-box/admin-stopped ]] || return 0
sub_service status sing-box >/dev/null 2>&1 || return 0
restartsb
}

argo_stop_fixed(){
local runlevel
if argo_unit_owned 2>/dev/null; then
    sub_service stop argo || return 1
    if command -v rc-service >/dev/null 2>&1; then
        for runlevel in /etc/runlevels/*; do
            [[ ! -L $runlevel/argo ]] || rc-update del argo "${runlevel##*/}" || return 1
        done
    elif sub_service enabled argo; then sub_service disable argo || return 1; fi
fi
project_process fixed '' stop
}

argo_stop_temporary(){
local jobs
if [[ -f /etc/local.d/alpineargo.start ]] &&
    grep -Eq '/etc/s-box/cloudflared tunnel --url |/usr/bin/sing_box --argo-boot' /etc/local.d/alpineargo.start; then
    rm -f /etc/local.d/alpineargo.start || return 1
fi
if jobs=$(crontab -l 2>&1); then :
elif [[ -z $jobs || $jobs == *"no crontab for"* || $jobs == *"No such file or directory"* ]]; then jobs=
else red "无法读取项目隧道的开机任务" >&2; return 1; fi
if grep -Eq '^@reboot .*(/etc/s-box/cloudflared tunnel --url |/usr/bin/sing_box --argo-boot$)' <<< "$jobs"; then
    (set -o pipefail; printf '%s\n' "$jobs" |
        sed -e '/^@reboot .*\/etc\/s-box\/cloudflared tunnel --url /d' -e '\|^@reboot sleep 10 && /usr/bin/sing_box --argo-boot$|d' |
        crontab -) || return 1
fi
project_process temporary-all '' stop
}

argo_stop_for_origin(){
argo_stop_fixed && argo_stop_temporary
}

singbox_finish_stop(){
singbox_require_owned || return 1
local failed=0 runlevel
argo_stop_for_origin && cronsb remove || return 1
sub_service stop sing-box || failed=1
if command -v rc-service >/dev/null 2>&1; then
    for runlevel in /etc/runlevels/*; do
        if [[ -L $runlevel/sing-box ]]; then
            rc-update del sing-box "${runlevel##*/}" && [[ ! -L $runlevel/sing-box ]] || failed=1
        fi
    done
elif sub_service enabled sing-box; then
    sub_service disable sing-box || failed=1
fi
if sub_service status sing-box >/dev/null 2>&1 || sub_service enabled sing-box >/dev/null 2>&1; then
    red "Sing-box 仍在运行或自启仍启用"; failed=1
fi
return "$failed"
}

singbox_stop(){
(
singbox_require_owned || exit 1
trap 'exit 1' HUP INT TERM
trap 'status=$?; trap - EXIT; trap "" HUP INT TERM
    if (( status )) && [[ -e /etc/s-box/admin-stopped ]]; then
        singbox_finish_stop || red "停服清理未完成，请检查服务、定时任务和自启项"
    fi
    exit "$status"' EXIT
: > /etc/s-box/admin-stopped && singbox_finish_stop
)
}

singbox_enable(){
(
singbox_require_owned || exit 1
local stopped=0 committed=0
[[ ! -e /etc/s-box/admin-stopped ]] || stopped=1
trap 'exit 1' HUP INT TERM
trap 'status=$?; trap - EXIT; trap "" HUP INT TERM
    if (( stopped && ! committed )); then
        : > /etc/s-box/admin-stopped || red "停服标记恢复失败，请检查文件系统"
        singbox_finish_stop || red "恢复停服状态失败，请检查服务、定时任务和自启项"
    fi
    exit "$status"' EXIT
rm -f /etc/s-box/admin-stopped &&
    restartsb && sub_service status sing-box && sub_service enable sing-box && cronsb || exit 1
committed=1
)
}

stclre(){
[[ -f /etc/s-box/sb.json ]] || { red "未正常安装Sing-box"; return 1; }
local menu
readp "1：启用并重启\n2：关闭并取消自启\n请选择：" menu || return 1
case "$menu" in
1) config_locked singbox_enable && green "Sing-box 服务已启用并重启" ;;
2) config_locked singbox_stop && green "Sing-box 服务已关闭" ;;
*) return 1 ;;
esac
}

cronsb(){
(
local cron_tmp line='0 1 * * * /usr/bin/sing_box --maintenance'
cron_tmp=$(mktemp) || exit 1
trap 'rm -f -- "$cron_tmp"' EXIT
crontab -l > "$cron_tmp" 2>/dev/null
sed -i -e '/^0 1 [*] [*] [*] systemctl restart sing-box;rc-service sing-box restart$/d' \
       -e '\|^0 1 [*] [*] [*] /usr/bin/sing_box --maintenance$|d' "$cron_tmp" || exit 1
if [[ ${1:-} != remove && ! -e /etc/s-box/admin-stopped ]]; then printf '%s\n' "$line" >> "$cron_tmp" || exit 1; fi
if [[ ${1:-} == remove ]]; then
    sed -i -e '/^@reboot .*\/etc\/s-box\/cloudflared tunnel --url /d' -e '\|^@reboot sleep 10 && /usr/bin/sing_box --argo-boot$|d' "$cron_tmp" || exit 1
fi
crontab "$cron_tmp"
)
}

uncronsb(){
(
local cron_tmp
cronsb remove || exit 1
cron_tmp=$(mktemp) || exit 1
trap 'rm -f -- "$cron_tmp"' EXIT
crontab -l > "$cron_tmp" 2>/dev/null
sed -i -e '/^@reboot .*\/etc\/s-box\/cloudflared tunnel --url /d' \
       -e '/^@reboot .*cat \/etc\/s-box\/sbwpph\.log /d' -e '\|^@reboot sleep 10 && /etc/s-box/sbwpph-run.sh |d' "$cron_tmp" || exit 1
crontab "$cron_tmp"
)
}

lnsb(){
local dir=${1:-$SB_RELEASE_DIR} tmp
[[ "$dir" =~ ^/etc/s-box/releases/[a-f0-9]{40}$ && -f "$dir/tools/releases.sha256" && -f "$dir/tools/firewall.py" ]] ||
{ red "请按 README 从已核实的完整提交安装发布目录"; return 1; }
bash -n "$dir/sb.sh" && chmod 700 "$dir/sb.sh" || return 1
tmp=$(mktemp -d /usr/bin/.sing_box.XXXXXX) || return 1
ln -s "$dir/sb.sh" "$tmp/sing_box" && mv -Tf -- "$tmp/sing_box" /usr/bin/sing_box ||
{ rm -rf -- "$tmp"; return 1; }
rmdir "$tmp"
if [[ -e /usr/bin/sb || -L /usr/bin/sb ]]; then ln -sfn /usr/bin/sing_box /usr/bin/sb || return 1; fi
}

migrate_shortcut(){
[[ /usr/bin/sb -ef /usr/bin/sing_box ]] && return 0
mv -f /usr/bin/sb /usr/bin/sing_box || { red "无法迁移旧脚本入口"; return 1; }
ln -s /usr/bin/sing_box /usr/bin/sb || { red "sing_box 已安装，但旧命令兼容链接创建失败"; return 1; }
}

update_script(){
local revision dir
readp "输入已按 README 验证并解包的完整提交号：" revision || return 1
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || { red "需要 40 位小写 Git 提交号"; return 1; }
dir="/etc/s-box/releases/$revision"
config_locked update_script_install "$dir" "$revision"
}

update_script_install(){
local dir=$1 revision=$2
lnsb "$dir" || return 1
if ! sub_service status sing-box >/dev/null 2>&1 && ! sub_service enabled sing-box >/dev/null 2>&1; then
    : > /etc/s-box/admin-stopped || return 1
fi
if crontab -l 2>/dev/null | grep -Eq '^0 1 [*] [*] [*] (systemctl restart sing-box;rc-service sing-box restart|/usr/bin/sing_box --maintenance)$'; then
    cronsb || return 1
fi
awk -F '更新内容' 'NR==1 {print $1}' "$dir/version" > /etc/s-box/v
green "已切换到受审提交 $revision"
}

lapre(){
local json page
latcore= precore=
json=$(curl -fLsS --connect-timeout 2 --max-time 3 https://data.jsdelivr.com/v1/package/gh/SagerNet/sing-box 2>/dev/null) || json=
if echo "$json"|grep -q '"versions"'; then
latcore=$(echo "$json"|grep -Eo '"[0-9]+\.[0-9]+\.[0-9]+"'|head -n1|tr -d '"')
precore=$(echo "$json"|grep -Eo '"[0-9.]*-[^"]*"'|head -n1|tr -d '",')
fi
if [[ -z $latcore ]]; then
page=$(curl -fLsS --connect-timeout 2 --max-time 3 https://github.com/SagerNet/sing-box/releases 2>/dev/null) || page=
latcore=$(echo "$page"|grep -oE 'tag/v[0-9]+\.[0-9]+\.[0-9]+(["/<>[:space:]]|$)'|head -n1|grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
[[ -n $precore ]] || precore=$(echo "$page"|grep -oE '/tag/v[0-9.]+-[^"]+'|head -n1|cut -d'v' -f2)
fi
inscore=$(/etc/s-box/sing-box version 2>/dev/null | awk '/version/{print $NF}')
}

menu_version_hint(){
local current=${1#v} remote=${2#v} action=$3 label=$4
if [[ ! $remote =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
printf '远端版本查询失败（%s更新）' "$action"
return
fi
printf '%s：%s' "$label" "$2"
if [[ $current == "$remote" ]]; then
printf '（已是最新）'
elif [[ $current =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
if [[ $(printf '%s\n' "$current" "$remote" | sort -V | head -n1) == "$current" ]]; then
printf '（发现新版本，选择%s更新）' "$action"
else
printf '（本地版本高于远端）'
fi
else
printf '（%s更新/切换）' "$action"
fi
}

menu_version_status(){
local insV inscore latcore precore remote_script
insV=$(cat /etc/s-box/v 2>/dev/null)
# Start the script lookup alongside the core lookup; keep both results local to this refresh.
{
lapre
IFS= read -r remote_script <&3 || remote_script=
} 3< <(
info=$(curl -fsSL --connect-timeout 2 --max-time 3 "$SB_RAW_BASE/version" 2>/dev/null) || exit
printf '%s\n' "$info" | awk -F '更新内容' 'NR == 1 {print $1}'
)
remote_script=${remote_script%$'\r'}
echo -e "当前 sing-box 脚本版本：${bblue}${insV:-未记录}${plain}；$(menu_version_hint "$insV" "$remote_script" 7 远端版本)"
echo -e "当前 Sing-box 内核版本：${bblue}${inscore:-未安装}${plain}；$(menu_version_hint "$inscore" "$latcore" 8 最新正式版)"
}

core_update(){
config_locked core_update_locked "$@"
}

core_update_locked(){
(
local requested=$1 work name candidate actual series template restore=0 extra=/etc/s-box/sb_extra.json
local check_args
[[ "$requested" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?$ ]] || { red "内核版本号格式无效"; exit 1; }
work=$(mktemp -d /etc/s-box/.core.XXXXXX) || exit 1
core_cleanup(){
local status=$?
trap - EXIT
trap '' HUP INT TERM
if (( restore )); then
if [[ -f "$work/old-extra" ]]; then cp -p "$work/old-extra" "$extra" || return 1; fi
if ! mv -f -- "$work/old-core" /etc/s-box/sing-box || ! cp -p "$work/old-config" /etc/s-box/sb.json; then
red "恢复旧内核或配置失败，备份保留在 $work"
return 1
fi
restartsb >/dev/null 2>&1 || red "旧内核及配置已恢复，但服务启动失败，请查看运行日志"
[[ ! -f /etc/s-box/firewall.json ]] || firewall_sync || red "恢复原内核后的防火墙同步失败"
red "内核切换未完成，已恢复旧内核及配置"
fi
rm -rf -- "$work"
return "$status"
}
trap core_cleanup EXIT
trap 'exit 1' HUP INT TERM
name="sing-box-$requested-linux-$cpu"
if ! release_fetch "$name.tar.gz" "$work/core.tar.gz" ||
   ! tar xzf "$work/core.tar.gz" -C "$work" "$name/sing-box"; then
red "内核下载或解包失败，已保留当前内核"
exit 1
fi
candidate="$work/$name/sing-box"
chmod +x "$candidate" || exit 1
actual=$("$candidate" version) || { red "候选内核无法运行，已保留当前内核"; exit 1; }
actual=$(printf '%s\n' "$actual" | awk '/^sing-box version /{print $3; exit}')
[[ "$actual" == "$requested" ]] || { red "候选内核版本不匹配"; exit 1; }
series=$(printf '%s' "$actual" | cut -d . -f 1,2)
[[ "$series" == 1.10 ]] && template=/etc/s-box/sb10.json || template=/etc/s-box/sb11.json
check_args=(-c "$template")
if [[ -f $extra ]]; then
    cp -p "$extra" "$work/old-extra" && extra_for_series "$extra" "$work/sb_extra.json" "$series" "$template" || exit 1
    check_args+=(-c "$work/sb_extra.json")
fi
if ! "$candidate" check "${check_args[@]}"; then
red "候选内核不兼容现有配置（Snell 需要 1.14.0+），已保留当前内核及配置"
exit 1
fi
cp -p /etc/s-box/sing-box "$work/old-core" && cp -p /etc/s-box/sb.json "$work/old-config" || exit 1
restore=1
if { [[ ! -f "$work/sb_extra.json" ]] || mv -f -- "$work/sb_extra.json" "$extra"; } &&
    mv -f -- "$candidate" /etc/s-box/sing-box && cp "$template" /etc/s-box/sb.json && restartsb checked && { [[ ! -f /etc/s-box/firewall.json ]] || firewall_sync; }; then
restore=0
sbnh=$series
if ! sbshare > /dev/null 2>&1; then
red "内核已切换，但订阅刷新失败，请通过菜单9重试"
exit 1
fi
exit 0
fi
exit 1
)
}

upsbcroe(){
sbactive
local upcore menu
green "1：受审正式版 1.14.3"
green "2：受审旧版 1.10.7"
green "3：从当前提交的 SHA-256 清单选择其他版本"
green "0：返回上层"
readp "请选择【0-3】：" menu || return 1
case "$menu" in
1) upcore=1.14.3 ;;
2) upcore=1.10.7 ;;
3) awk -v cpu="$cpu" '$2 ~ ("^sing-box-.*-linux-" cpu "\\.tar\\.gz$") {print $2}' "$SB_RELEASE_DIR/tools/releases.sha256"
   readp "请输入清单内的内核版本号：" upcore || return 1 ;;
0) return 0 ;; *) return 1 ;;
esac
if [[ -n $upcore ]]; then
green "开始下载并更新Sing-box内核……请稍等"
core_update "$upcore" || return 1
blue "成功升级/切换 Sing-box 内核版本：$(/etc/s-box/sing-box version | awk '/version/{print $NF}')" && sleep 3 && sing_box
else
red "版本号查询失败，可重新进入菜单8或指定版本号"
return 1
fi
}

unins(){
config_locked unins_locked "$@"
}

unins_locked(){
local unit_state svc
unit_state=$(singbox_unit_state)
[[ $unit_state == absent || $unit_state == project ]] || { red "同名 sing-box 服务归属未确认，已中止卸载"; return 1; }
firewall_legacy
sub_remove || { red "订阅入口清理失败，已中止卸载"; return 1; }
if [[ -f /etc/s-box/firewall.json ]]; then
python3 "$SB_RELEASE_DIR/tools/firewall.py" remove || { red "项目防火墙规则清理失败，已中止卸载"; return 1; }
fi
if grep -Fxq '# sing-box:managed firewall' /etc/systemd/system/sing-box.service.d/51-sing-box-managed-firewall.conf 2>/dev/null; then
rm -f -- /etc/systemd/system/sing-box.service.d/51-sing-box-managed-firewall.conf
fi
if grep -Fxq '# sing-box:managed firewall' /etc/init.d/sing-box-managed-firewall 2>/dev/null; then
rc-update del sing-box-managed-firewall default >/dev/null 2>&1
rm -f -- /etc/init.d/sing-box-managed-firewall
fi
if command -v apk >/dev/null 2>&1; then
for svc in sing-box argo; do
[[ $svc != sing-box || $unit_state == project ]] || continue
[[ $svc != argo ]] || argo_unit_owned || continue
rc-service "$svc" stop >/dev/null 2>&1
rc-update del "$svc" default >/dev/null 2>&1
rm -f -- "/etc/init.d/$svc"
done
else
for svc in sing-box argo; do
[[ $svc != sing-box || $unit_state == project ]] || continue
[[ $svc != argo ]] || argo_unit_owned || continue
systemctl stop "$svc" >/dev/null 2>&1
systemctl disable "$svc" >/dev/null 2>&1
rm -f -- "/etc/systemd/system/$svc.service"
done
fi
vm_port=$(config_get vmess-sb '.listen_port') && project_process temporary "$vm_port" stop
project_process socks '' stop
rm -rf /etc/s-box sing_box_update sbyg_update /usr/bin/sing_box /usr/bin/sb /root/geoip.db /root/geosite.db /root/warpapi /root/warpip /root/websbox
rm -f /etc/local.d/alpineargo.start /etc/local.d/alpinesub.start /etc/local.d/alpinews5.start
uncronsb
green "Sing-box卸载完成！"
blue "重新安装请按 README 选择已核实的完整 Git 提交"
echo
}

sblog(){
red "退出日志 Ctrl+c"
if command -v apk >/dev/null 2>&1; then
yellow "暂不支持alpine查看日志"
else
#systemctl status sing-box
journalctl -u sing-box.service -o cat -f
fi
}

sbactive(){
if [[ ! -f /etc/s-box/sb.json ]]; then
red "未正常启动Sing-box，请卸载重装或者选择10查看运行日志反馈" && exit
fi
}

sbshare(){
config_locked sbshare_locked "$@"
}

sbshare_locked(){
(
local publish_dir node_file v2sub sub_switching=0
if [[ ${1:-} == subscription-switch ]]; then
    sub_switching=1
else
    exec 9>/etc/s-box/subscription.lock || exit 1
    flock -x 9 || exit 1
fi
publish_dir=$(mktemp -d /etc/s-box/.share.XXXXXX) || exit 1
trap 'rm -rf -- "$publish_dir"' EXIT
trap 'exit 1' HUP INT TERM
rm -f /etc/s-box/{vl_reality,vm_ws_argols,vm_ws_argogd,vm_ws,vm_ws_tls,hy2,tuic5,an,trojan}.txt || exit 1
result_vl_vm_hy_tu && resvless && resvmess && reshy2 && restu5 || exit 1
if [[ "$sbnh" != "1.10" ]]; then
resan || exit 1
[[ $an_self == true || -s /etc/s-box/an.txt ]] || exit 1
fi
if [[ -s /etc/s-box/sb_extra.json ]]; then
extra_share || exit 1
fi
[[ -s /etc/s-box/vl_reality.txt && -s /etc/s-box/hy2.txt ]] || exit 1
[[ $tu5_self == true || -s /etc/s-box/tuic5.txt ]] || exit 1
[[ $tls != true || $vm_self == true || -s /etc/s-box/vm_ws_tls.txt ]] || exit 1
for node_file in /etc/s-box/{vl_reality,vm_ws_argols,vm_ws_argogd,vm_ws,vm_ws_tls,hy2,tuic5,an,trojan}.txt; do
if [ -e "$node_file" ]; then
[ -s "$node_file" ] && cat "$node_file" >> "$publish_dir/jhdy.txt" || exit 1
fi
done
v2sub=$(cat "$publish_dir/jhdy.txt") || exit 1
[ -n "$v2sub" ] || exit 1
printf '%s\n' "$v2sub" > "$publish_dir/jhsub.txt" || exit 1
chmod 600 "$publish_dir/jhsub.txt" || exit 1
sb_client "$publish_dir" && sub_publish_current "$publish_dir" sbox.json clmi.yaml jhsub.txt || exit 1
echo
white "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
red "🚀【 聚合节点 】节点信息如下：" && { [[ ! -t 1 ]] || sleep 2; }
echo
echo "分享链接"
echo -e "${yellow}$v2sub${plain}"
white "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
echo
)
}

clash_sb_share(){
sbactive
echo
yellow "1：刷新并查看各协议分享链接、二维码、聚合节点（不含客户端WARP前置）"
yellow "2：刷新并查看Mihomo、Sing-box完整配置及订阅链接（WARP前置仅在clmi.yaml中）"
yellow "3：推送最新节点配置信息(选项1+选项2)到Telegram通知"
yellow "0：返回上层"
readp "请选择【0-3】：" menu
if [ "$menu" = "1" ]; then
sbshare || return 1
if [[ -s /etc/s-box/subdomain.log && -s /etc/s-box/subtoken.log ]]; then
sub_show_urls
fi
elif  [ "$menu" = "2" ]; then
green "请稍等……"
if ! sbshare > /dev/null 2>&1; then
red "节点配置刷新失败，已保留原订阅文件"
return 1
fi
white "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
if [[ -s /etc/s-box/gitlab-publisher ]]; then gitlabsubgo || return 1; fi
if [[ -s /etc/s-box/subtoken.log ]]; then sub_show_urls; fi
white "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
red "🚀Mihomo配置文件显示如下："
red "文件目录 /etc/s-box/clmi.yaml ，复制自建以yaml文件格式为准" && sleep 2
echo
cat /etc/s-box/clmi.yaml
echo
white "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
echo
white "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
red "🚀SFA/SFI/SFW配置文件显示如下："
red "请使用与当前配置版本兼容的 sing-box 客户端导入配置。"
red "文件目录 /etc/s-box/sbox.json ，复制自建以json文件格式为准" && sleep 2
echo
cat /etc/s-box/sbox.json
echo
white "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
echo
elif [ "$menu" = "3" ]; then
tgnotice
else
sing_box
fi
}

acme_activate_locked(){
(
local domain=$1 path=$2 dir=/root/ygkkkca work configs= file status restore=0 active=0 restarting=0
local -a files=(cert.crt private.key ca.log) changed=() check_args=()
sub_valid_domain "$domain" && [[ $path == "/etc/letsencrypt/live/$domain" ]] || exit 1
mkdir -p -m 700 "$dir" && chmod 700 "$dir" || exit 1
work=$(mktemp -d "$dir/.deploy.XXXXXX") || exit 1
acme_finish(){
    status=$?
    trap - EXIT
    trap '' HUP INT TERM
    if (( restore )); then
        for file in "${files[@]}"; do
            if [[ -e $work/old-$file || -L $work/old-$file ]]; then
                mv -fT -- "$work/old-$file" "$dir/$file" || { red "旧证书材料保留在 $work" >&2; return 1; }
            else rm -f -- "$dir/$file" || return 1; fi
        done
        for file in "${changed[@]}"; do
            mv -fT -- "$configs/old-$file" "/etc/s-box/$file" || { red "旧配置保留在 $configs" >&2; return 1; }
        done
        if (( restarting )); then
            ip_priority_restart && config_refresh || red "已恢复证书和配置，请检查服务和订阅" >&2
        fi
    fi
    rm -rf -- "$work"
    [[ -z $configs ]] || rm -rf -- "$configs"
    return "$status"
}
trap acme_finish EXIT
trap 'exit 1' HUP INT TERM
install -m 600 "$path/privkey.pem" "$work/private.key" &&
    install -m 600 "$path/fullchain.pem" "$work/cert.crt" &&
    client_tls_material "$work/cert.crt" "$work/private.key" "$domain" >/dev/null &&
    printf '%s\n' "$domain" > "$work/ca.log" || exit 1
for file in "${files[@]}"; do
    if [[ -e $dir/$file || -L $dir/$file ]]; then cp -a -- "$dir/$file" "$work/old-$file" || exit 1; fi
done
for file in sb.json sb_extra.json sb10.json sb11.json; do
    [[ -f /etc/s-box/$file ]] || continue
    if jq -e --arg cert "$dir/cert.crt" 'any(.inbounds[]?; .tls.certificate_path==$cert)' "/etc/s-box/$file" >/dev/null; then
        [[ -n $configs ]] || configs=$(mktemp -d /etc/s-box/.acme.XXXXXX) || exit 1
        cp -p -- "/etc/s-box/$file" "$configs/old-$file" &&
            config_patch_acme "/etc/s-box/$file" "$configs/$file" "$domain" || exit 1
        changed+=("$file")
        if [[ $file == sb.json || $file == sb_extra.json ]]; then active=1; check_args+=(-c "$configs/$file"); fi
    else
        [[ $? == 1 ]] || exit 1
        if [[ $file == sb.json || $file == sb_extra.json ]]; then check_args+=(-c "/etc/s-box/$file"); fi
    fi
done
restore=1
for file in "${files[@]}"; do mv -fT -- "$work/$file" "$dir/$file" || exit 1; done
if (( active )); then /etc/s-box/sing-box check "${check_args[@]}" || exit 1; fi
for file in "${changed[@]}"; do mv -fT -- "$configs/$file" "/etc/s-box/$file" || exit 1; done
if (( active )); then
    restarting=1
    ip_priority_restart checked && config_refresh || exit 1
fi
restore=0
)
}

acme_renew_locked(){
local name
[[ -f /root/ygkkkca/ca.log ]] || return 0
IFS= read -r name < /root/ygkkkca/ca.log && sub_valid_domain "$name" || return 1
[[ $1 == "/etc/letsencrypt/live/$name" ]] || return 0
acme_activate_locked "$name" "$1"
}

acme_detach_legacy(){
(
local dir=/root/ygkkkca file stage
[[ -L $dir/cert.crt || -L $dir/private.key ]] || exit 0
chmod 700 "$dir" && stage=$(mktemp -d "$dir/.previous.XXXXXX") || exit 1
trap 'rm -rf -- "$stage"' EXIT
trap 'exit 1' HUP INT TERM
# Capture the live material before Certbot can move its lineage symlinks.
for file in cert.crt private.key; do install -m 600 "$dir/$file" "$stage/$file" || exit 1; done
for file in cert.crt private.key; do mv -fT -- "$stage/$file" "$dir/$file" || exit 1; done
)
}

acme(){
local domain path method
if ! command -v certbot >/dev/null; then
if command -v apt-get >/dev/null; then apt-get install -y certbot || return 1
elif command -v apk >/dev/null; then apk add certbot || return 1
elif command -v dnf >/dev/null; then dnf --setopt=gpgcheck=1 install -y certbot || return 1
else red "请通过发行版签名软件源安装 Certbot"; return 1; fi
fi
readp "输入证书域名：" domain || return 1
sub_valid_domain "$domain" || return 1
readp "回车使用 HTTP-01 独立监听 TCP 80；输入 2 使用 Certbot 已安装的认证插件：" method || return 1
case "$method" in ""|1|2) ;; *) return 1 ;; esac
local hooks=/etc/letsencrypt/renewal-hooks/deploy hook
mkdir -p "$hooks" && hook=$(mktemp "$hooks/.sing-box.XXXXXX") || return 1
cat > "$hook" <<'HOOK' && chmod 700 "$hook" && mv -fT -- "$hook" "$hooks/51-sing-box-certificate" || return 1
#!/bin/sh
[ "${SBOX_ACME_INTERACTIVE:-}" != 1 ] && [ -x /usr/bin/sing_box ] && [ -s /etc/s-box/sb.json ] || exit 0
exec /usr/bin/sing_box --acme-renew "$RENEWED_LINEAGE"
HOOK
config_locked acme_detach_legacy || return 1
case "$method" in
""|1) SBOX_ACME_INTERACTIVE=1 certbot certonly --standalone --cert-name "$domain" -d "$domain" || return 1 ;;
2) SBOX_ACME_INTERACTIVE=1 certbot certonly --cert-name "$domain" -d "$domain" || return 1 ;;
*) return 1 ;;
esac
path="/etc/letsencrypt/live/$domain"
[[ -s "$path/fullchain.pem" && -s "$path/privkey.pem" ]] || return 1
config_locked acme_activate_locked "$domain" "$path"
}
cfwarp(){
local action
if ! command -v warp-cli >/dev/null; then
yellow "请先按 Cloudflare 官方 Linux 软件源说明核实并配置签名密钥。"
readp "从本机已配置的软件源安装 cloudflare-warp？输入 1 确认：" action || return 1
[[ "$action" == 1 ]] || return 0
if command -v apt-get >/dev/null; then apt-get install -y cloudflare-warp || return 1
elif command -v dnf >/dev/null; then dnf --setopt=gpgcheck=1 install -y cloudflare-warp || return 1
else red "本机包管理器未适配；请用供应商签名包安装 warp-cli"; return 1; fi
fi
readp "系统 WARP：1 状态（默认），2 注册本机，3 连接（可能影响 SSH 路由），4 断开：" action || return 1
case "$action" in
""|1) warp-cli status ;;
2) warp-cli registration new ;;
3) warp-cli connect ;;
4) warp-cli disconnect ;;
*) return 1 ;;
esac
}
bbr(){
local config=/etc/sysctl.d/90-sing-box-bbr.conf answer
readp "使用当前发行版内核启用 BBR？输入 1 确认：" answer || return 1
[[ "$answer" == 1 ]] || return 0
modprobe tcp_bbr 2>/dev/null || :
sysctl -n net.ipv4.tcp_available_congestion_control | grep -qw bbr ||
{ red "当前内核未提供 BBR。请使用发行版签名软件源升级内核后重试。"; return 1; }
sysctl -w net.ipv4.tcp_congestion_control=bbr &&
printf 'net.ipv4.tcp_congestion_control=bbr\n' > "$config"
}

showprotocol(){
allports
sbymfl
tls=$(config_get vmess-sb '.tls.enabled') || return 1
if [[ "$tls" = "false" ]]; then
if project_process fixed '' status || project_process temporary "$vm_port" status; then
vm_zs="TLS关闭"
argoym="已开启"
else
vm_zs="TLS关闭"
argoym="未开启"
fi
else
vm_zs="TLS开启"
argoym="不支持开启"
fi
hy2_sniname=$(config_get hy2-sb '.tls.key_path') || return 1
[[ "$hy2_sniname" = '/etc/s-box/private.key' ]] && hy2_zs="自签证书" || hy2_zs="域名证书"
tu5_sniname=$(config_get tuic5-sb '.tls.key_path') || return 1
[[ "$tu5_sniname" = '/etc/s-box/private.key' ]] && tu5_zs="自签证书" || tu5_zs="域名证书"
an_sniname=; [[ "$sbnh" == "1.10" ]] || an_sniname=$(config_get anytls-sb '.tls.key_path') || return 1
[[ "$an_sniname" = '/etc/s-box/private.key' ]] && an_zs="自签证书" || an_zs="域名证书"
echo -e "Sing-box节点关键信息、已分流域名情况如下："
echo -e "🚀【 Vless-reality 】${yellow}端口:$vl_port  Reality域名证书伪装地址：$(config_get vless-sb '.tls.server_name')${plain}"
if [[ "$tls" = "false" ]]; then
echo -e "🚀【   Vmess-ws    】${yellow}端口:$vm_port   证书形式:$vm_zs   Argo状态:$argoym${plain}"
else
echo -e "🚀【 Vmess-ws-tls  】${yellow}端口:$vm_port   证书形式:$vm_zs   Argo状态:$argoym${plain}"
fi
echo -e "🚀【  Hysteria-2   】${yellow}端口:$hy2_port  证书形式:$hy2_zs  转发多端口: $hy2zfport${plain}"
echo -e "🚀【    Tuic-v5    】${yellow}端口:$tu5_port  证书形式:$tu5_zs  转发多端口: $tu5zfport${plain}"
if [[ "$sbnh" != "1.10" ]]; then
echo -e "🚀【    Anytls     】${yellow}端口:$an_port  证书形式:$an_zs${plain}"
fi
if [[ -s /etc/s-box/sb_extra.json ]]; then
jq -r '.inbounds[] | select(.type == "trojan" or .type == "snell") |
    "🚀【 " + (if .type == "trojan" then "Trojan + TLS" else "Snell" end) +
    " 】TCP端口:" + (.listen_port|tostring) + "  管理：3 → 11"' /etc/s-box/sb_extra.json
fi
if [[ -s /etc/s-box/subtoken.log ]]; then sub_show_urls noqr; fi
if [ "$argoym" = "已开启" ]; then
#echo -e "Vmess-UUID：${yellow}$(config_get vless-sb '.users[0].uuid')${plain}"
#echo -e "Vmess-Path：${yellow}$(config_get vmess-sb '.transport.path')${plain}"
if project_process temporary "$vm_port" status; then
echo -e "Argo临时域名：${yellow}$(quick_tunnel_hostname)${plain}"
fi
if project_process fixed '' status; then
local fixed_host
fixed_host=$(cat /etc/s-box/sbargoym.log 2>/dev/null)
if sub_valid_domain "$fixed_host"; then echo -e "Argo固定域名：${yellow}$fixed_host${plain}"
else yellow "固定隧道域名无效，请重新设置"; fi
fi
fi
echo "------------------------------------------------------------------------------------"
if project_process socks '' status; then
s5port=$(sed -n '1p' /etc/s-box/sbwpph.args)
s5gj=$(sed -n '3p' /etc/s-box/sbwpph.args)
case "$s5gj" in
AT) showgj="奥地利" ;;
AU) showgj="澳大利亚" ;;
BE) showgj="比利时" ;;
BG) showgj="保加利亚" ;;
CA) showgj="加拿大" ;;
CH) showgj="瑞士" ;;
CZ) showgj="捷克" ;;
DE) showgj="德国" ;;
DK) showgj="丹麦" ;;
EE) showgj="爱沙尼亚" ;;
ES) showgj="西班牙" ;;
FI) showgj="芬兰" ;;
FR) showgj="法国" ;;
GB) showgj="英国" ;;
HR) showgj="克罗地亚" ;;
HU) showgj="匈牙利" ;;
IE) showgj="爱尔兰" ;;
IN) showgj="印度" ;;
IT) showgj="意大利" ;;
JP) showgj="日本" ;;
LT) showgj="立陶宛" ;;
LV) showgj="拉脱维亚" ;;
NL) showgj="荷兰" ;;
NO) showgj="挪威" ;;
PL) showgj="波兰" ;;
PT) showgj="葡萄牙" ;;
RO) showgj="罗马尼亚" ;;
RS) showgj="塞尔维亚" ;;
SE) showgj="瑞典" ;;
SG) showgj="新加坡" ;;
SK) showgj="斯洛伐克" ;;
US) showgj="美国" ;;
esac
[[ -n $s5gj ]] && s5ms="多地区Psiphon代理模式 (端口:$s5port  国家:$showgj)" || s5ms="本地Warp代理模式 (端口:$s5port)"
echo -e "WARP-plus-Socks5状态：$yellow已启动 $s5ms$plain"
else
echo -e "WARP-plus-Socks5状态：$yellow未启动$plain"
fi
echo "------------------------------------------------------------------------------------"
ww4="warp-wireguard-ipv4优先分流域名：$wfl4"
ww6="warp-wireguard-ipv6优先分流域名：$wfl6"
ws4="warp-socks5-ipv4优先分流域名：$sfl4"
ws6="warp-socks5-ipv6优先分流域名：$sfl6"
l4="VPS本地ipv4优先分流域名：$adfl4"
l6="VPS本地ipv6优先分流域名：$adfl6"
[[ "$sbnh" == "1.10" ]] && ymflzu=("ww4" "ww6" "ws4" "ws6" "l4" "l6") || ymflzu=("ww6" "ws4" "l4" "l6")
for ymfl in "${ymflzu[@]}"; do
if [[ ${!ymfl} != *"】未分流"* ]]; then
echo -e "${!ymfl}"
fi
done
if [[ $ww4 = *"】未分流"* && $ww6 = *"】未分流"* && $ws4 = *"】未分流"* && $ws6 = *"】未分流"* && $l4 = *"】未分流"* && $l6 = *"】未分流"* ]] ; then
echo -e "未设置域名分流"
fi
}

warp_socks_boot(){
(
local enable=$1 cron_tmp
cron_tmp=$(mktemp) || exit 1
trap 'rm -f -- "$cron_tmp"' EXIT
crontab -l > "$cron_tmp" 2>/dev/null
sed -i -e '\|^@reboot .*cat /etc/s-box/sbwpph\.log |d' -e '\|^@reboot sleep 10 && /etc/s-box/sbwpph-run.sh |d' "$cron_tmp" || exit 1
if [[ $enable == yes ]] && ! command -v apk >/dev/null 2>&1; then
    printf '%s\n' '@reboot sleep 10 && /etc/s-box/sbwpph-run.sh >/dev/null 2>&1' >> "$cron_tmp" || exit 1
fi
crontab "$cron_tmp" || exit 1
rm -f /etc/local.d/alpinews5.start || exit 1
if [[ $enable == yes ]] && command -v apk >/dev/null 2>&1; then
    printf '%s\n' '#!/bin/sh' 'sleep 10' '/etc/s-box/sbwpph-run.sh >/dev/null 2>&1 &' > /etc/local.d/alpinews5.start &&
        chmod 700 /etc/local.d/alpinews5.start && rc-update add local default || exit 1
fi
)
}

warp_socks_probe(){
local port=$1 proxy response address code
sub_valid_port "$port" || return 1
for proxy in socks5 socks5h; do
    response=$(curl -q -fsS --proto '=https' --noproxy '' --proxy "$proxy://127.0.0.1:$port" \
        --connect-timeout 2 --max-time 5 --write-out $'\n%{http_code}' https://icanhazip.com 2>/dev/null) || continue
    code=${response##*$'\n'}; address=${response%$'\n'*}
    address=${address%$'\n'}; address=${address%$'\r'}
    [[ $code == 200 ]] && { ip_literal_valid 4 "$address" || ip_literal_valid 6 "$address"; } && return 0
done
return 1
}

inssbwpph(){
local choice port country= family=6
sbactive
yellow "1：启用本地 WARP Socks5   2：启用多地区 Psiphon   3：停止   0：返回"
readp "请选择【0-3】：" choice || return 1
case "$choice" in
3) warp_socks_boot no && project_process socks '' stop &&
       rm -f /etc/s-box/{sbwpph.log,sbwpph.args,sbwpph-run.sh}; return ;;
1|2) ;;
*) return ;;
esac
readp "WARP Socks5 端口（回车使用 40000）：" port || return 1
port=${port:-40000}
sub_valid_port "$port" || { red "无效的端口"; return 1; }
if [[ $choice == 2 ]]; then
    readp "输入两位大写国家代码（例如 US）：" country || return 1
    [[ $country =~ ^[A-Z]{2}$ ]] || { red "国家代码无效"; return 1; }
fi
if ! release_matches "sbwpph_$cpu" /etc/s-box/sbwpph; then
    release_fetch "sbwpph_$cpu" /etc/s-box/sbwpph && chmod 755 /etc/s-box/sbwpph || return 1
fi
v4v6; [[ -z $v4 ]] || family=4
warp_socks_boot no && project_process socks '' stop || return 1
if ss -H -lntu | awk -v p="$port" '$5 ~ (":"p"$") {found=1} END {exit !found}'; then
    red "端口已被占用"; return 1
fi
rm -f -- /etc/s-box/sbwpph.log || return 1
printf '%s\n' "$port" "$family" "$country" > /etc/s-box/sbwpph.args || return 1
cat > /etc/s-box/sbwpph-run.sh <<'RUN' || return 1
#!/bin/bash
{ IFS= read -r port && IFS= read -r family && IFS= read -r country; } < /etc/s-box/sbwpph.args || exit 1
[[ $port =~ ^[1-9][0-9]{0,4}$ && $port -le 65535 && $family =~ ^[46]$ ]] || exit 1
args=(-b "127.0.0.1:$port")
if [[ -n $country ]]; then
    [[ $country =~ ^[A-Z]{2}$ ]] || exit 1
    args+=(--cfon --country "$country")
fi
exec /etc/s-box/sbwpph "${args[@]}" "-$family" --endpoint 162.159.192.1:2408
RUN
chmod 700 /etc/s-box/sbwpph-run.sh || return 1
nohup /etc/s-box/sbwpph-run.sh >/dev/null 2>&1 &
sleep 20
if ! warp_socks_probe "$port" || ! config_apply config_patch_socks_port "$port" || ! warp_socks_boot yes; then
    project_process socks '' stop
    red "WARP Socks5 探测或启用失败"; return 1
fi
green "已启用 WARP Socks5"
}

sbsm(){
echo
green "sing-box 多协议部署与订阅管理脚本"
blue "项目地址：https://github.com/$SB_REPO"
blue "使用说明：https://github.com/$SB_REPO/blob/$SB_BRANCH/README.md"
blue "管理命令：sing_box；Trojan/Snell：3 → 11；域名分发：3 → 10；订阅管理：3 → 8；刷新节点：9"
echo
}

# Older menu-7 updaters still save and launch the new script as /usr/bin/sb.
if [[ "${1:-}" == --secure-routes ]]; then
    [[ -s /etc/s-box/sb.json && -n $sbnh ]] || { red "未找到已安装的服务端配置"; exit 1; }
    config_apply secure_server_routes
    exit $?
fi
if [[ "${1:-}" == --acme-renew ]]; then config_locked acme_renew_locked "${2:-}"; exit $?; fi
if [[ "${1:-}" == --argo-boot ]]; then
    config_locked argo_start_temporary && { sleep 10; sbshare >/dev/null; }
    exit $?
fi
if [[ "${1:-}" == --maintenance ]]; then config_locked singbox_maintenance; exit $?; fi
if [[ "${1:-}" == --firewall-enable ]]; then
shift
firewall_legacy
firewall_enable "$@"
exit $?
fi
if [[ -f /etc/s-box/sbwpph.log ]]; then
    warp_socks_boot no && rm -f -- /etc/s-box/sbwpph.log || { red "旧菜单 14 启动项清理失败"; exit 1; }
    yellow "已清理旧菜单 14 自启动命令。需要继续使用时，请在菜单 14 重新设置。"
fi
if [[ "$0" == /usr/bin/sb ]]; then
migrate_shortcut || exit 1
fi
clear
white "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
white "sing-box 多协议部署与订阅管理"
white "项目地址：https://github.com/$SB_REPO"
white "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
white "Vless-reality、Vmess-ws/Argo、Hy2、Tuic、Anytls、Trojan-TLS、Snell 多协议脚本"
white "脚本快捷方式：sing_box（安装完成后可用）"
red "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
green " 1. 一键安装 Sing-box" 
green " 2. 删除卸载 Sing-box"
white "----------------------------------------------------------------------------------"
green " 3. 变更配置 【TLS/UUID/Argo/IP出站/TG/Warp/订阅/域名分发/CDN/Trojan/Snell】"
green " 4. 更改主端口/添加多端口跳跃复用" 
green " 5. 三通道域名分流"
green " 6. 关闭/重启 Sing-box"   
green " 7. 更新 sing-box 脚本"
green " 8. 更新/切换/指定 Sing-box 内核版本"
white "----------------------------------------------------------------------------------"
green " 9. 刷新并查看节点 【Mihomo/SFA+SFI+SFW三合一配置/订阅链接/推送TG通知】"
green "10. 查看 Sing-box 运行日志"
green "11. 启用当前内核的 BBR 拥塞控制"
green "12. 通过 Certbot 申请域名证书"
green "13. 系统 WARP 客户端安装、注册及连接管理"
green "14. 添加 WARP-plus-Socks5 代理模式 【本地Warp/多地区Psiphon-VPN】"
green "15. 选择IPv4/IPv6配置输出"
white "----------------------------------------------------------------------------------"
green "16. sing-box脚本使用说明书"
green "17. 修复脚本依赖 / OpenVZ TUN"
white "----------------------------------------------------------------------------------"
green " 0. 退出脚本"
red "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
menu_version_status
red "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
echo -e "VPS状态如下："
echo -e "系统:$blue$op$plain  \c";echo -e "内核:$blue$version$plain  \c";echo -e "处理器:$blue$cpu$plain  \c";echo -e "虚拟化:$blue$vi$plain  \c";echo -e "BBR算法:$blue$bbr$plain"
menu_ip_status
echo -e "公网探测 IPv4：$blue$vps_ipv4$plain   IPv6：$blue$vps_ipv6$plain"
egress_status
if [[ -f /etc/s-box/sb.json ]]; then
rpip=$(ip_priority_get 2>/dev/null) || rpip=unknown
v4_6=$(ip_priority_label "$rpip")
echo -e "代理IP优先级：$blue$v4_6$plain"
fi
if command -v apk >/dev/null 2>&1; then
status_cmd="rc-service sing-box status"
status_pattern="started"
else
status_cmd="systemctl is-active sing-box"
status_pattern="active"
fi
if [[ -n $($status_cmd 2>/dev/null | grep -w "$status_pattern") && -f '/etc/s-box/sb.json' ]]; then
echo -e "Sing-box状态：$blue运行中$plain"
elif [[ -z $($status_cmd 2>/dev/null | grep -w "$status_pattern") && -f '/etc/s-box/sb.json' ]]; then
echo -e "Sing-box状态：$yellow未启动，选择10查看日志并反馈，建议切换正式版内核或卸载重装脚本$plain"
else
echo -e "Sing-box状态：$red未安装$plain"
fi
red "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
if [ -f '/etc/s-box/sb.json' ]; then
if command -v jq >/dev/null 2>&1; then
showprotocol
else
yellow "缺少jq，详细配置暂无法读取；选择17修复依赖"
fi
fi
red "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
echo
while true; do
readp "请输入数字【0-17】:" Input || exit 1
case "$Input" in
0|[1-9]|1[0-7]) break ;;
*) red "输入无效，请输入 0 到 17 之间的数字。" ;;
esac
done
case "$Input" in  
 1 ) instsllsingbox;;
 2 ) unins;;
 3 ) changeserv;;
 4 ) changeport;;
 5 ) changefl;;
 6 ) stclre;;
 7 ) update_script;;
 8 ) upsbcroe;;
 9 ) clash_sb_share;;
10 ) sblog;;
11 ) bbr;;
12 ) acme;;
13 ) cfwarp;;
14 ) inssbwpph;;
15 ) config_locked ipuuid select && sbshare;;
16 ) sbsm;;
17 ) if ensure_dependencies && setup_tun; then green "依赖检查/修复完成"; else red "依赖修复失败，请检查软件源和缺失命令"; fi ;;
 * ) exit 
esac
