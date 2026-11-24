"""Examples from the official controllers' own test suites, at the release
the registry indexes. Each loader returns {(group, Kind): [sample]}, a
sample being

  {"title", "tested": "upstream", "line", "file", "url", "kinds", "yaml"}

with the kinds it needs first and the kind itself last.

  Config Connector  config/samples/resources/<kind>/[<variant>/]*.yaml: one
                    sample per directory, with the objects it needs; its
                    samples test creates and deletes each against Google Cloud
  ACK               test/e2e/resources/*.yaml in each controller: the
                    manifests its end-to-end tests apply against AWS, with
                    $VARIABLES the tests fill in
  ASO               v2/samples/<group>/<version>/*.yaml, with refs/ and the
                    owner chain (spec.owner.name) joined in; its samples test
                    creates and deletes each against recordings of Azure
"""
import glob
import os
import re

import yaml

MAX_PER_KIND = 16
MAX_BYTES = 24_000


def clean(text):
    """Drop a leading comment block (license headers) and blank lines."""
    lines = text.splitlines()
    i = 0
    while i < len(lines) and (lines[i].startswith("#") or not lines[i].strip()):
        i += 1
    head = lines[:i]
    # Keep a leading comment that isn't a license.
    if head and not any(re.search(r"copyright|license", l, re.I) for l in head):
        i = 0
    return "\n".join(lines[i:]).strip() + "\n"


def docs_of(text):
    try:
        return [d for d in yaml.safe_load_all(text) if isinstance(d, dict) and d.get("kind") and d.get("apiVersion")]
    except yaml.YAMLError:
        return []


def key(d):
    return (d["apiVersion"].split("/")[0], d["kind"])


def human(s):
    return s.replace("_", " ").replace("-", " ").strip()


def add(out, k, sample):
    if len(sample["yaml"]) <= MAX_BYTES and len(out.setdefault(k, [])) < MAX_PER_KIND:
        out[k].append(sample)


def kcc(repo_dir, repo_url, tag):
    base = os.path.join(repo_dir, "config", "samples", "resources")
    out = {}
    for kdir in sorted(glob.glob(os.path.join(base, "*"))):
        name = os.path.basename(kdir)
        sets = [kdir] if glob.glob(os.path.join(kdir, "*.yaml")) else sorted(d for d in glob.glob(os.path.join(kdir, "*")) if os.path.isdir(d))
        for sdir in sets:
            parts = []
            for f in sorted(glob.glob(os.path.join(sdir, "*.yaml"))):
                text = clean(open(f).read())
                parts.append((docs_of(text), text))
            main = [(ds, t) for ds, t in parts if any(d["kind"].lower() == name for d in ds)]
            if not main:
                continue
            deps = [(ds, t) for ds, t in parts if (ds, t) not in main]
            ordered = deps + main
            kinds = [d["kind"] for ds, _ in ordered for d in ds]
            rel = os.path.relpath(sdir, repo_dir)
            variant = human(os.path.basename(sdir)) if sdir != kdir else ""
            others = sorted({x for x in kinds if x.lower() != name})
            title = variant.capitalize() if variant else ("With " + ", ".join(others) if others else "Sample")
            k = key(next(d for d in main[0][0] if d["kind"].lower() == name))
            add(out, k, {"title": title, "tested": "upstream", "file": rel, "url": f"{repo_url}/tree/{tag}/{rel}", "kinds": kinds,
                         "line": "From Config Connector's samples, which its tests create and delete against Google Cloud. Replace ${PROJECT_ID?} and the like.",
                         "yaml": "---\n".join(t for _, t in ordered)})
    return out


SKIP_ACK = re.compile(r"adopt|invalid|error|fail|negative|immutable", re.I)


def ack(repo_dir, repo_url, tag):
    out = {}
    for f in sorted(glob.glob(os.path.join(repo_dir, "test", "e2e", "resources", "*.yaml"))):
        stem = os.path.splitext(os.path.basename(f))[0]
        if SKIP_ACK.search(stem):
            continue
        text = clean(open(f).read())
        ds = docs_of(text)
        if not ds:
            continue
        main = ds[-1]
        kl = re.sub(r"(?<!^)(?=[A-Z])", "_", main["kind"]).lower()
        rest = stem[len(kl):].strip("_") if stem.startswith(kl) else stem
        others = [d["kind"] for d in ds[:-1]]
        title = human(rest).capitalize() if rest else "Sample"
        if others:
            title += " with " + ", ".join(sorted(set(others)))
        rel = os.path.relpath(f, repo_dir)
        add(out, key(main), {"title": title, "tested": "upstream", "file": rel, "url": f"{repo_url}/blob/{tag}/{rel}",
                             "kinds": [d["kind"] for d in ds],
                             "line": f"From {os.path.basename(repo_dir)}'s end-to-end tests, which apply it against AWS. The tests fill in the $VARIABLES.",
                             "yaml": text})
    # The plain one first, then the rest by name.
    for v in out.values():
        v.sort(key=lambda s: (s["title"] != "Sample", s["title"]))
    return out


def aso(repo_dir, repo_url, tag, versions):
    """versions: {(group, Kind): version} of the kinds indexed, to prefer
    samples at that version."""
    base = os.path.join(repo_dir, "v2", "samples")
    rg = glob.glob(os.path.join(base, "resources", "*", "*resourcegroup*.yaml"))
    pool = {}  # metadata.name -> (doc, text), shared refs
    for f in rg:
        t = clean(open(f).read())
        for d in docs_of(t):
            pool.setdefault(d["metadata"]["name"], (d, t))
    out = {}
    for vdir in sorted(glob.glob(os.path.join(base, "*", "*"))):
        if not os.path.isdir(vdir) or os.path.basename(os.path.dirname(vdir)) == "resources":
            continue
        files = sorted(glob.glob(os.path.join(vdir, "*.yaml"))) + sorted(glob.glob(os.path.join(vdir, "refs", "*.yaml")))
        local = dict(pool)
        entries = []
        for f in files:
            t = clean(open(f).read())
            ds = docs_of(t)
            for d in ds:
                local.setdefault((d.get("metadata") or {}).get("name"), (d, t))
            if "/refs/" not in f:
                entries.append((f, ds, t))
        vname = os.path.basename(vdir)
        for f, ds, t in entries:
            if not ds:
                continue
            main = ds[-1]
            k = key(main)
            want = versions.get(k)
            if want and main["apiVersion"].split("/")[1] != want:
                continue
            chain, seen, cur = [], {id(main)}, main
            while True:  # owners, nearest first
                owner = ((cur.get("spec") or {}).get("owner") or {}).get("name")
                hit = local.get(owner)
                if not hit or id(hit[0]) in seen:
                    break
                seen.add(id(hit[0]))
                chain.append(hit)
                cur = hit[0]
            texts = [h[1] for h in reversed(chain)] + [t]
            kinds = [h[0]["kind"] for h in reversed(chain)] + [d["kind"] for d in ds]
            rel = os.path.relpath(f, repo_dir)
            add(out, k, {"title": ("With " + ", ".join(kinds[:-1])) if len(kinds) > 1 else "Sample", "tested": "upstream",
                         "file": rel, "url": f"{repo_url}/blob/{tag}/{rel}", "kinds": kinds,
                         "line": f"From ASO's samples ({vname}) with their owners, which its tests create and delete against recordings of Azure.",
                         "yaml": "---\n".join(dict.fromkeys(texts))})
    return out
