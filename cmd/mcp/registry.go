package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

// source reads a built site's data/ files from a local directory or the
// published registry, and keeps what it parsed. With a ttl >= 0 a file is
// read again after ttl, so a long-running server sees a publish without a
// restart; a negative ttl keeps files for the process's life.
type source struct {
	site, url string
	ttl       time.Duration
	client    *http.Client

	mu    sync.Mutex
	cache map[string]cached
}

type cached struct {
	val any
	at  time.Time
}

// sourceError is a data source that failed (not a file that is missing).
// It is reported as a JSON-RPC internal error, not as a tool's answer.
type sourceError struct{ err error }

func (e *sourceError) Error() string { return e.err.Error() }
func (e *sourceError) Unwrap() error { return e.err }

func newSource(site, url string, ttl time.Duration) *source {
	if url != "" {
		url = strings.TrimRight(url, "/") + "/"
	}
	return &source{site: site, url: url, ttl: ttl, client: &http.Client{Timeout: 30 * time.Second}, cache: map[string]cached{}}
}

// get returns rel parsed by parse, or nil when rel does not exist.
func (s *source) get(rel string, parse func([]byte) (any, error)) (any, error) {
	s.mu.Lock()
	hit, ok := s.cache[rel]
	s.mu.Unlock()
	if ok && (s.ttl < 0 || time.Since(hit.at) < s.ttl) {
		return hit.val, nil
	}
	raw, err := s.fetch(rel)
	if err != nil {
		return nil, &sourceError{err}
	}
	var v any
	if raw != nil {
		if v, err = parse(raw); err != nil {
			return nil, &sourceError{fmt.Errorf("%s: %w", rel, err)}
		}
	}
	s.mu.Lock()
	s.cache[rel] = cached{v, time.Now()}
	s.mu.Unlock()
	return v, nil
}

func (s *source) fetch(rel string) ([]byte, error) {
	if s.url == "" {
		b, err := os.ReadFile(filepath.Join(s.site, filepath.FromSlash(rel)))
		if errors.Is(err, os.ErrNotExist) {
			return nil, nil
		}
		return b, err
	}
	req, err := http.NewRequest(http.MethodGet, s.url+rel, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "krm-registry-mcp")
	resp, err := s.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	switch {
	case resp.StatusCode == http.StatusNotFound:
		return nil, nil
	case resp.StatusCode != http.StatusOK:
		return nil, fmt.Errorf("GET %s%s: %s", s.url, rel, resp.Status)
	}
	return io.ReadAll(resp.Body)
}

// ---------------------------------------------------------------- the data

// raw is a value passed through as the site has it; absent or null, it
// marshals as null.
type raw = json.RawMessage

type catalog struct {
	Surfaces  surfaces   `json:"surfaces"`
	Providers []provider `json:"providers"`
	Kinds     []kind     `json:"kinds"`
	Charts    []chart    `json:"charts"`
}

type surface struct {
	Title    raw `json:"title"`
	Category raw `json:"category"`
}

// surfaces keeps catalog.json's order, which list_clouds answers in.
type surfaces []struct {
	ID string
	surface
}

func (ss *surfaces) UnmarshalJSON(b []byte) error {
	d := json.NewDecoder(bytes.NewReader(b))
	if t, err := d.Token(); err != nil || t != json.Delim('{') {
		return fmt.Errorf("surfaces: want an object")
	}
	for d.More() {
		t, err := d.Token()
		if err != nil {
			return err
		}
		var s surface
		if err := d.Decode(&s); err != nil {
			return err
		}
		*ss = append(*ss, struct {
			ID string
			surface
		}{t.(string), s})
	}
	return nil
}

type provider struct {
	ID        string  `json:"id"`
	Title     raw     `json:"title"`
	Surface   *string `json:"surface"`
	Tier      raw     `json:"tier"`
	Publisher raw     `json:"publisher"`
	Version   raw     `json:"version"`
	NKinds    raw     `json:"nkinds"`
	Repo      raw     `json:"repo"`
	Install   raw     `json:"install"`
	Creds     raw     `json:"credentials"`
}

type api struct {
	API string   `json:"api"`
	TF  []string `json:"tf"`
}

type kind struct {
	ID        string  `json:"id"`
	Kind      string  `json:"kind"`
	Group     string  `json:"group"`
	Version   string  `json:"version"`
	Provider  string  `json:"provider"`
	Surface   *string `json:"surface"`
	Service   *string `json:"service"`
	Tier      *string `json:"tier"`
	Stability *string `json:"stability"`
	Readiness *string `json:"readiness"`
	Desc      *string `json:"desc"`
	API       []api   `json:"api"`

	// In the per-group shards only.
	Versions raw       `json:"versions"`
	Scope    raw       `json:"scope"`
	RepoURL  raw       `json:"repoUrl"`
	SrcURL   raw       `json:"srcUrl"`
	DocsURL  raw       `json:"docsUrl"`
	Gap      raw       `json:"gap"`
	Overlays raw       `json:"overlays"`
	Refs     raw       `json:"refs"`
	UsedBy   raw       `json:"usedBy"`
	Spec     []field   `json:"spec"`
	Status   []field   `json:"status"`
	Example  *example  `json:"example"`
	Samples  []sample  `json:"samples"`
	Related  []related `json:"related"`
}

// field is one row of a kind's spec or status: path, type, required,
// description, enum, default, immutable.
type field struct {
	P  string `json:"p"`
	T  raw    `json:"t"`
	R  raw    `json:"r"`
	D  string `json:"d"`
	E  raw    `json:"e"`
	DF raw    `json:"df"` // non-nil when the key is there, even as null
	IM raw    `json:"im"`
}

type example struct {
	Tested raw `json:"tested"`
	Line   raw `json:"line"`
	YAML   raw `json:"yaml"`
	Date   raw `json:"date"`
	Notes  raw `json:"notes"`
}

type sample struct {
	Title raw `json:"title"`
	File  raw `json:"file"`
	URL   raw `json:"url"`
	Kinds raw `json:"kinds"`
	Line  raw `json:"line"`
	YAML  raw `json:"yaml"`
}

type related struct {
	Main  string `json:"main"`
	File  raw    `json:"file"`
	URL   raw    `json:"url"`
	Kinds raw    `json:"kinds"`
	YAML  raw    `json:"yaml"`
}

type chart struct {
	ID      string
	Package string
	Name    string
	Cloud   string
	Raw     raw
}

func (c *chart) UnmarshalJSON(b []byte) error {
	var v struct{ ID, Package, Name, Cloud string }
	if err := json.Unmarshal(b, &v); err != nil {
		return err
	}
	*c = chart{v.ID, v.Package, v.Name, v.Cloud, append(raw(nil), b...)}
	return nil
}

func str(p *string) string {
	if p == nil {
		return ""
	}
	return *p
}

// truthy is Python's truth for a JSON value: not absent, null, false, 0,
// "", [] or {}.
func truthy(r raw) bool {
	s := bytes.TrimSpace(r)
	switch string(s) {
	case "", "null", "false", `""`, "[]", "{}":
		return false
	}
	if c := s[0]; c == '-' || (c >= '0' && c <= '9') {
		var f float64
		return json.Unmarshal(s, &f) != nil || f != 0
	}
	return true
}

func (k *kind) terraform() []string {
	set := map[string]bool{}
	for _, a := range k.API {
		for _, t := range a.TF {
			set[t] = true
		}
	}
	out := make([]string, 0, len(set))
	for t := range set {
		out = append(out, t)
	}
	sort.Strings(out)
	return out
}

// ---------------------------------------------------------------- the registry

type registry struct{ src *source }

// toolError is a tool's own failure (a kind not found, a bad argument):
// the agent gets it as the tool's answer, with isError.
type toolError struct{ msg string }

func (e *toolError) Error() string { return e.msg }

func errorf(format string, a ...any) error { return &toolError{fmt.Sprintf(format, a...)} }

func (r *registry) cat() (*catalog, error) {
	v, err := r.src.get("data/catalog.json", func(b []byte) (any, error) {
		var c catalog
		err := json.Unmarshal(b, &c)
		return &c, err
	})
	if err != nil {
		return nil, err
	}
	if v == nil {
		return nil, errorf("no data/catalog.json: build the site first (build.sh), or pass --url")
	}
	return v.(*catalog), nil
}

func (r *registry) shard(provider, group string) ([]kind, error) {
	v, err := r.src.get("data/k/"+provider+"/"+group+".json", func(b []byte) (any, error) {
		var ks []kind
		err := json.Unmarshal(b, &ks)
		return ks, err
	})
	if v == nil || err != nil {
		return nil, err
	}
	return v.([]kind), nil
}

type summary struct {
	ID          string   `json:"id"`
	Kind        string   `json:"kind"`
	APIVersion  string   `json:"apiVersion"`
	Provider    string   `json:"provider"`
	Cloud       *string  `json:"cloud"`
	Service     *string  `json:"service"`
	Tier        *string  `json:"tier"`
	Stability   *string  `json:"stability"`
	Readiness   string   `json:"readiness,omitempty"`
	Terraform   []string `json:"terraform"`
	Description string   `json:"description"`
}

func summarize(k *kind, tf []string) summary {
	desc := []rune(strings.Join(strings.Fields(str(k.Desc)), " "))
	if len(desc) > 240 {
		desc = desc[:240]
	}
	return summary{k.ID, k.Kind, k.Group + "/" + k.Version, k.Provider, k.Surface, k.Service, k.Tier, k.Stability,
		str(k.Readiness), tf, string(desc)}
}

// ---------------------------------------------------------------- tools

type cloud struct {
	ID        string         `json:"id"`
	Title     raw            `json:"title"`
	Category  raw            `json:"category"`
	Providers []string       `json:"providers"`
	Kinds     int            `json:"kinds"`
	Services  map[string]int `json:"services"` // marshals sorted by name
}

func (r *registry) listClouds() (any, error) {
	c, err := r.cat()
	if err != nil {
		return nil, err
	}
	services := map[string]map[string]int{}
	for _, k := range c.Kinds {
		sid := str(k.Surface)
		if services[sid] == nil {
			services[sid] = map[string]int{}
		}
		svc := str(k.Service)
		if svc == "" {
			svc = "other"
		}
		services[sid][svc]++
	}
	provs := map[string][]string{}
	for _, p := range c.Providers {
		provs[str(p.Surface)] = append(provs[str(p.Surface)], p.ID)
	}
	out := []cloud{}
	for _, s := range c.Surfaces {
		sv := services[s.ID]
		if sv == nil {
			sv = map[string]int{}
		}
		n := 0
		for _, v := range sv {
			n += v
		}
		ps := provs[s.ID]
		if ps == nil {
			ps = []string{}
		}
		out = append(out, cloud{s.ID, s.Title, s.Category, ps, n, sv})
	}
	return map[string]any{"clouds": out}, nil
}

type searchArgs struct {
	Query, Cloud, Service, Provider, Tier string
	Limit                                 *float64
}

func (r *registry) searchKinds(a searchArgs) (any, error) {
	c, err := r.cat()
	if err != nil {
		return nil, err
	}
	words := strings.Fields(strings.ToLower(a.Query))
	q, exact := strings.Join(words, ""), strings.ToLower(strings.TrimSpace(a.Query))
	type hit struct {
		score int
		k     *kind
		tf    []string
	}
	var hits []hit
	for i := range c.Kinds {
		k := &c.Kinds[i]
		if (a.Cloud != "" && str(k.Surface) != a.Cloud) || (a.Service != "" && str(k.Service) != a.Service) ||
			(a.Provider != "" && k.Provider != a.Provider) || (a.Tier != "" && str(k.Tier) != a.Tier) {
			continue
		}
		tf := k.terraform()
		hay := strings.ToLower(strings.Join(append([]string{k.Kind, k.Group, str(k.Service), str(k.Desc)}, tf...), " "))
		match := true
		for _, w := range words {
			if !strings.Contains(hay, w) {
				match = false
				break
			}
		}
		if !match {
			continue
		}
		// Exact kind or Terraform type first, then a kind that ends in the
		// word (StorageBucket for "bucket"), then a kind that has it; official
		// and live-tested kinds before others, shorter names before longer.
		kl, score := strings.ToLower(k.Kind), 0
		if q != "" {
			if kl == q {
				score += 20
			} else {
				for _, t := range tf {
					if t == exact {
						score += 20
						break
					}
				}
			}
		}
		for _, w := range words {
			switch {
			case strings.HasSuffix(kl, w):
				score += 6
			case strings.Contains(kl, w):
				score += 3
			default:
				score++
			}
		}
		if str(k.Tier) == "official" {
			score += 2
		}
		if str(k.Readiness) == "live" {
			score++
		}
		hits = append(hits, hit{score, k, tf})
	}
	sort.SliceStable(hits, func(i, j int) bool {
		a, b := hits[i], hits[j]
		if a.score != b.score {
			return a.score > b.score
		}
		if len(a.k.Kind) != len(b.k.Kind) {
			return len(a.k.Kind) < len(b.k.Kind)
		}
		return a.k.Kind < b.k.Kind
	})
	limit := 25
	if a.Limit != nil {
		limit = max(1, min(int(*a.Limit), 200))
	}
	out := []summary{}
	for _, h := range hits[:min(limit, len(hits))] {
		out = append(out, summarize(h.k, h.tf))
	}
	return map[string]any{"count": len(hits), "kinds": out}, nil
}

// find names a kind by id (provider/group/Kind), group/Kind or Kind.
func (r *registry) find(name string) (*kind, error) {
	c, err := r.cat()
	if err != nil {
		return nil, err
	}
	name = strings.TrimSpace(name)
	var hit []*kind
	for _, match := range []func(k *kind) bool{
		func(k *kind) bool { return k.ID == name },
		func(k *kind) bool { return k.Group+"/"+k.Kind == name },
		func(k *kind) bool { return strings.EqualFold(k.Kind, name) },
	} {
		for i := range c.Kinds {
			if match(&c.Kinds[i]) {
				hit = append(hit, &c.Kinds[i])
			}
		}
		if len(hit) > 0 {
			break
		}
	}
	switch len(hit) {
	case 0:
		return nil, errorf("no kind %q; search_kinds finds them", name)
	case 1:
		return hit[0], nil
	}
	ids := []string{}
	for _, k := range hit[:min(20, len(hit))] {
		ids = append(ids, k.ID)
	}
	return nil, errorf("%q is in more than one provider or group; name one of: %s", name, strings.Join(ids, ", "))
}

type fieldOut struct {
	Path        string `json:"path"`
	Type        raw    `json:"type"`
	Required    bool   `json:"required"`
	Description string `json:"description"`
	Enum        raw    `json:"enum,omitempty"`
	Default     *raw   `json:"default,omitempty"`
	Immutable   bool   `json:"immutable,omitempty"`
}

type exampleOut struct {
	Tested   raw `json:"tested"`
	Line     raw `json:"line"`
	Manifest raw `json:"manifest"`
	LiveDate raw `json:"liveDate,omitempty"`
	Notes    raw `json:"notes,omitempty"`
}

type sampleOut struct {
	Title    raw `json:"title"`
	File     raw `json:"file"`
	URL      raw `json:"url"`
	Kinds    raw `json:"kinds"`
	Line     raw `json:"line"`
	Manifest raw `json:"manifest"`
}

type relatedOut struct {
	File     raw `json:"file"`
	URL      raw `json:"url"`
	Kinds    raw `json:"kinds"`
	Manifest raw `json:"manifest"`
}

type kindOut struct {
	summary
	Versions         raw           `json:"versions"`
	Scope            raw           `json:"scope"`
	Repo             raw           `json:"repo"`
	CRD              raw           `json:"crd"`
	Docs             raw           `json:"docs"`
	APIPaths         []string      `json:"apiPaths"`
	GapItFills       raw           `json:"gapItFills,omitempty"`
	Overlays         raw           `json:"overlays,omitempty"`
	References       raw           `json:"references,omitempty"`
	UsedByPackages   raw           `json:"usedByPackages,omitempty"`
	Spec             []fieldOut    `json:"spec"`
	Status           []fieldOut    `json:"status"`
	Example          *exampleOut   `json:"example,omitempty"`
	UpstreamSamples  []sampleOut   `json:"upstreamSamples,omitempty"`
	WithRelated      *[]relatedOut `json:"withRelated,omitempty"`
	UsedInExamplesOf *[]string     `json:"usedInExamplesOf,omitempty"`
}

// ifTruthy keeps r when Python would have (a non-empty value), else drops it.
func ifTruthy(r raw) raw {
	if truthy(r) {
		return r
	}
	return nil
}

func (r *registry) getKind(name string, fields []string) (any, error) {
	k, err := r.find(name)
	if err != nil {
		return nil, err
	}
	shard, err := r.shard(k.Provider, k.Group)
	if err != nil {
		return nil, err
	}
	full := k
	for i := range shard {
		if shard[i].Kind == k.Kind {
			full = &shard[i]
			break
		}
	}
	paths := map[string]bool{}
	for _, a := range full.API {
		if a.API != "" {
			paths[a.API] = true
		}
	}
	out := kindOut{
		summary: summarize(full, full.terraform()), Versions: full.Versions, Scope: full.Scope,
		Repo: full.RepoURL, CRD: full.SrcURL, Docs: full.DocsURL, APIPaths: []string{},
		GapItFills: ifTruthy(full.Gap), Overlays: ifTruthy(full.Overlays), References: ifTruthy(full.Refs),
		UsedByPackages: ifTruthy(full.UsedBy),
	}
	for p := range paths {
		out.APIPaths = append(out.APIPaths, p)
	}
	sort.Strings(out.APIPaths)

	rows := func(rs []field) []fieldOut {
		sel := []fieldOut{}
		for _, f := range rs {
			path := f.P
			if i := strings.IndexByte(path, '.'); i >= 0 {
				path = path[i+1:]
			}
			keep := len(fields) == 0
			for _, pre := range fields {
				if strings.HasPrefix(path, pre) {
					keep = true
					break
				}
			}
			if !keep {
				continue
			}
			row := fieldOut{Path: path, Type: f.T, Required: truthy(f.R), Description: f.D, Enum: ifTruthy(f.E), Immutable: truthy(f.IM)}
			if f.DF != nil {
				df := f.DF
				row.Default = &df
			}
			sel = append(sel, row)
		}
		return sel
	}
	out.Spec, out.Status = rows(full.Spec), rows(full.Status)

	if ex := full.Example; ex != nil {
		out.Example = &exampleOut{ex.Tested, ex.Line, ex.YAML, ifTruthy(ex.Date), ifTruthy(ex.Notes)}
	}
	for _, s := range full.Samples {
		out.UpstreamSamples = append(out.UpstreamSamples, sampleOut{s.Title, s.File, s.URL, s.Kinds, s.Line, s.YAML})
	}
	if len(full.Related) > 0 {
		with, used := []relatedOut{}, []string{}
		for _, rel := range full.Related {
			if rel.Main == full.Kind {
				with = append(with, relatedOut{rel.File, rel.URL, rel.Kinds, rel.YAML})
			} else {
				used = append(used, rel.Main)
			}
		}
		out.WithRelated, out.UsedInExamplesOf = &with, &used
	}
	return out, nil
}

type tfKind struct {
	ID         string  `json:"id"`
	Kind       string  `json:"kind"`
	APIVersion string  `json:"apiVersion"`
	Provider   string  `json:"provider"`
	Tier       *string `json:"tier"`
	Stability  *string `json:"stability"`
	Readiness  *string `json:"readiness"`
}

func (r *registry) kindsForTerraform(types []string) (any, error) {
	c, err := r.cat()
	if err != nil {
		return nil, err
	}
	out := map[string][]tfKind{}
	for _, t := range types {
		out[t] = []tfKind{}
	}
	for i := range c.Kinds {
		k := &c.Kinds[i]
		for _, a := range k.API {
			for _, t := range a.TF {
				if _, ok := out[t]; ok {
					out[t] = append(out[t], tfKind{k.ID, k.Kind, k.Group + "/" + k.Version, k.Provider, k.Tier, k.Stability, k.Readiness})
				}
			}
		}
	}
	return map[string]any{"kinds": out}, nil
}

type providerOut struct {
	ID        string  `json:"id"`
	Title     raw     `json:"title"`
	Cloud     *string `json:"cloud"`
	Tier      raw     `json:"tier"`
	Publisher raw     `json:"publisher"`
	Version   raw     `json:"version"`
	Kinds     raw     `json:"kinds"`
	Repo      raw     `json:"repo"`
	Install   raw     `json:"install,omitempty"`     // [{name, pick, when, note, tested, steps, source}]
	Creds     raw     `json:"credentials,omitempty"` // same shape: the identity to set up first
}

func (r *registry) listProviders() (any, error) {
	c, err := r.cat()
	if err != nil {
		return nil, err
	}
	out := []providerOut{}
	for _, p := range c.Providers {
		out = append(out, providerOut{p.ID, p.Title, p.Surface, p.Tier, p.Publisher, p.Version, p.NKinds, p.Repo, nullless(p.Install), nullless(p.Creds)})
	}
	return map[string]any{"providers": out}, nil
}

// nullless drops a JSON null, so omitempty leaves the field out.
func nullless(r raw) raw {
	if string(r) == "null" {
		return nil
	}
	return r
}

// object is a JSON object that keeps its keys' order.
type object []struct {
	k string
	v raw
}

func (o object) MarshalJSON() ([]byte, error) {
	var b bytes.Buffer
	b.WriteByte('{')
	for i, kv := range o {
		if i > 0 {
			b.WriteByte(',')
		}
		k, _ := json.Marshal(kv.k)
		b.Write(k)
		b.WriteByte(':')
		b.Write(kv.v)
	}
	b.WriteByte('}')
	return b.Bytes(), nil
}

func (r *registry) getPackage(name string) (any, error) {
	c, err := r.cat()
	if err != nil {
		return nil, err
	}
	var ch *chart
	for i := range c.Charts {
		if x := &c.Charts[i]; name == x.ID || name == x.Package || name == x.Name {
			ch = x
			break
		}
	}
	if ch == nil {
		ids := []string{}
		for _, x := range c.Charts[:min(8, len(c.Charts))] {
			ids = append(ids, x.ID)
		}
		return nil, errorf("no package %q; the catalog has %d, e.g. %s", name, len(c.Charts), strings.Join(ids, ", "))
	}
	v, err := r.src.get("data/charts/"+ch.Cloud+"-"+ch.Name+".json", func(b []byte) (any, error) {
		var m map[string]raw
		err := json.Unmarshal(b, &m)
		return m, err
	})
	if err != nil {
		return nil, err
	}
	full, _ := v.(map[string]raw)
	if full == nil {
		if err := json.Unmarshal(ch.Raw, &full); err != nil {
			return nil, &sourceError{err}
		}
	}
	out := object{}
	for _, key := range []string{"id", "package", "version", "desc", "module", "upstream", "uses", "values", "outputs", "providers", "refused"} {
		if v, ok := full[key]; ok {
			out = append(out, struct {
				k string
				v raw
			}{key, v})
		}
	}
	return out, nil
}
