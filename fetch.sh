#!/usr/bin/env bash
# Download what registry.lock.json names into <root>/upstream (CRDs, and the
# official controllers' test samples), and the repos we own into <root>/src.   fetch.sh <lock.json> <root>
set -euo pipefail
lock=$1 root=$2
mkdir -p "$root/upstream/ack" "$root/src"
q() { python3 -c "import json,sys;d=json.load(open('$lock'));print(eval(sys.argv[1]))" "$1"; }

sparse() { # repo dir ref path...   (a kept clone gains any path it lacks)
  local repo=$1 dir=$2 ref=$3; shift 3
  if [ -d "$dir/.git" ]; then
    for p in "$@"; do git -C "$dir" sparse-checkout list | grep -qx "$p" || git -C "$dir" sparse-checkout add "$p"; done
    return 0
  fi
  git clone -q --filter=blob:none --no-checkout --depth 1 --branch "$ref" "$repo" "$dir"
  git -C "$dir" sparse-checkout set "$@" && git -C "$dir" checkout -q "$ref"
}

sparse https://github.com/GoogleCloudPlatform/k8s-config-connector.git "$root/upstream/kcc" \
  "$(q "d['operators']['config-connector']['tag']")" config/crds config/samples/resources

aso_tag=$(q "d['operators']['aso']['tag']") aso_asset=$(q "d['operators']['aso']['crds_asset']")
[ -s "$root/upstream/aso-crds.yaml" ] || curl -fsSL -o "$root/upstream/aso-crds.yaml" \
  "https://github.com/Azure/azure-service-operator/releases/download/$aso_tag/$aso_asset"
# ASO's samples, for examples (its CRDs come from the release asset above).
sparse https://github.com/Azure/azure-service-operator.git "$root/upstream/aso" "$aso_tag" v2/samples

q "'\n'.join(c['repo']+' '+c['tag'] for c in d['operators']['ack']['controllers'])" |
  while read -r repo tag; do echo "$repo $tag"; done |
  xargs -P 12 -n 2 bash -c "$(declare -f sparse); sparse https://github.com/aws-controllers-k8s/\$0.git $root/upstream/ack/\$0 \$1 helm/crds test/e2e/resources"

for r in ack-extensions aso-extensions config-connector-extensions krm-packages; do
  [ -d "$root/src/$r/.git" ] || git clone -q --depth 1 "https://github.com/highfivelabs/$r.git" "$root/src/$r"
done
echo "fetched: kcc $(q "d['operators']['config-connector']['tag']"), aso $aso_tag, $(ls "$root/upstream/ack" | wc -l) ACK controllers" >&2
