# krm-registry

The Terraform Registry, for KRM: documentation for the Kubernetes Resource
Model operators of each provider (Config Connector, AWS Controllers for
Kubernetes, Azure Service Operator, vendors' and the community's operators from
Grafana to Oracle and MongoDB, and High Five Labs' own: `*-extensions` for the
clouds, `<vendor>-krm-extensions` for the rest), and for the catalog's Helm
chart packages built from them. Published to registry.highfivelabs.dev
by `publish.sh` ("Publishing").

The words, here and on every page:

| Word | Means | For example |
| --- | --- | --- |
| provider | a cloud or vendor, as Terraform uses the word (`providers:` in `registry.yaml`) | `gcp`, `aws`, `grafana`, `okta` |
| operator | a set of controllers and the CRDs they install, for one provider (`operators:`) | Config Connector, ACK, `okta-krm-extensions` |
| controller | one controller inside an operator | ACK's `s3-controller` |
| tier | who publishes an operator: `official` (the provider itself), `community`, `hfl` (High Five Labs) | |

A provider whose kinds cannot be indexed (no operator, CRDs not published
as source, an API the REST runtime does not drive) carries `notes` in
`registry.yaml`; its page says why.

## What gets published

| Path | For | What |
| --- | --- | --- |
| `index.html` + `data/` | people | the browsable site: providers, operators, kinds, packages, coverage, search, filters |
| `llms.txt` | agents | the entry point: what this is, where to start, how to look up |
| `prompt.txt` | people, for their agents | a prompt to paste into a coding agent; the landing page shows it with a copy button (source: `prompt.txt` in this repo) |
| `<provider>/index.md` → `<provider>/<service>/index.md` → `kinds/<operator>/<group>/<Kind>.md` | agents | drill-down, a few KB per step; a kind page has front matter, links, an example manifest, every spec and status field |

A High Five Labs REST kind's example is the spec its live tests ran against the real API (`hack/live/live.yaml`, with the date from `records.json`), else the sample its fake-API test runs; the page and the front matter (`example_tested: live|live-failing|fake|none`) say which (`examples.py`). A kind also shows its repository's `examples/<Kind>/*.yaml` (krm-rest-runtime's `gen/examples.py`): the kind with the kinds it needs, joined by `spec.fieldRefs`, each tested on the fake API; a kind others need lists those examples under "Also needed by".

An official kind (Config Connector, ACK, ASO) shows the samples from its controller's own tests, at the release indexed (`samples.py`): Config Connector's `config/samples/resources/<kind>/` (one per directory, with the objects it needs), each ACK controller's `test/e2e/resources/*.yaml` (adoption and negative cases left out), and ASO's `v2/samples/<group>/<version>/` at the kind's version, joined with its `refs/` and owner chain. `fetch.sh` adds those paths to the sparse checkouts. The page, the Markdown (`## From the controller's tests`) and `get_kind` (`upstreamSamples`) carry them.
| `terraform/<type>.md` | agents | a Terraform resource type → the kinds that stand in for it |
| `operators/`, `packages/` | agents | each operator's kinds; each chart's source module, kinds, values, outputs |
| `registry.lock.json` | both | exactly which releases this build documents |

## For agents (MCP)

Hosted: **`https://registry.highfivelabs.dev/mcp`**, streamable HTTP, no
sign-in, read only. In Claude Code:
`claude mcp add --transport http krm-registry https://registry.highfivelabs.dev/mcp`.

`cmd/mcp` (Go) is that server. It speaks stdio by default, or streamable
HTTP with `--http PORT` (stateless: `POST /mcp`, one JSON-RPC message per
request, a JSON response, no sessions or server-sent events; `GET /healthz`).
It reads a built site's `data/`: a local build (`--site site`, which
`.mcp.json` registers for sessions opened here) or the published registry
(`--url <its base URL>`, over HTTPS). With `--http` it re-reads a file after
`--ttl` seconds (300, the CDN's cache time), so a publish shows up without a
restart. Go's standard library only.

The hosted one is the `Dockerfile` (`--url https://registry.highfivelabs.dev
--http 0`) on Cloud Run as `registry-mcp` in highfivelabs-prd, behind the
registry's load balancer at `/mcp` (tf-infra, README "KRM Registry").
`cloudbuild.yaml` builds and rolls it out: Cloud Build's `krm-registry-mcp`
trigger runs it on every push to `main` that changes the server.

| Tool | Answers |
| --- | --- |
| `list_providers` | every provider: its operators and, per service, how many kinds |
| `search_kinds` | kinds across every operator by text (kind, group, service, Terraform type, description), provider, service, operator or tier |
| `get_kind` | one kind: every spec and status field, Terraform equivalents, API paths, the gap it fills, its tested example, the samples from the official controller's tests, and the examples that show it with the kinds it needs (or that use it) |
| `kinds_for_terraform` | Terraform resource types to the kinds that stand in for each |
| `list_operators` | every operator: provider, tier, publisher, version, GitHub stars, number of kinds |
| `get_install` | one operator's install and credentials steps for a chosen variant, with placeholders and how to verify |
| `get_package` | a catalog chart: its upstream module, kinds, values and outputs |

```sh
./build.sh /tmp/krm-root site && go run ./cmd/mcp --site site
go run ./cmd/mcp --url https://registry.highfivelabs.dev --http 8080   # as hosted, locally
go test ./cmd/mcp   # the tools against cmd/mcp/testdata/site
```

Each vendor repository also serves its own kinds in more depth (validate a
manifest, dry-run it on the fake API): `go run ./cmd/mcp` there.

## How a build works

```
build.sh <root> <site>
  1. clone krm-packages and every operator repo registry.yaml says is ours
  2. resolve.py   which releases exist (APIs)            → registry.lock.json
  3. fetch.sh, fetch_sources.py
                  the CRDs at those releases             → <root>/upstream
  4. gen.py       CRDs + kind docs + coverage + charts   → <site>
```

`resolve.py` asks the sources' APIs instead of keeping pins here:

- **Releases** (any operator with `release:` in registry.yaml): newest tag
  matching the pattern, from GitHub's Releases API or git tags (GitLab);
  `fetch_sources.py` takes their CRDs from a release asset or a repo path.
  An operator shipped through a public Cloud Storage bucket instead
  (`release: {gcs: bucket}`) gets its newest version-named folder, and
  `source: {gcs: bucket/{tag}/file.tar.gz}` unpacks the tarball there. The
  Config Connector operator is fetched this way.
- **Install variants.** An operator's `install:` list becomes an Install card
  on its page, a section of its agent page and the `install` field of the MCP
  server's `list_providers`. Each variant has a `name`, `steps` and optionally
  `when`, `note` and `tested` (how and when the steps were last run, shown
  under them). In `steps`, `{tag}` is the release tag the lock resolved and
  `{version}` its semver as charts carry it (`v5.26.0` → `5.26.0`,
  `gha-runner-scale-set-0.15.1` → `0.15.1`). An operator that `installs:`
  another is linked from it ("installed by"). Each block of steps has a Copy
  button, and "Copy all steps" copies credentials and install together.
  - **Choices.** A variant may have `pick: {Axis: value, ...}` instead of a
    `name` (Config Connector: `Method` × `Cluster`). The page shows one radio
    group per axis and the variant matching the choice; a variant without
    `pick` is its own value on one unnamed axis.
  - **Credentials.** `credentials:` (same shape) is shown above the install
    steps as step 1: the identity the controller acts as and the roles it
    needs. A credentials entry follows the install choice on the axes its
    `pick` names (Config Connector's follow `Cluster`).
  - **Credentials as a package.** A credentials entry may carry `package:`
    (`chart`, a krm-packages chart such as `gcp/iam-service-account`;
    `release`; `vars`, the placeholders line; `values`; `note`): the same
    identity as KRM objects, applied by a Config Connector that already
    manages the project (a management cluster's). gen.py writes its steps;
    the page offers "A package (KRM)" or "gcloud", the package first, and MCP
    `get_install` returns it as `credentials.package` (HFL-766).
  - **Every controller.** For an operator with one controller per service
    (ACK), `{controllers}` in steps becomes one `<service> <version>` line per
    controller the lock resolved and `{ncontrollers}` their count, so the
    "All services" variant installs each at its own latest release.
  - **Not published yet.** An operator whose only variants are unpublished OCI
    charts shows where the chart will be instead of steps.
  - **Where the steps come from** (`config.py`). A repository of ours may
    carry a `krm-registry.yaml` at its root with its own `install:` (format in
    `config.py`'s docstring); `build.sh` clones it, and the page says the steps
    came from there. `registry.yaml` wins: an operator with `install:` there
    keeps it. That is how the registry describes the operators we do not
    control (ACK, ASO, the vendor and community operators), written from
    upstream's docs and checked (`tested:`), and how it overrides one of ours.
  - **Our OCI charts.** A variant with `oci: <repo>` (e.g.
    `charts/config-connector`) installs a chart from `cr.highfivelabs.dev`.
    `resolve.py` finds the version (the variant's `version:`, else the
    operator's resolved release, else, for an operator at `main`, the newest
    release tag `cr` lists), asks `cr` for that manifest and records it in the
    lock (`oci.<operator>.<repo>: {version, digest}`, or null). `gen.py` shows
    the variant only when it is published, so a new upstream release falls
    back to upstream's own steps until our chart for it is pushed, and an
    operator's card appears with its first published chart.
- **Terraform, by the numbers.** For each provider's Terraform provider: rank,
  downloads (all time), tier, source repository, latest version and full
  resource list, as the Terraform Registry API reports them on the day read
  (`terraform.read` in the lock), plus its top 200 providers for the long-tail
  table. The page names the Terraform Registry as the source of each.
  Operators' kinds map to Terraform types in `terraform-map.yaml`; generated
  kinds carry theirs in the CRD's `hfl.dev/terraform` annotation.
- **GitHub stars.** Of each operator's repository and each Terraform provider's
  source repository (lock `stars`, null without a token). It is the measure the
  OpenTofu Registry publishes as popularity, and the one both sides of the
  Coverage page can be compared by.
- **Official operators.** Config Connector and ASO: latest stable release from
  GitHub's Releases API. ACK: every non-archived `*-controller` repository in
  `aws-controllers-k8s` (org listing API), each at its latest release.
- **Upstream modules of the catalog's charts.** The Terraform Registry API when
  the module is published there (Cloud Foundation Toolkit, terraform-aws-modules,
  Azure Verified Modules), GitHub's Releases API otherwise (Cloud Foundation
  Fabric). Charts behind their module show "vX out" on the site and are listed
  in each run's summary.
- Without `GITHUB_TOKEN` (local runs) it falls back to `git ls-remote --tags`
  and to the ACK controller list ack-extensions pins.

The repos we own are read at `main`. `registry.yaml` lists every source.

## Publishing

The site is served from the private bucket `gs://highfivelabs-prd-registry`
through tf-infra's `prod/gcp` unit `sites/registry`: an HTTPS load balancer
with Cloud CDN for `registry.highfivelabs.dev`, and `reg.hfl.dev` as a 301 to
it. Responses are cached for 300 seconds, so a publish shows up within five
minutes and needs no invalidation.

```sh
./publish.sh                 # build (GITHUB_TOKEN or `gh auth token`), then publish
./publish.sh site            # publish a site already built
DRY_RUN=1 ./publish.sh site  # list what would change
```

It syncs the site to the bucket as `registry-publisher@highfivelabs-prd`, by
impersonation: the caller needs `roles/iam.serviceAccountTokenCreator` on that
account (granted in tf-infra), and no key exists. Objects no longer in the
site are deleted. `404.html` is a copy of `index.html`, so an unknown path
still loads the app.

Cloud Build runs it for you (`cloudbuild.site.yaml`): the `krm-registry-site`
trigger in highfivelabs-prd (tf-infra, README "KRM Registry") fires on every
push to `main` that changes what the site is built from, and Cloud Scheduler
runs it once a day, so upstream releases show up with no merge here. It runs
as `registry-publisher@` itself (`REGISTRY_IMPERSONATE=0`). Its GitHub token
comes from the `krm-registry-reader` GitHub App, which holds Metadata: read
and nothing else (every repository the build reads is public; the token is
for the API's rate limit and release data). `hack/github-app-token.sh` mints
it from the App's key in the secret `registry-github-app-key`. Run
`./publish.sh` by hand only to publish a branch or between runs.

## Joining the registry

An operator repo is described by a block in `registry.yaml` (later: a
`krm-registry.yaml` at its own root). Per-kind docs are Markdown in
`docs/kinds/<Kind>.md` with front matter naming the kinds they cover:

```markdown
---
kinds: [VaultSecret]
apiVersion: keyvault.azure.hfl.dev/v1
---
# VaultSecret
**Gap it fills.** …
```

Without kind docs the registry uses the README's kind table, then the CRD alone.

## Local

```sh
pip install pyyaml
./build.sh /tmp/krm-root site && python3 -m http.server -d site 8000
python3 pack.py site preview   # the bundled copy for hosts that cap file counts
```
