#!/usr/bin/env python3
"""Find the versions the registry should document, by asking the sources' APIs.

  resolve.py <src> <lock.json>

<src> holds the clones of the repos we own (the extension repos and krm-packages).
Writes a lock file naming exactly what to fetch and what each chart's
upstream module has released since its pin:

  providers   latest release of Config Connector and ASO; every ACK controller
              with a release, at its latest release
  modules     for each chart: its pin, and the newest release upstream
              (Terraform Registry API when the module is published there,
              GitHub releases otherwise)

APIs first: GitHub's REST API with $GITHUB_TOKEN (Actions provides one), the
Terraform Registry's public API. Without a token it falls back to reading tags
over git (`git ls-remote`), which needs no API at all, and to the ACK
controller list that ack-extensions pins.
"""
import glob, json, os, re, subprocess, sys, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

import yaml

SRC, LOCK = sys.argv[1], sys.argv[2]
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
WARNED = set()


def warn(what, e):
    if type(e).__name__ + str(getattr(e, "code", "")) not in WARNED:
        WARNED.add(type(e).__name__ + str(getattr(e, "code", "")))
        print(f"{what}: {e}; falling back to git tags (further fallbacks not shown)", file=sys.stderr)
SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def get(url, auth=False):
    h = {"Accept": "application/json", "User-Agent": "krm-registry"}
    if auth and TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
        return json.load(r)


def gh(path):
    return get("https://api.github.com/" + path, auth=True)


def owner_repo(url):
    m = re.search(r"github\.com/([^/]+)/([^/.]+)", url)
    return m.group(1), m.group(2)


def git_tags(url, pattern=None):
    """{tag: commit} for release tags (semver, or matching pattern), by git (no API)."""
    out = subprocess.run(["git", "ls-remote", "--tags", url], capture_output=True, text=True, timeout=120).stdout
    tags = {}
    for line in out.splitlines():
        sha, ref = line.split("\t")
        name = ref.removeprefix("refs/tags/")
        deref = name.endswith("^{}")
        name = name.removesuffix("^{}")
        ok = re.match(pattern, name) if pattern else SEMVER.match(name)
        if ok and (deref or name not in tags):
            tags[name] = sha
    return tags


def version_key(tag):
    return tuple(int(x) for x in re.findall(r"[0-9]+", tag))


def latest_tag(repo, pattern):
    """Newest release tag matching pattern: GitHub's Releases API for GitHub repos with a
    token, git tags otherwise (and for GitLab)."""
    if TOKEN and "github.com" in repo:
        try:
            o, r = owner_repo(repo)
            rels = [x for x in gh(f"repos/{o}/{r}/releases?per_page=100") if not x["draft"] and not x["prerelease"] and re.match(pattern, x["tag_name"])]
            if rels:
                best = max(rels, key=lambda x: version_key(x["tag_name"]))
                ref = gh(f"repos/{o}/{r}/commits/{best['tag_name']}")
                return {"tag": best["tag_name"], "commit": ref["sha"], "published": best["published_at"][:10], "via": "github-api"}
        except Exception as e:
            warn(f"github api {repo}", e)
    tags = git_tags(repo + ".git", pattern)
    if not tags:
        return None
    t = max(tags, key=version_key)
    return {"tag": t, "commit": tags[t], "via": "git-tags"}


def latest_gcs(bucket, pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$"):
    """Newest version in a public Cloud Storage bucket whose top-level folders are
    versions (Config Connector's operator: gs://configconnector-operator/1.162.0/)."""
    base = f"https://storage.googleapis.com/storage/v1/b/{bucket}/o"
    prefixes, token = [], None
    while True:
        q = {"delimiter": "/", "fields": "prefixes,nextPageToken"} | ({"pageToken": token} if token else {})
        req = urllib.request.Request(base + "?" + urllib.parse.urlencode(q), headers={"User-Agent": "krm-registry"})
        d = json.load(urllib.request.urlopen(req, timeout=60))
        prefixes += [x.rstrip("/") for x in d.get("prefixes", [])]
        token = d.get("nextPageToken")
        if not token:
            break
    versions = [v for v in prefixes if re.match(pattern, v)]
    if not versions:
        return None
    v = max(versions, key=version_key)
    req = urllib.request.Request(base + "?" + urllib.parse.urlencode({"prefix": v + "/", "fields": "items(name,updated)"}),
                                 headers={"User-Agent": "krm-registry"})
    items = json.load(urllib.request.urlopen(req, timeout=60)).get("items", [])
    published = max((i["updated"][:10] for i in items), default=None)
    return {"tag": v, "commit": None, "published": published, "via": f"gcs:{bucket}"}


def terraform_provider(name, top):
    """Rank, downloads, latest version and resource list of a Terraform provider (Registry API)."""
    p = next((x for x in top if x["name"].lower() == name.lower()), None)
    if p is None:
        return None
    d = get(f"https://registry.terraform.io/v2/providers/{p['id']}?include=provider-versions")
    v = max(d["included"], key=lambda v: v["attributes"]["published-at"])
    res, page = [], 1
    while True:
        q = urllib.parse.urlencode({"filter[provider-version]": v["id"], "filter[category]": "resources", "page[size]": 100, "page[number]": page})
        dd = get("https://registry.terraform.io/v2/provider-docs?" + q)
        res += [x["attributes"]["slug"] for x in dd["data"] if x["attributes"].get("language", "hcl") == "hcl"]
        if len(dd["data"]) < 100:
            break
        page += 1
    prefix = name.split("/")[1].lower()
    return {"name": p["name"], "rank": p["rank"], "downloads": p["downloads"], "tier": p["tier"],
            "version": v["attributes"]["version"], "resources": sorted({s if s.startswith(prefix + "_") else f"{prefix}_{s}" for s in res})}


def terraform_top(n=200):
    out = []
    for page in range(1, n // 100 + 1):
        d = get(f"https://registry.terraform.io/v2/providers?page%5Bsize%5D=100&page%5Bnumber%5D={page}&sort=-downloads")
        for p in d["data"]:
            a = p["attributes"]
            out.append({"id": p["id"], "name": a["full-name"], "downloads": a["downloads"], "tier": a["tier"],
                        "rank": len(out) + 1, "description": (a.get("description") or "")[:160]})
    return out


def newest(tags, prefix=None):
    cands = [t for t in tags if not prefix or t.startswith(prefix)]
    return max(cands, key=lambda t: tuple(map(int, SEMVER.match(t).groups()))) if cands else None


def latest_release(url, prefix=None):
    """{tag, commit, published, via}: the newest stable release."""
    o, r = owner_repo(url)
    if TOKEN:
        try:
            rels = [x for x in gh(f"repos/{o}/{r}/releases?per_page=50") if not x["draft"] and not x["prerelease"]
                    and SEMVER.match(x["tag_name"]) and (not prefix or x["tag_name"].startswith(prefix))]
            if rels:
                best = max(rels, key=lambda x: tuple(map(int, SEMVER.match(x["tag_name"]).groups())))
                ref = gh(f"repos/{o}/{r}/commits/{best['tag_name']}")
                return {"tag": best["tag_name"], "commit": ref["sha"], "published": best["published_at"][:10],
                        "assets": [a["name"] for a in best.get("assets", [])], "via": "github-api"}
        except Exception as e:  # rate limit, private repo: fall back to git
            warn(f"github api {o}/{r}", e)
    tags = git_tags(url)
    t = newest(tags, prefix)
    return {"tag": t, "commit": tags.get(t), "via": "git-tags"} if t else None


def ack_controllers():
    if TOKEN:
        try:
            names, page = [], 1
            while True:
                batch = gh(f"orgs/aws-controllers-k8s/repos?per_page=100&page={page}")
                names += [x["name"] for x in batch if x["name"].endswith("-controller") and not x["archived"]]
                if len(batch) < 100:
                    return sorted(names), "github-api"
                page += 1
        except Exception as e:
            print(f"github api org listing: {e}; using ack-extensions' list", file=sys.stderr)
    d = yaml.safe_load(open(f"{SRC}/ack-extensions/docs/coverage/sources.yaml"))
    return sorted(c["name"] + "-controller" for c in d["ack"]["controllers"]), "ack-extensions sources.yaml"


def module_latest(up):
    reg = up.get("registry_url")
    if reg:
        m = re.search(r"registry\.terraform\.io/modules/([^/]+)/([^/]+)/([^/?#]+)", reg)
        try:
            d = get(f"https://registry.terraform.io/v1/modules/{m.group(1)}/{m.group(2)}/{m.group(3)}")
            return {"version": "v" + d["version"].lstrip("v"), "published": d["published_at"][:10], "via": "terraform-registry-api"}
        except Exception as e:
            print(f"registry {reg}: {e}", file=sys.stderr)
    rel = latest_release(up["repo"])
    return rel and {"version": rel["tag"], "published": rel.get("published"), "via": rel["via"]}


def main():
    lock = {"providers": {}, "modules": {}}
    kcc = latest_release("https://github.com/GoogleCloudPlatform/k8s-config-connector")
    aso = latest_release("https://github.com/Azure/azure-service-operator", prefix="v2.")
    aso["crds_asset"] = f"azureserviceoperator_customresourcedefinitions_{aso['tag']}.yaml"
    lock["providers"]["config-connector"] = kcc
    lock["providers"]["aso"] = aso
    names, via = ack_controllers()
    with ThreadPoolExecutor(16) as ex:
        rels = list(ex.map(lambda n: (n, latest_release(f"https://github.com/aws-controllers-k8s/{n}")), names))
    lock["providers"]["ack"] = {"listed_via": via, "controllers": [dict(repo=n, **r) for n, r in rels if r]}

    ups = {}
    for f in sorted(glob.glob(f"{SRC}/krm-packages/charts/*/*/upstream.yaml")):
        cloud, name = f.split("/")[-3:-1]
        ups[f"{cloud}/{name}"] = yaml.safe_load(open(f))
    repos = {}
    for up in ups.values():  # one lookup per repo or registry module, not per chart
        repos.setdefault(up.get("registry_url") or up["repo"], up)
    with ThreadPoolExecutor(16) as ex:
        latest = dict(zip(repos, ex.map(module_latest, repos.values())))
    for cid, up in ups.items():
        lt = latest.get(up.get("registry_url") or up["repo"]) or {}
        pin = up.get("version")
        behind = bool(lt.get("version") and SEMVER.match(lt["version"]) and SEMVER.match(pin or "")
                      and tuple(map(int, SEMVER.match(lt["version"]).groups())) > tuple(map(int, SEMVER.match(pin).groups())))
        lock["modules"][cid] = {"pinned": pin, "latest": lt.get("version"), "published": lt.get("published"),
                                "via": lt.get("via"), "behind": behind}
    # Operators and other providers that name a release pattern.
    cfg = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "registry.yaml")))
    for p in cfg["providers"]:
        if p.get("release"):
            r = p["release"]
            if "gcs" in r:
                try:
                    rel = latest_gcs(r["gcs"], r.get("versions", r"^[0-9]+\.[0-9]+\.[0-9]+$"))
                except Exception as e:
                    warn(f"gcs {r['gcs']}", e)
                    rel = None
            else:
                rel = latest_tag(p["repo"], r["tags"])
            if rel:
                lock["providers"][p["id"]] = rel
            else:
                print(f"{p['id']}: no release matching {p['release']}", file=sys.stderr)

    # Terraform, by the numbers: every surface's provider, and the top providers overall.
    top = terraform_top()
    lock["terraform"] = {"top": top, "surfaces": {}}
    for sid, s in cfg["surfaces"].items():
        if s.get("terraform"):
            lock["terraform"]["surfaces"][sid] = terraform_provider(s["terraform"], top)

    json.dump(lock, open(LOCK, "w"), indent=1)
    n = len(lock["providers"]["ack"]["controllers"])
    print(f"kcc {kcc['tag']} · aso {aso['tag']} · {n} ACK controllers · "
          f"{sum(m['behind'] for m in lock['modules'].values())}/{len(ups)} modules behind", file=sys.stderr)


if __name__ == "__main__":
    main()
