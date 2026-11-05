"""Runtime regressions without external services or live dictionary traffic."""
import asyncio
import json
import unittest

from backend.runtime import AnalysisGate, SlidingWindowLimiter, progress_snapshot


def job(name="job"):
    return {"id": name, "revision": 2, "pages": [{"glyphs": [{"text": "private original"}]}],
            "occurrences": [], "marks": [],
            "analysis": {"state": "idle", "done": 0, "total": 1, "message": ""}}


class RateLimitTests(unittest.TestCase):
    def test_window_expires_and_rejections_do_not_extend_block(self):
        now = [0.0]
        limiter = SlidingWindowLimiter(2, 60, clock=lambda: now[0])
        self.assertTrue(limiter.check("client-a").allowed)
        self.assertTrue(limiter.check("client-a").allowed)
        self.assertEqual(limiter.check("client-a").retry_after, 60)
        now[0] = 30
        self.assertEqual(limiter.check("client-a").retry_after, 30)
        now[0] = 60
        self.assertTrue(limiter.check("client-a").allowed)

    def test_clients_are_independent_and_bucket_count_is_bounded(self):
        now = [0.0]
        limiter = SlidingWindowLimiter(1, 10, max_clients=2, clock=lambda: now[0])
        self.assertTrue(limiter.check("client-a").allowed)
        self.assertTrue(limiter.check("client-b").allowed)
        self.assertFalse(limiter.check("client-c").allowed)
        self.assertEqual(len(limiter._events), 2)
        self.assertFalse(limiter.check("client-a").allowed)
        now[0] = 10
        self.assertTrue(limiter.check("client-c").allowed)
        self.assertEqual(len(limiter._events), 1)


class AnalysisGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_queue_is_bounded_by_active_slots_and_cancelled_waiter_never_runs(self):
        gate = AnalysisGate(1)
        first, second = job("one"), job("two")
        entered = asyncio.Event()
        release = asyncio.Event()
        ran_second = False

        async def slow(item):
            entered.set()
            await release.wait()
            item["analysis"]["state"] = "complete"

        async def waiting_worker(item):
            nonlocal ran_second
            ran_second = True

        a = asyncio.create_task(gate.run(first, slow))
        await entered.wait()
        b = asyncio.create_task(gate.run(second, waiting_worker))
        await asyncio.sleep(0)
        self.assertEqual(second["analysis"]["state"], "queued")
        self.assertEqual((gate.active, gate.waiting), (1, 1))
        b.cancel()
        await b
        self.assertFalse(ran_second)
        self.assertEqual(second["analysis"]["state"], "cancelled")
        release.set()
        await a
        self.assertEqual((gate.active, gate.waiting), (0, 0))

    async def test_timeout_survives_worker_swallowing_cancellation_and_releases_slot(self):
        gate = AnalysisGate(1, run_timeout=0.01)
        item = job()

        async def swallowing_worker(item):
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                item["analysis"]["state"] = "cancelled"

        await gate.run(item, swallowing_worker)
        self.assertEqual(item["analysis"]["error_code"], "analysis_timeout")
        self.assertEqual(gate.active, 0)
        next_job = job("next")

        async def success(item): item["analysis"]["state"] = "complete"
        await gate.run(next_job, success)
        self.assertEqual(next_job["analysis"]["state"], "complete")

    async def test_unknown_failure_is_sanitized_and_cleanup_runs(self):
        gate = AnalysisGate(1)
        item = job()
        completed = []

        async def failed(item):
            raise RuntimeError("https://example.invalid?key=synthetic-sensitive-token")

        await gate.run(item, failed, on_done=lambda: completed.append(True))
        self.assertEqual(item["analysis"]["error_code"], "analysis_internal")
        self.assertNotIn("synthetic-sensitive-token", json.dumps(item))
        self.assertEqual(completed, [True])
        self.assertEqual(gate.active, 0)

    async def test_waiting_job_expires_without_running(self):
        gate = AnalysisGate(1, queue_timeout=.01)
        item = job()
        await gate._slots.acquire()
        called = False

        async def worker(item):
            nonlocal called
            called = True

        await gate.run(item, worker)
        gate._slots.release()
        self.assertFalse(called)
        self.assertEqual(item["analysis"]["error_code"], "queue_timeout")
        self.assertEqual((gate.active, gate.waiting), (0, 0))

    async def test_progress_never_sends_document_or_dictionary_payload(self):
        item = job()
        item["_dictionary"] = "synthetic secret placeholder"
        item["_path"] = "/tmp/private-original.pdf"
        snapshot = progress_snapshot(item, gate=AnalysisGate(2))
        encoded = json.dumps(snapshot)
        for secret in ("private original", "secret placeholder", "private-original.pdf"):
            self.assertNotIn(secret, encoded)
        self.assertEqual(snapshot["capacity"]["concurrent_limit"], 2)
        snapshot["analysis"]["state"] = "mutated"
        self.assertEqual(item["analysis"]["state"], "idle")


if __name__ == "__main__": unittest.main()
