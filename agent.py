"""The registry as Markdown pages an agent can walk, a few KB per step.

  llms.txt                                 what this is, where to start, how to look things up
  <cloud>/index.md                         the cloud's services, official and extension counts
  <cloud>/<service>/index.md               every kind of the service, one line each
  kinds/<provider>/<group>/<Kind>.md       one kind: front matter, example, spec, status
  providers/index.md, providers/<id>.md    each provider's kinds by service
  packages/index.md, packages/<cloud>/<chart>.md
  terraform/<type>.md                      a Terraform type → the kinds that stand in for it

Links are relative, so the tree works under any base path (GitHub Pages
project sites included).
"""
import os, re
from collections import defaultdict

import yaml

SURF = {}  # filled from the catalog's surfaces
MCP_URL = "https://registry.highfivelabs.dev/mcp"  # the hosted MCP server (README "For agents (MCP)")
TFNOTE = ("Terraform equivalents manage the same cloud resource. They are not used here: "
          "a kind is a Kubernetes object that its provider's controller keeps in sync, with no state file and no plan/apply.")


def svc_slug(s):
    return re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-") or "other"


def kind_path(k):
    return f"kinds/{k['provider']}/{k['group']}/{k['kind']}.md"


class Writer:
    def __init__(self, out):
        self.out, self.n = out, 0

    def __call__(self, rel, text):
        p = os.path.join(self.out, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text.rstrip() + "\n")
        self.n += 1


def link(frm, to, label):
    return f"[{label}]({os.path.relpath(to, os.path.dirname(frm) or '.')})"


def cell(s, n=None):
    s = " ".join(str(s or "").split()).replace("|", "\\|")
    s = re.sub(r"\[([^\]]*)\]\((?!https?://)", r"\\[\1\\](", s)  # regexes like [a-z](-*[a-z]) are not links
    if n is None or len(s) <= n:
        return s
    t = s[: n - 1]
    i = t.rfind("[")
    if i != -1 and "](" in t[i:] and ")" not in t[t.index("](", i) + 2:]:
        t = t[:i]  # don't cut a link in half
    return t.rstrip() + "…"


def tfs(k):
    return sorted({t for a in k.get("api") or [] for t in a["tf"]})


def apis(k):
    return sorted({a["api"] for a in k.get("api") or [] if a.get("api")})


# ---------------------------------------------------------------- example

def placeholder(f):
    t, leaf = f["t"], f["p"].split(".")[-1]
    if f.get("e"):
        return f["e"][0]
    if "df" in f:
        return f["df"]
    if t.startswith("[]"):
        return []
    if t.startswith("map") or t == "object":
        return {}
    if t in ("integer", "number", "int|string"):
        return 0
    if t == "boolean":
        return False
    return f"<{leaf}>"


def example(k):
    """A manifest with every required field, nested where the parent is required too."""
    rows = {f["p"]: f for f in k["spec"]}
    req = [f for f in k["spec"] if f.get("r") and "[]" not in f["p"]]
    keep = [f for f in req if all(rows.get(".".join(f["p"].split(".")[:i]), {}).get("r")
                                  for i in range(2, f["p"].count(".") + 1))]
    spec = {}
    for f in sorted(keep, key=lambda f: f["p"].count(".")):
        parts = f["p"].split(".")[1:]
        node = spec
        for p in parts[:-1]:
            node = node.setdefault(p, {}) if isinstance(node.get(p, {}), dict) else node
        if parts[-1] not in node:
            node[parts[-1]] = placeholder(f)
            if node[parts[-1]] == {} and f"{f['p']}.name" in rows:  # references: owner, *Ref
                node[parts[-1]] = {"name": f"<{parts[-1].removesuffix('Ref')}-name>"}
    meta = {"name": "example"}
    if k["scope"] == "Namespaced":
        meta["namespace"] = "default"
    doc = {"apiVersion": f"{k['group']}/{k['version']}", "kind": k["kind"], "metadata": meta, "spec": spec}
    y = yaml.safe_dump(doc, sort_keys=False, default_flow_style=False, width=200)
    if not spec:
        y = y.rstrip() + "  # no required fields\n"
    return y


# ---------------------------------------------------------------- pages

def kind_page(k, providers, charts):
    p = providers[k["provider"]]
    fm = {"kind": k["kind"], "apiVersion": f"{k['group']}/{k['version']}", "provider": k["provider"],
          "provider_tier": k["tier"], "cloud": k["surface"], "service": k["service"], "stability": k["stability"],
          "scope": k["scope"], "versions": k["versions"]}
    if apis(k):
        fm["cloud_api_resource"] = apis(k)
    if tfs(k):
        fm["terraform_equivalents"] = tfs(k)
    if k.get("overlays"):
        fm["overlays"] = k["overlays"]["id"]
    fm["example_tested"] = (k.get("example") or {}).get("tested", "upstream" if k.get("samples") else "none")
    if any(r["main"] == k["kind"] for r in k.get("related") or []):
        fm["example_with_related"] = [r["file"] for r in k["related"] if r["main"] == k["kind"]]
    if (k.get("example") or {}).get("tested") in ("live", "live-failing") and k["example"].get("date"):
        fm["example_live_date"] = k["example"]["date"]
    if k.get("usedBy"):
        fm["used_by_packages"] = k["usedBy"]
    if k.get("docsUrl"):
        fm["docs"] = k["docsUrl"]
    fm["repo"] = k["repoUrl"]
    fm["crd"] = k["srcUrl"]
    if k.get("doc"):
        fm["kind_doc"] = f"{p['repo']}/blob/{p['version']}/" + k["doc"]["file"].split("/", 1)[1]
    me = kind_path(k)
    L = ["---", yaml.safe_dump(fm, sort_keys=False, width=200).rstrip(), "---", "", f"# {k['kind']}", "",
         f"`{k['group']}/{k['version']}` from {link(me, f'providers/{p['id']}.md', p['title'])} ({k['tier']}), "
         f"{k['stability']}, {k['scope'].lower()}. Service: {link(me, f'{k['surface']}/{svc_slug(k['service'])}/index.md', k['service'])}.", ""]
    links = ([f"[{p['title']} reference]({k['docsUrl']})"] if k.get("docsUrl") else []) + \
        [f"[source repo]({k['repoUrl']})", f"[CRD]({k['srcUrl']})"] + ([f"[kind doc]({fm['kind_doc']})"] if fm.get("kind_doc") else [])
    L += ["Links: " + " · ".join(links), ""]
    if k.get("desc") and "is the Schema for" not in k["desc"]:
        L += [k["desc"], ""]
    if k.get("gap"):
        L += [f"**Gap it fills.** {k['gap']['gap']}", ""]
        if k["gap"].get("upstream"):
            L += [f"**Upstream.** {k['gap']['upstream']}", ""]
    if k.get("overlays"):
        o = k["overlays"]
        og, okind = o["id"].split("/", 1)[1].rsplit("/", 1)
        L += [f"**Overlays** `{og}` `{okind}` ({link(me, f'kinds/{o['id']}.md', 'page')}): same kind in this provider's own group. "
              f"Adds {len(o['added'])} fields" + (f": {', '.join('`'+a+'`' for a in o['added'][:25])}{' …' if len(o['added']) > 25 else ''}" if o["added"] else "")
              + (f"; does not carry {len(o['removed'])}." if o["removed"] else "."), ""]
    if tfs(k):
        L += ["**Terraform equivalent:** " + ", ".join(f"`{t}`" for t in tfs(k)) + ".", "", f"_{TFNOTE}_", ""]
    if k.get("usedBy"):
        L += ["**Used by packages:** " + ", ".join(link(me, f"packages/{c}.md", c) for c in k["usedBy"]), ""]
    if k.get("refs"):
        L += ["**References:** " + "; ".join(f"`{r['field']}` → {', '.join(r['kinds'])}" for r in k["refs"]), ""]
    if k.get("doc"):
        body = re.sub(r"^# .*\n", "", k["doc"]["md"]).strip()
        L += ["## Notes", "", re.sub(r"^(#+) ", r"#\1 ", body, flags=re.M), ""]
    ex = k.get("example")
    if ex:
        L += ["## Example", "", ex["line"], "", "```yaml", ex["yaml"].rstrip(), "```", ""]
        if ex.get("prereqs"):
            L += ["The live test makes these first; `<...>` values come from them: " + "; ".join(f"`{c}`" for c in ex["prereqs"]) + ".", ""]
        L += [n for n in ex.get("notes") or [] if n] + ([""] if ex.get("notes") else [])
        L += [f"The object reaches the API through the Connection named `default` in its namespace (see {link(me, f'providers/{p['id']}.md', p['title'])}).", ""]
    else:
        L += ["## Example", "", "Every required field, with placeholders; untested.", "", "```yaml", example(k).rstrip(), "```", ""]
    ups = k.get("samples") or []
    if ups:
        L += ["## From the controller's tests", "", ups[0]["line"], ""]
        for x in ups:
            L += [f"### {x['title']}", "", f"From [`{x['file']}`]({x['url']}):", "", "```yaml", x["yaml"].rstrip(), "```", ""]
    rel = k.get("related") or []
    own = [r for r in rel if r["main"] == k["kind"]]
    used = [r for r in rel if r["main"] != k["kind"]]
    if own:
        L += ["## With the kinds it needs", "",
              "Each object takes the ID of the one before it through `spec.fieldRefs`; nothing is sent until that one is Ready. "
              "Tested on the fake API: every object is made, in this order, and deleted.", ""]
        for r in own:
            L += [f"From [`{r['file']}`]({r['url']}):", "", "```yaml", r["yaml"].rstrip(), "```", ""]
    if used:
        names = [f"[{r['main']}]({r['url']})" for r in used]
        more = f", and {len(names) - 12} more (`get_kind` in the registry's MCP server lists them all)" if len(names) > 12 else ""
        L += ["## Used by", "", "Examples in which other kinds take this one's ID through `spec.fieldRefs`: "
              + ", ".join(names[:12]) + more + ".", ""]
    for sec in ("spec", "status"):
        L += [f"## {sec.capitalize()}", ""]
        if not k[sec]:
            L += ["None.", ""]
            continue
        L += ["| Field | Type | Required | Description |", "| --- | --- | --- | --- |"]
        for f in k[sec]:
            extra = []
            if f.get("e"):
                extra.append("One of: " + ", ".join(f"`{e}`" for e in f["e"][:12]) + (" …" if len(f["e"]) > 12 else ""))
            if "df" in f:
                extra.append(f"Default `{f['df']}`")
            if f.get("im"):
                extra.append("Immutable")
            d = cell(" ".join([f.get("d", "")] + [x + "." for x in extra]), 700)
            L.append(f"| `{f['p'].split('.', 1)[1] if '.' in f['p'] else f['p']}` | {cell(f['t'])} | {'yes' if f.get('r') else ''} | {d} |")
        L.append("")
    return "\n".join(L)


def write_agent_tree(out, cat, full_kinds, charts_full):
    SURF.update({k: v["title"] for k, v in cat["surfaces"].items()})
    w = Writer(out)
    providers = {p["id"]: p for p in cat["providers"]}
    by_surf_svc = defaultdict(lambda: defaultdict(list))
    tf_index = defaultdict(list)
    for k in full_kinds:
        by_surf_svc[k["surface"]][k["service"]].append(k)
        for t in tfs(k):
            tf_index[t].append(k)
        w(kind_path(k), kind_page(k, providers, charts_full))

    # Clouds and services (and surfaces with nothing indexed, for their notes).
    for s in cat["surfaces"]:
        if cat["surfaces"][s].get("notes") and s not in by_surf_svc:
            by_surf_svc[s] = {}
    for s, svcs in by_surf_svc.items():
        me = f"{s}/index.md"
        ps = [p for p in cat["providers"] if p["surface"] == s]
        note = cat["surfaces"][s].get("notes")
        L = [f"# {SURF[s]}", "", ("Providers: " + ", ".join(f"{link(me, f'providers/{p['id']}.md', p['title'])} ({p['tier']})" for p in ps) + ".") if ps else "No provider indexed.",
             *([f"Not indexed: {note}"] if note else []),
             *(["Extension kinds are listed in the same service as the official kinds they sit beside.", "",
                "| Service | Official kinds | Extension kinds | |", "| --- | --- | --- | --- |"] if svcs else [])]
        for svc in sorted(svcs):
            ks = svcs[svc]
            o = sum(k["tier"] == "official" for k in ks)
            L.append(f"| {svc} | {o} | {len(ks) - o} | {link(me, f'{s}/{svc_slug(svc)}/index.md', 'kinds')} |")
        w(me, "\n".join(L))
        for svc, ks in svcs.items():
            me = f"{s}/{svc_slug(svc)}/index.md"
            L = [f"# {svc} ({SURF[s]})", "", f"{len(ks)} kinds. {TFNOTE}", "",
                 "| Kind | apiVersion | Provider | Stability | Terraform equivalent | Summary |", "| --- | --- | --- | --- | --- | --- |"]
            for k in sorted(ks, key=lambda k: (k["kind"], k["tier"] != "official")):
                summ = (k.get("gap") or {}).get("gap") or ("" if "is the Schema for" in (k.get("desc") or "") else k.get("desc"))
                L.append(f"| {link(me, kind_path(k), k['kind'])} | `{k['group']}/{k['version']}` | {k['provider']} | {k['stability']} | "
                         f"{', '.join('`'+t+'`' for t in tfs(k))} | {cell(summ, 160)} |")
            w(me, "\n".join(L))

    # Providers.
    me = "providers/index.md"
    L = ["# Providers", "", "| Provider | Cloud | Tier | Version | Kinds |", "| --- | --- | --- | --- | --- |"]
    for p in cat["providers"]:
        L.append(f"| {link(me, f'providers/{p['id']}.md', p['title'])} | {p['surface']} | {p['tier']}{' (extends ' + p['extends'] + ')' if p.get('extends') else ''} | {p['version']} | {p['nkinds']} |")
    w(me, "\n".join(L))
    for p in cat["providers"]:
        me = f"providers/{p['id']}.md"
        mine = [k for k in full_kinds if k["provider"] == p["id"]]
        L = [f"# {p['title']}", "", f"Publisher {p['publisher']} · {p['tier']}{' · extends ' + link(me, f'providers/{p['extends']}.md', p['extends']) if p.get('extends') else ''}"
             f" · version {p['version']}{' (' + p['commit'][:12] + ')' if p.get('commit') else ''} · source {p['repo']} · {len(mine)} kinds", ""]
        by_ = [x for x in cat["providers"] if x.get("installs") == p["id"]]
        if p.get("installs"):
            L += [f"Installs {link(me, f'providers/{p['installs']}.md', p['installs'])}.", ""]
        if by_:
            L += ["Installed by " + ", ".join(link(me, f"providers/{x['id']}.md", x["title"]) for x in by_) + ".", ""]
        if p.get("install"):
            L += [f"To install with an agent: the MCP server https://registry.highfivelabs.dev/mcp, tool `get_install` with "
                  f"`{{\"provider\": \"{p['id']}\"}}` (add `choice` for a variant), returns the steps below for one variant, "
                  "the placeholders to ask the user for, how to verify, and how to run them with the user.", ""]
        for v in p.get("credentials") or []:
            L += [f"## Credentials: {v['name']}", "", "```sh", v["steps"], "```", ""] + ([v["note"], ""] if v.get("note") else [])
        if p.get("pending") and not p.get("install"):
            L += ["## Install", "", "Not published yet; once released it installs from "
                  + ", ".join(f"oci://cr.highfivelabs.dev/{o}" for o in p["pending"]) + ".", ""]
        for v in p.get("install") or []:
            L += [f"## Install: {v['name']}", ""] + ([f"For {v['when']}."] if v.get("when") else []) + \
                 ["", "```sh", v["steps"], "```", ""] + ([v["note"], ""] if v.get("note") else []) + \
                 (["Check it worked:", "", "```sh", v["verify"], "```", ""] if v.get("verify") else []) + \
                 ([f"Steps from the provider's [krm-registry.yaml]({p['repo']}/blob/main/krm-registry.yaml)."
                   + (f" Tested: {v['tested']}." if v.get("tested") else ""), ""] if v.get("source") == "repo" else
                  [((f"Steps written by High Five Labs from {p['publisher'].removesuffix(' (community)')}'s documentation."
                     if p.get("publisher") != "High Five Labs" and not v.get("ours") else "")
                    + (f" Tested: {v['tested']}." if v.get("tested") else "")).strip(), ""]
                  if v.get("tested") or (p.get("publisher") != "High Five Labs" and not v.get("ours")) else [])
        bysvc = defaultdict(list)
        for k in mine:
            bysvc[k["service"]].append(k)
        for svc in sorted(bysvc):
            L.append(f"- **{svc}**: " + ", ".join(link(me, kind_path(k), k["kind"]) for k in sorted(bysvc[svc], key=lambda k: k["kind"])))
        w(me, "\n".join(L))

    # Packages.
    me = "packages/index.md"
    L = ["# Packages", "", "Helm charts of provider objects, each a port of a Terraform module that keeps its variables, names and outputs.", "",
         "| Package | Version | Ported from | Upstream status | Kinds |", "| --- | --- | --- | --- | --- |"]
    for c in charts_full:
        m = c["module"]
        L.append(f"| {link(me, f'packages/{c['id']}.md', c['id'])} | {c['version']} | {m.get('family')}: `{m.get('repoName')}`"
                 f"{' `' + m['path'] + '`' if m.get('path') not in (None, '.', '') else ''} @ {m.get('version')} | {status(c)} | {len(c['uses'])} |")
    w(me, "\n".join(L))
    for c in charts_full:
        me = f"packages/{c['id']}.md"
        m = c["module"]
        fm = {"package": c["id"], "version": c["version"], "ported_from": {k: m.get(k) for k in ("family", "repo", "path", "version", "commit", "registry_url")},
              "upstream_latest": (c.get("upstream") or {}).get("latest"), "kinds": c["uses"]}
        L = ["---", yaml.safe_dump(fm, sort_keys=False, width=200).rstrip(), "---", "", f"# {c['id']}", "", c["desc"], "",
             f"**Ported from** the Terraform module {m.get('family')} `{m.get('repoName')}`"
             f"{', path `' + m['path'] + '`' if m.get('path') not in (None, '.', '') else ''}, release `{m.get('version')}` ({m.get('module_url')}).", "", f"**Upstream:** {status(c)}.", "",
             "## Kinds it renders", ""]
        kmap = {k["id"]: k for k in full_kinds}
        L += [f"- {link(me, kind_path(kmap[u]), kmap[u]['kind'])} `{kmap[u]['group']}/{kmap[u]['version']}` ({kmap[u]['provider']})" for u in c["uses"] if u in kmap]
        if c["refused"]:
            L += ["", "## Module variables not carried yet", "", "| Variable | Why | Reason |", "| --- | --- | --- |"]
            L += [f"| `{r.get('variable')}`{' (partial)' if r.get('partial') else ''} | {r.get('kind')} | {cell(r.get('reason'))} |" for r in c["refused"]]
        if c["values"]:
            L += ["", "## Values", "", "| Value | Type | Default | Description |", "| --- | --- | --- | --- |"]
            L += [f"| `{v['p']}` | {cell(v['t'])} | {cell(repr(v['df']) if 'df' in v else '')} | {cell(v.get('d'), 400)} |" for v in c["values"]]
        if c["outputExprs"]:
            L += ["", "## Outputs", ""] + [f"- `{n}`: `{cell(e, 200)}`" for n, e in c["outputExprs"].items()]
        w(me, "\n".join(L))

    # Terraform lookups.
    for t, ks in tf_index.items():
        me = f"terraform/{t}.md"
        L = [f"# Terraform `{t}`", "", TFNOTE, "", "Kinds that stand in for it:", ""]
        L += [f"- {link(me, kind_path(k), k['kind'])} `{k['group']}/{k['version']}`, {k['provider']} ({k['tier']}, {k['stability']})" for k in ks]
        w(me, "\n".join(L))

    # Terraform coverage.
    me = "terraform/index.md"
    L = ["# Terraform, by the numbers", "", TFNOTE, "",
         "| Surface | Terraform provider | Rank by downloads | Resources | Covered | Official | Community | Extension |",
         "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for sid, t in cat["terraform"]["surfaces"].items():
        b = t["byTier"]
        L.append(f"| {link(me, f'{sid}/index.md', SURF[sid])} | `{t['name']}` {t['version']} | #{t['rank']} ({t['downloads']:,}) | {t['total']} | "
                 f"{t['covered']} ({100 * t['covered'] // max(t['total'], 1)}%) | {b.get('official', 0)} | {b.get('community', 0)} | {b.get('extension', 0)} |")
    w(me, "\n".join(L))

    # Entry point.
    nk, nc = len(full_kinds), len(charts_full)
    L = ["# KRM Registry", "",
         f"> Documentation for {nk} Kubernetes Resource Model kinds from {len(cat['providers'])} providers (Config Connector, AWS Controllers for Kubernetes, "
         f"Azure Service Operator, and High Five Labs' extensions that fill their gaps), and {nc} Helm chart packages built from them.", "",
         "Every page below is Markdown. A kind page has front matter, an example manifest, and tables of every spec and status field.", "",
         "## MCP server", "",
         f"If you can connect MCP servers, connect {MCP_URL} (streamable HTTP, no sign-in, read only) and use its tools instead of walking these pages: "
         "`list_clouds`, `search_kinds`, `get_kind`, `kinds_for_terraform`, `list_providers`, `get_package`. "
         f"In Claude Code: `claude mcp add --transport http krm-registry {MCP_URL}`.", "",
         "## Start here", "",
         *[f"- [{SURF[s]}]({s}/index.md): services, then the kinds in each" for s in cat["surfaces"] if any(k["surface"] == s for k in full_kinds) or cat["surfaces"][s].get("notes")],
         "- [Terraform, by the numbers](terraform/index.md): how much of each Terraform provider the kinds cover",
         "- [Providers](providers/index.md): each provider's kinds by service",
         "- [Packages](packages/index.md): charts, the Terraform module each ports, the kinds each renders", "",
         "## Look up directly", "",
         "- A kind: `kinds/<provider>/<api group>/<Kind>.md`, e.g. [kinds/config-connector/storage.cnrm.cloud.google.com/StorageBucket.md](kinds/config-connector/storage.cnrm.cloud.google.com/StorageBucket.md)",
         "- From Terraform: `terraform/<type>.md` lists the kinds that stand in for a Terraform resource type, e.g. [terraform/aws_ec2_tag.md](terraform/aws_ec2_tag.md)",
         "- A package: `packages/<cloud>/<chart>.md`", "",
         "## Providers", ""]
    L += [f"- `{p['id']}`: {p['title']}, {p['surface']}, {p['tier']}{', extends ' + p['extends'] if p.get('extends') else ''}, {p['version']}, {p['nkinds']} kinds" for p in cat["providers"]]
    L += ["", "## Machine-readable", "", "- `data/catalog.json`: every kind and chart without fields (large)",
          "- `data/k/<provider>/<api group>.json`: one API group with every field", "- `data/charts/<cloud>-<chart>.json`: one chart"]
    w("llms.txt", "\n".join(L))
    return w.n


def status(c):
    u = c.get("upstream") or {}
    if not u.get("latest"):
        return "upstream release unknown"
    return (f"{u['latest']} is out (found via {u.get('via')}); this chart is ported from an older release" if u.get("behind")
            else f"at the latest release ({u['latest']}, via {u.get('via')})")
