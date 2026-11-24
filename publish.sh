#!/usr/bin/env bash
# Build the site and publish it to registry.highfivelabs.dev.
#
#   publish.sh [SITE]       publish SITE (a built site directory), or build
#                           one first into a temporary directory
#
# The site is served from the private bucket gs://highfivelabs-prd-registry
# behind tf-infra's prod/gcp unit sites/registry (HTTPS load balancer, Cloud
# CDN, 300 s cache, so no invalidation). Only registry-publisher@ may write
# it; this script impersonates that account, so the caller needs
# roles/iam.serviceAccountTokenCreator on it (tf-infra grants that) and no
# key exists anywhere. Calls are billed to REGISTRY_QUOTA_PROJECT
# (--billing-project), where the publisher holds serviceUsageConsumer, never
# to the caller's own gcloud billing/quota_project. Objects that are no
# longer in the site are deleted.
#
# A build clones the private highfivelabs repos and asks GitHub's API for
# releases: GITHUB_TOKEN, else `gh auth token`.
#
# Environment: REGISTRY_BUCKET, REGISTRY_PUBLISHER, REGISTRY_QUOTA_PROJECT,
# REGISTRY_ROOT (the build's work directory, kept between runs for faster
# clones), DRY_RUN=1 (show what would change, write nothing).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
bucket=${REGISTRY_BUCKET:-highfivelabs-prd-registry}
publisher=${REGISTRY_PUBLISHER:-registry-publisher@highfivelabs-prd.iam.gserviceaccount.com}
# The quota project for calls made as the publisher. tf-infra grants it
# serviceUsageConsumer there only, so it must not fall back to the caller's
# own billing/quota_project (often hfl-dev-514205), which it cannot use.
quota_project=${REGISTRY_QUOTA_PROJECT:-highfivelabs-prd}
site=${1:-}

if [[ -z "$site" ]]; then
  export GITHUB_TOKEN=${GITHUB_TOKEN:-$(gh auth token)}
  root=${REGISTRY_ROOT:-$(mktemp -d)}
  site=$(mktemp -d)/site
  "$here/build.sh" "$root" "$site"
fi
[[ -f "$site/index.html" && -f "$site/registry.lock.json" ]] || { echo "publish: $site is not a built site" >&2; exit 2; }

# The bucket's not-found page: the app itself, which shows its own not-found view.
[[ -f "$site/404.html" ]] || cp "$site/index.html" "$site/404.html"

args=(storage rsync "$site" "gs://$bucket" --recursive --delete-unmatched-destination-objects
  --impersonate-service-account="$publisher" --billing-project="$quota_project")
[[ "${DRY_RUN:-}" == 1 ]] && args+=(--dry-run)
gcloud "${args[@]}"

python3 - "$site/registry.lock.json" <<'PY'
import json, sys
l = json.load(open(sys.argv[1]))
print(f"published {len(l['providers'])} providers")
PY
