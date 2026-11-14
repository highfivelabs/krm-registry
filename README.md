# krm-registry

The Terraform Registry, for KRM: documentation for the Kubernetes Resource
Model providers (Config Connector, AWS Controllers for Kubernetes, Azure
Service Operator, and vendors' own operators from Grafana to Oracle and
MongoDB) and High Five Labs' extensions that fill their gaps
(`*-extensions` for the clouds, `<vendor>-krm-extensions` for the rest), and for
the catalog's Helm chart packages built from them. Published to GitHub Pages
by `.github/workflows/pages.yml`.

A surface whose providers cannot be indexed (no operator, CRDs not published
as source, an API the REST runtime does not drive) carries `notes` in
`registry.yaml`; its page says why.

## What gets published

| Path | For | What |
| --- | --- | --- |
| `index.html` + `data/` | people | the browsable site: clouds, providers, kinds, packages, search, filters |
| `llms.txt` | agents | the entry point: what this is, where to start, how to look up |
| `<cloud>/index.md` → `<cloud>/<service>/index.md` → `kinds/<provider>/<group>/<Kind>.md` | agents | drill-down, a few KB per step; a kind page has front matter, links, an example manifest, every spec and status field |
| `terraform/<type>.md` | agents | a Terraform resource type → the kinds that stand in for it |
| `providers/`, `packages/` | agents | each provider's kinds; each chart's source module, kinds, values, outputs |
| `registry.lock.json` | both | exactly which releases this build documents |

## How a build works

```
build.sh <root> <site>
  1. clone the catalog and every provider repo registry.yaml says is ours
  2. resolve.py   which releases exist (APIs)            → registry.lock.json
  3. fetch.sh, fetch_sources.py
                  the CRDs at those releases             → <root>/upstream
  4. gen.py       CRDs + kind docs + coverage + charts   → <site>
```

`resolve.py` asks the sources' APIs instead of keeping pins here:

- **Operators** (any provider with `release:` in registry.yaml): newest tag
  matching the pattern, from GitHub's Releases API or git tags (GitLab);
  `fetch_sources.py` takes their CRDs from a release asset or a repo path.
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

## Setup

1. Create `highfivelabs/krm-registry` (public) and push this repository.
2. Settings → Pages → Source: **GitHub Actions**.
3. The first push to `main` builds and deploys; after that it runs nightly.

## Rebuilding on change

The nightly run picks up anything new. To rebuild as soon as an extension repo
or the catalog merges, add this to their push-to-main workflow, with a token
that can write to krm-registry's contents (a fine-grained token or a GitHub
App installation token) in the `REGISTRY_DISPATCH_TOKEN` secret:

```yaml
- run: gh api repos/highfivelabs/krm-registry/dispatches -f event_type=source-changed
  env:
    GH_TOKEN: ${{ secrets.REGISTRY_DISPATCH_TOKEN }}
```

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
