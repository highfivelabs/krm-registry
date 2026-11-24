#!/usr/bin/env bash
# Build the whole site: clone what we own, resolve versions through the APIs,
# fetch the upstream CRDs the lock names, generate.   build.sh <root> <site>
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd) root=$1 site=$2
mkdir -p "$root/src"
# Our own repos: krm-packages (the catalog's charts), and every provider whose repo is ours.
owned=$(python3 -c "import yaml,sys; d=yaml.safe_load(open(sys.argv[1])); print(' '.join(['krm-packages']+[p['repo'].rsplit('/',1)[1] for p in d['providers'] if p.get('repo','').startswith('https://github.com/highfivelabs/')]))" "$here/registry.yaml")
for r in $owned; do
  [ -d "$root/src/$r/.git" ] || git clone -q --depth 1 "https://github.com/highfivelabs/$r.git" "$root/src/$r" \
    || echo "skipping $r: not cloneable yet" >&2
done
python3 "$here/resolve.py" "$root/src" "$root/registry.lock.json"
bash "$here/fetch.sh" "$root/registry.lock.json" "$root"
python3 "$here/fetch_sources.py" "$root/registry.lock.json" "$root"
rm -rf "$site"
GEN_DATE=${GEN_DATE:-$(date -u +%F)} python3 "$here/gen.py" "$root" "$site" "$root/registry.lock.json"
# Favicons: High Five Labs brand icons (website repo, brand/icons).
cp "$here/index.html" "$here/prompt.txt" "$here/favicon.svg" "$here/favicon.ico" "$here/apple-touch-icon.png" "$root/registry.lock.json" "$site/"
