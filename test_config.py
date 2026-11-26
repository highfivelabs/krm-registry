"""config.load: a provider repo's krm-registry.yaml, and registry.yaml's override.
python3 -m unittest test_config"""
import os, tempfile, textwrap, unittest

import config


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(textwrap.dedent(text))


class Load(unittest.TestCase):
    def setUp(self):
        self.src = tempfile.mkdtemp()

    def by_id(self):
        return {p["id"]: p for p in config.load(self.src)["providers"]}

    def test_repo_file_supplies_install(self):
        write(f"{self.src}/github-krm-extensions/krm-registry.yaml", """
            schema: 1
            install:
              - name: High Five Labs chart
                oci: charts/github-krm-extensions
                steps: helm install x oci://cr.highfivelabs.dev/charts/github-krm-extensions --version {version}
            """)
        p = self.by_id()["github-krm-extensions"]
        self.assertEqual(p["install"][0]["oci"], "charts/github-krm-extensions")
        self.assertEqual(p["install_source"], "repo")

    def test_registry_yaml_overrides_and_others_untouched(self):
        ps = self.by_id()
        self.assertEqual(ps["config-connector-operator"]["install_source"], "registry")
        self.assertNotIn("install", ps["github-krm-extensions"])  # no file, nothing invented

    def test_only_our_repos_are_read(self):
        # A file under a third-party repo's name is never consulted.
        write(f"{self.src}/grafana-operator/krm-registry.yaml", "install: [{name: x, steps: y}]\n")
        self.assertEqual(self.by_id()["grafana-operator"]["install"][0]["name"], "Helm (OCI)")

    def test_unknown_schema_fails(self):
        write(f"{self.src}/okta-krm-extensions/krm-registry.yaml", "schema: 2\n")
        with self.assertRaises(SystemExit):
            config.load(self.src)


if __name__ == "__main__":
    unittest.main()
