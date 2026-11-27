#!/usr/bin/env python3
"""Build the KRM registry's index from registry.yaml.

Reads every operator's CRDs, our operator repos' kind docs and coverage
tables, and the catalog's charts, and writes:

  out/data/catalog.json            providers, operators, services, kind and chart summaries (search index)
  out/data/k/<operator>/<group>.json   every kind of one API group, with its fields
  out/data/charts/<cloud>-<chart>.json one chart: metadata, values, README, kinds it renders
  out/llms.txt, out/<provider>/... the same pages as Markdown, for agents (agent.py)

Usage: gen.py <root> <out>   (root holds upstream/ and src/ as registry.yaml names them)
"""
import csv, glob, gzip, json, os, re, sys
from collections import defaultdict

import yaml

import config
import examples
import samples

L = yaml.CSafeLoader
ROOT, OUT, LOCKF = sys.argv[1], sys.argv[2], sys.argv[3]
LOCK = json.load(open(LOCKF))
HERE = os.path.dirname(os.path.abspath(__file__))
CFG = config.load(os.path.join(ROOT, "src"), loader=L)  # registry.yaml + our repos' krm-registry.yaml
for _p in CFG["operators"]:  # operators document the release the lock resolved
    _l = LOCK["operators"].get(_p["id"])
    if _l and _p["id"] != "ack":
        _p["version"], _p["commit"] = _l["tag"], _l.get("commit")
TFMAP = yaml.load(open(os.path.join(HERE, CFG.get("terraform_map", "terraform-map.yaml"))), Loader=L) or {}
TFLOCK = (LOCK.get("terraform") or {}).get("providers") or {}
STARS = LOCK.get("stars") or {}


def tf_prefix(provider):
    t = CFG["providers"][provider].get("terraform") or ""
    return t.split("/")[-1].lower().replace("azurerm", "azurerm")


def tf_area(provider, t):
    rest = t[len(tf_prefix(provider)) + 1:]
    w = rest.split("_")
    return "zero_trust" if w[:2] == ["zero", "trust"] else w[0]
FAMILIES = [(r"github\.com/GoogleCloudPlatform/cloud-foundation-fabric", "Cloud Foundation Fabric"),
            (r"github\.com/terraform-google-modules/", "Cloud Foundation Toolkit"),
            (r"github\.com/terraform-aws-modules/", "terraform-aws-modules"),
            (r"github\.com/Azure/terraform-azure(rm)?-avm-", "Azure Verified Modules")]
P = lambda p: os.path.join(ROOT, p)


# ---------------------------------------------------------------- CRDs

def load_crds(paths):
    for pat in paths:
        for f in sorted(glob.glob(P(pat))):
            with open(f) as fh:
                text = fh.read()
                if "{{" in text:  # a Helm template: drop the directive lines
                    text = "\n".join(l for l in text.splitlines() if not l.strip().startswith("{{"))
                for d in yaml.load_all(text, Loader=L):
                    if isinstance(d, dict) and d.get("kind") == "CustomResourceDefinition":
                        yield f, d


ASO_VER = re.compile(r"^v1api(\d{8})(preview)?$")


def pick_version(prov, versions):
    """The version a page shows: the newest served, stable before preview."""
    served = [v for v in versions if v.get("served", True)]
    names = [v["name"] for v in served]
    if prov["provider"] == "azure" and prov["tier"] == "official" or any(ASO_VER.match(n) for n in names):
        def key(n):
            m = ASO_VER.match(n)
            if m:
                return (1, m.group(2) is None, m.group(1))
            if n.startswith("v") and "storage" not in n:
                m2 = re.match(r"^v(\d{8})(preview)?$", n)
                if m2:
                    return (1, m2.group(2) is None, m2.group(1))
                return (0, True, n)
            return (-1, False, n)
        cands = [n for n in names if "storage" not in n] or names
        return max(cands, key=key)

    def k8s_key(n):
        m = re.match(r"^v(\d+)(alpha|beta)?(\d*)$", n)
        if not m:
            return (0, 0, 0)
        rank = {"alpha": 1, "beta": 2, None: 3}[m.group(2)]
        return (rank, int(m.group(1)), int(m.group(3) or 0))
    return max(names, key=k8s_key)


def stability(prov, crd, version, controller_release=None):
    labels = crd["metadata"].get("labels") or {}
    if prov["id"] == "config-connector":
        s = labels.get("cnrm.cloud.google.com/stability-level")
        if s:
            return "ga" if s == "stable" else s
    if prov["id"] == "ack" and controller_release:
        return "ga" if not controller_release.startswith("v0.") else "preview"
    if "preview" in version or "alpha" in version:
        return "alpha" if "alpha" in version else "preview"
    if "beta" in version:
        return "beta"
    return "ga"


IMMUTABLE = re.compile(r"immutable|cannot be changed|can't be changed|is read-only once", re.I)


def flatten(schema, prefix="", out=None, required=(), depth=0):
    """Schema → [{p,t,r,d,...}] rows, depth first."""
    if out is None:
        out = []
    props = schema.get("properties") or {}
    req = set(schema.get("required") or ())
    for name in props:
        s = props[name] or {}
        path = f"{prefix}.{name}" if prefix else name
        t = s.get("type") or ("object" if "properties" in s else "")
        if s.get("x-kubernetes-int-or-string"):
            t = "int|string"
        item = s.get("items") or {}
        if t == "array":
            it = item.get("type") or ("object" if "properties" in item else "")
            t = f"[]{it}" if it else "array"
        if t == "object" and not s.get("properties") and isinstance(s.get("additionalProperties"), dict):
            at = s["additionalProperties"].get("type", "")
            t = f"map[string]{at}" if at else "map"
        row = {"p": path, "t": t}
        if name in req:
            row["r"] = 1
        d = (s.get("description") or "").strip()
        if d:
            row["d"] = d
        for k, short in (("enum", "e"), ("default", "df"), ("format", "f"), ("pattern", "pt"),
                         ("maxLength", "mx"), ("minimum", "mn"), ("maximum", "mxv")):
            if k in s:
                row[short] = s[k]
        rules = s.get("x-kubernetes-validations") or []
        if any(IMMUTABLE.search(r.get("message", "") + r.get("rule", "")) or "self == oldSelf" in r.get("rule", "")
               for r in rules) or IMMUTABLE.search(d[:400] if d else ""):
            row["im"] = 1
        out.append(row)
        if depth < 12:
            if s.get("properties"):
                flatten(s, path, out, depth=depth + 1)
            elif item.get("properties"):
                flatten(item, path + "[]", out, depth=depth + 1)
    return out


REF_FIELD = re.compile(r"(Ref|Refs|Reference|References|owner)$")


def service_of(prov, group):
    suf = prov.get("group_suffix", "")
    g = group[: -len(suf)] if suf and group.endswith(suf) else group.split(".")[0]
    g = g.replace(".cloudcontrol", "")
    return g


def ack_controller_of(path):
    m = re.search(r"/ack/([^/]+)/helm/crds/", path)
    return m.group(1) if m else None


ACK_COMMON = {"adoptedresources", "fieldexports", "iamroleselectors"}


def docs_url(prov, k):
    """The operator's own reference page for the kind, where it publishes one."""
    short, v, low = k["group"].split(".")[0], k["version"], k["kind"].lower()
    if prov["id"] == "config-connector" and k["stability"] != "alpha":
        return f"https://docs.cloud.google.com/config-connector/docs/reference/resource-docs/{short}/{low}"
    if prov["id"] == "ack" and k["group"] != "services.k8s.aws":
        return f"https://aws-controllers-k8s.github.io/community/reference/{short}/{v}/{low}/"
    if prov["id"] == "aso":
        return f"https://azure.github.io/azure-service-operator/reference/{short}/{v}/#{k['kind']}"
    return None


def install_of(prov, key="install"):
    """Install variants (key "install") or credential steps (key "credentials"), with
    {tag} (the release tag) and {version} (its semver, as a chart carries it) filled in:
    [{name, pick, when, note, tested, steps, source, ours}]. A variant's pick ({axis:
    value}, e.g. {Method: High Five Labs chart, Cluster: GKE Autopilot}) places it on the
    page's radio buttons; credentials are matched to the install variant on the axes they
    share. In an ACK-style operator (one controller per service), {controllers} becomes
    one "<service> <version>" line per controller the lock resolved, {ncontrollers} their
    count. A variant naming one of our OCI charts (oci:) is left out unless resolve.py
    found it published, and takes the version it found there, so the site never points
    at a chart that is not there."""
    out = []
    published = (LOCK.get("oci") or {}).get(prov["id"]) or {}
    tag = prov.get("version") if prov.get("version") not in (None, "main") else None
    for v in prov.get(key) or []:
        t, ver = tag or "latest", re.search(r"[0-9]+[.][0-9]+[.][0-9]+[-+.A-Za-z0-9]*$", tag or "")
        ver = ver.group(0) if ver else t
        if v.get("oci"):
            pub = published.get(v["oci"])
            if not pub:
                continue
            ver = pub["version"]
            t = t if tag else ver
        ctrls = sorted((c["repo"].removesuffix("-controller"), c["tag"].lstrip("v"))
                       for c in ((LOCK.get("providers") or {}).get(prov["id"]) or {}).get("controllers") or [] if c.get("tag"))
        fill = lambda x: (x or "").replace("{tag}", t).replace("{version}", ver).replace(
            "{controllers}", "\n".join(f"{n} {cv}" for n, cv in ctrls)).replace("{ncontrollers}", str(len(ctrls))).strip()
        if "{controllers}" in v["steps"] and not ctrls:
            continue
        steps = fill(v["steps"])
        pick = v.get("pick") or None
        out.append({"name": v.get("name") or " · ".join(pick.values()), "pick": pick, "when": v.get("when"),
                    "note": fill(v.get("note")) or None, "verify": fill(v.get("verify")) or None, "tested": v.get("tested"), "steps": steps,
                    "package": package_of(v.get("package")),
                    "source": prov.get(key + "_source"), "ours": bool(v.get("oci")) or None})
    return out or None


PACKAGES = "https://github.com/highfivelabs/krm-packages"


def package_of(pkg):
    """A credentials variant's package: the same identity as KRM objects, a krm-packages
    chart that a Config Connector already running (a management cluster's) applies.
    {chart, release, note, steps}, the steps runnable as they stand. The charts are not on
    cr.highfivelabs.dev yet, so the steps install from a clone of krm-packages."""
    if not pkg:
        return None
    rel, values = pkg["release"], pkg["values"].rstrip("\n")
    steps = "\n".join([
        "# Against a cluster whose Config Connector already manages the project (a management",
        "# cluster), not the one you are installing on. NAMESPACE is a namespace it manages.",
        pkg["vars"],
        f"git clone --depth 1 {PACKAGES}",
        f"cat > {rel}.yaml <<EOF", values, "EOF",
        f"helm install {rel} krm-packages/charts/{pkg['chart']} \\",
        f"  --namespace $NAMESPACE -f {rel}.yaml",
        f"helm get manifest {rel} --namespace $NAMESPACE \\",
        "  | kubectl wait --for=condition=Ready --timeout 10m -f -",
    ])
    return {"chart": pkg["chart"], "release": rel, "url": f"{PACKAGES}/tree/main/charts/{pkg['chart']}",
            "note": (pkg.get("note") or "").strip() or None, "steps": steps}


def src_url(prov, rel, ctrl, release):
    if prov["id"] == "config-connector":
        return f"{prov['repo']}/blob/{prov['commit']}/" + rel.split("upstream/kcc/", 1)[1]
    if prov["id"] == "ack":
        return f"https://github.com/aws-controllers-k8s/{ctrl}/blob/{release}/" + rel.split(f"/{ctrl}/", 1)[1]
    if prov["id"] == "aso":
        return f"{prov['repo']}/releases/tag/{prov['version']}"
    src = prov.get("source") or {}
    if "asset" in src:
        return src["asset"].format(tag=prov["version"])
    if "gcs" in src:
        return "https://storage.googleapis.com/" + src["gcs"].format(tag=prov["version"])
    inner = rel.split("/", 2)[2]
    blob = "/-/blob/" if "gitlab.com" in prov["repo"] else "/blob/"
    return f"{prov['repo']}{blob}{prov['version']}/" + inner


# ---------------------------------------------------------------- docs

FRONT = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)


def kind_docs(prov):
    """docs/kinds/<Kind>.md with front matter {kinds, apiVersion}: the per-kind doc format."""
    docs = {}
    d = prov.get("kind_docs")
    if not d:
        return docs
    for f in sorted(glob.glob(P(d) + "/*.md")):
        txt = open(f).read()
        m = FRONT.match(txt)
        meta, body = (yaml.load(m.group(1), Loader=L) or {}, m.group(2)) if m else ({}, txt)
        group = (meta.get("apiVersion") or "").split("/")[0]
        for k in meta.get("kinds") or [os.path.basename(f)[:-3]]:
            docs[(group, k)] = {"md": body.strip(), "file": os.path.relpath(f, P("src"))}
    return docs


CODE = re.compile(r"`([a-z0-9.-]+\.[a-z]+)/(v[0-9a-z]+)`\s*((?:`[A-Za-z0-9]+`(?:,\s*|\s+and\s+|\s*;\s*|\s+)?)+)")


def readme_gaps(prov):
    """Rows of the README's kind table: | `group/version` `Kind`, ... | gap | upstream |."""
    gaps = {}
    f = prov.get("readme")
    if not f or not os.path.exists(P(f)):
        return gaps
    for line in open(P(f)):
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
        if len(cells) < 2:
            continue
        for m in re.finditer(r"`([a-z0-9.-]+\.[a-z]+)/(v[0-9a-z]+)`((?:\s*,?\s*(?:and\s+)?`[A-Z][A-Za-z0-9]*`)+)", cells[0]):
            group = m.group(1)
            for k in re.findall(r"`([A-Z][A-Za-z0-9]*)`", m.group(3)):
                gaps[(group, k)] = {"gap": cells[1], "upstream": cells[2] if len(cells) > 2 else ""}
    return gaps


def coverage_index(prov, tf):
    """kind → [{api resource, terraform types}] from our operator repo's coverage tables."""
    out = defaultdict(list)
    f = prov.get("coverage")
    if not f or not os.path.exists(P(f)):
        return out
    for row in csv.DictReader(open(P(f))):
        res = row["resource"]
        tft = tf.get((row["service"], res), [])
        for col, origin in (("official_kinds", "official"), ("ours_kinds", "hfl")):
            for k in filter(None, (row.get(col) or "").split(";")):
                k = k.strip()
                cc = "(Cloud Control)" in k
                k = k.replace("(Cloud Control)", "").strip()
                group = None
                if "/" in k:
                    group, k = k.split("/", 1)
                key = (origin, row["service"] if prov["provider"] == "aws" else None, group, k, cc)
                entry = {"api": res, "tf": tft}
                if entry not in out[key]:
                    out[key].append(entry)
    return out


def tf_index(prov):
    f = prov.get("terraform")
    idx = {}
    if f and os.path.exists(P(f)):
        for row in csv.DictReader(open(P(f))):
            idx[(row["service"], row["resource"])] = [t for t in row["terraform_resources"].split(";") if t]
    return idx


# ---------------------------------------------------------------- build

def build():
    providers = {p["id"]: p for p in CFG["operators"]}
    ack_rel = {}
    for c in LOCK["operators"]["ack"]["controllers"]:
        ack_rel[c["repo"]] = (c["tag"], c["commit"])

    kinds = []            # summaries
    shards = defaultdict(list)
    for prov in CFG["operators"]:
        n = 0
        for path, crd in load_crds(prov["crds"]):
            spec = crd["spec"]
            group, kind, plural = spec["group"], spec["names"]["kind"], spec["names"]["plural"]
            ctrl = ack_controller_of(path) if prov["id"] == "ack" else None
            if prov["id"] == "ack" and plural in ACK_COMMON and group == "services.k8s.aws":
                if any(k["group"] == group and k["kind"] == kind for k in kinds):
                    continue
            versions = spec.get("versions") or []
            ver = pick_version(prov, versions)
            vobj = next(v for v in versions if v["name"] == ver)
            schema = (vobj.get("schema") or {}).get("openAPIV3Schema") or {}
            props = schema.get("properties") or {}
            spec_fields = flatten(props.get("spec") or {}, "spec") if props.get("spec") else []
            status_fields = flatten(props.get("status") or {}, "status") if props.get("status") else []
            if not spec_fields and not status_fields:
                spec_fields = flatten({"properties": {k: v for k, v in props.items()
                                                      if k not in ("apiVersion", "kind", "metadata")}})
            desc = (schema.get("description") or "").strip()
            if not desc or desc.startswith("Generated from:") is False and len(desc) < 3:
                desc = (props.get("spec") or {}).get("description", "") or desc
            svc = service_of(prov, group)
            if prov["id"] == "ack" and ctrl:
                svc = ctrl.replace("-controller", "")
            rel = ack_rel.get(ctrl, (None, None)) if ctrl else (None, None)
            k = {
                "id": f"{prov['id']}/{group}/{kind}",
                "operator": prov["id"], "provider": prov["provider"], "tier": prov["tier"],
                "group": group, "kind": kind, "plural": plural, "service": svc,
                "scope": spec.get("scope", "Namespaced"),
                "version": ver,
                "versions": [v["name"] for v in versions if v.get("served", True) and "storage" not in v["name"]],
                "stability": stability(prov, crd, ver, rel[0]),
                "desc": desc,
                "nspec": len(spec_fields), "nstatus": len(status_fields),
                "short": spec["names"].get("shortNames") or [],
                "cloudcontrol": ".cloudcontrol." in group,
            }
            if ctrl:
                k["controller"] = {"repo": ctrl, "release": rel[0], "commit": rel[1]}
            k["source"] = os.path.relpath(path, ROOT)
            k["srcUrl"] = src_url(prov, k["source"], ctrl, rel[0])
            k["docsUrl"] = docs_url(prov, k)
            k["repoUrl"] = (f"https://github.com/aws-controllers-k8s/{ctrl}/tree/{rel[0]}" if ctrl
                            else f"{prov['repo']}/tree/{prov['version']}")
            k["docRepoBase"] = f"{prov['repo']}/blob/{prov['version']}/"
            ann = crd["metadata"].get("annotations") or {}
            tfs = [t.strip() for t in (ann.get("hfl.dev/terraform") or "").split(",") if t.strip()]
            pm = TFMAP.get(prov["id"]) or {}
            tfs += pm.get(f"{group}/{kind}") or pm.get(kind) or []  # group/Kind where kinds repeat across groups (OSOK)
            if tfs:
                k["api"] = [{"api": ann.get("hfl.dev/api-path", ""), "tf": sorted(set(tfs))}]
            if ann.get("hfl.dev/readiness"):
                k["readiness"] = ann["hfl.dev/readiness"]
            if prov.get("manages"):
                k["manages"] = prov["manages"]
            if CFG["providers"][prov["provider"]].get("category", "cloud") != "cloud":
                k["service"] = tf_area(prov["provider"], tfs[0]) if tfs else "other"
            kinds.append(k)
            shards[(prov["id"], group)].append(dict(k, spec=spec_fields, status=status_fields))
            n += 1
        print(f"{prov['id']}: {n} kinds", file=sys.stderr)

    by_name = defaultdict(list)
    for k in kinds:
        by_name[(k["provider"], k["kind"])].append(k)

    # References: *Ref / *Reference / owner fields → kinds named in the description.
    kind_names = defaultdict(set)
    for k in kinds:
        kind_names[k["provider"]].add(k["kind"])
    for (pid, group), ks in shards.items():
        for k in ks:
            refs = []
            for f in k["spec"]:
                leaf = f["p"].split(".")[-1].rstrip("[]")
                if not REF_FIELD.search(leaf) or f["p"].count(".") > 3:
                    continue
                d = f.get("d", "")
                targets = set(re.findall(r"`([A-Z][A-Za-z0-9]+)`", d)) | set(re.findall(r"\b([a-z0-9.]+/[A-Z][A-Za-z0-9]+)\b", d))
                targets |= set(re.findall(r"\b(?:a|an|the)\s+([A-Z][A-Za-z0-9]+)\s+resource", d))
                tg = []
                for t in targets:
                    name = t.split("/")[-1]
                    if name in kind_names[k["provider"]] and name != k["kind"]:
                        tg.append(name)
                if tg:
                    refs.append({"field": f["p"], "kinds": sorted(set(tg))})
            k["refs"] = refs

    # Examples from the official controllers' own tests.
    official = {}
    kcc = providers.get("config-connector")
    if kcc and os.path.isdir(P("upstream/kcc/config/samples")):
        official["config-connector"] = samples.kcc(P("upstream/kcc"), kcc["repo"], kcc["version"])
    aso = providers.get("aso")
    if aso and os.path.isdir(P("upstream/aso/v2/samples")):
        vers = {(g, k["kind"]): k["version"] for (pid, g), ks in shards.items() if pid == "aso" for k in ks}
        official["aso"] = samples.aso(P("upstream/aso"), aso["repo"], aso["version"], vers)
    ack_s = {}
    for d in sorted(glob.glob(P("upstream/ack/*"))):
        ctrl = os.path.basename(d)
        tag = (next((c for c in LOCK["operators"]["ack"]["controllers"] if c["repo"] == ctrl), None) or {}).get("tag")
        if tag:
            ack_s.update(samples.ack(d, f"https://github.com/aws-controllers-k8s/{ctrl}", tag))
    official["ack"] = ack_s
    n_samples = 0
    for (pid, group), ks in shards.items():
        for k in ks:
            got = (official.get(pid) or {}).get((group, k["kind"]))
            if got:
                k["samples"] = got
                n_samples += 1
    print(f"upstream test samples: {n_samples} kinds", file=sys.stderr)

    # Our operators' metadata, coverage and Terraform mapping.
    for prov in CFG["operators"]:
        if prov["tier"] != "hfl":
            continue
        docs, gaps = kind_docs(prov), readme_gaps(prov)
        repo_dir = P("src/" + prov["repo"].rstrip("/").rsplit("/", 1)[1]) if prov.get("repo") else None
        tested = examples.load(repo_dir) if repo_dir else {}
        with_related = examples.related(repo_dir, prov["repo"].rstrip("/")) if repo_dir else {}
        tf = tf_index(prov)
        cov = coverage_index(prov, tf)
        base = prov.get("extends")
        for (pid, group), ks in shards.items():
            for k in ks:
                if k["provider"] != prov["provider"]:
                    continue
                origin = "hfl" if pid == prov["id"] else ("official" if pid == base else None)
                if origin is None:
                    continue
                svc_key = k["service"] if prov["provider"] == "aws" else None
                hits = (cov.get((origin, svc_key, k["group"] if prov["provider"] == "azure" else None, k["kind"], k["cloudcontrol"]))
                        or cov.get((origin, svc_key, None, k["kind"], k["cloudcontrol"])) or [])
                if hits:
                    k["api"] = [dict(h) for h in hits]
                if pid != prov["id"]:
                    continue
                if (group, k["kind"]) in docs:
                    k["doc"] = docs[(group, k["kind"])]
                if (group, k["kind"]) in tested:
                    ex = dict(tested[(group, k["kind"])])
                    for path in examples.typed(ex["spec"], k["spec"]):
                        ex["notes"].append(f"`{path}` is a number the live test takes from a value it saves first; replace the 0.")
                    ex["yaml"] = examples.manifest(k, ex)
                    ex["line"] = examples.tested_line(ex)
                    k["example"] = ex
                if (group, k["kind"]) in with_related:
                    # Its own first, then those that need it.
                    rel = with_related[(group, k["kind"])]
                    k["related"] = [r for r in rel if r["main"] == k["kind"]] + [r for r in rel if r["main"] != k["kind"]]
                if (group, k["kind"]) in gaps:
                    k["gap"] = gaps[(group, k["kind"])]
                elif k.get("readiness") == "fake" and k.get("api"):
                    title = CFG["providers"][prov["provider"]]["title"]
                    tfl = ", ".join(f"`{t}`" for t in k["api"][0]["tf"])
                    k["gap"] = {"gap": f"Stands in for {tfl}, which no other operator for {title} covers. Generated from {title}'s OpenAPI spec "
                                       f"(`{k['api'][0]['api']}`); proven against a fake API only, not yet run against {title}'s.", "upstream": ""}
                # Overlay: the vendor's kind this one complements (same name, same provider).
                twins = [o for o in by_name[(k["provider"], k["kind"])] if o["operator"] == base
                         and (o["service"] == k["service"] or prov["provider"] != "aws")]
                twins = [o for o in twins if o["service"] == k["service"]] or twins
                if twins:
                    o = twins[0]
                    ospec = {f["p"] for f in next(x for x in shards[(o["operator"], o["group"])] if x["kind"] == o["kind"])["spec"]}
                    mine = {f["p"] for f in k["spec"]}
                    k["overlays"] = {"id": o["id"], "added": sorted(mine - ospec), "removed": sorted(ospec - mine)}

    ex_count = defaultdict(int)
    for (pid, group), ks in shards.items():
        if providers[pid]["tier"] == "hfl":
            for k in ks:
                ex_count[(k.get("example") or {}).get("tested", "none")] += 1
    print("examples: " + ", ".join(f"{n} {t}" for t, n in sorted(ex_count.items(), key=lambda x: -x[1])), file=sys.stderr)
    print(f"examples with related kinds: {sum(1 for ks in shards.values() for k in ks if any(r['main'] == k['kind'] for r in k.get('related') or []))} kinds", file=sys.stderr)

    # Terraform resource types the catalog maps to kinds by hand (modules it tracks).
    rt = P("src/krm-packages/docs/coverage/resource-types.csv")
    if os.path.exists(rt):
        byg = {(k["group"], k["kind"]): k for ks in shards.values() for k in ks}
        bys = defaultdict(list)
        for ks in shards.values():
            for k in ks:
                bys[(k["provider"], k["service"], k["kind"])].append(k)
                bys[(k["provider"], None, k["kind"])].append(k)
        for row in csv.DictReader(open(rt)):
            kd, tft = (row.get("kind") or "").strip(), row["resource_type"]
            if not kd or ":" in tft:
                continue
            m = re.match(r"^([A-Za-z0-9.-]+/)?([A-Z][A-Za-z0-9]*)(?:\s*\(([a-z0-9.-]+)/v[0-9a-z]+\))?", kd)
            if not m:
                continue
            pre, name, grp = (m.group(1) or "").rstrip("/"), m.group(2), m.group(3)
            surf = {"gcp": "gcp", "aws": "aws", "azure": "azure"}[row["cloud"]]
            if grp or "." in pre:
                tgt = [byg.get((grp or pre, name))]
            elif pre:
                tgt = bys.get((surf, pre, name), [])[:1]
            else:
                tgt = [k for k in bys.get((surf, None, name), []) if k["tier"] == "official"][:1] or bys.get((surf, None, name), [])[:1]
            for k in filter(None, tgt):
                api = k.setdefault("api", [])
                if not any(tft in a["tf"] for a in api):
                    note = kd[m.end():].strip()
                    api.append({"api": "", "tf": [tft], "via": "catalog" + (f": {note}" if note else "")})

    # Charts.
    charts, charts_full = [], []
    gk = {}
    for k in kinds:
        gk.setdefault((k["group"], k["kind"]), k["id"])
    for pk in CFG["packages"]:
        for d in sorted(glob.glob(P(pk["charts"]))):
            if not os.path.exists(f"{d}/Chart.yaml"):
                continue
            cloud, name = d.split("/")[-2:]
            ch = yaml.load(open(f"{d}/Chart.yaml"), Loader=L)
            up = yaml.load(open(f"{d}/upstream.yaml"), Loader=L) if os.path.exists(f"{d}/upstream.yaml") else {}
            pkg = yaml.load(open(f"{d}/package.yaml"), Loader=L) if os.path.exists(f"{d}/package.yaml") else {}
            vs = json.load(open(f"{d}/values.schema.json")) if os.path.exists(f"{d}/values.schema.json") else {}
            readme = open(f"{d}/README.md").read() if os.path.exists(f"{d}/README.md") else ""
            used = defaultdict(int)
            for g in glob.glob(f"{d}/tests/golden/*.yaml"):
                try:
                    for doc in yaml.load_all(open(g), Loader=L):
                        if isinstance(doc, dict) and doc.get("kind") and doc.get("apiVersion"):
                            used[(doc["apiVersion"].split("/")[0], doc["kind"])] += 1
                except yaml.YAMLError:
                    pass
            uses = sorted({gk[x] for x in used if x in gk})
            unknown = sorted(f"{g}/{k}" for g, k in used if (g, k) not in gk)
            refused = up.get("refused") or []
            partial = "partial" in readme[:4000].lower().split("## values")[0] or bool(refused)
            c = {
                "id": f"{cloud}/{name}", "cloud": cloud, "name": name, "package": (pkg.get("metadata") or {}).get("name"),
                "version": ch.get("version"), "desc": " ".join((ch.get("description") or "").split()),
                "module": {k2: up.get(k2) for k2 in ("module", "repo", "path", "version", "commit", "license", "module_url", "registry_url", "synced")}
                          | {"family": next((f for r, f in FAMILIES if re.search(r, up.get("repo") or "")), "Other") if up.get("repo") else "Original",
                             "repoName": re.sub(r"^https://github\.com/", "", up.get("repo") or "")},
                "upstream": LOCK["modules"].get(f"{cloud}/{name}"),
                "keywords": ch.get("keywords") or [], "uses": uses, "unknownKinds": unknown,
                "refused": refused, "nrefused": len(refused),
                "outputs": list(((pkg.get("spec") or {}).get("outputs") or {}).keys()),
                "providers": (pkg.get("spec") or {}).get("providers") or [],
            }
            charts.append(c)
            charts_full.append(None)
            c_full = dict(c, readme=readme, values=flatten(vs) if vs else [],
                          outputExprs=((pkg.get("spec") or {}).get("outputs") or {}))
            charts_full[-1] = c_full
            write_json(f"data/charts/{cloud}-{name}.json", c_full)
    used_by = defaultdict(list)
    for c in charts:
        for u in c["uses"]:
            used_by[u].append(c["id"])

    # Write shards and the catalog.
    summary_extra = {}
    for (pid, group), ks in shards.items():
        for k in ks:
            k["usedBy"] = used_by.get(k["id"], [])
            summary_extra[k["id"]] = {x: k[x] for x in ("api", "overlays", "gap", "usedBy") if k.get(x)}
            if "doc" in k:
                summary_extra[k["id"]]["hasDoc"] = 1
            if "overlays" in summary_extra[k["id"]]:
                o = summary_extra[k["id"]]["overlays"]
                summary_extra[k["id"]]["overlays"] = {"id": o["id"], "added": len(o["added"]), "removed": len(o["removed"])}
        write_json(f"data/k/{pid}/{group}.json", ks)
    for k in kinds:
        k.update(summary_extra.get(k["id"], {}))
        if k.get("gap"):
            k["gap"] = {"gap": k["gap"]["gap"][:300]}
        k["desc"] = k["desc"][:280]
        if k.get("api"):
            k["api"] = [{"api": a["api"], "tf": a["tf"]} for a in k["api"]]
    provs = []
    for p in CFG["operators"]:
        mine = [k for k in kinds if k["operator"] == p["id"]]
        provs.append({x: p.get(x) for x in ("id", "title", "provider", "publisher", "tier", "extends", "repo", "docs", "version", "commit", "manages", "installs")} |
                     {"install": install_of(p), "credentials": install_of(p, "credentials"),
                      "pending": [v["oci"] for v in p.get("install") or [] if v.get("oci")
                                  and not ((LOCK.get("oci") or {}).get(p["id"]) or {}).get(v["oci"])] or None} |
                     {"nkinds": len(mine), "ngroups": len({k["group"] for k in mine}), "nservices": len({k["service"] for k in mine}),
                      "controllers": len({k["controller"]["repo"] for k in mine if k.get("controller")}) or None,
                      "stars": STARS.get(p.get("repo"))})
    # Terraform, by the numbers: how much of each provider's Terraform provider the kinds cover.
    # downloads, rank and tier are the Terraform Registry's; stars are the source repo's.
    tfl = LOCK.get("terraform") or {}
    terraform = {"providers": {}, "read": tfl.get("read"),
                 "top": [t | {"stars": STARS.get(t.get("source"))} for t in tfl.get("top", [])]}
    for sid, tfp in TFLOCK.items():
        if not tfp:
            continue
        res = set(tfp["resources"])
        cov = {}
        for k in kinds:
            if k["provider"] != sid:
                continue
            for a in k.get("api") or []:
                for t in a["tf"]:
                    if t in res:
                        cov.setdefault(t, set()).add(k["tier"])
        tiers = {}
        for t, ts in cov.items():
            best = "official" if "official" in ts else ("community" if "community" in ts else "hfl")
            tiers[best] = tiers.get(best, 0) + 1
        terraform["providers"][sid] = {k: tfp.get(k) for k in ("name", "rank", "downloads", "version", "tier", "source")} | {
            "stars": STARS.get(tfp.get("source")), "total": len(res), "covered": len(cov), "byTier": tiers,
            "uncovered": sorted(res - set(cov))}
    cat = {"terraform": terraform, "generated": os.environ.get("GEN_DATE", ""), "lock": {"aso": LOCK["operators"]["aso"]["tag"], "config-connector": LOCK["operators"]["config-connector"]["tag"]}, "providers": CFG["providers"], "operators": provs,
           "kinds": kinds, "charts": charts}
    write_json("data/catalog.json", cat)
    for cf in charts_full:
        cf["upstream"] = next(c for c in charts if c["id"] == cf["id"])["upstream"]
    import agent
    n = agent.write_agent_tree(OUT, cat, [k for ks in shards.values() for k in ks], charts_full)
    print(f"{n} agent pages", file=sys.stderr)
    print(f"{len(kinds)} kinds, {len(shards)} shards, {len(charts)} charts", file=sys.stderr)


def write_json(rel, obj):
    p = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(obj, f, separators=(",", ":"), ensure_ascii=False, default=str)


if __name__ == "__main__":
    build()
