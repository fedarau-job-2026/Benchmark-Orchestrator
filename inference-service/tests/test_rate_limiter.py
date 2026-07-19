"""Unit tests for the hand-built rate limiter, driven by a fake clock."""

from inference_service.rate_limiter import Decision, RateLimiter


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def make(rpm=60, max_concurrency=4, t0=1000.0):
    clock = FakeClock(t0)
    return RateLimiter(rpm=rpm, max_concurrency=max_concurrency, clock=clock), clock


# --- RPM sliding window ---

def test_rpm_allows_up_to_limit_and_rejects_next():
    lim, clock = make(rpm=3, max_concurrency=100)
    for _ in range(3):
        d = lim.try_acquire()
        assert d.allowed
        lim.release()
    d = lim.try_acquire()
    assert d == Decision(allowed=False, reason="rpm", retry_after=60)


def test_rpm_slot_frees_exactly_when_oldest_ages_out():
    lim, clock = make(rpm=2, max_concurrency=100)
    assert lim.try_acquire().allowed  # t=1000
    lim.release()
    clock.advance(10)
    assert lim.try_acquire().allowed  # t=1010
    lim.release()

    d = lim.try_acquire()
    assert not d.allowed and d.reason == "rpm"
    assert d.retry_after == 50  # oldest (t=1000) ages out at t=1060

    clock.advance(49.999)  # t=1059.999 — still inside window
    assert not lim.try_acquire().allowed

    clock.advance(0.001)  # t=1060.0 — oldest exactly 60s old, evicted (>= boundary)
    d = lim.try_acquire()
    assert d.allowed
    lim.release()


def test_rpm_retry_after_is_ceiled_and_at_least_one():
    lim, clock = make(rpm=1, max_concurrency=100)
    assert lim.try_acquire().allowed
    lim.release()
    clock.advance(59.5)
    d = lim.try_acquire()
    assert not d.allowed
    assert d.retry_after == 1  # ceil(0.5)


def test_rejected_requests_do_not_consume_window_slots():
    lim, clock = make(rpm=1, max_concurrency=100)
    assert lim.try_acquire().allowed
    lim.release()
    # Hammer with 50 rejected requests
    for _ in range(50):
        assert not lim.try_acquire().allowed
    # Slot still frees when the single accepted request ages out
    clock.advance(60)
    assert lim.try_acquire().allowed


# --- Concurrency cap ---

def test_concurrency_cap_rejects_when_full():
    lim, _ = make(rpm=1000, max_concurrency=2)
    assert lim.try_acquire().allowed
    assert lim.try_acquire().allowed
    d = lim.try_acquire()
    assert d == Decision(allowed=False, reason="concurrency", retry_after=1)
    lim.release()
    assert lim.try_acquire().allowed  # slot freed


def test_concurrency_rejection_does_not_touch_rpm_window():
    lim, clock = make(rpm=2, max_concurrency=1)
    assert lim.try_acquire().allowed          # in flight, 1 window slot used
    assert not lim.try_acquire().allowed      # concurrency reject
    lim.release()
    assert lim.try_acquire().allowed          # 2nd window slot — not eaten by the reject
    lim.release()


def test_concurrency_checked_before_rpm():
    lim, _ = make(rpm=1, max_concurrency=1)
    assert lim.try_acquire().allowed
    d = lim.try_acquire()  # both limits breached; concurrency wins
    assert d.reason == "concurrency"


def test_in_flight_tracking():
    lim, _ = make()
    assert lim.in_flight == 0
    lim.try_acquire()
    lim.try_acquire()
    assert lim.in_flight == 2
    lim.release()
    assert lim.in_flight == 1
