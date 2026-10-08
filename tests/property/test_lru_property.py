from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from screener.core.clock import FakeClock
from screener.core.lru_cache import LRUCache, OrderedDictLRUCache


class LRUCacheParityComparison(RuleBasedStateMachine):
    """Hypothesis state machine verifying that hand-crafted LRUCache produces

    behavior identical to reference OrderedDictLRUCache across arbitrary operation sequences.
    """

    def __init__(self):
        super().__init__()
        self.clock = FakeClock(100.0)
        self.capacity = 10
        self.lru = LRUCache(max_items=self.capacity, clock=self.clock, sizeof=lambda _: 10)
        self.ref = OrderedDictLRUCache(max_items=self.capacity, clock=self.clock, sizeof=lambda _: 10)

    @rule(key=st.sampled_from(["k1", "k2", "k3", "k4", "k5", "k6", "k7", "k8", "k9", "k10", "k11", "k12"]),
          val=st.integers(min_value=0, max_value=1000))
    def put_item(self, key: str, val: int):
        self.lru.put(key, val)
        self.ref.put(key, val)

    @rule(key=st.sampled_from(["k1", "k2", "k3", "k4", "k5", "k6", "k7", "k8", "k9", "k10", "k11", "k12"]))
    def get_item(self, key: str):
        v_lru = self.lru.get(key)
        v_ref = self.ref.get(key)
        assert v_lru == v_ref, f"Parity mismatch on get({key}): LRU got {v_lru}, Ref got {v_ref}"

    @rule(key=st.sampled_from(["k1", "k2", "k3", "k4", "k5", "k6", "k7", "k8", "k9", "k10", "k11", "k12"]))
    def pop_item(self, key: str):
        p_lru = self.lru.pop(key)
        p_ref = self.ref.pop(key)
        assert p_lru == p_ref, f"Parity mismatch on pop({key}): LRU got {p_lru}, Ref got {p_ref}"

    @rule(seconds=st.floats(min_value=0.1, max_value=10.0))
    def advance_time(self, seconds: float):
        self.clock.advance(seconds)

    @invariant()
    def check_length_and_stats(self):
        assert len(self.lru) == len(self.ref), f"Length mismatch: LRU {len(self.lru)} != Ref {len(self.ref)}"
        assert len(self.lru) <= self.capacity
        stats_lru = self.lru.stats()
        stats_ref = self.ref.stats()
        assert stats_lru.hits == stats_ref.hits, f"Hits mismatch: {stats_lru.hits} != {stats_ref.hits}"
        assert stats_lru.misses == stats_ref.misses, f"Misses mismatch: {stats_lru.misses} != {stats_ref.misses}"
        assert stats_lru.evictions == stats_ref.evictions, f"Evictions mismatch: {stats_lru.evictions} != {stats_ref.evictions}"


TestLRUParity = LRUCacheParityComparison.TestCase
