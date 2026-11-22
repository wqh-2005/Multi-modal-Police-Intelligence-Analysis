"""Offline tests for bounded scheduling and graceful cancellation."""

import asyncio
import unittest

from experiments.KEO.core.run_experiment import _run_workers


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_parallelism_and_single_commit(self):
        active = peak = 0
        committed = []

        async def process(question, method):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.001)
            active -= 1
            return question, method

        jobs = [(str(i), "direct") for i in range(20)]
        await _run_workers(jobs, process, committed.append, 3)
        self.assertEqual(peak, 3)
        self.assertEqual(len(committed), 20)
        self.assertEqual(set(committed), set(jobs))

    async def test_cancellation_drains_started_work_without_scheduling_more(self):
        started = []
        committed = []
        ready, release = asyncio.Event(), asyncio.Event()

        async def process(question, method):
            started.append(question)
            if len(started) == 3:
                ready.set()
            await release.wait()
            return question

        task = asyncio.create_task(_run_workers(
            [(i, "direct") for i in range(20)], process, committed.append, 3))
        await ready.wait()
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(started, [0, 1, 2])
        self.assertEqual(sorted(committed), [0, 1, 2])

    async def test_worker_error_propagates_after_other_started_work_finishes(self):
        committed = []

        async def process(question, method):
            await asyncio.sleep(0)
            if question == 0:
                raise RuntimeError("failed")
            return question

        with self.assertRaisesRegex(RuntimeError, "failed"):
            await _run_workers([(i, "direct") for i in range(10)],
                               process, committed.append, 3)
        self.assertEqual(sorted(committed), [1, 2])

    async def test_empty_pending(self):
        async def process(*args):
            self.fail("No work expected")
        await _run_workers([], process, self.fail, 3)


if __name__ == "__main__":
    unittest.main()
