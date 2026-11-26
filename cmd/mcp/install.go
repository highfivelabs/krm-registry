// Copyright 2026 High Five Labs
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"encoding/json"
	"regexp"
	"sort"
	"strings"
)

// variant is one install or credentials entry of a provider (gen.py's install_of).
type variant struct {
	Name   string            `json:"name"`
	Pick   map[string]string `json:"pick,omitempty"`
	When   string            `json:"when,omitempty"`
	Note   string            `json:"note,omitempty"`
	Tested string            `json:"tested,omitempty"`
	Verify string            `json:"verify,omitempty"`
	Steps  string            `json:"steps"`
	Source string            `json:"source,omitempty"`
}

func (v variant) picks() map[string]string {
	if len(v.Pick) > 0 {
		return v.Pick
	}
	return map[string]string{"variant": v.Name}
}

// matches: v agrees with every axis of choice that v names.
func (v variant) matches(choice map[string]string) bool {
	for k, x := range v.picks() {
		if c, ok := choice[k]; ok && c != x {
			return false
		}
	}
	return true
}

type placeholder struct {
	Name    string `json:"name"`
	Example string `json:"example,omitempty"` // the value the steps set it to, to be replaced
}

type installPlan struct {
	Provider     string              `json:"provider"`
	Title        raw                 `json:"title"`
	Via          string              `json:"via,omitempty"` // the provider asked for is installed by this one
	Published    bool                `json:"published"`
	Pending      []string            `json:"pending,omitempty"`
	Choices      map[string][]string `json:"choices,omitempty"` // every axis and its values
	Chosen       map[string]string   `json:"chosen,omitempty"`
	Credentials  *variant            `json:"credentials,omitempty"`
	Install      *variant            `json:"install,omitempty"`
	Placeholders []placeholder       `json:"placeholders,omitempty"`
	Namespace    string              `json:"namespace,omitempty"`
	Verify       []string            `json:"verify,omitempty"`
	Page         string              `json:"page"`
	Markdown     string              `json:"markdown"`
	HowToRun     []string            `json:"how_to_run"`
}

// howToRun is how an agent carries a plan out with a person.
var howToRun = []string{
	"Confirm the target with the user first: the kube context (kubectl config current-context) and, where there are choices, which one fits their cluster; call get_install again with choice if it differs from chosen.",
	"Ask the user for each placeholder's real value; never keep an example value (my-project, my-cluster).",
	"Show each command before running it. The credentials steps change cloud IAM (service accounts, role grants, keys): run them only with the user's explicit go-ahead, or hand them to the user to run.",
	"Run credentials first, then install, stopping at the first error and reporting it with its output.",
	"Then run the verify commands and report what they show.",
}

var (
	assignRE = regexp.MustCompile(`(?:^|[\s;])([A-Z][A-Z0-9_]{2,})=([^\s\\;]+)`)
	useRE    = regexp.MustCompile(`\$\{?([A-Z][A-Z0-9_]{2,})\}?`)
	nsRE     = regexp.MustCompile(`--namespace[ =]([a-z0-9][a-z0-9-]*)`)
	readRE   = regexp.MustCompile(`\bread(?:\s+-r)?((?:\s+[A-Z][A-Z0-9_]*)+)`)
)

// placeholders are the shell variables the steps set to examples or use unset.
func placeholders(steps ...string) []placeholder {
	seen := map[string]int{}
	var out []placeholder
	for _, s := range steps {
		// Set by the steps themselves: a read loop's variables, a command's output.
		for _, m := range readRE.FindAllStringSubmatch(s, -1) {
			for _, n := range strings.Fields(m[1]) {
				seen[n] = -1
			}
		}
		for _, m := range assignRE.FindAllStringSubmatch(s, -1) {
			if strings.HasPrefix(m[2], "$") {
				seen[m[1]] = -1
			}
		}
	}
	for _, s := range steps {
		for _, m := range assignRE.FindAllStringSubmatch(s, -1) {
			if _, ok := seen[m[1]]; !ok {
				seen[m[1]] = len(out)
				out = append(out, placeholder{m[1], m[2]})
			}
		}
		for _, m := range useRE.FindAllStringSubmatch(s, -1) {
			if _, ok := seen[m[1]]; !ok {
				seen[m[1]] = len(out)
				out = append(out, placeholder{Name: m[1]})
			}
		}
	}
	return out
}

func (r *registry) getInstall(id string, choice map[string]string) (any, error) {
	c, err := r.cat()
	if err != nil {
		return nil, err
	}
	var p *provider
	for i := range c.Providers {
		if c.Providers[i].ID == id {
			p = &c.Providers[i]
		}
	}
	if p == nil {
		return nil, errorf("get_install: no provider %q (list_providers lists them)", id)
	}
	plan := installPlan{Provider: p.ID, Title: p.Title}
	// A provider installed by another (Config Connector, by its operator).
	if !hasSteps(p) && len(p.Pending) == 0 {
		for i := range c.Providers {
			if q := &c.Providers[i]; q.Installs != nil && *q.Installs == id && hasSteps(q) {
				plan.Via, p = q.ID, q
				break
			}
		}
	}
	plan.Page = "https://registry.highfivelabs.dev/#pr-" + p.ID
	plan.Markdown = "https://registry.highfivelabs.dev/providers/" + p.ID + ".md"
	plan.HowToRun = howToRun
	var ins, creds []variant
	if err := decodeRaw(p.Install, &ins); err != nil {
		return nil, err
	}
	if err := decodeRaw(p.Creds, &creds); err != nil {
		return nil, err
	}
	if len(ins) == 0 {
		plan.Pending = p.Pending
		if len(p.Pending) == 0 {
			return nil, errorf("get_install: the registry has no install steps for %s", id)
		}
		return plan, nil // not published yet: nothing to run
	}
	plan.Published = true

	// Axes, in the order the variants name them, and the choice: the
	// caller's where it names a value that exists, else the first.
	plan.Choices = map[string][]string{}
	var axes []string
	for _, v := range ins {
		for k, x := range v.picks() {
			if _, ok := plan.Choices[k]; !ok {
				axes = append(axes, k)
			}
			if !contains(plan.Choices[k], x) {
				plan.Choices[k] = append(plan.Choices[k], x)
			}
		}
	}
	sort.Strings(axes)
	for k, x := range choice {
		if vals, ok := plan.Choices[k]; !ok || !contains(vals, x) {
			return nil, errorf("get_install: %s has no choice %s=%q; choices: %s", p.ID, k, x, mustJSON(plan.Choices))
		}
	}
	var pick *variant
	for i := range ins {
		if ins[i].matches(choice) {
			pick = &ins[i]
			break
		}
	}
	if pick == nil {
		return nil, errorf("get_install: no %s variant has %s; choices: %s", p.ID, mustJSON(choice), mustJSON(plan.Choices))
	}
	plan.Chosen = pick.picks()
	plan.Install = pick
	for i := range creds {
		if creds[i].matches(plan.Chosen) {
			plan.Credentials = &creds[i]
			break
		}
	}
	steps := []string{pick.Steps}
	if plan.Credentials != nil {
		steps = append([]string{plan.Credentials.Steps}, steps...)
	}
	plan.Placeholders = placeholders(steps...)
	if pick.Verify != "" {
		plan.Verify = strings.Split(strings.TrimSpace(pick.Verify), "\n")
	}
	if m := nsRE.FindStringSubmatch(pick.Steps); m != nil && pick.Verify == "" {
		plan.Namespace = m[1]
		plan.Verify = []string{"kubectl get pods --namespace " + m[1], "helm list --namespace " + m[1]}
		if !strings.Contains(pick.Steps, "helm ") {
			plan.Verify = plan.Verify[:1]
		}
	}
	return plan, nil
}

func hasSteps(p *provider) bool { return len(nullless(p.Install)) > 0 }

func decodeRaw(r raw, v any) error {
	if len(nullless(r)) == 0 {
		return nil
	}
	return json.Unmarshal(r, v)
}

func contains(xs []string, x string) bool {
	for _, y := range xs {
		if y == x {
			return true
		}
	}
	return false
}

func mustJSON(v any) string { b, _ := json.Marshal(v); return string(b) }
