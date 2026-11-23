# krm-registry

The Terraform Registry, for KRM: documentation for the Kubernetes Resource
Model providers (Config Connector, AWS Controllers for Kubernetes, Azure
Service Operator, and vendors' own operators from Grafana to Oracle and
MongoDB) and High Five Labs' extensions that fill their gaps
(`*-extensions` for the clouds, `<vendor>-krm-extensions` for the rest), and for
the catalog's Helm chart packages built from them. Published to registry.highfivelabs.dev
by `publish.sh` ("Publishing").

A surface whose providers cannot be indexed (no operator, CRDs not published
as source, an API the REST runtime does not drive) carries `notes` in
`registry.yaml`; its page says why.

## What gets published

| Path | For | What |
| --- | --- | --- |
| `index.html` + `data/` | people | the browsable site: clouds, providers, kinds, packages, search, filters |
| `llms.txt` | agents | the entry point: what this is, where to start, how to look up |
| `<cloud>/index.md` → `<cloud>/<service>/index.md` → `kinds/<provider>/<group>/<Kind>.md` | agents | drill-down, a few KB per step; a kind page has front matter, links, an example manifest, every spec and status field |

A REST extension kind's example is the spec its live tests ran against the real API (`hack/live/live.yaml`, with the date from `records.json`), else the sample its fake-API test runs; the page and the front matter (`example_tested: live|live-failing|fake|none`) say which (`examples.py`). A kind also shows its repository's `examples/<Kind>/*.yaml` (krm-rest-runtime's `gen/examples.py`): the kind with the kinds it needs, joined by `spec.fieldRefs`, each tested on the fake API; a kind others need lists those examples under "Used by".
| `terraform/<type>.md` | agents | a Terraform resource type → the kinds that stand in for it |
| `providers/`, `packages/` | agents | each provider's kinds; each chart's source module, kinds, values, outputs |
| `registry.lock.json` | both | exactly which releases this build documents |

## For agents (MCP)

`mcp_server.py` serves the registry over the Model Context Protocol
(stdio), from a built site's `data/`: a local build (`--site site`, which
`.mcp.json` registers for sessions opened here), or the published registry
(`--url <its base URL>`, read over HTTPS on demand), so nothing new is
hosted. Python's standard library only.

| Tool | Answers |
| --- | --- |
| `search_kinds` | kinds across every provider by text (kind, group, service, Terraform type, description), cloud, service, provider or tier |
| `get_kind` | one kind: every spec and status field, Terraform equivalents, API paths, the gap it fills, its tested example, and the examples that show it with the kinds it needs (or that use it) |
| `kinds_for_terraform` | Terraform resource types to the kinds that stand in for each |
| `list_providers` | every provider: cloud, tier, publisher, version, number of kinds |
| `get_package` | a catalog chart: its upstream module, kinds, values and outputs |

```sh
./build.sh /tmp/krm-root site && python3 mcp_server.py --site site
python3 -m unittest discover -s tests   # the tools against tests/fixture-site
```

Each vendor repository also serves its own kinds in more depth (validate a
manifest, dry-run it on the fake API): `go run ./cmd/mcp` there.

## How a build works

```
build.sh <root> <site>
  1. clone krm-packages and every provider repo registry.yaml says is ours
  2. resolve.py   which releases exist (APIs)            → registry.lock.json
  3. fetch.sh, fetch_sources.py
                  the CRDs at those releases             → <root>/upstream
  4. gen.py       CRDs + kind docs + coverage + charts   → <site>
```

`resolve.py` asks the sources' APIs instead of keeping pins here:

- **Operators** (any provider with `release:` in registry.yaml): newest tag
  matching the pattern, from GitHub's Releases API or git tags (GitLab);
  `fetch_sources.py` takes their CRDs from a release asset or a repo path.
  A provider shipped through a public Cloud Storage bucket instead
  (`release: {gcs: bucket}`) gets its newest version-named folder, and
  `source: {gcs: bucket/{tag}/file.tar.gz}` unpacks the tarball there. The
  Config Connector operator is fetched this way.
- **Install variants.** A provider's `install:` list (name, when, steps with
  `{tag}`) becomes an Install card on its page and a section of its agent page,
  e.g. the operator's GKE Standard and GKE Autopilot manifests. A provider that
  `installs:` another is linked from it ("installed by").
- **Terraform, by the numbers.** For each surface's Terraform provider: rank,
  downloads, latest version and full resource list (Terraform Registry API),
  plus the top 200 providers for the long-tail table. Operator kinds map to
  Terraform types in `terraform-map.yaml`; generated kinds carry theirs in the
  CRD's `hfl.dev/terraform` annotation.
- **Official providers.** Config Connector and ASO: latest stable release from
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

Run it after merging to `main`, and after a provider release you want
listed. GitHub Actions and Pages are off for the org, so nothing runs it on a
schedule yet; the old Pages workflow is gone.

## Joining the registry

A provider repo is described by a block in `registry.yaml` (later: a
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
