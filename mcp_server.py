#!/usr/bin/env python3
"""The registry over the Model Context Protocol (stdio), for agents.

  mcp_server.py [--site DIR | --url URL] [--http PORT]

Over stdio by default; --http serves MCP's streamable HTTP transport on
POST /mcp instead (stateless: no sessions, every response is JSON), which
is how registry.highfivelabs.dev/mcp runs on Cloud Run.

Answers from a built site's data/ (catalog.json, the per-group kind shards
and the chart files): a local build (--site, default ./site), or the
published registry (--url, read over HTTPS on demand), so nothing new is
hosted. Python's standard library only.

Tools:
  list_clouds          every cloud and vendor, with its services and kind counts
  search_kinds         kinds by text, cloud, service, provider or tier
  get_kind             one kind: its fields, Terraform equivalents, tested
                       example, and the examples with the kinds it needs
  kinds_for_terraform  Terraform resource types -> the kinds that stand in
  list_providers       every provider: its cloud, tier, version, kinds
  get_package          a catalog chart: its module, kinds, values, outputs
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]


class Source:
    """data/ files from a local site or the published one."""

    def __init__(self, site=None, url=None, ttl=None):
        self.site, self.url = site, (url.rstrip("/") + "/" if url else None)
        # A long-running server re-reads files after ttl seconds, so a publish
        # shows up without a restart; None keeps them for the process's life.
        self.ttl = ttl
        self.cache = {}

    def read(self, rel):
        hit = self.cache.get(rel)
        if hit and (self.ttl is None or time.monotonic() - hit[1] < self.ttl):
            return hit[0]
        if self.url:
            req = urllib.request.Request(self.url + rel, headers={"User-Agent": "krm-registry-mcp"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    v = json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    v = None
                else:
                    raise
        else:
            p = os.path.join(self.site, rel)
            v = json.load(open(p)) if os.path.exists(p) else None
        self.cache[rel] = (v, time.monotonic())
        return v


class Registry:
    def __init__(self, src):
        self.src = src
        self._cat = None

    @property
    def cat(self):
        cat = self.src.read("data/catalog.json")
        if cat is None:
            raise RuntimeError("no data/catalog.json: build the site first (build.sh), or pass --url")
        return cat

    def shard(self, provider, group):
        return self.src.read(f"data/k/{provider}/{group}.json") or []

    # ------------------------------------------------------------ tools

    def list_clouds(self):
        services = {}
        for k in self.cat["kinds"]:
            sv = services.setdefault(k["surface"], {})
            sv[k.get("service") or "other"] = sv.get(k.get("service") or "other", 0) + 1
        provs = {}
        for p in self.cat["providers"]:
            provs.setdefault(p.get("surface"), []).append(p["id"])
        return {"clouds": [{"id": sid, "title": s.get("title"), "category": s.get("category"), "providers": provs.get(sid, []),
                            "kinds": sum(services.get(sid, {}).values()), "services": dict(sorted(services.get(sid, {}).items()))}
                           for sid, s in self.cat["surfaces"].items()]}

    def search_kinds(self, query="", cloud="", service="", provider="", tier="", limit=25):
        words = [w for w in query.lower().split() if w]
        out = []
        for k in self.cat["kinds"]:
            if cloud and k.get("surface") != cloud:
                continue
            if service and k.get("service") != service:
                continue
            if provider and k.get("provider") != provider:
                continue
            if tier and k.get("tier") != tier:
                continue
            tf = sorted({t for a in k.get("api") or [] for t in a.get("tf") or []})
            hay = " ".join([k["kind"], k["group"], k.get("service", ""), k.get("desc", "")] + tf).lower()
            if words and not all(w in hay for w in words):
                continue
            kl, q = k["kind"].lower(), "".join(words)
            # Exact kind or Terraform type first, then a kind that ends in the
            # word (StorageBucket for "bucket"), then a kind that has it; official
            # and live-tested kinds before others, shorter names before longer.
            score = (20 if q and (kl == q or any(t == query.strip().lower() for t in tf)) else 0) \
                + sum(6 if kl.endswith(w) else 3 if w in kl else 1 for w in words) \
                + (2 if k.get("tier") == "official" else 0) + (1 if k.get("readiness") == "live" else 0)
            out.append((score, k, tf))
        out.sort(key=lambda x: (-x[0], len(x[1]["kind"]), x[1]["kind"]))
        return {"count": len(out), "kinds": [summary(k, tf) for _, k, tf in out[: max(1, min(int(limit), 200))]]}

    def find(self, kind):
        """A kind by id (provider/group/Kind), group/Kind or Kind."""
        kind = kind.strip()
        ks = self.cat["kinds"]
        hit = [k for k in ks if k["id"] == kind] or [k for k in ks if f"{k['group']}/{k['kind']}" == kind] \
            or [k for k in ks if k["kind"].lower() == kind.lower()]
        if not hit:
            raise ValueError(f"no kind {kind!r}; search_kinds finds them")
        if len(hit) > 1:
            raise ValueError(f"{kind!r} is in more than one provider or group; name one of: " + ", ".join(k["id"] for k in hit[:20]))
        return hit[0]

    def get_kind(self, kind, fields=None):
        k = self.find(kind)
        full = next((x for x in self.shard(k["provider"], k["group"]) if x["kind"] == k["kind"]), k)
        tf = sorted({t for a in full.get("api") or [] for t in a.get("tf") or []})
        out = summary(full, tf)
        out.update({"versions": full.get("versions"), "scope": full.get("scope"), "repo": full.get("repoUrl"),
                    "crd": full.get("srcUrl"), "docs": full.get("docsUrl"), "apiPaths": sorted({a["api"] for a in full.get("api") or [] if a.get("api")})})
        for key, name in (("gap", "gapItFills"), ("overlays", "overlays"), ("refs", "references"), ("usedBy", "usedByPackages")):
            if full.get(key):
                out[name] = full[key]

        def rows(rs):
            sel = [r for r in rs or [] if not fields or any(r["p"].split(".", 1)[-1].startswith(f) for f in fields)]
            return [{"path": r["p"].split(".", 1)[-1], "type": r.get("t"), "required": bool(r.get("r")),
                     "description": r.get("d", ""), **({"enum": r["e"]} if r.get("e") else {}),
                     **({"default": r["df"]} if "df" in r else {}), **({"immutable": True} if r.get("im") else {})} for r in sel]
        out["spec"], out["status"] = rows(full.get("spec")), rows(full.get("status"))
        ex = full.get("example")
        if ex:
            out["example"] = {"tested": ex.get("tested"), "line": ex.get("line"), "manifest": ex.get("yaml"),
                              **({"liveDate": ex["date"]} if ex.get("date") else {}), **({"notes": ex["notes"]} if ex.get("notes") else {})}
        if full.get("samples"):
            out["upstreamSamples"] = [{"title": x["title"], "file": x["file"], "url": x["url"], "kinds": x["kinds"], "line": x["line"], "manifest": x["yaml"]}
                                      for x in full["samples"]]
        rel = full.get("related") or []
        if rel:
            out["withRelated"] = [{"file": r["file"], "url": r["url"], "kinds": r["kinds"], "manifest": r["yaml"]} for r in rel if r["main"] == full["kind"]]
            out["usedInExamplesOf"] = [r["main"] for r in rel if r["main"] != full["kind"]]
        return out

    def kinds_for_terraform(self, types):
        out = {t: [] for t in types}
        for k in self.cat["kinds"]:
            for a in k.get("api") or []:
                for t in a.get("tf") or []:
                    if t in out:
                        out[t].append({"id": k["id"], "kind": k["kind"], "apiVersion": f"{k['group']}/{k['version']}",
                                       "provider": k["provider"], "tier": k["tier"], "stability": k.get("stability"), "readiness": k.get("readiness")})
        return {"kinds": out}

    def list_providers(self):
        return {"providers": [{"id": p["id"], "title": p.get("title"), "cloud": p.get("surface"), "tier": p.get("tier"),
                               "publisher": p.get("publisher"), "version": p.get("version"), "kinds": p.get("nkinds"),
                               "repo": p.get("repo")} for p in self.cat["providers"]]}

    def get_package(self, package):
        c = next((c for c in self.cat["charts"] if package in (c["id"], c.get("package"), c.get("name"))), None)
        if c is None:
            raise ValueError(f"no package {package!r}; the catalog has {len(self.cat['charts'])}, e.g. " + ", ".join(x["id"] for x in self.cat["charts"][:8]))
        full = self.src.read(f"data/charts/{c['cloud']}-{c['name']}.json") or c
        keep = ("id", "package", "version", "desc", "module", "upstream", "uses", "values", "outputs", "providers", "refused")
        return {k: full[k] for k in keep if k in full}


def summary(k, tf):
    return {"id": k["id"], "kind": k["kind"], "apiVersion": f"{k['group']}/{k['version']}", "provider": k["provider"],
            "cloud": k.get("surface"), "service": k.get("service"), "tier": k.get("tier"), "stability": k.get("stability"),
            **({"readiness": k["readiness"]} if k.get("readiness") else {}), "terraform": tf,
            "description": " ".join((k.get("desc") or "").split())[:240]}


S = {"type": "string"}
TOOLS = [
    {"name": "list_clouds", "description": "Every cloud and vendor the registry covers (gcp, aws, azure, cloudflare, datadog, ...): its providers and, per service, how many kinds. Use it to learn the names search_kinds filters on.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "search_kinds", "description": "Find Kubernetes kinds across every provider in the registry (Config Connector, ACK, ASO, High Five Labs' extensions and REST vendor kinds) by text (kind, group, service, Terraform type, description; all words must match), and optionally cloud (gcp, aws, azure, cloudflare, ...), service, provider id or tier (official, extension).",
     "inputSchema": {"type": "object", "properties": {"query": S, "cloud": S, "service": S, "provider": S, "tier": S, "limit": {"type": "integer"}}}},
    {"name": "get_kind", "description": "One kind in full: description, every spec and status field (type, required, enum, default, immutable), its Terraform equivalents and API paths, the gap it fills, its tested example (live or fake), the samples from the official controller's own tests, and the examples that show it with the kinds it needs. Name it by id (provider/group/Kind), group/Kind or Kind.",
     "inputSchema": {"type": "object", "properties": {"kind": S, "fields": {"type": "array", "items": S, "description": "only fields whose path starts with one of these"}}, "required": ["kind"]}},
    {"name": "kinds_for_terraform", "description": "Terraform resource types (e.g. google_storage_bucket, cloudflare_access_rule) -> the kinds that stand in for each, with provider, tier and readiness.",
     "inputSchema": {"type": "object", "properties": {"types": {"type": "array", "items": S}}, "required": ["types"]}},
    {"name": "list_providers", "description": "Every provider the registry indexes: cloud, tier (official or extension), publisher, version and number of kinds.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_package", "description": "A catalog Helm chart (a Terraform module ported to KRM kinds): its upstream module, the kinds it uses, its values and outputs. Name it by id (aws/acm), package (aws-acm) or name.",
     "inputSchema": {"type": "object", "properties": {"package": S}, "required": ["package"]}},
]


for _t in TOOLS:  # every tool only reads
    _t["annotations"] = {"readOnlyHint": True, "openWorldHint": False}


def call(reg, name, args):
    if name == "list_clouds":
        return reg.list_clouds()
    if name == "search_kinds":
        return reg.search_kinds(**{k: v for k, v in args.items() if k in ("query", "cloud", "service", "provider", "tier", "limit")})
    if name == "get_kind":
        return reg.get_kind(args["kind"], args.get("fields"))
    if name == "kinds_for_terraform":
        return reg.kinds_for_terraform(args["types"])
    if name == "list_providers":
        return reg.list_providers()
    if name == "get_package":
        return reg.get_package(args["package"])
    raise ValueError(f"no tool {name!r}")


def handle(reg, msg):
    """One JSON-RPC message -> its response, or None for a notification."""
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        return None
    if method == "initialize":
        v = params.get("protocolVersion")
        result = {"protocolVersion": v if v in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "krm-registry", "version": "1"},
                  "instructions": "The KRM Registry: Kubernetes kinds for clouds and SaaS vendors, and the Terraform types they stand in for. "
                                  "list_clouds names the clouds, services and providers; search_kinds or kinds_for_terraform finds kinds; get_kind gives every field and the tested examples."}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        try:
            out = call(reg, params.get("name"), params.get("arguments") or {})
            result = {"content": [{"type": "text", "text": json.dumps(out, indent=1, default=str)}], "structuredContent": out, "isError": False}
        except (ValueError, KeyError, TypeError, RuntimeError) as e:
            result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


class HTTPHandler(BaseHTTPRequestHandler):
    """MCP's streamable HTTP transport, stateless: POST a JSON-RPC message to
    /mcp, get its response as application/json (202 for a notification).
    No server-sent events, so GET and DELETE are 405."""
    reg = None
    server_version = "krm-registry-mcp/1"
    MAX_BODY = 1 << 20

    def _send(self, code, body=None, ctype="application/json", extra=None):
        data = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        self.send_response(code)
        # The data is public; any origin may call it.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept, Mcp-Protocol-Version, Mcp-Session-Id")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        if body is not None:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _mcp_path(self):
        return self.path.split("?", 1)[0].rstrip("/") in ("/mcp", "")

    def do_OPTIONS(self):
        self._send(204)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            return self._send(200, b"ok\n", "text/plain")
        if self._mcp_path():
            return self._send(405, {"error": "this server does not stream; POST JSON-RPC messages to /mcp"}, extra={"Allow": "POST, OPTIONS"})
        self._send(404, {"error": "not found"})

    def do_DELETE(self):
        self._send(405, {"error": "no sessions to end"}, extra={"Allow": "POST, OPTIONS"})

    def do_POST(self):
        if not self._mcp_path():
            return self._send(404, {"error": "not found"})
        v = self.headers.get("Mcp-Protocol-Version")
        if v and v not in PROTOCOL_VERSIONS:
            return self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": f"unsupported MCP-Protocol-Version {v}"}})
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > self.MAX_BODY:
            return self._send(400 if n <= 0 else 413, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "a JSON-RPC message of at most 1 MiB"}})
        try:
            msg = json.loads(self.rfile.read(n))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
        if not isinstance(msg, dict):
            return self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "one JSON-RPC message per request; batches are not supported"}})
        try:
            resp = handle(self.reg, msg)
        except Exception as e:  # the data source failed: report it, keep serving
            resp = {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32603, "message": f"internal error: {e}"}}
        if resp is None:
            return self._send(202)
        self._send(200, resp)

    def log_message(self, fmt, *args):  # one line per request on stderr (Cloud Logging)
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--site", default="site", help="a built site directory (default ./site)")
    ap.add_argument("--url", help="the published registry's base URL, read instead of --site")
    ap.add_argument("--http", type=int, metavar="PORT", help="serve streamable HTTP on PORT (0: $PORT, else 8080) instead of stdio")
    ap.add_argument("--ttl", type=float, default=300, help="with --http, seconds before a data file is read again (default 300, the CDN's cache time)")
    a = ap.parse_args()
    ttl = a.ttl if a.http is not None else None
    reg = Registry(Source(url=a.url, ttl=ttl) if a.url else Source(site=a.site, ttl=ttl))
    if a.http is not None:
        port = a.http or int(os.environ.get("PORT", "8080"))
        HTTPHandler.reg = reg
        srv = ThreadingHTTPServer(("", port), HTTPHandler)
        srv.daemon_threads = True
        print(f"krm-registry MCP on :{port}/mcp, data from {a.url or a.site}", file=sys.stderr)
        srv.serve_forever()
        return
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            resp = handle(reg, msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
