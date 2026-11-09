"""Expose the running engine and validated build identity without live requests."""
import importlib
import json
import unittest
from unittest.mock import patch

import httpx

from backend import main as service


RENDER_COMMIT = "0123456789abcdef0123456789abcdef01234567"
LOCAL_COMMIT = "abcdef0123456789abcdef0123456789abcdef01"
CAPABILITIES = {
    "kiwi_morphology",
    "compound_components",
    "numeric_readings",
    "numeric_reading_selection",
    "particle_boundaries",
    "original_glyph_alignment",
}


def identity():
    # Keep collection possible before the implementation exists so the initial
    # failing run records both missing helper and legacy health behavior.
    return importlib.import_module("backend.release").release_identity()


class ReleaseIdentityTests(unittest.TestCase):
    def test_unknown_commit_still_identifies_engine_and_capabilities(self):
        with patch.dict("os.environ", {}, clear=True):
            result = identity()
        self.assertEqual(result["version"], "0.2.0")
        self.assertEqual(result["engine_version"], "2026.11.09.2")
        self.assertIn("build_commit", result)
        self.assertIsNone(result["build_commit"])
        self.assertIsInstance(result["capabilities"], list)
        self.assertTrue(CAPABILITIES.issubset(result["capabilities"]))

    def test_render_commit_takes_precedence_over_local_build_commit(self):
        with patch.dict("os.environ", {
            "RENDER_GIT_COMMIT": RENDER_COMMIT,
            "BUILD_COMMIT": LOCAL_COMMIT,
        }, clear=True):
            self.assertEqual(identity()["build_commit"], RENDER_COMMIT)

    def test_local_build_commit_is_used_when_render_commit_is_absent(self):
        with patch.dict("os.environ", {"BUILD_COMMIT": LOCAL_COMMIT}, clear=True):
            self.assertEqual(identity()["build_commit"], LOCAL_COMMIT)

    def test_invalid_render_commit_can_fall_back_to_valid_build_commit(self):
        with patch.dict("os.environ", {
            "RENDER_GIT_COMMIT": "unexpanded-render-variable",
            "BUILD_COMMIT": LOCAL_COMMIT,
        }, clear=True):
            result = identity()
        self.assertEqual(result["build_commit"], LOCAL_COMMIT)
        self.assertNotIn("unexpanded-render-variable", json.dumps(result))

    def test_invalid_commit_values_are_not_published(self):
        invalid_values = [
            "", "main", "a" * 39, "a" * 41, "g" * 40,
            " " + RENDER_COMMIT, RENDER_COMMIT + "\n",
            "private-value-that-is-not-a-commit",
        ]
        for variable in ("RENDER_GIT_COMMIT", "BUILD_COMMIT"):
            for value in invalid_values:
                with self.subTest(variable=variable, value=value):
                    with patch.dict("os.environ", {variable: value}, clear=True):
                        result = identity()
                    self.assertIsNone(result["build_commit"])
                    if value:
                        self.assertNotIn(value, json.dumps(result))

    def test_identity_reads_environment_each_call_without_mutating_results(self):
        with patch.dict("os.environ", {"BUILD_COMMIT": LOCAL_COMMIT}, clear=True):
            first = identity()
        first["capabilities"].clear()
        with patch.dict("os.environ", {"RENDER_GIT_COMMIT": RENDER_COMMIT}, clear=True):
            second = identity()
        self.assertEqual(second["build_commit"], RENDER_COMMIT)
        self.assertTrue(CAPABILITIES.issubset(second["capabilities"]))


class HealthReleaseIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_reports_release_and_commit_without_caching_or_secrets(self):
        secret = "synthetic-private-dictionary-test-key"
        with patch.dict("os.environ", {
            "RENDER_GIT_COMMIT": RENDER_COMMIT,
            "STDICT_API_KEY": secret,
        }, clear=True):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=service.app),
                base_url="http://testserver",
            ) as client:
                response = await client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        result = response.json()
        self.assertTrue(result["ok"])
        self.assertTrue(result["dictionary_configured"])
        self.assertEqual(result["version"], "0.2.0")
        self.assertEqual(result["engine_version"], "2026.11.09.2")
        self.assertEqual(result["build_commit"], RENDER_COMMIT)
        self.assertIsInstance(result["capabilities"], list)
        self.assertTrue(CAPABILITIES.issubset(result["capabilities"]))
        self.assertNotIn(secret, response.text)
        self.assertNotIn(secret, str(response.headers))
        for field in ("max_upload_bytes", "max_pages", "retention_seconds", "ocr_supported"):
            self.assertIn(field, result)

    async def test_health_explicitly_reports_unknown_build_when_unconfigured(self):
        with patch.dict("os.environ", {}, clear=True):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=service.app),
                base_url="http://testserver",
            ) as client:
                response = await client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        result = response.json()
        self.assertIn("build_commit", result)
        self.assertIsNone(result["build_commit"])
        self.assertFalse(result["dictionary_configured"])


if __name__ == "__main__":
    unittest.main()
