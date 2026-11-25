#!/usr/bin/env bash
# Print an installation token for a GitHub App, good for an hour.
#
#   hack/github-app-token.sh APP_ID KEY_FILE [OWNER]
#
# KEY_FILE is the App's private key (PEM). OWNER (default highfivelabs) picks
# the installation. The Cloud Build site publish (cloudbuild.site.yaml) uses
# it as krm-registry-reader, an App with Metadata: read and nothing else, so
# the token reads public data at the authenticated rate limit and cannot
# push, merge or read private repositories (like any signed-in account it
# could still open issues on public ones; README "Publishing"). Needs
# openssl, curl and python3.
set -euo pipefail
app_id=$1 key=$2 owner=${3:-highfivelabs}

b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }
now=$(date +%s)
# iat a minute back for clock drift; GitHub allows at most ten minutes.
header=$(printf '{"alg":"RS256","typ":"JWT"}' | b64url)
payload=$(printf '{"iat":%d,"exp":%d,"iss":"%s"}' $((now - 60)) $((now + 540)) "$app_id" | b64url)
sig=$(printf '%s.%s' "$header" "$payload" | openssl dgst -sha256 -sign "$key" -binary | b64url)
jwt="$header.$payload.$sig"

api() {
  curl -fsS -H "Authorization: Bearer $jwt" -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" "$@"
}
installs=$(api https://api.github.com/app/installations) ||
  { echo "github-app-token: GitHub refused App $app_id's key (wrong App ID or key?)" >&2; exit 1; }
id=$(python3 -c '
import json, sys
owner = sys.argv[1].lower()
ids = [i["id"] for i in json.load(sys.stdin) if i["account"]["login"].lower() == owner]
if not ids:
    sys.exit(f"github-app-token: the App is not installed on {owner}")
print(ids[0])' "$owner" <<<"$installs")
api -X POST "https://api.github.com/app/installations/$id/access_tokens" |
  python3 -c 'import json, sys; print(json.load(sys.stdin)["token"])'
