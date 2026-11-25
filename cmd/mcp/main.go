// Command mcp serves the KRM Registry over the Model Context Protocol, for
// agents.
//
//	mcp [--site DIR | --url URL] [--http PORT] [--ttl SECONDS]
//
// Over stdio by default; --http serves MCP's streamable HTTP transport on
// POST /mcp instead (stateless: no sessions, every response is JSON), which
// is how registry.highfivelabs.dev/mcp runs on Cloud Run.
//
// It answers from a built site's data/ (catalog.json, the per-group kind
// shards and the chart files): a local build (--site, default ./site), or the
// published registry (--url, read over HTTPS on demand), so nothing new is
// hosted. Go's standard library only.
//
// Tools:
//
//	list_clouds          every cloud and vendor, with its services and kind counts
//	search_kinds         kinds by text, cloud, service, provider or tier
//	get_kind             one kind: its fields, Terraform equivalents, tested
//	                     example, and the examples with the kinds it needs
//	kinds_for_terraform  Terraform resource types -> the kinds that stand in
//	list_providers       every provider: its cloud, tier, version, kinds
//	get_package          a catalog chart: its module, kinds, values, outputs
package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/signal"
	"slices"
	"strconv"
	"strings"
	"syscall"
	"time"
)

var protocolVersions = []string{"2025-06-18", "2025-03-26", "2024-11-05"}

// ---------------------------------------------------------------- tools

type tool struct {
	Name        string          `json:"name"`
	Description string          `json:"description"`
	InputSchema json.RawMessage `json:"inputSchema"`
	Annotations map[string]bool `json:"annotations"`
}

// Every tool only reads.
var readOnly = map[string]bool{"readOnlyHint": true, "openWorldHint": false}

var tools = []tool{
	{"list_clouds", "Every cloud and vendor the registry covers (gcp, aws, azure, cloudflare, datadog, ...): its providers and, per service, how many kinds. Use it to learn the names search_kinds filters on.",
		json.RawMessage(`{"type":"object","properties":{}}`), readOnly},
	{"search_kinds", "Find Kubernetes kinds across every provider in the registry (Config Connector, ACK, ASO, High Five Labs' extensions and REST vendor kinds) by text (kind, group, service, Terraform type, description; all words must match), and optionally cloud (gcp, aws, azure, cloudflare, ...), service, provider id or tier (official, extension).",
		json.RawMessage(`{"type":"object","properties":{"query":{"type":"string"},"cloud":{"type":"string"},"service":{"type":"string"},"provider":{"type":"string"},"tier":{"type":"string"},"limit":{"type":"integer"}}}`), readOnly},
	{"get_kind", "One kind in full: description, every spec and status field (type, required, enum, default, immutable), its Terraform equivalents and API paths, the gap it fills, its tested example (live or fake), the samples from the official controller's own tests, and the examples that show it with the kinds it needs. Name it by id (provider/group/Kind), group/Kind or Kind.",
		json.RawMessage(`{"type":"object","properties":{"kind":{"type":"string"},"fields":{"type":"array","items":{"type":"string"},"description":"only fields whose path starts with one of these"}},"required":["kind"]}`), readOnly},
	{"kinds_for_terraform", "Terraform resource types (e.g. google_storage_bucket, cloudflare_access_rule) -> the kinds that stand in for each, with provider, tier and readiness.",
		json.RawMessage(`{"type":"object","properties":{"types":{"type":"array","items":{"type":"string"}}},"required":["types"]}`), readOnly},
	{"list_providers", "Every provider the registry indexes: cloud, tier (official or extension), publisher, version and number of kinds.",
		json.RawMessage(`{"type":"object","properties":{}}`), readOnly},
	{"get_package", "A catalog Helm chart (a Terraform module ported to KRM kinds): its upstream module, the kinds it uses, its values and outputs. Name it by id (aws/acm), package (aws-acm) or name.",
		json.RawMessage(`{"type":"object","properties":{"package":{"type":"string"}},"required":["package"]}`), readOnly},
}

// call runs one tool. A *toolError is the tool's own answer; any other
// error is the server's.
func (r *registry) call(name string, args json.RawMessage) (any, error) {
	if len(bytes.TrimSpace(args)) == 0 || string(bytes.TrimSpace(args)) == "null" {
		args = json.RawMessage("{}")
	}
	decode := func(v any) error {
		if err := json.Unmarshal(args, v); err != nil {
			return errorf("%s: bad arguments: %v", name, err)
		}
		return nil
	}
	switch name {
	case "list_clouds":
		return r.listClouds()
	case "search_kinds":
		var a searchArgs
		if err := decode(&a); err != nil {
			return nil, err
		}
		return r.searchKinds(a)
	case "get_kind":
		var a struct {
			Kind   *string  `json:"kind"`
			Fields []string `json:"fields"`
		}
		if err := decode(&a); err != nil {
			return nil, err
		}
		if a.Kind == nil {
			return nil, errorf("get_kind: kind is required")
		}
		return r.getKind(*a.Kind, a.Fields)
	case "kinds_for_terraform":
		var a struct {
			Types *[]string `json:"types"`
		}
		if err := decode(&a); err != nil {
			return nil, err
		}
		if a.Types == nil {
			return nil, errorf("kinds_for_terraform: types is required")
		}
		return r.kindsForTerraform(*a.Types)
	case "list_providers":
		return r.listProviders()
	case "get_package":
		var a struct {
			Package *string `json:"package"`
		}
		if err := decode(&a); err != nil {
			return nil, err
		}
		if a.Package == nil {
			return nil, errorf("get_package: package is required")
		}
		return r.getPackage(*a.Package)
	}
	return nil, errorf("no tool %q", name)
}

// ---------------------------------------------------------------- JSON-RPC

type rpcError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

type response struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id"`
	Result  any             `json:"result,omitempty"`
	Error   *rpcError       `json:"error,omitempty"`
}

func errResponse(id json.RawMessage, code int, msg string) *response {
	if id == nil {
		id = json.RawMessage("null")
	}
	return &response{JSONRPC: "2.0", ID: id, Error: &rpcError{code, msg}}
}

type content struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

type callResult struct {
	Content           []content `json:"content"`
	StructuredContent any       `json:"structuredContent,omitempty"`
	IsError           bool      `json:"isError"`
}

type initResult struct {
	ProtocolVersion string            `json:"protocolVersion"`
	Capabilities    map[string]any    `json:"capabilities"`
	ServerInfo      map[string]string `json:"serverInfo"`
	Instructions    string            `json:"instructions"`
}

// marshal is json.Marshal that leaves <, > and & alone: manifests are full
// of <value> placeholders.
func marshal(v any, indent string) ([]byte, error) {
	var b bytes.Buffer
	e := json.NewEncoder(&b)
	e.SetEscapeHTML(false)
	if indent != "" {
		e.SetIndent("", indent)
	}
	if err := e.Encode(v); err != nil {
		return nil, err
	}
	return bytes.TrimRight(b.Bytes(), "\n"), nil
}

// handle answers one JSON-RPC message; nil for a notification.
func (r *registry) handle(msg []byte) (resp *response) {
	var m struct {
		ID     json.RawMessage `json:"id"`
		Method string          `json:"method"`
		Params json.RawMessage `json:"params"`
	}
	if err := json.Unmarshal(msg, &m); err != nil {
		return errResponse(nil, -32600, "invalid request: "+err.Error())
	}
	if m.ID == nil || string(m.ID) == "null" {
		return nil
	}
	defer func() { // a bug in a tool: report it, keep serving
		if p := recover(); p != nil {
			resp = errResponse(m.ID, -32603, fmt.Sprintf("internal error: %v", p))
		}
	}()
	var result any
	switch m.Method {
	case "initialize":
		var p struct {
			ProtocolVersion string `json:"protocolVersion"`
		}
		_ = json.Unmarshal(m.Params, &p)
		v := protocolVersions[0]
		if slices.Contains(protocolVersions, p.ProtocolVersion) {
			v = p.ProtocolVersion
		}
		result = initResult{v, map[string]any{"tools": map[string]any{}}, map[string]string{"name": "krm-registry", "version": "1"},
			"The KRM Registry: Kubernetes kinds for clouds and SaaS vendors, and the Terraform types they stand in for. " +
				"list_clouds names the clouds, services and providers; search_kinds or kinds_for_terraform finds kinds; get_kind gives every field and the tested examples."}
	case "ping":
		result = map[string]any{}
	case "tools/list":
		result = map[string]any{"tools": tools}
	case "tools/call":
		var p struct {
			Name      string          `json:"name"`
			Arguments json.RawMessage `json:"arguments"`
		}
		if err := json.Unmarshal(m.Params, &p); err != nil && len(m.Params) > 0 {
			return errResponse(m.ID, -32602, "invalid params: "+err.Error())
		}
		out, err := r.call(p.Name, p.Arguments)
		var te *toolError
		switch {
		case errors.As(err, &te):
			result = callResult{Content: []content{{"text", te.msg}}, IsError: true}
		case err != nil:
			return errResponse(m.ID, -32603, "internal error: "+err.Error())
		default:
			text, err := marshal(out, " ")
			if err != nil {
				return errResponse(m.ID, -32603, "internal error: "+err.Error())
			}
			result = callResult{Content: []content{{"text", string(text)}}, StructuredContent: out}
		}
	default:
		return errResponse(m.ID, -32601, "method not found: "+m.Method)
	}
	return &response{JSONRPC: "2.0", ID: m.ID, Result: result}
}

// ---------------------------------------------------------------- stdio

func (r *registry) serveStdio(in io.Reader, out io.Writer) error {
	br, w := bufio.NewReader(in), bufio.NewWriter(out)
	for {
		line, err := br.ReadBytes('\n')
		if line = bytes.TrimSpace(line); len(line) > 0 {
			var resp *response
			if !json.Valid(line) {
				resp = errResponse(nil, -32700, "parse error")
			} else {
				resp = r.handle(line)
			}
			if resp != nil {
				b, merr := marshal(resp, "")
				if merr != nil {
					return merr
				}
				w.Write(b)
				w.WriteByte('\n')
				if ferr := w.Flush(); ferr != nil {
					return ferr
				}
			}
		}
		if err == io.EOF {
			return nil
		} else if err != nil {
			return err
		}
	}
}

// ---------------------------------------------------------------- HTTP

const maxBody = 1 << 20

// httpHandler is MCP's streamable HTTP transport, stateless: POST a
// JSON-RPC message to /mcp, get its response as application/json (202 for a
// notification). No server-sent events, so GET and DELETE are 405.
func (r *registry) httpHandler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		path := req.URL.Path
		isMCP := strings.TrimRight(path, "/") == "/mcp" || strings.TrimRight(path, "/") == ""
		h := w.Header()
		// The data is public; any origin may call it.
		h.Set("Access-Control-Allow-Origin", "*")
		h.Set("Access-Control-Allow-Headers", "Content-Type, Accept, Mcp-Protocol-Version, Mcp-Session-Id")
		h.Set("Access-Control-Allow-Methods", "POST, OPTIONS")
		h.Set("Cache-Control", "no-store")
		send := func(code int, body any) {
			if body == nil {
				h.Set("Content-Length", "0")
				w.WriteHeader(code)
				return
			}
			b, _ := marshal(body, "")
			h.Set("Content-Type", "application/json")
			h.Set("Content-Length", strconv.Itoa(len(b)))
			w.WriteHeader(code)
			w.Write(b)
		}
		notAllowed := func(msg string) {
			h.Set("Allow", "POST, OPTIONS")
			send(http.StatusMethodNotAllowed, map[string]string{"error": msg})
		}
		switch req.Method {
		case http.MethodOptions:
			send(http.StatusNoContent, nil)
		case http.MethodGet, http.MethodHead:
			switch {
			case path == "/healthz":
				h.Set("Content-Type", "text/plain")
				w.Write([]byte("ok\n"))
			case isMCP:
				notAllowed("this server does not stream; POST JSON-RPC messages to /mcp")
			default:
				send(http.StatusNotFound, map[string]string{"error": "not found"})
			}
		case http.MethodPost:
			if !isMCP {
				send(http.StatusNotFound, map[string]string{"error": "not found"})
				return
			}
			if v := req.Header.Get("Mcp-Protocol-Version"); v != "" && !slices.Contains(protocolVersions, v) {
				send(http.StatusBadRequest, errResponse(nil, -32600, "unsupported MCP-Protocol-Version "+v))
				return
			}
			if req.ContentLength > maxBody {
				send(http.StatusRequestEntityTooLarge, errResponse(nil, -32600, "a JSON-RPC message of at most 1 MiB"))
				return
			}
			body, err := io.ReadAll(http.MaxBytesReader(w, req.Body, maxBody))
			body = bytes.TrimSpace(body)
			var tooBig *http.MaxBytesError
			switch {
			case errors.As(err, &tooBig):
				send(http.StatusRequestEntityTooLarge, errResponse(nil, -32600, "a JSON-RPC message of at most 1 MiB"))
				return
			case err != nil || len(body) == 0:
				send(http.StatusBadRequest, errResponse(nil, -32600, "a JSON-RPC message of at most 1 MiB"))
				return
			case !json.Valid(body):
				send(http.StatusBadRequest, errResponse(nil, -32700, "parse error"))
				return
			case body[0] != '{':
				send(http.StatusBadRequest, errResponse(nil, -32600, "one JSON-RPC message per request; batches are not supported"))
				return
			}
			if resp := r.handle(body); resp == nil {
				send(http.StatusAccepted, nil)
			} else {
				send(http.StatusOK, resp)
			}
		case http.MethodDelete:
			notAllowed("no sessions to end")
		default:
			notAllowed("POST JSON-RPC messages to /mcp")
		}
	})
}

// logged writes one line per request on stderr (Cloud Logging).
func logged(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		rw := &statusWriter{ResponseWriter: w, code: http.StatusOK}
		start := time.Now()
		next.ServeHTTP(rw, req)
		log.Printf("%s %s %s %d %s", req.RemoteAddr, req.Method, req.URL.Path, rw.code, time.Since(start).Round(time.Millisecond))
	})
}

type statusWriter struct {
	http.ResponseWriter
	code int
}

func (s *statusWriter) WriteHeader(code int) {
	s.code = code
	s.ResponseWriter.WriteHeader(code)
}

// ---------------------------------------------------------------- main

func main() {
	site := flag.String("site", "site", "a built site directory")
	url := flag.String("url", "", "the published registry's base URL, read instead of --site")
	port := flag.Int("http", -1, "serve streamable HTTP on `PORT` (0: $PORT, else 8080) instead of stdio")
	ttl := flag.Float64("ttl", 300, "with --http, seconds before a data file is read again (the CDN's cache time)")
	flag.Usage = func() {
		fmt.Fprintln(flag.CommandLine.Output(), "The KRM Registry over the Model Context Protocol.\n\nusage: mcp [--site DIR | --url URL] [--http PORT] [--ttl SECONDS]")
		flag.PrintDefaults()
	}
	flag.Parse()
	log.SetFlags(0)

	keep := time.Duration(-1) // stdio: read each file once
	if *port >= 0 {
		keep = time.Duration(*ttl * float64(time.Second))
	}
	reg := &registry{newSource(*site, *url, keep)}
	from := *site
	if *url != "" {
		from = *url
	}

	if *port < 0 {
		if err := reg.serveStdio(os.Stdin, os.Stdout); err != nil {
			log.Fatal(err)
		}
		return
	}
	if *port == 0 {
		*port = 8080
		if p, err := strconv.Atoi(os.Getenv("PORT")); err == nil {
			*port = p
		}
	}
	srv := &http.Server{Addr: fmt.Sprintf(":%d", *port), Handler: logged(reg.httpHandler()), ReadHeaderTimeout: 10 * time.Second}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	go func() {
		<-ctx.Done()
		shut, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		srv.Shutdown(shut)
	}()
	log.Printf("krm-registry MCP on :%d/mcp, data from %s", *port, from)
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatal(err)
	}
}
