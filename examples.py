"""Tested examples for the REST extension repos' kinds.

Each vendor repository (krm-rest-runtime) has, per kind, a sample spec
that the every-kind test runs on the fake API (types.yaml), and for most
kinds a spec its live tests ran against the real API (hack/live/live.yaml),
with the last result (hack/live/records.json). A kind page shows the most
tested of these, and says how it was tested:

  live          passed create, update, drift repair and delete against the real API
  live-failing  run live, but its latest run failed
  fake          the sample, run on the fake API built from the descriptors
"""
import json
import os
import re
from datetime import date, timedelta

import yaml

TEMPLATE = re.compile(r"\{\{([a-zA-Z_]+)(?::([A-Za-z0-9_.-]+))?\}\}")


def load(repo_dir):
    """{(group, Kind): example} for a vendor repository, or {} when it is not one."""
    types_path = os.path.join(repo_dir, "types.yaml")
    if not os.path.exists(types_path):
        return {}
    types = yaml.safe_load(open(types_path)) or {}
    live, records = {}, {}
    lp = os.path.join(repo_dir, "hack", "live", "live.yaml")
    if os.path.exists(lp):
        live = yaml.safe_load(open(lp)) or {}
    rp = os.path.join(repo_dir, "hack", "live", "records.json")
    if os.path.exists(rp):
        records = json.load(open(rp))
    out = {}
    for k in types.get("kinds") or []:
        ex = one(k, live, records)
        if ex:
            out[(k["group"], k["kind"])] = ex
    return out


def one(k, live, records):
    name = "example-" + k["kind"].lower()
    lk = next((x for x in live.get("kinds") or []
               if x.get("kind") == k["kind"] and x.get("group", k["group"]) == k["group"]), None)
    ex = {"notes": []}
    if lk and not lk.get("skip"):
        spec = {} if lk.get("replaceSample") else json.loads(json.dumps(k.get("sample") or {}))
        for f, v in (lk.get("spec") or {}).items():
            if v is None:
                spec.pop(f, None)
            else:
                spec[f] = fill(v, name)
        r = records.get(f"{k['kind']}.{k['group']}")
        if r and r.get("result") == "pass":
            ex.update(tested="live", date=r.get("date", "")[:10], target=r.get("target") or live.get("target", ""))
        elif r:
            ex.update(tested="live-failing", date=(r.get("lastPass") or "")[:10], target=r.get("target") or live.get("target", ""))
            ex["notes"].append(f"The latest live run failed: {r.get('reason', '')}".strip())
        else:
            ex["tested"] = "fake"
            ex["notes"].append("Listed in the live tests, not yet run live.")
        # Only the calls that save a value this spec uses ({{var:...}}).
        used = set(re.findall(r"\{\{var:([A-Za-z0-9_.-]+)\}\}", json.dumps(lk.get("spec") or {})))
        calls = (live.get("before") or []) + (lk.get("before") or [])
        prereq = [f"{c['method']} {fill_str(c['path'], name)}" for c in calls if used & set((c.get("save") or {}).keys())]
        if prereq:
            ex["prereqs"] = prereq
    elif k.get("sample"):
        spec = json.loads(json.dumps(k["sample"]))
        ex["tested"] = "fake"
        if lk and lk.get("skip"):
            ex["notes"].append("Not run live: " + lk["skip"])
    else:
        return None
    ex["spec"] = spec
    return ex


def fill(v, name):
    if isinstance(v, str):
        return fill_str(v, name)
    if isinstance(v, dict):
        return {k: fill(e, name) for k, e in v.items()}
    if isinstance(v, list):
        return [fill(e, name) for e in v]
    return v


def fill_str(s, name):
    def sub(m):
        kind, arg = m.group(1), m.group(2)
        if kind in ("name", "run"):
            return name
        if kind in ("name_", "run_"):
            return name.replace("-", "_")
        if kind in ("NAME", "RUN"):
            return name.upper()
        if kind in ("NAME_", "RUN_"):
            return name.replace("-", "_").upper()
        if kind in ("cred", "env", "var"):
            return f"<{arg}>"
        if kind == "date" and arg and arg.lstrip("-").isdigit():
            return (date.today() + timedelta(days=int(arg))).isoformat()
        return m.group(0)
    return TEMPLATE.sub(sub, s)


def typed(spec, rows):
    """A "<placeholder>" in a field the CRD types as a number becomes 0
    (so the manifest stays valid); returns the paths, for a note."""
    types = {f["p"]: f["t"] for f in rows}
    out = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                p = f"{path}.{k}"
                if isinstance(v, str) and re.fullmatch(r"<[^<> ]+>", v) and types.get(p) in ("integer", "number"):
                    node[k] = 0
                    out.append(p)
                else:
                    walk(v, p)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                p = f"{path}[]"
                if isinstance(v, str) and re.fullmatch(r"<[^<> ]+>", v) and types.get(p) in ("[]integer", "[]number", "integer", "number"):
                    node[i] = 0
                    out.append(f"{path}[{i}]")
                else:
                    walk(v, p)
    walk(spec, "spec")
    return out


def manifest(k, ex):
    """The example as YAML: the object only (its Connection is the provider's)."""
    meta = {"name": "example-" + k["kind"].lower()}
    if k.get("scope", "Namespaced") == "Namespaced":
        meta["namespace"] = "default"
    doc = {"apiVersion": f"{k['group']}/{k['version']}", "kind": k["kind"], "metadata": meta, "spec": ex["spec"]}
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False, width=200)


def tested_line(ex):
    t = ex.get("tested")
    if t == "live":
        return f"Tested live: create, update, drift repair and delete passed against {ex.get('target') or 'the real API'} on {ex.get('date')}."
    if t == "live-failing":
        last = f" It last passed on {ex['date']}." if ex.get("date") else ""
        return f"Run live against {ex.get('target') or 'the real API'}, but its latest run failed.{last}"
    return "Tested on the fake API built from the descriptors only; not yet run live."
