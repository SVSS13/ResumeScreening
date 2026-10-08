import pytest
from screener.core.clock import FakeClock, wall_clock


def test_wall_clock_is_monotonic():
    t1 = wall_clock()
    t2 = wall_clock()
    assert t2 >= t1


def test_fake_clock_initial_and_advance():
    clock = FakeClock(100.0)
    assert clock() == 100.0
    assert clock.now() == 100.0

    advanced = clock.advance(5.5)
    assert advanced == 105.5
    assert clock() == 105.5


def test_fake_clock_set():
    clock = FakeClock(50.0)
    clock.set(120.0)
    assert clock() == 120.0


def test_fake_clock_rejects_negative_advance():
    clock = FakeClock(10.0)
    with pytest.raises(ValueError):
        clock.advance(-1.0)


def test_fake_clock_rejects_backwards_set():
    clock = FakeClock(50.0)
    with pytest.raises(ValueError):
        clock.set(40.0)
