"""paths: the registry's URLs, and the check gen.py runs on them.
python3 -m unittest test_paths"""
import unittest

import paths

OPS = [{"id": "config-connector", "publisher": "Google"},
       {"id": "config-connector-extensions", "publisher": "High Five Labs"},
       {"id": "ack", "publisher": "Amazon Web Services", "namespace": "aws"},
       {"id": "stringke", "publisher": "StringKe (community)"},
       {"id": "adyanth", "publisher": "adyanth (community)"}]


def k(op, group, kind, provider="gcp", service="storage"):
    return {"id": f"{op}/{group}/{kind}", "operator": op, "group": group, "kind": kind, "provider": provider, "service": service}


class Paths(unittest.TestCase):
    def test_namespace_is_the_publisher(self):
        self.assertEqual([paths.namespace(o) for o in OPS], ["google", "highfivelabs", "aws", "stringke", "adyanth"])

    def test_pages(self):
        self.assertEqual(paths.kind(k("config-connector", "storage.cnrm.cloud.google.com", "StorageBucket"), "google"),
                         "/gcp/storage/google/StorageBucket")
        self.assertEqual(paths.service("cloudflare", "zero_trust"), "/cloudflare/zero-trust")
        self.assertEqual(paths.operator(OPS[2]), "/operators/aws/ack")
        self.assertEqual(paths.chart({"cloud": "aws", "name": "acm"}), "/packages/aws/acm")

    def test_publisher_tells_same_named_kinds_apart(self):
        ks = [k("stringke", "a.example", "Tunnel", "cloudflare", "zero_trust"), k("adyanth", "b.example", "Tunnel", "cloudflare", "zero_trust")]
        self.assertEqual(paths.check({"cloudflare": {}}, OPS, ks, []), [])

    def test_one_operator_two_groups_qualifies_the_longer(self):
        ks = [k("config-connector-extensions", "autoscaling.cloudcontrol.aws.hfl.dev", "LifecycleHook", "aws", "autoscaling"),
              k("config-connector-extensions", "autoscaling.aws.hfl.dev", "LifecycleHook", "aws", "autoscaling")]
        nss = {o["id"]: paths.namespace(o) for o in OPS}
        self.assertTrue(paths.check({"aws": {}}, OPS, ks, []))  # unqualified, they collide
        paths.qualify(ks, nss)
        self.assertEqual([paths.kind(x, "highfivelabs") for x in ks],
                         ["/aws/autoscaling/highfivelabs/LifecycleHook.autoscaling.cloudcontrol.aws.hfl.dev",
                          "/aws/autoscaling/highfivelabs/LifecycleHook"])
        self.assertEqual(paths.check({"aws": {}}, OPS, ks, []), [])

    def test_provider_cannot_take_a_section(self):
        self.assertEqual(paths.check({"operators": {}}, OPS, [], []), ["provider operators: takes a section's path"])


if __name__ == "__main__":
    unittest.main()
