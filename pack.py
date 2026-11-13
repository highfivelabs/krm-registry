#!/usr/bin/env python3
"""Pack per-group shards into ~2 MB files for hosts that cap file counts. pack.py <out> <site>"""
import glob, json, os, shutil, sys
OUT, SITE = sys.argv[1], sys.argv[2]
shutil.rmtree(SITE, ignore_errors=True); os.makedirs(f"{SITE}/data/p")
cat = json.load(open(f"{OUT}/data/catalog.json")); packs = {}
for prov in sorted(os.listdir(f"{OUT}/data/k")):
    buf, size, n = {}, 0, 0
    files = sorted(glob.glob(f"{OUT}/data/k/{prov}/*.json"))
    for i, f in enumerate(files):
        g = os.path.basename(f)[:-5]; buf[g] = json.load(open(f)); size += os.path.getsize(f)
        if size > 2_000_000 or i == len(files) - 1:
            name = f"data/p/{prov}-{n}.json"; json.dump(buf, open(f"{SITE}/{name}", "w"), separators=(",", ":"), ensure_ascii=False)
            for g2 in buf: packs[f"{prov}/{g2}"] = name
            buf, size, n = {}, 0, n + 1
charts = {os.path.basename(f)[:-5]: json.load(open(f)) for f in glob.glob(f"{OUT}/data/charts/*.json")}
json.dump(charts, open(f"{SITE}/data/charts.json", "w"), separators=(",", ":"), ensure_ascii=False)
cat["packs"] = packs
json.dump(cat, open(f"{SITE}/data/catalog.json", "w"), separators=(",", ":"), ensure_ascii=False)
for f in ("index.html", "llms.txt"): shutil.copy(f"{OUT}/{f}", f"{SITE}/{f}")
print(len(os.listdir(f"{SITE}/data/p")), "packs")
open(f"{SITE}/llms.txt", "w").write(
    "# KRM Registry (preview copy)\n\nThis copy carries the browsable site only. The agent layout (llms.txt, then one small Markdown page per "
    "cloud, service, kind, package and Terraform type) is published to GitHub Pages by the krm-registry repository's pages workflow.\n\n"
    "Here, data/catalog.json lists every kind and chart; its `packs` maps \"<provider>/<group>\" to the file holding that API group.\n")
