# SPDX-License-Identifier: Apache-2.0
"""The registry's URLs: one path per page, the same in the site (index.html's
path builders mirror these), the page stubs gen.py writes and the agent pages
(the path plus .md).

  /<provider>                                   a provider: gcp, aws, datadog...
  /<provider>/<service>                         a service: every operator's kinds
  /<provider>/<service>/<namespace>/<Kind>      a kind, under its operator's publisher
  /operators/<namespace>/<operator>             an operator
  /packages/<cloud>/<chart>                     a package
  /providers /coverage /operators /packages /guide /search   the sections

A namespace is the publisher, as in the Terraform Registry (hashicorp/google):
an operator's `namespace:` in registry.yaml, else its publisher's name in
lower case with spaces and "(community)" dropped. A provider needs none: it
names an API, and there is one of each. The publisher tells apart two kinds
of one name under one provider (ours overlaying the vendor's; two community
operators for the same vendor). Where one operator has a kind of one name in
two API groups of a service, the one with the longer group is <Kind>.<group>,
as a CRD is named, and the catalog carries that kind's `path` (qualify).
"""
import re

SECTIONS = {"providers", "coverage", "operators", "packages", "guide", "search",
            # paths the site serves that a provider id must not take
            "kinds", "terraform", "data", "assets", "mcp"}


def slug(s):
    return re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-") or "other"


def namespace(op):
    return op.get("namespace") or re.sub(r"[^a-z0-9-]", "", re.sub(r"\s*\(.*\)$", "", op["publisher"]).lower())


def provider(p):
    return f"/{p}"


def service(p, svc):
    return f"/{p}/{slug(svc)}"


def kind(k, ns):
    """ns: the kind's operator's namespace."""
    return k.get("path") or f"{service(k['provider'], k['service'])}/{ns}/{k['kind']}"


def qualify(kinds, nss):
    """Set `path` on each kind that would share its path with another kind of the
    same group-less name: all but the one with the shortest group."""
    by = {}
    for k in kinds:
        by.setdefault(kind(dict(k, path=None), nss[k["operator"]]), []).append(k)
    for p, ks in by.items():
        for k in sorted(ks, key=lambda k: (len(k["group"]), k["group"]))[1:]:
            k["path"] = f"{p}.{k['group']}"


def operator(op):
    return f"/operators/{namespace(op)}/{op['id']}"


def chart(c):
    return f"/packages/{c['cloud']}/{c['name']}"


def check(providers, operators, kinds, charts):
    """Every page has one path, and no provider takes a section's name. Returns problems."""
    bad = [f"provider {p}: takes a section's path" for p in providers if p in SECTIONS]
    nss = {op["id"]: namespace(op) for op in operators}
    bad += [f"operator {i}: namespace {n!r} is not a path segment" for i, n in nss.items() if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", n)]
    seen = {}
    for what, path in ([(f"kind {k['id']}", kind(k, nss[k["operator"]])) for k in kinds]
                       + [(f"operator {op['id']}", operator(op)) for op in operators]
                       + [(f"package {c['id']}", chart(c)) for c in charts]):
        if path in seen:
            bad.append(f"{what} and {seen[path]} share {path}")
        seen[path] = what
    svcs = {}
    for k in kinds:
        s = service(k["provider"], k["service"])
        if svcs.setdefault(s, k["service"]) != k["service"]:
            bad.append(f"services {svcs[s]!r} and {k['service']!r} share {s}")
    return sorted(set(bad))
