#!/usr/bin/env bash
# Schedule after SSH provisioning: */10 * * * * /usr/local/bin/bash "$HOME/.sing-box-serv00/release/serv00keep.sh"
set -eu
umask 077
bundle=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
exec bash "$bundle/serv00.sh" --keep
