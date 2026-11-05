"""ASGI integration of queued jobs and limits; no live dictionary calls."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz
import httpx

from backend import main as service
from backend.runtime import AnalysisGate, SlidingWindowLimiter


def sample():
    document = fitz.open()
    page = document.new_page()
    page.insert_text((70, 100), "천천히", fontname="korea", fontsize=14)
    result = document.tobytes()
    document.close()
    return result


class RuntimeAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(service, "ROOT", Path(self.directory.name)),
            patch.object(service, "SESSIONS", {}),
            patch.object(service, "TASKS", {}),
            patch.object(service, "GATE", AnalysisGate(1)),
            patch.object(service, "UPLOAD_LIMIT", SlidingWindowLimiter(100, 60)),
            patch.object(service, "ANALYZE_LIMIT", SlidingWindowLimiter(100, 60)),
            patch.dict("os.environ", {"STDICT_API_KEY": "synthetic-runtime-test-key"}),
        ]
        for patched in self.patches: patched.start()
        # ASGI requests share this test's event loop, so background analysis tasks
        # persist between requests (unlike a short-lived synchronous test portal).
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app),
                                        base_url="http://testserver")

    async def asyncTearDown(self):
        for task in list(service.TASKS.values()): task.cancel()
        if service.TASKS:
            await asyncio.gather(*list(service.TASKS.values()), return_exceptions=True)
        await self.client.aclose()
        for patched in reversed(self.patches): patched.stop()
        self.directory.cleanup()

    async def upload(self):
        response = await self.client.post("/api/jobs", files={"file": ("test.pdf", sample(), "application/pdf")})
        self.assertEqual(response.status_code, 201)
        return response.json()

    async def until(self, condition):
        for _ in range(200):
            if condition(): return
            await asyncio.sleep(.001)
        self.fail("Runtime state did not settle")

    async def test_progress_is_small_private_and_noncacheable(self):
        item = await self.upload()
        response = await self.client.get(f"/api/jobs/{item['id']}/progress")
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["analysis"]["state"], "idle")
        for name in ("pages", "glyphs", "occurrences", "_path", "_dictionary"):
            self.assertNotIn(name, result)
        self.assertLess(len(response.content), 1200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    async def test_unknown_api_does_not_return_successful_html(self):
        response = await self.client.get("/api/not-a-real-endpoint")
        self.assertEqual(response.status_code, 404)
        self.assertIn("application/json", response.headers["content-type"])
        self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_duplicate_active_request_reuses_task_even_after_rate_allowance_used(self):
        service.ANALYZE_LIMIT = SlidingWindowLimiter(1, 60)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def worker(item):
            entered.set()
            await release.wait()
            item["analysis"]["state"] = "complete"

        with patch.object(service, "analyze_job", worker):
            item = await self.upload()
            path = f"/api/jobs/{item['id']}"
            self.assertEqual((await self.client.post(path + "/analyze")).status_code, 200)
            await entered.wait()
            original = service.TASKS[item["id"]]
            duplicate = await self.client.post(path + "/analyze")
            self.assertEqual(duplicate.status_code, 200)
            self.assertIs(service.TASKS[item["id"]], original)
            release.set()
            await original
            blocked = await self.client.post(path + "/analyze")
            self.assertEqual(blocked.status_code, 429)
            self.assertGreater(int(blocked.headers["retry-after"]), 0)

    async def test_queued_job_cannot_mutate_or_export_and_can_cancel_without_running(self):
        first_entered = asyncio.Event()
        release = asyncio.Event()
        called = []

        async def worker(item):
            called.append(item["id"])
            first_entered.set()
            await release.wait()
            item["analysis"]["state"] = "complete"

        with patch.object(service, "analyze_job", worker):
            first, second = await self.upload(), await self.upload()
            a = f"/api/jobs/{first['id']}"
            b = f"/api/jobs/{second['id']}"
            await self.client.post(a + "/analyze")
            await first_entered.wait()
            await self.client.post(b + "/analyze")
            self.assertEqual(service.SESSIONS[second["id"]]["analysis"]["state"], "queued")
            snapshot = (await self.client.get(b + "/progress")).json()
            self.assertEqual(snapshot["capacity"]["active"], 1)
            self.assertEqual(snapshot["capacity"]["waiting"], 1)
            self.assertEqual((await self.client.patch(b + "/state", json={"marks": []})).status_code, 409)
            self.assertEqual((await self.client.get(b + "/export/pdf")).status_code, 409)
            cancelled = await self.client.post(b + "/cancel")
            self.assertEqual(cancelled.json()["analysis"]["state"], "cancelled")
            self.assertNotIn(second["id"], called)
            release.set()
            await self.until(lambda: service.GATE.active == 0)
            self.assertEqual(service.GATE.waiting, 0)

    async def test_unexpected_worker_failure_is_sanitized_and_releases_capacity(self):
        async def failed(item):
            raise RuntimeError("https://example.invalid?key=synthetic-runtime-sensitive")

        with patch.object(service, "analyze_job", failed):
            item = await self.upload()
            path = f"/api/jobs/{item['id']}"
            await self.client.post(path + "/analyze")
            await self.until(lambda: service.SESSIONS[item["id"]]["analysis"]["state"] == "error")
            response = await self.client.get(path + "/progress")
            self.assertEqual(response.json()["analysis"]["error_code"], "analysis_internal")
            self.assertNotIn("synthetic-runtime-sensitive", response.text)
            await self.until(lambda: service.GATE.active == 0 and item["id"] not in service.TASKS)

    async def test_upload_rate_limit_has_retry_after_and_does_not_trust_forwarded_identity(self):
        service.UPLOAD_LIMIT = SlidingWindowLimiter(1, 60)
        await self.upload()
        blocked = await self.client.post("/api/jobs", files={"file": ("test.pdf", sample(), "application/pdf")},
                                         headers={"X-Forwarded-For": "203.0.113.9"})
        self.assertEqual(blocked.status_code, 429)
        self.assertGreater(int(blocked.headers["retry-after"]), 0)
        self.assertEqual(blocked.headers["cache-control"], "no-store")


if __name__ == "__main__": unittest.main()
