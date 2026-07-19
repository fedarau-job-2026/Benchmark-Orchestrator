"""AIMD controller unit tests, driven by a fake clock and direct feedback calls."""

from orchestrator.controller import (
    CLEAN_STREAK_FOR_INCREASE,
    INITIAL_CONCURRENCY,
    AdaptiveController,
)


class FakeClock:
    def __init__(self, t: float = 0.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def make():
    clock = FakeClock()
    return AdaptiveController(clock=clock), clock


def admit(ctrl, n=1):
    """Simulate n admitted dispatches without going through the async gate."""
    for _ in range(n):
        ctrl._in_flight += 1


def test_additive_increase_when_window_pressed():
    ctrl, _ = make()
    before = ctrl.stats().concurrency_limit
    admit(ctrl, int(before))  # fill the window: it is the binding constraint
    for _ in range(CLEAN_STREAK_FOR_INCREASE):
        ctrl.on_accepted()
        ctrl.on_success()   # completes with in_flight == window -> pressure
        admit(ctrl)         # keep the window full
    assert ctrl.stats().concurrency_limit == before + 1


def test_no_window_growth_without_pressure():
    """The window must not grow when in-flight never reaches it (rate binds first)."""
    ctrl, _ = make()
    before = ctrl.stats().concurrency_limit
    for _ in range(CLEAN_STREAK_FOR_INCREASE * 3):
        admit(ctrl)         # one at a time — window never full
        ctrl.on_accepted()
        ctrl.on_success()
    assert ctrl.stats().concurrency_limit == before


def test_concurrency_429_sets_window_to_observed_inflight():
    ctrl, _ = make()
    admit(ctrl, 6)  # 5 running + the one that got rejected
    ctrl.on_rate_limited("concurrency", retry_after=1)
    # Server rejected us while 5 others were in flight -> cap ~= 5
    assert ctrl.stats().concurrency_limit == 5.0
    assert ctrl.stats().total_429_concurrency == 1


def test_concurrency_window_never_below_one():
    ctrl, _ = make()
    admit(ctrl, 1)
    ctrl.on_rate_limited("concurrency", retry_after=1)
    assert ctrl.stats().concurrency_limit == 1.0


def test_rpm_429_learns_rate_from_accepted_window():
    ctrl, clock = make()
    # 30 accepted requests over 60s -> observed RPM = 30
    for _ in range(30):
        admit(ctrl)
        ctrl.on_accepted()
        ctrl.on_success()
        clock.advance(2.0)
    admit(ctrl)
    ctrl.on_rate_limited("rpm", retry_after=10)
    s = ctrl.stats()
    # rate adopts observed RPM (with safety margin), i.e. just under 0.5 req/s
    assert 0.4 <= s.rate <= 0.5
    assert s.paused_until >= clock.t + 10
    assert s.total_429_rpm == 1


def test_rate_grows_during_clean_periods():
    ctrl, clock = make()
    r0 = ctrl.stats().rate
    for _ in range(10):
        clock.advance(3.0)
        ctrl._refill(clock.t)
    assert ctrl.stats().rate > r0  # multiplicative creep rediscovers raised limits


def test_no_growth_while_paused():
    ctrl, clock = make()
    admit(ctrl)
    ctrl.on_accepted()
    ctrl.on_rate_limited("rpm", retry_after=30)
    r_after_429 = ctrl.stats().rate
    clock.advance(5.0)
    ctrl._refill(clock.t)  # still inside the pause window
    assert ctrl.stats().rate == r_after_429


def test_unknown_reason_shrinks_both_knobs():
    ctrl, clock = make()
    ctrl._concurrency = 8.0
    r0 = ctrl._rate = 2.0
    admit(ctrl, 4)
    ctrl.on_rate_limited(None, retry_after=2)
    s = ctrl.stats()
    assert s.concurrency_limit < 8.0
    assert s.rate < r0
    assert s.paused_until >= clock.t + 2
    assert s.total_429_unknown == 1


def test_429_resets_clean_streak():
    ctrl, _ = make()
    admit(ctrl, int(INITIAL_CONCURRENCY))  # window full -> growth is armed
    for _ in range(CLEAN_STREAK_FOR_INCREASE - 1):
        ctrl.on_accepted()
        ctrl.on_success()
        admit(ctrl)
    ctrl.on_rate_limited("concurrency", retry_after=1)
    window_after = ctrl.stats().concurrency_limit
    admit(ctrl, int(window_after))
    ctrl.on_accepted()
    ctrl.on_success()  # streak restarted: one success must not trigger +1
    assert ctrl.stats().concurrency_limit == window_after


def test_slow_start_ends_after_first_429():
    ctrl, clock = make()
    r0 = ctrl.stats().rate
    clock.advance(2.5)
    ctrl._refill(clock.t)
    assert ctrl.stats().rate == r0 * 2.0  # slow start doubles
    admit(ctrl)
    ctrl.on_rate_limited("concurrency", retry_after=1)
    r1 = ctrl.stats().rate
    clock.advance(2.5)
    ctrl._refill(clock.t)
    assert r1 < ctrl.stats().rate < r1 * 1.5  # gentle growth now


def test_blocked_for_gates_on_pause_tokens_and_window():
    ctrl, clock = make()
    assert ctrl._blocked_for(clock.t) == 0.0  # fresh controller can dispatch
    ctrl._tokens = 0.0
    assert ctrl._blocked_for(clock.t) > 0.0   # out of tokens -> wait
    ctrl._tokens = 1.0
    ctrl._in_flight = int(INITIAL_CONCURRENCY)
    assert ctrl._blocked_for(clock.t) > 0.0   # window full -> wait
    ctrl._in_flight = 0
    ctrl._pause(clock.t, 5.0)
    assert ctrl._blocked_for(clock.t) >= 5.0  # Retry-After pause dominates
