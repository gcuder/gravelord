from __future__ import annotations

from gravelord.orchestrator import compute_backoff_ms


def test_backoff_grows_exponentially():
    base_max = 300_000
    a1 = compute_backoff_ms(1, base_max)
    a3 = compute_backoff_ms(3, base_max)
    # attempt 1 ≈ 10s ± 10%, attempt 3 ≈ 40s ± 10%
    assert 9_000 <= a1 <= 11_000
    assert 36_000 <= a3 <= 44_000


def test_backoff_capped():
    capped = compute_backoff_ms(20, 300_000)
    # cap = 300s, jitter ±10% -> [270s, 330s]
    assert 270_000 <= capped <= 330_000


def test_backoff_minimum_floor():
    # tiny max still respects a 1s floor
    assert compute_backoff_ms(1, 500) >= 1_000
