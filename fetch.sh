#!/usr/bin/env bash
# Download what registry.lock.json names into <root>/upstream, and the repos we
# own into <root>/src.   fetch.sh <lock.json> <root>
set -euo pipefail
lock=$1 root=$2
mkdir -p "$root/upstream/ack" "$root/src"
q() { python3 -c "import json,sys;d=json.load(open('$lock'));print(eval(sys.argv[1]))" "$1"; }

sparse() { # repo dir ref path
  [ -d "$2/.git" ] && return 0
  git clone -q --filter=blob:none --no-checkout --depth 1 --branch "$3" "$1" "$2"
  git -C "$2" sparse-checkout set "$4" && git -C "$2" checkout -q "$3"
}

sparse https://github.com/GoogleCloudPlatform/k8s-config-connector.git "$root/upstream/kcc" \
  "$(q "d['providers']['config-connector']['tag']")" config/crds

aso_tag=$(q "d['providers']['aso']['tag']") aso_asset=$(q "d['providers']['aso']['crds_asset']")
[ -s "$root/upstream/aso-crds.yaml" ] || curl -fsSL -o "$root/upstream/aso-crds.yaml" \
  "https://github.com/Azure/azure-service-operator/releases/download/$aso_tag/$aso_asset"

q "'\n'.join(c['repo']+' '+c['tag'] for c in d['providers']['ack']['controllers'])" |
  while read -r repo tag; do echo "$repo $tag"; done |
  xargs -P 12 -n 2 bash -c "$(declare -f sparse); sparse https://github.com/aws-controllers-k8s/\$0.git $root/upstream/ack/\$0 \$1 helm/crds"

for r in ack-extensions aso-extensions config-connector-extensions catalog; do
  [ -d "$root/src/$r/.git" ] || git clone -q --depth 1 "https://github.com/highfivelabs/$r.git" "$root/src/$r"
done
echo "fetched: kcc $(q "d['providers']['config-connector']['tag']"), aso $aso_tag, $(ls "$root/upstream/ack" | wc -l) ACK controllers" >&2
