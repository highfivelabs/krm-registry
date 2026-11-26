"""registry.yaml, with what each of our provider repositories says about itself.

A provider repository we own may carry a `krm-registry.yaml` at its root (the
registry's build clones it into <src>/<repo>/). Today it carries one field:

    schema: 1
    install:            # the provider page's Install card, one entry per variant
      - name: High Five Labs chart
        oci: charts/<chart>          # optional: shown only while cr.highfivelabs.dev serves it
        when: which clusters this variant is for
        note: one paragraph shown under the steps
        tested: how and when these steps were last run
        steps: |
          helm install ... --version {version}
    credentials:        # shown above the install steps: the identity the controller acts as
      - pick: {Cluster: GKE Standard}  # matched to the install variant on shared axes
        steps: |
          gcloud iam service-accounts create ...

registry.yaml is read first and wins: a provider whose entry there has its own
`install:` keeps it, which is how the registry describes the repositories we
do not control (upstream and vendor operators), and how it overrides one of
ours that is wrong.
"""
import os

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
OURS = "https://github.com/highfivelabs/"
FROM_REPO = ("install", "credentials")  # fields a provider repository may set for itself


def repo_file(src, prov):
    """The provider's own krm-registry.yaml under src, or None (not ours, not cloned, none)."""
    repo = prov.get("repo") or ""
    if not repo.startswith(OURS):
        return None
    f = os.path.join(src, repo[len(OURS):].strip("/"), "krm-registry.yaml")
    return f if os.path.exists(f) else None


def load(src, loader=yaml.SafeLoader):
    """registry.yaml with each owned provider's krm-registry.yaml folded in.
    Each field gains <field>_source: "registry" (registry.yaml) or "repo"."""
    cfg = yaml.load(open(os.path.join(HERE, "registry.yaml")), Loader=loader)
    for p in cfg["providers"]:
        for k in FROM_REPO:
            if p.get(k):
                p[k + "_source"] = "registry"
        f = repo_file(src, p)
        if not f:
            continue
        own = yaml.load(open(f), Loader=loader) or {}
        if own.get("schema", 1) != 1:
            raise SystemExit(f"{f}: schema {own.get('schema')} (this build reads 1)")
        for k in FROM_REPO:
            if own.get(k) and not p.get(k):
                p[k] = own[k]
                p[k + "_source"] = "repo"
    return cfg
