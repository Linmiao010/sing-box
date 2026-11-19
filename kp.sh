#!/usr/bin/env bash
# ACCOUNTS is a JSON array, passed by the scheduler. Use key auth or SSH_PASS.
# SSH_KNOWN_HOSTS_FILE must contain keys verified through the provider console.
set -eu
set +x
umask 077
: "${SSH_KNOWN_HOSTS_FILE:?set SSH_KNOWN_HOSTS_FILE}"
[[ -r $SSH_KNOWN_HOSTS_FILE && -s $SSH_KNOWN_HOSTS_FILE ]] || exit 1
case ${1:---keep} in
    --keep) remote='exec bash "$HOME/.sing-box-serv00/release/serv00keep.sh"' ;;
    --login) remote='exit 0' ;;
    --install|--update) operation=$1 ;;
    *) exit 2 ;;
esac
if ! jq -e 'type == "array" and length > 0 and all(.[]; type == "object")' >/dev/null 2>&1 <<< "${ACCOUNTS:?set ACCOUNTS}"; then
    echo "account configuration invalid" >&2
    exit 1
fi
count=0
failed=0
if [[ -n ${operation:-} ]]; then
    bundle=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
    payload=$(mktemp -d) || exit 1
    trap 'rm -f "$payload/input.json" "$payload/bundle.tar"; rmdir "$payload"' EXIT
    trap 'exit 1' HUP INT TERM
    # operation is selected exclusively by the case above. Credentials travel on stdin.
    provision='
        umask 077
        d=$(mktemp -d "$HOME/.serv00-deploy.XXXXXX") || exit 1
        cleanup() {
            rm -f "$d/serv00.sh" "$d/serv00keep.sh" "$d/app.js" "$d/serv00_runtime.py" "$d/input.json"
            rmdir "$d"
        }
        trap cleanup EXIT
        trap "exit 1" HUP INT TERM
        tar -xf - -C "$d" && bash "$d/serv00.sh" "$1" --account-file "$d/input.json"
    '
    remote="sh -c '$provision' sh $operation"
fi
while IFS= read -r account; do
    count=$((count + 1))
    user=$(jq -r '.SSH_USER // ""' <<< "$account")
    host=$(jq -r '.HOST // ""' <<< "$account")
    password=$(jq -r '.SSH_PASS // ""' <<< "$account")
    options=(-F /dev/null -o StrictHostKeyChecking=yes -o UpdateHostKeys=no
        -o "UserKnownHostsFile=$SSH_KNOWN_HOSTS_FILE" -o GlobalKnownHostsFile=/dev/null
        -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=2
        -o ForwardAgent=no -o ClearAllForwardings=yes -T)
    command=(ssh)
    if [[ -n $password ]]; then
        command=(sshpass -e ssh)
        options+=(-o NumberOfPasswordPrompts=1)
    else
        options+=(-o BatchMode=yes)
    fi
    input=/dev/null
    if [[ -n ${operation:-} ]]; then
        jq '{SUUID, REALITY, IP, TCP1_PORT, TCP2_PORT, UDP_PORT, ARGO_DOMAIN, ARGO_AUTH,
            SERV00_ARGO_PORT, SERV00_LOOPBACK_PORT_CONFIRMED, SERV00_FIXED_ORIGIN_CONFIRMED,
            SERV00_CLIENT_RULES_REVISION}' <<< "$account" > "$payload/input.json"
        tar -cf "$payload/bundle.tar" -C "$bundle" serv00.sh serv00keep.sh serv00_runtime.py app.js -C "$payload" input.json
        input=$payload/bundle.tar
    fi
    if [[ $user =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$ && $host =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] &&
        SSHPASS="$password" "${command[@]}" "${options[@]}" -- "$user@$host" "$remote" < "$input" >/dev/null 2>&1; then
        printf 'account %d ok\n' "$count"
    else
        printf 'account %d failed\n' "$count" >&2
        failed=1
    fi
done < <(jq -c '.[]' <<< "$ACCOUNTS")
exit "$failed"
