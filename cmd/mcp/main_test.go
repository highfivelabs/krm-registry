package main

// The tools over stdio and streamable HTTP against testdata/site (a few
// kinds and a chart cut from a real build).   go test ./cmd/mcp

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"slices"
	"sort"
	"strings"
	"testing"
)

const site = "testdata/site"

// session drives the stdio transport: each rpc runs the messages so far.
type session struct {
	t   *testing.T
	reg *registry
	n   int
}

func newSession(t *testing.T) *session {
	return &session{t: t, reg: &registry{newSource(site, "", -1)}}
}

func (s *session) rpc(method string, params any) map[string]any {
	s.t.Helper()
	s.n++
	msg, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": s.n, "method": method, "params": params})
	var out bytes.Buffer
	if err := s.reg.serveStdio(bytes.NewReader(append(msg, '\n')), &out); err != nil {
		s.t.Fatal(err)
	}
	var resp map[string]any
	if err := json.Unmarshal(out.Bytes(), &resp); err != nil {
		s.t.Fatalf("%s: %v: %q", method, err, out.String())
	}
	if resp["id"] != float64(s.n) {
		s.t.Fatalf("%s: id %v, want %d", method, resp["id"], s.n)
	}
	return resp
}

// tool calls a tool and returns its result.
func (s *session) tool(name string, args map[string]any) map[string]any {
	s.t.Helper()
	r := s.rpc("tools/call", map[string]any{"name": name, "arguments": args})["result"].(map[string]any)
	if r["isError"] == true {
		return r
	}
	// The text is the structured content, as JSON.
	var text any
	if err := json.Unmarshal([]byte(r["content"].([]any)[0].(map[string]any)["text"].(string)), &text); err != nil {
		s.t.Fatal(err)
	}
	if fmt.Sprint(text) != fmt.Sprint(r["structuredContent"]) {
		s.t.Errorf("%s: text and structuredContent differ", name)
	}
	return r
}

func sc(r map[string]any) map[string]any { return r["structuredContent"].(map[string]any) }

func strs(v any) []string {
	out := []string{}
	for _, x := range v.([]any) {
		out = append(out, x.(string))
	}
	return out
}

func pluck(v any, key string) []string {
	out := []string{}
	for _, x := range v.([]any) {
		out = append(out, x.(map[string]any)[key].(string))
	}
	return out
}

func eq[T comparable](t *testing.T, what string, got, want T) {
	t.Helper()
	if got != want {
		t.Errorf("%s = %v, want %v", what, got, want)
	}
}

func eqs(t *testing.T, what string, got, want []string) {
	t.Helper()
	if !slices.Equal(got, want) {
		t.Errorf("%s = %q, want %q", what, got, want)
	}
}

func TestInitialize(t *testing.T) {
	s := newSession(t)
	r := s.rpc("initialize", map[string]any{"protocolVersion": "2025-06-18", "capabilities": map[string]any{}})["result"].(map[string]any)
	eq(t, "protocolVersion", r["protocolVersion"], any("2025-06-18"))
	if _, ok := r["capabilities"].(map[string]any)["tools"]; !ok {
		t.Error("no tools capability")
	}
	r = s.rpc("initialize", map[string]any{"protocolVersion": "1999-01-01"})["result"].(map[string]any)
	eq(t, "fallback protocolVersion", r["protocolVersion"], any(protocolVersions[0]))
	if _, ok := s.rpc("ping", nil)["result"].(map[string]any); !ok {
		t.Error("ping: no result")
	}
}

func TestToolsList(t *testing.T) {
	s := newSession(t)
	ts := s.rpc("tools/list", nil)["result"].(map[string]any)["tools"]
	names := pluck(ts, "name")
	sort.Strings(names)
	eqs(t, "tools", names, []string{"get_install", "get_kind", "get_package", "kinds_for_terraform", "list_operators", "list_providers", "search_kinds"})
	for _, x := range ts.([]any) {
		tl := x.(map[string]any)
		if tl["description"] == "" || tl["inputSchema"].(map[string]any)["type"] != "object" {
			t.Errorf("%s: no description or object schema", tl["name"])
		}
		if tl["annotations"].(map[string]any)["readOnlyHint"] != true {
			t.Errorf("%s: not read-only", tl["name"])
		}
	}
}

func TestListProviders(t *testing.T) {
	s := newSession(t)
	ps := sc(s.tool("list_providers", nil))["providers"]
	eqs(t, "provider order", pluck(ps, "id"), []string{"gcp", "cloudflare", "gitlab"})
	byID := map[string]map[string]any{}
	for _, p := range ps.([]any) {
		byID[p.(map[string]any)["id"].(string)] = p.(map[string]any)
	}
	g := byID["gitlab"]
	eq(t, "gitlab kinds", g["kinds"], any(2.0))
	if !slices.Contains(strs(g["operators"]), "gitlab-krm-extensions") {
		t.Errorf("gitlab operators = %v", g["operators"])
	}
	n := 0.0
	for _, v := range g["services"].(map[string]any) {
		n += v.(float64)
	}
	eq(t, "gitlab services total", n, 2)
}

// list_clouds, the old name, still answers (unlisted), with list_providers' answer.
func TestListCloudsAlias(t *testing.T) {
	s := newSession(t)
	old := s.tool("list_clouds", nil)
	if old["isError"] == true {
		t.Fatalf("list_clouds: %v", old)
	}
	eq(t, "list_clouds = list_providers", fmt.Sprint(sc(old)), fmt.Sprint(sc(s.tool("list_providers", nil))))
}

func TestSearchKinds(t *testing.T) {
	s := newSession(t)
	r := sc(s.tool("search_kinds", map[string]any{"query": "wiki"}))
	eqs(t, "wiki", pluck(r["kinds"], "kind"), []string{"ProjectWikiPage"})
	eqs(t, "wiki terraform", strs(r["kinds"].([]any)[0].(map[string]any)["terraform"]), []string{"gitlab_project_wiki_page"})
	eq(t, "wiki operator", r["kinds"].([]any)[0].(map[string]any)["operator"], any("gitlab-krm-extensions"))
	eq(t, "wiki provider", r["kinds"].([]any)[0].(map[string]any)["provider"], any("gitlab"))
	eq(t, "gitlab count", sc(s.tool("search_kinds", map[string]any{"provider": "gitlab"}))["count"], any(2.0))
	eq(t, "operator filter", sc(s.tool("search_kinds", map[string]any{"operator": "cloudflare-krm-extensions"}))["count"], any(1.0))
	eq(t, "operator and other provider", sc(s.tool("search_kinds", map[string]any{"operator": "cloudflare-krm-extensions", "provider": "gcp"}))["count"], any(0.0))
	eqs(t, "official bucket", pluck(sc(s.tool("search_kinds", map[string]any{"query": "bucket", "tier": "official"}))["kinds"], "kind"), []string{"StorageBucket"})
	eq(t, "hfl tier", sc(s.tool("search_kinds", map[string]any{"tier": "hfl"}))["count"], any(3.0))
	eqs(t, "terraform type", pluck(sc(s.tool("search_kinds", map[string]any{"query": "google_storage_bucket"}))["kinds"], "kind"), []string{"StorageBucket"})
	r = sc(s.tool("search_kinds", map[string]any{"limit": 0}))
	eq(t, "limit 0 returns one", len(r["kinds"].([]any)), 1)
	if r["count"].(float64) < 2 {
		t.Errorf("count = %v", r["count"])
	}
	if r := s.tool("search_kinds", map[string]any{"limit": "x"}); r["isError"] != true {
		t.Error("bad limit: no error")
	}
}

// The old argument forms still work: cloud for provider, an operator id
// passed as provider, and tier extension for hfl.
func TestSearchKindsAliases(t *testing.T) {
	s := newSession(t)
	ids := func(args map[string]any) []string {
		t.Helper()
		got := pluck(sc(s.tool("search_kinds", args))["kinds"], "id")
		sort.Strings(got)
		return got
	}
	gitlab := ids(map[string]any{"provider": "gitlab"})
	eq(t, "gitlab kinds", len(gitlab), 2)
	eqs(t, "cloud alias", ids(map[string]any{"cloud": "gitlab"}), gitlab)
	eqs(t, "provider wins over cloud", ids(map[string]any{"provider": "gitlab", "cloud": "gcp"}), gitlab)
	eqs(t, "operator id as provider", ids(map[string]any{"provider": "gitlab-krm-extensions"}), ids(map[string]any{"operator": "gitlab-krm-extensions"}))
	eqs(t, "operator id as cloud", ids(map[string]any{"cloud": "config-connector"}), []string{"config-connector/storage.cnrm.cloud.google.com/StorageBucket"})
	hfl := ids(map[string]any{"tier": "hfl"})
	eq(t, "hfl kinds", len(hfl), 3)
	eqs(t, "extension alias", ids(map[string]any{"tier": "extension"}), hfl)
	eq(t, "unknown provider", sc(s.tool("search_kinds", map[string]any{"provider": "mars"}))["count"], any(0.0))
}

func TestGetKind(t *testing.T) {
	s := newSession(t)
	k := sc(s.tool("get_kind", map[string]any{"kind": "ProjectWikiPage"}))
	eq(t, "apiVersion", k["apiVersion"], any("project.gitlab.hfl.dev/v1alpha1"))
	eq(t, "operator", k["operator"], any("gitlab-krm-extensions"))
	eq(t, "provider", k["provider"], any("gitlab"))
	eq(t, "tier", k["tier"], any("hfl"))
	ex := k["example"].(map[string]any)
	eq(t, "tested", ex["tested"], any("live"))
	if !strings.Contains(ex["manifest"].(string), "kind: ProjectWikiPage") {
		t.Error("example has no manifest")
	}
	rel := k["withRelated"].([]any)[0].(map[string]any)
	kinds := strs(rel["kinds"])
	eqs(t, "withRelated kinds", kinds[len(kinds)-2:], []string{"Project", "ProjectWikiPage"})
	if !strings.Contains(rel["manifest"].(string), "fieldRefs") {
		t.Error("withRelated manifest has no fieldRefs")
	}
	paths := pluck(k["spec"], "path")
	for _, p := range []string{"title", "fieldRefs"} {
		if !slices.Contains(paths, p) {
			t.Errorf("spec has no %s", p)
		}
	}
	some := sc(s.tool("get_kind", map[string]any{"kind": "project.gitlab.hfl.dev/ProjectWikiPage", "fields": []string{"title"}}))
	eqs(t, "fields", pluck(some["spec"], "path"), []string{"title"})
	p := sc(s.tool("get_kind", map[string]any{"kind": "gitlab-krm-extensions/project.gitlab.hfl.dev/Project"}))
	if !slices.Contains(strs(p["usedInExamplesOf"]), "ProjectWikiPage") {
		t.Errorf("usedInExamplesOf = %v", p["usedInExamplesOf"])
	}
	// Placeholders like <bucket-name> stay as written in the text, not escaped.
	b := s.tool("get_kind", map[string]any{"kind": "StorageBucket"})
	if strings.Contains(b["content"].([]any)[0].(map[string]any)["text"].(string), "\\"+"u003c") {
		t.Error("text escapes < as \\u003c")
	}
	bad := s.tool("get_kind", map[string]any{"kind": "Nope"})
	eq(t, "isError", bad["isError"], any(true))
	if !strings.Contains(bad["content"].([]any)[0].(map[string]any)["text"].(string), "search_kinds") {
		t.Error("not-found message does not point at search_kinds")
	}
	if r := s.tool("get_kind", map[string]any{}); r["isError"] != true {
		t.Error("no kind: no error")
	}
}

func TestKindsForTerraform(t *testing.T) {
	s := newSession(t)
	r := sc(s.tool("kinds_for_terraform", map[string]any{"types": []string{"cloudflare_access_rule", "google_storage_bucket", "nope_thing"}}))["kinds"].(map[string]any)
	eqs(t, "cloudflare_access_rule", pluck(r["cloudflare_access_rule"], "kind"), []string{"AccessRule"})
	eqs(t, "google_storage_bucket operator", pluck(r["google_storage_bucket"], "operator"), []string{"config-connector"})
	eqs(t, "google_storage_bucket provider", pluck(r["google_storage_bucket"], "provider"), []string{"gcp"})
	eq(t, "nope_thing", len(r["nope_thing"].([]any)), 0)
}

func TestListOperatorsAndPackage(t *testing.T) {
	s := newSession(t)
	ops := sc(s.tool("list_operators", nil))["operators"]
	ids := pluck(ops, "id")
	sort.Strings(ids)
	eqs(t, "operators", ids, []string{"cloudflare-krm-extensions", "config-connector", "config-connector-operator", "gitlab-krm-extensions"})
	for _, x := range ops.([]any) {
		o := x.(map[string]any)
		switch o["id"] {
		case "config-connector":
			eq(t, "kcc provider", o["provider"], any("gcp"))
			eq(t, "kcc stars", o["stars"], any(1050.0))
		case "gitlab-krm-extensions":
			eq(t, "gitlab tier", o["tier"], any("hfl"))
			if v, ok := o["stars"]; !ok || v != nil {
				t.Errorf("gitlab stars = %v, %v; want null", v, ok)
			}
		}
	}
	for _, name := range []string{"aws/acm", "aws-acm", "acm"} {
		pkg := sc(s.tool("get_package", map[string]any{"package": name}))
		eq(t, name, pkg["id"], any("aws/acm"))
		if _, ok := pkg["values"]; !ok {
			t.Errorf("%s: no values", name)
		}
	}
	if s.tool("get_package", map[string]any{"package": "nope"})["isError"] != true {
		t.Error("nope: no error")
	}
}

func TestGetInstall(t *testing.T) {
	s := newSession(t)
	// Config Connector is installed by its operator; the default is the first variant.
	p := sc(s.tool("get_install", map[string]any{"operator": "config-connector"}))
	eq(t, "operator", p["operator"], any("config-connector"))
	eq(t, "via", p["via"], any("config-connector-operator"))
	eq(t, "published", p["published"], any(true))
	eq(t, "default install", p["install"].(map[string]any)["name"], any("High Five Labs chart · GKE Standard"))
	eq(t, "matching credentials", p["credentials"].(map[string]any)["name"], any("GKE Standard"))
	eq(t, "namespace", p["namespace"], any("configconnector-operator-system"))
	// The package's NAMESPACE (the management cluster's) is asked for too.
	eqs(t, "placeholders", pluck(p["placeholders"], "name"), []string{"PROJECT_ID", "CLUSTER", "NAMESPACE"})
	// The credentials as a package: a krm-packages chart that a running Config Connector applies.
	pkg, _ := p["credentials"].(map[string]any)["package"].(map[string]any)
	if pkg == nil || pkg["chart"] != "gcp/iam-service-account" || !strings.Contains(pkg["steps"].(string), "helm install config-connector-identity") {
		t.Errorf("credentials package %v", pkg)
	}
	if len(p["how_to_run"].([]any)) == 0 || len(p["verify"].([]any)) == 0 {
		t.Error("no how_to_run or verify")
	}
	// A choice on one axis picks the credentials that follow it.
	p = sc(s.tool("get_install", map[string]any{"operator": "config-connector-operator", "choice": map[string]string{"Cluster": "Outside GKE"}}))
	eq(t, "outside GKE install", p["install"].(map[string]any)["name"], any("High Five Labs chart · Outside GKE"))
	eq(t, "outside GKE credentials", p["credentials"].(map[string]any)["name"], any("Outside GKE"))
	// Not published yet: says where, no steps.
	p = sc(s.tool("get_install", map[string]any{"operator": "gitlab-krm-extensions"}))
	eq(t, "pending published", p["published"], any(false))
	eqs(t, "pending", toStrings(p["pending"]), []string{"charts/gitlab-krm-extensions"})
	// provider, the old name of operator, still works.
	old := sc(s.tool("get_install", map[string]any{"provider": "config-connector"}))
	eq(t, "provider alias", old["via"], any("config-connector-operator"))
	for _, bad := range []map[string]any{
		{"operator": "nope"},
		{"operator": "gcp"},
		{"operator": "config-connector-operator", "choice": map[string]string{"Cluster": "Mars"}},
		{"operator": "config-connector-operator", "choice": map[string]string{"Method": "Google's manifests", "Cluster": "Outside GKE"}},
		{},
	} {
		if s.tool("get_install", bad)["isError"] != true {
			t.Errorf("%v: no error", bad)
		}
	}
}

func toStrings(v any) []string {
	var out []string
	for _, x := range v.([]any) {
		out = append(out, x.(string))
	}
	return out
}

func TestPlaceholders(t *testing.T) {
	got := placeholders("AWS_REGION=us-west-2\nwhile read -r SERVICE VERSION; do\n  helm install ack-$SERVICE-controller --version $VERSION --set aws.region=$AWS_REGION --set x=$ROLE_ARN\ndone",
		"RELEASE_VERSION=$(curl -sL x)\necho $RELEASE_VERSION")
	b, _ := json.Marshal(got)
	if string(b) != `[{"name":"AWS_REGION","example":"us-west-2"},{"name":"ROLE_ARN"}]` {
		t.Errorf("placeholders %s", b)
	}
}

func TestStdioErrors(t *testing.T) {
	s := newSession(t)
	eq(t, "unknown method", s.rpc("resources/list", nil)["error"].(map[string]any)["code"], any(-32601.0))
	if s.tool("no_tool", nil)["isError"] != true {
		t.Error("unknown tool: no error")
	}
	// A notification gets no response, a bad line a parse error; the server keeps reading.
	var out bytes.Buffer
	in := "{\"jsonrpc\":\"2.0\",\"method\":\"notifications/initialized\"}\n\n{not json\n{\"jsonrpc\":\"2.0\",\"id\":\"a\",\"method\":\"ping\"}\n"
	if err := s.reg.serveStdio(strings.NewReader(in), &out); err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(out.String()), "\n")
	eq(t, "responses", len(lines), 2)
	if !strings.Contains(lines[0], "-32700") || !strings.Contains(lines[1], `"id":"a"`) {
		t.Errorf("responses = %q", lines)
	}
}

// A data source that fails is a JSON-RPC internal error, not a tool answer.
func TestSourceFailure(t *testing.T) {
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { http.Error(w, "down", 503) }))
	defer up.Close()
	reg := &registry{newSource("", up.URL, 0)}
	resp := reg.handle([]byte(`{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_providers"}}`))
	if resp.Error == nil || resp.Error.Code != -32603 {
		t.Errorf("response = %+v", resp)
	}
}

// --url reads the same files over HTTP, 404 as missing, and caches for ttl.
func TestURLSource(t *testing.T) {
	hits := 0
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits++
		http.FileServer(http.Dir(site)).ServeHTTP(w, r)
	}))
	defer up.Close()
	s := &session{t: t, reg: &registry{newSource("", up.URL+"/", -1)}}
	eqs(t, "wiki", pluck(sc(s.tool("search_kinds", map[string]any{"query": "wiki"}))["kinds"], "kind"), []string{"ProjectWikiPage"})
	eq(t, "tested", sc(s.tool("get_kind", map[string]any{"kind": "ProjectWikiPage"}))["example"].(map[string]any)["tested"], any("live"))
	before := hits
	s.tool("search_kinds", map[string]any{"query": "bucket"})
	eq(t, "cached catalog reads", hits, before)
	if s.tool("get_package", map[string]any{"package": "nope"})["isError"] != true {
		t.Error("nope: no error")
	}
}

func TestHTTP(t *testing.T) {
	srv := httptest.NewServer((&registry{newSource(site, "", 0)}).httpHandler())
	defer srv.Close()
	req := func(method, path string, body any, headers map[string]string) (int, http.Header, map[string]any) {
		t.Helper()
		var r io.Reader
		switch b := body.(type) {
		case nil:
		case []byte:
			r = bytes.NewReader(b)
		default:
			j, _ := json.Marshal(b)
			r = bytes.NewReader(j)
		}
		hr, _ := http.NewRequest(method, srv.URL+path, r)
		hr.Header.Set("Content-Type", "application/json")
		hr.Header.Set("Accept", "application/json, text/event-stream")
		for k, v := range headers {
			hr.Header.Set(k, v)
		}
		resp, err := http.DefaultClient.Do(hr)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		raw, _ := io.ReadAll(resp.Body)
		var out map[string]any
		if len(raw) > 0 && resp.Header.Get("Content-Type") == "application/json" {
			if err := json.Unmarshal(raw, &out); err != nil {
				t.Fatalf("%s %s: %v", method, path, err)
			}
		}
		return resp.StatusCode, resp.Header, out
	}

	t.Run("initialize and call", func(t *testing.T) {
		code, h, r := req("POST", "/mcp", map[string]any{"jsonrpc": "2.0", "id": 1, "method": "initialize",
			"params": map[string]any{"protocolVersion": "2025-06-18", "capabilities": map[string]any{}}}, nil)
		eq(t, "code", code, 200)
		eq(t, "content type", h.Get("Content-Type"), "application/json")
		eq(t, "protocolVersion", r["result"].(map[string]any)["protocolVersion"], any("2025-06-18"))
		eq(t, "session id", h.Get("Mcp-Session-Id"), "") // stateless
		code, _, r = req("POST", "/mcp", map[string]any{"jsonrpc": "2.0", "method": "notifications/initialized"}, nil)
		eq(t, "notification code", code, 202)
		if r != nil {
			t.Errorf("notification body = %v", r)
		}
		_, _, r = req("POST", "/mcp", map[string]any{"jsonrpc": "2.0", "id": 2, "method": "tools/call",
			"params": map[string]any{"name": "search_kinds", "arguments": map[string]any{"query": "wiki"}}}, map[string]string{"Mcp-Protocol-Version": "2025-06-18"})
		eqs(t, "wiki", pluck(r["result"].(map[string]any)["structuredContent"].(map[string]any)["kinds"], "kind"), []string{"ProjectWikiPage"})
	})

	t.Run("refusals", func(t *testing.T) {
		ping := map[string]any{"jsonrpc": "2.0", "id": 1, "method": "ping"}
		for _, c := range []struct {
			name, method, path string
			body               any
			headers            map[string]string
			want               int
		}{
			{"GET", "GET", "/mcp", nil, nil, 405},
			{"DELETE", "DELETE", "/mcp", nil, nil, 405},
			{"not JSON", "POST", "/mcp", []byte("{not json"), nil, 400},
			{"empty", "POST", "/mcp", []byte(""), nil, 400},
			{"batch", "POST", "/mcp", []any{ping}, nil, 400},
			{"protocol version", "POST", "/mcp", ping, map[string]string{"Mcp-Protocol-Version": "1999-01-01"}, 400},
			{"too big", "POST", "/mcp", bytes.Repeat([]byte(" "), maxBody+1), nil, 413},
			{"other path", "POST", "/other", ping, nil, 404},
			{"GET other path", "GET", "/other", nil, nil, 404},
		} {
			if code, _, _ := req(c.method, c.path, c.body, c.headers); code != c.want {
				t.Errorf("%s: %d, want %d", c.name, code, c.want)
			}
		}
		code, h, _ := req("OPTIONS", "/mcp", nil, nil)
		eq(t, "OPTIONS", code, 204)
		eq(t, "CORS", h.Get("Access-Control-Allow-Origin"), "*")
		code, _, _ = req("GET", "/healthz", nil, nil)
		eq(t, "healthz", code, 200)
	})
}

// Over HTTP the server logs one JSON line per initialize and tool call, for
// Cloud Logging; stdio (events nil) logs none.
func TestEvents(t *testing.T) {
	var buf bytes.Buffer
	events = &buf
	defer func() { events = nil }()
	s := newSession(t)
	s.rpc("initialize", map[string]any{"protocolVersion": protocolVersions[0], "clientInfo": map[string]any{"name": "claude-code", "version": "2.1"}})
	s.tool("search_kinds", map[string]any{"query": "bucket"})
	s.tool("get_kind", map[string]any{"kind": "NoSuchKind"})

	var got []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(buf.String()), "\n") {
		var e map[string]any
		if err := json.Unmarshal([]byte(line), &e); err != nil {
			t.Fatalf("%q: %v", line, err)
		}
		got = append(got, e)
	}
	if len(got) != 3 {
		t.Fatalf("%d events, want 3: %s", len(got), buf.String())
	}
	if got[0]["event"] != "initialize" || got[0]["client"] != "claude-code" || got[0]["clientVersion"] != "2.1" || got[0]["severity"] != "INFO" {
		t.Errorf("initialize: %v", got[0])
	}
	if got[1]["tool"] != "search_kinds" || got[1]["outcome"] != "ok" || got[1]["args"] != `{"query":"bucket"}` {
		t.Errorf("search_kinds: %v", got[1])
	}
	if got[2]["tool"] != "get_kind" || got[2]["outcome"] != "tool_error" {
		t.Errorf("get_kind: %v", got[2])
	}

	buf.Reset()
	events = nil
	s.tool("list_operators", nil)
	if buf.Len() != 0 {
		t.Errorf("logged with events nil: %s", buf.String())
	}
}
