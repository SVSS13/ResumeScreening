import pytest
from screener.core.lru_cache import LRUCache, OrderedDictLRUCache


@pytest.mark.benchmark(group="lru_put")
def test_benchmark_lru_put_1k(benchmark):
    cache: LRUCache[int, int] = LRUCache(max_items=1_000, max_bytes=10_000_000)
    def run_put():
        for i in range(1_000):
            cache.put(i, i)
    benchmark(run_put)


@pytest.mark.benchmark(group="lru_put")
def test_benchmark_lru_put_10k(benchmark):
    cache: LRUCache[int, int] = LRUCache(max_items=10_000, max_bytes=100_000_000)
    def run_put():
        for i in range(1_000):
            cache.put(i, i)
    benchmark(run_put)


@pytest.mark.benchmark(group="lru_get")
def test_benchmark_lru_get_hit(benchmark):
    cache: LRUCache[int, int] = LRUCache(max_items=10_000)
    for i in range(5_000):
        cache.put(i, i)
    def run_get():
        for i in range(1_000):
            cache.get(i)
    benchmark(run_get)


@pytest.mark.benchmark(group="ordered_dict_parity")
def test_benchmark_ordered_dict_put(benchmark):
    cache: OrderedDictLRUCache[int, int] = OrderedDictLRUCache(max_items=10_000)
    def run_put():
        for i in range(1_000):
            cache.put(i, i)
    benchmark(run_put)
