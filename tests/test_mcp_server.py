"""mcp_server.py over stdio against tests/fixture-site (a few kinds and a chart
cut from a real build).   python3 -m unittest discover -s tests"""
import json
import os
import subprocess
import sys
import unittest

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
                         ["get_kind", "get_package", "kinds_for_terraform", "list_providers", "search_kinds"])
        for t in tools:
            self.assertTrue(t["description"] and t["inputSchema"]["type"] == "object", t["name"])

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


if __name__ == "__main__":
    unittest.main()
