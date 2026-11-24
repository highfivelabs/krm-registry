"""mcp_server.py over stdio and streamable HTTP against tests/fixture-site (a few kinds and a chart
cut from a real build).   python3 -m unittest discover -s tests"""
import json
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "..", "mcp_server.py")
SITE = os.path.join(HERE, "fixture-site")


class Session:
    def __init__(self):
        self.p = subprocess.Popen([sys.executable, SERVER, "--site", SITE], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.n = 0

    def rpc(self, method, params=None):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def notify(self, method):
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.p.stdin.flush()

    def tool(self, name, args):
        r = self.rpc("tools/call", {"name": name, "arguments": args})["result"]
        return r

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=10)


class TestServer(unittest.TestCase):
    def setUp(self):
        self.s = Session()
        init = self.s.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
        self.assertEqual(init["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", init["result"]["capabilities"])
        self.s.notify("notifications/initialized")

    def tearDown(self):
        self.s.close()

    def test_tools_list(self):
        tools = self.s.rpc("tools/list")["result"]["tools"]
        self.assertEqual(sorted(t["name"] for t in tools),
                         ["get_kind", "get_package", "kinds_for_terraform", "list_clouds", "list_providers", "search_kinds"])
        for t in tools:
            self.assertTrue(t["description"] and t["inputSchema"]["type"] == "object", t["name"])
            self.assertTrue(t["annotations"]["readOnlyHint"], t["name"])

    def test_list_clouds(self):
        cs = {c["id"]: c for c in self.s.tool("list_clouds", {})["structuredContent"]["clouds"]}
        self.assertEqual(cs["gitlab"]["kinds"], 2)
        self.assertIn("gitlab-krm-extensions", cs["gitlab"]["providers"])
        self.assertEqual(sum(cs["gitlab"]["services"].values()), 2)

    def test_search_kinds(self):
        r = self.s.tool("search_kinds", {"query": "wiki"})["structuredContent"]
        self.assertEqual([k["kind"] for k in r["kinds"]], ["ProjectWikiPage"])
        self.assertEqual(r["kinds"][0]["terraform"], ["gitlab_project_wiki_page"])
        r = self.s.tool("search_kinds", {"cloud": "gitlab"})["structuredContent"]
        self.assertEqual(r["count"], 2)
        r = self.s.tool("search_kinds", {"query": "bucket", "tier": "official"})["structuredContent"]
        self.assertEqual([k["kind"] for k in r["kinds"]], ["StorageBucket"])

    def test_get_kind(self):
        k = self.s.tool("get_kind", {"kind": "ProjectWikiPage"})["structuredContent"]
        self.assertEqual(k["apiVersion"], "project.gitlab.hfl.dev/v1alpha1")
        self.assertEqual(k["example"]["tested"], "live")
        self.assertIn("kind: ProjectWikiPage", k["example"]["manifest"])
        self.assertEqual(k["withRelated"][0]["kinds"][-2:], ["Project", "ProjectWikiPage"])
        self.assertIn("fieldRefs", k["withRelated"][0]["manifest"])
        paths = {f["path"] for f in k["spec"]}
        self.assertIn("title", paths)
        self.assertIn("fieldRefs", paths)
        some = self.s.tool("get_kind", {"kind": "project.gitlab.hfl.dev/ProjectWikiPage", "fields": ["title"]})["structuredContent"]
        self.assertEqual([f["path"] for f in some["spec"]], ["title"])
        p = self.s.tool("get_kind", {"kind": "gitlab-krm-extensions/project.gitlab.hfl.dev/Project"})["structuredContent"]
        self.assertIn("ProjectWikiPage", p["usedInExamplesOf"])
        bad = self.s.tool("get_kind", {"kind": "Nope"})
        self.assertTrue(bad["isError"])
        self.assertIn("search_kinds", bad["content"][0]["text"])

    def test_kinds_for_terraform(self):
        r = self.s.tool("kinds_for_terraform", {"types": ["cloudflare_access_rule", "google_storage_bucket", "nope_thing"]})["structuredContent"]["kinds"]
        self.assertEqual([k["kind"] for k in r["cloudflare_access_rule"]], ["AccessRule"])
        self.assertEqual([k["provider"] for k in r["google_storage_bucket"]], ["config-connector"])
        self.assertEqual(r["nope_thing"], [])

    def test_list_providers_and_package(self):
        ps = self.s.tool("list_providers", {})["structuredContent"]["providers"]
        self.assertEqual(sorted(p["id"] for p in ps), ["cloudflare-krm-extensions", "config-connector", "gitlab-krm-extensions"])
        for name in ("aws/acm", "aws-acm"):
            pkg = self.s.tool("get_package", {"package": name})["structuredContent"]
            self.assertEqual(pkg["id"], "aws/acm")
            self.assertIn("values", pkg)
        self.assertTrue(self.s.tool("get_package", {"package": "nope"})["isError"])

    def test_unknown_method(self):
        self.assertEqual(self.s.rpc("resources/list")["error"]["code"], -32601)


class TestHTTP(unittest.TestCase):
    """--http: POST /mcp, one JSON-RPC message per request, JSON back."""

    @classmethod
    def setUpClass(cls):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        cls.p = subprocess.Popen([sys.executable, SERVER, "--site", SITE, "--http", str(cls.port)], stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/healthz", timeout=1).read()
                break
            except OSError:
                time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.p.terminate()
        cls.p.wait(timeout=10)

    def req(self, method, body=None, path="/mcp", headers=None):
        data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **(headers or {})}
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                raw = resp.read()
                return resp.status, resp.headers, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, e.headers, (json.loads(raw) if raw else None)

    def test_initialize_and_call(self):
        code, h, r = self.req("POST", {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                       "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}})
        self.assertEqual((code, h["Content-Type"], r["result"]["protocolVersion"]), (200, "application/json", "2025-06-18"))
        self.assertIsNone(h.get("Mcp-Session-Id"))  # stateless
        code, _, r = self.req("POST", {"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual((code, r), (202, None))
        code, _, r = self.req("POST", {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "search_kinds", "arguments": {"query": "wiki"}}},
                              headers={"Mcp-Protocol-Version": "2025-06-18"})
        self.assertEqual([k["kind"] for k in r["result"]["structuredContent"]["kinds"]], ["ProjectWikiPage"])

    def test_refusals(self):
        self.assertEqual(self.req("GET")[0], 405)
        self.assertEqual(self.req("DELETE")[0], 405)
        self.assertEqual(self.req("POST", b"{not json")[0], 400)
        self.assertEqual(self.req("POST", [{"jsonrpc": "2.0", "id": 1, "method": "ping"}])[0], 400)
        self.assertEqual(self.req("POST", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={"Mcp-Protocol-Version": "1999-01-01"})[0], 400)
        self.assertEqual(self.req("POST", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, path="/other")[0], 404)
        code, h, _ = self.req("OPTIONS")
        self.assertEqual((code, h["Access-Control-Allow-Origin"]), (204, "*"))


if __name__ == "__main__":
    unittest.main()
