"""Login lockout stays in force after the failure window would have slid away."""

from mediatracker.security.rate_limit import LoginRateLimiter


def test_lockout_survives_the_failure_window():
    now = {"t": 0.0}
    limiter = LoginRateLimiter(limit=5, window_seconds=60, lockout_seconds=900, clock=lambda: now["t"])
    for _ in range(5):
        limiter.record_failure("ip:1")
    assert limiter.too_many("ip:1")
    assert limiter.retry_after("ip:1") == 900
    now["t"] = 120
    assert limiter.too_many("ip:1")
    assert limiter.retry_after("ip:1") == 780
    now["t"] = 900
    assert not limiter.too_many("ip:1")
    limiter.record_failure("ip:1")
    assert not limiter.too_many("ip:1")


def test_success_clears_the_lockout():
    limiter = LoginRateLimiter(limit=2, window_seconds=60, lockout_seconds=900, clock=lambda: 10.0)
    limiter.record_failure("user:admin")
    limiter.record_failure("user:admin")
    assert limiter.too_many("user:admin")
    limiter.reset("user:admin")
    assert not limiter.too_many("user:admin")
