#!/usr/bin/env python3
"""Fetch the CRDs of every provider with a `source` in registry.yaml, at the
release registry.lock.json names.   fetch_sources.py <lock.json> <root>

  source: {asset: URL with {tag}}   download one file
  source: {gcs: bucket/path with {tag}}
                                    download a tarball from a public Cloud Storage
                                    bucket and unpack it (Config Connector's operator)
  source: {path: dir}               sparse-checkout dir of the repository at the tag
  source: {path: a/{newest}/b}      ... where {newest} is the highest-versioned directory
                                    under a/ at that tag (MongoDB's operator keeps
                                    releases/<version>/deploy/crds); copied to <dest>/crds
"""
import io, json, os, re, shutil, subprocess, sys, tarfile, urllib.request

import yaml

LOCK, ROOT = sys.argv[1], sys.argv[2]
cfg = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "registry.yaml")))
lock = json.load(open(LOCK))


def run(*a):
    subprocess.run(a, check=True, capture_output=True)


for p in cfg["operators"]:
    src = p.get("source")
    rel = lock["operators"].get(p["id"])
    if not src or not rel:
        continue
    dest = os.path.join(ROOT, "upstream", p["id"])
    if os.path.exists(dest):
        continue
    if "gcs" in src:
        os.makedirs(dest)
        url = "https://storage.googleapis.com/" + src["gcs"].format(tag=rel["tag"])
        req = urllib.request.Request(url, headers={"User-Agent": "krm-registry"})
        with urllib.request.urlopen(req, timeout=120) as r:
            with tarfile.open(fileobj=io.BytesIO(r.read()), mode="r:*") as t:
                t.extractall(dest, filter="data")  # refuses absolute paths, links out of dest
    elif "asset" in src:
        os.makedirs(dest)
        url = src["asset"].format(tag=rel["tag"])
        req = urllib.request.Request(url, headers={"User-Agent": "krm-registry"})
        with urllib.request.urlopen(req, timeout=120) as r, open(os.path.join(dest, os.path.basename(url)), "wb") as f:
            f.write(r.read())
    else:
        path = src["path"]
        head, _, tail = path.partition("/{newest}/")
        run("git", "clone", "-q", "--filter=blob:none", "--no-checkout", "--depth", "1", "--branch", rel["tag"], p["repo"] + ".git", dest)
        run("git", "-C", dest, "sparse-checkout", "set", head if tail else path)
        run("git", "-C", dest, "checkout", "-q", rel["tag"])
        if tail:
            vkey = lambda d: tuple(int(x) for x in re.findall(r"\d+", d))
            newest = max(os.listdir(os.path.join(dest, head)), key=vkey)
            shutil.copytree(os.path.join(dest, head, newest, tail), os.path.join(dest, "crds"))
            print(f"  {p['id']}: CRDs from {head}/{newest}/{tail}", file=sys.stderr)
    print(f"fetched {p['id']} {rel['tag']}", file=sys.stderr)
