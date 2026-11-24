"""The surfaces capability: what a manifest may ask for, and what it implies.

The capability grants the `surfaces.open` bridge operation. It deliberately
does *not* require an http connection in the manifest: a manifest-level hard
requirement would make the two declarations one, and an app with `surfaces`
but no connection fails cleanly at runtime instead — the open operation and
the proxy routes answer with a clear error. Everything uses disposable data.
"""
import copy
import unittest
from pathlib import Path

import test_app_contract as base
from vela.manifest import SUPPORTED_CAPABILITIES, validate_manifest

ROOT = base.ROOT


def check(data):
    return validate_manifest(data, "chat-fixture", Path("chat-fixture"))


class SurfacesCapabilityTests(unittest.TestCase):
    def test_surfaces_is_a_capability_the_engine_knows(self):
        self.assertIn("surfaces", SUPPORTED_CAPABILITIES)

    def test_the_capability_is_granted_when_asked_for(self):
        data = copy.deepcopy(base.FIXTURE)
        data["capabilities"] = {"required": ["storage"], "optional": ["surfaces"]}
        manifest = check(data)
        self.assertIn("surfaces", manifest.capabilities)

    def test_the_capability_does_not_require_a_connection(self):
        # The fixture declares no connection. An app that can never resolve a
        # document learns that from the runtime error, not from an install
        # refusal: whether a window's document can be fetched is a fact about
        # the connection, and the connection is checked where it is used.
        data = copy.deepcopy(base.FIXTURE)
        data["capabilities"] = {"required": ["storage", "surfaces"]}
        manifest = check(data)
        self.assertIn("surfaces", manifest.capabilities)
        self.assertIsNone(manifest.raw.get("connection"))


if __name__ == "__main__":
    unittest.main()
