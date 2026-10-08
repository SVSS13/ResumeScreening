import asyncio
import concurrent.futures
import time

from screener.core.single_flight import AsyncSingleFlight, SingleFlight


def test_sync_single_flight_suppresses_stampede():
    sf = SingleFlight[str]()
    call_count = 0
    lock = asyncio.Lock() if False else None  # Dummy check

    def expensive_loader() -> str:
        nonlocal call_count
        call_count += 1
        time.sleep(0.05)  # Simulate slow network call
        return "loaded_result"

    # Launch 50 concurrent threads requesting the exact same key
    results: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(sf.execute, "hot_key", expensive_loader) for _ in range(50)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    # Invariants: All 50 threads got the loaded result, but loader was executed EXACTLY ONCE
    assert len(results) == 50
    assert all(r == "loaded_result" for r in results)
    assert call_count == 1


def test_async_single_flight_suppresses_stampede():
    sf = AsyncSingleFlight[str]()
    call_count = 0

    async def expensive_async_loader() -> str:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return "async_result"

    async def run_concurrent():
        tasks = [asyncio.create_task(sf.execute("async_hot_key", expensive_async_loader)) for _ in range(50)]
        return await asyncio.gather(*tasks)

    results = asyncio.run(run_concurrent())
    assert len(results) == 50
    assert all(r == "async_result" for r in results)
    assert call_count == 1
