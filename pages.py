# SPDX-License-Identifier: Apache-2.0
"""The site's pages: index.html split into a small shell and its assets, and a
copy of the shell at every page's path (paths.py) with that page's title,
description and canonical URL, so each path answers 200 with its own title.
The shell's script then draws the page, as it does at /.

  index.html, 404.html               the shell (the bucket serves 404.html, with
                                     status 404, for a path with no object)
  assets/app.<hash>.css, .js         index.html's style and script
  <path>/index.html                  one per page (the bucket's main page suffix)
  sitemap.xml                        every page
"""
import hashlib, html, os, re

import paths

SITE = "https://registry.highfivelabs.dev"


def _asset(out, name, ext, text):
    h = hashlib.sha256(text.encode()).hexdigest()[:12]
    rel = f"assets/{name}.{h}.{ext}"
    os.makedirs(os.path.join(out, "assets"), exist_ok=True)
    with open(os.path.join(out, rel), "w") as f:
        f.write(text)
    return "/" + rel


def shell(src, out):
    """index.html with its main style and script moved to assets."""
    s = open(src).read()
    style = re.search(r"<style>\n(.*?)</style>\n", s, re.S)
    script = re.search(r"</footer>\n<script>\n(.*?)</script>\n</body>", s, re.S)
    assert style and script, "index.html: no main <style> or closing <script> to move to assets"
    s = s.replace(style.group(0), f'<link rel="stylesheet" href="{_asset(out, "app", "css", style.group(1))}">\n')
    return s.replace(script.group(0), f'</footer>\n<script src="{_asset(out, "app", "js", script.group(1))}"></script>\n</body>')


def head(title, desc, path, md=None):
    e = lambda x: html.escape(x, quote=True)
    h = [f"<title>{e(title)}</title>", f'<link rel="canonical" href="{SITE}{path}">']
    if desc:
        h.append(f'<meta name="description" content="{e(desc)}">')
    if md:
        h.append(f'<link rel="alternate" type="text/markdown" href="{md}">')
    return "\n".join(h)


def first_sentence(s, n=200):
    s = re.sub(r"\s+", " ", s or "").strip()
    m = re.match(r"(.+?[.!?])(\s|$)", s)
    s = m.group(1) if m and len(m.group(1)) >= 20 else s
    return s if len(s) <= n else s[:n - 1].rsplit(" ", 1)[0] + "…"


def pages(cat):
    """(path, title, description, agent page) for every page."""
    T = lambda *a: " · ".join([x for x in a if x] + ["KRM Registry"])
    prov = {s: v["title"] for s, v in cat["providers"].items()}
    ops = {op["id"]: op for op in cat["operators"]}
    yield "/", "KRM Registry", "Every cloud and vendor resource as a Kubernetes kind: the kinds, fields and tested examples of each operator, and which Terraform resources they stand in for.", "/llms.txt"
    yield "/providers", T("Providers"), "Clouds and vendors, and how much of each API the operators' kinds cover.", None
    yield "/coverage", T("Coverage"), "How much of each provider's API and Terraform provider the kinds cover.", "/terraform/index.md"
    yield "/operators", T("Operators"), "Every operator the registry indexes: its provider, publisher, version and kinds.", "/operators/index.md"
    yield "/packages", T("Packages"), "Helm charts that port Terraform modules to kinds.", "/packages/index.md"
    yield "/guide", T("Guide"), "How the registry is built and how to use it, for people and agents.", "/llms.txt"
    yield "/search", T("Search"), None, None
    by = {}
    for k in cat["kinds"]:
        by.setdefault(k["provider"], {}).setdefault(k["service"], []).append(k)
    for s, svcs in by.items():
        p = paths.provider(s)
        yield p, T(prov[s]), f"{prov[s]} as Kubernetes kinds: {sum(map(len, svcs.values())):,} kinds in {len(svcs)} services.", p + ".md"
        for svc, ks in svcs.items():
            p = paths.service(s, svc)
            yield p, T(svc, prov[s]), f"The {len(ks):,} kinds for {prov[s]} {svc}, from every operator that has one.", p + ".md"
            for k in ks:
                op = ops[k["operator"]]
                p = paths.kind(k, paths.namespace(op))
                yield p, T(k["kind"], f"{prov[s]} {svc}", op["title"]), first_sentence(k.get("desc")) or f"{k['kind']}, a {prov[s]} kind from {op['title']}.", p + ".md"
    for s, v in cat["providers"].items():
        if s not in by and v.get("notes"):
            yield paths.provider(s), T(v["title"]), first_sentence(v["notes"]), paths.provider(s) + ".md"
    for op in cat["operators"]:
        p = paths.operator(op)
        yield p, T(op["title"]), f"{op['title']}, by {op['publisher']}: {op['nkinds']:,} kinds for {prov.get(op['provider'], op['provider'])}.", p + ".md"
    for c in cat["charts"]:
        p = paths.chart(c)
        yield p, T(c["name"], "Packages"), first_sentence(c.get("desc") or c.get("description")) or None, f"/packages/{c['id']}.md"


def write(src, out, cat):
    """Write the shell, its assets, every page and the sitemap. Returns the page count."""
    sh = shell(src, out)
    assert "<title>KRM Registry</title>" in sh
    n, urls = 0, []
    for path, title, desc, md in pages(cat):
        d = os.path.join(out, path.lstrip("/"))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "index.html"), "w") as f:
            f.write(sh.replace("<title>KRM Registry</title>", head(title, desc, path, md), 1))
        urls.append(SITE + path)
        n += 1
    with open(os.path.join(out, "404.html"), "w") as f:
        f.write(sh.replace("<title>KRM Registry</title>", '<title>Not found · KRM Registry</title>\n<meta name="robots" content="noindex">', 1))
    with open(os.path.join(out, "sitemap.xml"), "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        f.writelines(f"<url><loc>{html.escape(u)}</loc></url>\n" for u in urls)
        f.write("</urlset>\n")
    with open(os.path.join(out, "robots.txt"), "w") as f:
        f.write(f"User-agent: *\nAllow: /\nSitemap: {SITE}/sitemap.xml\n")
    return n
