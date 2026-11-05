"""Tests for the virtual 5m portfolio HTF indicator."""
import pytest
from decimal import Decimal

from futures_portfolio.backtest.htf_5m_indicator import (
    Virtual5MPortfolio,
    HTF5MIndicator,
    INITIAL_5M_TARGETS,
    run_htf_5m_experiment,
    apply_variant_targets,
)


class TestVirtual5MPortfolio:
    """Tests for the virtual 5m portfolio."""

    def test_initialization(self):
        """Test portfolio initializes with correct values."""
        portfolio = Virtual5MPortfolio(Decimal('1000'))
        portfolio.initialize(Decimal('100'))

        # LONG: 25% * 1000 = 250 margin, 3x leverage = 750 qty at 100 = 7.5
        # SHORT: 25% * 1000 = 250 margin, 3x leverage = 750 qty at 100 = 7.5
        # VIRTUAL: 50% * 1000 = 500 USDT
        assert portfolio.long_qty == Decimal('7.5')
        assert portfolio.short_qty == Decimal('7.5')
        assert portfolio.virtual_value == Decimal('500')
        assert portfolio.long_entry_price == Decimal('100')
        assert portfolio.short_entry_price == Decimal('100')

    def test_revalue_no_price_change(self):
        """Test revalue when price unchanged."""
        portfolio = Virtual5MPortfolio(Decimal('1000'))
        portfolio.initialize(Decimal('100'))
        vals = portfolio.revalue(Decimal('100'))

        assert vals["long"] == Decimal('250')  # margin only
        assert vals["short"] == Decimal('250')  # margin only
        assert vals["virtual"] == Decimal('500')
        assert vals["total"] == Decimal('1000')

    def test_revalue_price_up(self):
        """Test revalue when price goes up (LONG profits, SHORT loses)."""
        portfolio = Virtual5MPortfolio(Decimal('1000'))
        portfolio.initialize(Decimal('100'))
        vals = portfolio.revalue(Decimal('110'))  # +10%

        # LONG: 7.5 qty, entry 100, margin 250, upnl = 7.5 * 10 = 75, total 325
        assert vals["long"] == Decimal('325')
        # SHORT: 7.5 qty, entry 100, margin 250, upnl = 7.5 * -10 = -75, total 175
        assert vals["short"] == Decimal('175')
        # VIRTUAL unchanged
        assert vals["virtual"] == Decimal('500')
        # Total = 1000 (conserved)
        assert vals["total"] == Decimal('1000')

    def test_revalue_price_down(self):
        """Test revalue when price goes down (SHORT profits, LONG loses)."""
        portfolio = Virtual5MPortfolio(Decimal('1000'))
        portfolio.initialize(Decimal('100'))
        vals = portfolio.revalue(Decimal('90'))  # -10%

        # LONG: 7.5 * -10 = -75, margin 250 = 175
        assert vals["long"] == Decimal('175')
        # SHORT: 7.5 * 10 = 75, margin 250 = 325
        assert vals["short"] == Decimal('325')
        assert vals["virtual"] == Decimal('500')
        assert vals["total"] == Decimal('1000')

    def test_get_shares(self):
        """Test share calculation."""
        portfolio = Virtual5MPortfolio(Decimal('1000'))
        portfolio.initialize(Decimal('100'))
        w_long, w_short, w_virt = portfolio.get_shares(Decimal('100'))

        assert w_long == Decimal('0.25')
        assert w_short == Decimal('0.25')
        assert w_virt == Decimal('0.50')

    def test_get_shares_price_up(self):
        """Test shares when price up -> LONG share > 25%."""
        portfolio = Virtual5MPortfolio(Decimal('1000'))
        portfolio.initialize(Decimal('100'))
        w_long, w_short, w_virt = portfolio.get_shares(Decimal('110'))

        # LONG 325/1000 = 32.5%, SHORT 175/1000 = 17.5%, VIRTUAL 50%
        assert w_long == Decimal('0.325')
        assert w_short == Decimal('0.175')
        assert w_virt == Decimal('0.50')

    def test_measure_and_reset_neutral(self):
        """Test measure and reset when no threshold crossed."""
        portfolio = Virtual5MPortfolio(Decimal('1000'))
        portfolio.initialize(Decimal('100'))
        event = portfolio.measure_and_reset(Decimal('100'), 0, 0, 4)

        # No excess
        assert event["excess_long_pp"] == 0.0
        assert event["excess_short_pp"] == 0.0
        assert event["signals"]["1pp"] == "NEUTRAL"
        assert event["signals"]["2pp"] == "NEUTRAL"
        # Targets unchanged
        assert event["targets"]["1pp"]["LONG"] == 0.25
        assert event["targets"]["1pp"]["SHORT"] == 0.25

    def test_measure_and_reset_long_1pp(self):
        """Test measure and reset with LONG excess >= 1pp."""
        portfolio = Virtual5MPortfolio(Decimal('1000'))
        portfolio.initialize(Decimal('100'))
        # Price up enough to give LONG > 26% share
        # Need w_long >= 0.26 -> long_value >= 260
        # long_value = 250 + 7.5 * (p - 100)
        # 260 = 250 + 7.5 * (p - 100) -> p = 100 + 10/7.5 = 101.33
        event = portfolio.measure_and_reset(Decimal('102'), 0, 0, 4)

        assert event["excess_long_pp"] > 1.0  # ~2.5pp
        assert event["signals"]["1pp"] == "LONG"
        assert event["targets"]["1pp"]["LONG"] == 0.26
        assert event["targets"]["1pp"]["SHORT"] == 0.24
        # 2pp threshold not reached
        assert event["signals"]["2pp"] == "NEUTRAL"

    def test_measure_and_reset_long_2pp(self):
        """Test measure and reset with LONG excess >= 2pp."""
        portfolio = Virtual5MPortfolio(Decimal('1000'))
        portfolio.initialize(Decimal('100'))
        # Price up enough for LONG >= 27% share
        event = portfolio.measure_and_reset(Decimal('104'), 0, 0, 4)

        assert event["excess_long_pp"] > 2.0
        assert event["signals"]["2pp"] == "LONG"
        assert event["targets"]["2pp"]["LONG"] == 0.27
        assert event["targets"]["2pp"]["SHORT"] == 0.23

    def test_measure_and_reset_short_1pp(self):
        """Test measure and reset with SHORT excess >= 1pp."""
        portfolio = Virtual5MPortfolio(Decimal('1000'))
        portfolio.initialize(Decimal('100'))
        event = portfolio.measure_and_reset(Decimal('98'), 0, 0, 4)

        assert event["excess_short_pp"] > 1.0
        assert event["signals"]["1pp"] == "SHORT"
        assert event["targets"]["1pp"]["LONG"] == 0.24
        assert event["targets"]["1pp"]["SHORT"] == 0.26

    def test_measure_and_reset_short_2pp(self):
        """Test measure and reset with SHORT excess >= 2pp."""
        portfolio = Virtual5MPortfolio(Decimal('1000'))
        portfolio.initialize(Decimal('100'))
        event = portfolio.measure_and_reset(Decimal('96'), 0, 0, 4)

        assert event["excess_short_pp"] > 2.0
        assert event["signals"]["2pp"] == "SHORT"
        assert event["targets"]["2pp"]["LONG"] == 0.23
        assert event["targets"]["2pp"]["SHORT"] == 0.27

    def test_reset_preserves_total_value(self):
        """Test that reset preserves total portfolio value."""
        portfolio = Virtual5MPortfolio(Decimal('1000'))
        portfolio.initialize(Decimal('100'))
        # Price moves up, total value conserved at 1000
        before = portfolio.revalue(Decimal('110'))["total"]
        event = portfolio.measure_and_reset(Decimal('110'), 0, 0, 4)
        after = portfolio.revalue(Decimal('110'))["total"]
        assert before == after == Decimal('1000')
        # After reset, shares back to 25/25/50
        w_long, w_short, w_virt = portfolio.get_shares(Decimal('110'))
        assert w_long == Decimal('0.25')
        assert w_short == Decimal('0.25')
        assert w_virt == Decimal('0.50')

    def test_virtual_value_fixed_usdt(self):
        """Test VIRTUAL leg maintains fixed USDT value."""
        portfolio = Virtual5MPortfolio(Decimal('1000'))
        portfolio.initialize(Decimal('100'))
        # Even with large price moves, VIRTUAL value in USDT unchanged
        vals = portfolio.revalue(Decimal('200'))
        assert vals["virtual"] == Decimal('500')
        vals = portfolio.revalue(Decimal('50'))
        assert vals["virtual"] == Decimal('500')


class TestHTF5MIndicator:
    """Tests for the HTF 5m indicator."""

    def test_initialize(self):
        """Test indicator initialization."""
        indicator = HTF5MIndicator(1000.0)
        indicator.initialize(100.0)
        assert indicator.initialized
        assert indicator.bars_since_5m_boundary == 0
        assert indicator.current_5m_interval == 0

    def test_first_5m_boundary_at_bar_4(self):
        """First 5m boundary after bars 0,1,2,3,4 (5 bars) at timestamp boundary."""
        indicator = HTF5MIndicator(1000.0)
        # Start at timestamp 0 (00:00:00)
        # Bars: 0@0, 1@60000, 2@120000, 3@180000, 4@240000
        # Next boundary after bar 4 close is 300000 (00:05:00)
        # Bar 4 timestamp = 240000, next_boundary = 300000, 300000 % 300000 == 0 ✓
        indicator.initialize(100.0)

        # Bars 0-3: no boundary
        for i in range(4):
            event = indicator.on_bar(100.0, i, i * 60000)
            assert event is None

        # Bar 4: completes 5m interval, boundary at 300000
        event = indicator.on_bar(100.0, 4, 4 * 60000)
        assert event is not None
        assert event["interval_idx"] == 0
        assert event["interval_start_bar"] == 0
        assert event["interval_end_bar"] == 4
        assert event["signal_available_at_bar"] == 5  # Next bar
        assert event["signal_available_at_ts"] == 300000

    def test_signal_not_from_unclosed_candle(self):
        """Test that signal doesn't include current/unclosed 5m candle."""
        indicator = HTF5MIndicator(1000.0)
        indicator.initialize(100.0)

        # Complete first 5m interval (bars 0-4)
        for i in range(5):
            indicator.on_bar(100.0, i, i * 60000)

        # Now at bar 5 (timestamp 300000 = 00:05:00 open)
        # This bar is the FIRST bar of the NEXT 5m interval
        # Its close will be at 360000 (00:06:00), not a 5m boundary
        event = indicator.on_bar(110.0, 5, 300000)
        assert event is None  # Not a boundary

        # Bar 9 (timestamp 540000) closes at 600000 = 00:10:00 boundary
        for i in range(6, 9):
            indicator.on_bar(110.0, i, i * 60000)

        event = indicator.on_bar(110.0, 9, 9 * 60000)
        assert event is not None
        assert event["interval_idx"] == 1
        assert event["interval_start_bar"] == 5
        assert event["interval_end_bar"] == 9
        # Signal available at bar 10
        assert event["signal_available_at_bar"] == 10

    def test_no_signal_before_first_complete_interval(self):
        """Test no signal before first 5 bars."""
        indicator = HTF5MIndicator(1000.0)
        indicator.initialize(100.0)

        # Only 3 bars
        for i in range(3):
            event = indicator.on_bar(100.0, i, i * 60000)
            assert event is None

    def test_get_targets_baseline(self):
        """Test baseline targets."""
        indicator = HTF5MIndicator(1000.0)
        indicator.initialize(100.0)
        targets = indicator.get_targets_for_variant("baseline")
        assert targets["LONG"] == 0.25
        assert targets["SHORT"] == 0.25
        assert targets["VIRTUAL"] == 0.50


class TestApplyVariantTargets:
    """Tests for applying variant targets to main bot."""

    def test_baseline_unchanged(self):
        """Baseline variant returns original targets."""
        base = {
            "BASE_LONG": {"share": 0.3, "leverage": 3},
            "BASE_SHORT": {"share": 0.3, "leverage": 3},
            "VIRTUAL": {"share": 0.4, "leverage": 1},
        }
        indicator = HTF5MIndicator(1000.0)
        indicator.initialize(100.0)
        out = apply_variant_targets(base, "baseline", indicator)
        assert out == base
        assert out is not base  # deep copy

    def test_1pp_applies_bias(self):
        """1pp variant applies bias to LONG/SHORT."""
        base = {
            "BASE_LONG": {"share": 0.25, "leverage": 3},
            "BASE_SHORT": {"share": 0.25, "leverage": 3},
            "VIRTUAL": {"share": 0.5, "leverage": 1},
        }
        indicator = HTF5MIndicator(1000.0)
        indicator.initialize(100.0)
        # Force a LONG signal
        indicator.portfolio.measure_and_reset(Decimal('102'), 0, 0, 4)

        out = apply_variant_targets(base, "1pp", indicator)
        assert out["BASE_LONG"]["share"] == 0.26
        assert out["BASE_SHORT"]["share"] == 0.24
        assert out["VIRTUAL"]["share"] == 0.5

    def test_2pp_applies_bias(self):
        """2pp variant applies bias to LONG/SHORT."""
        base = {
            "BASE_LONG": {"share": 0.25, "leverage": 3},
            "BASE_SHORT": {"share": 0.25, "leverage": 3},
            "VIRTUAL": {"share": 0.5, "leverage": 1},
        }
        indicator = HTF5MIndicator(1000.0)
        indicator.initialize(100.0)
        indicator.portfolio.measure_and_reset(Decimal('104'), 0, 0, 4)

        out = apply_variant_targets(base, "2pp", indicator)
        assert out["BASE_LONG"]["share"] == 0.27
        assert out["BASE_SHORT"]["share"] == 0.23
        assert out["VIRTUAL"]["share"] == 0.5


class TestRunExperiment:
    """Integration test for run_htf_5m_experiment."""

    def test_monotonic_up_1pp(self):
        """Monotonic price up produces LONG signals."""
        # 60 bars = 12 5m intervals
        prices = [100.0 * (1.002 ** i) for i in range(60)]
        timestamps = [i * 60000 for i in range(60)]

        result = run_htf_5m_experiment(prices, timestamps, 0, 60, 1000.0)

        # Should have signals from interval 1 onwards (bar 4, 9, 14...)
        events = result["events"]
        assert len(events) > 0

        # All signals should be LONG for 1pp
        for event in events:
            if event["signals"]["1pp"] != "NEUTRAL":
                assert event["signals"]["1pp"] == "LONG"
            if event["signals"]["2pp"] != "NEUTRAL":
                assert event["signals"]["2pp"] == "LONG"

        # Check targets_by_variant
        targets_1pp = result["targets_by_variant"]["1pp"]
        assert len(targets_1pp) > 0
        for t in targets_1pp:
            if t["signal"] == "LONG":
                assert t["targets"]["LONG"] == 0.26
                assert t["targets"]["SHORT"] == 0.24

    def test_monotonic_down_1pp(self):
        """Monotonic price down produces SHORT signals."""
        prices = [100.0 * (0.998 ** i) for i in range(60)]
        timestamps = [i * 60000 for i in range(60)]

        result = run_htf_5m_experiment(prices, timestamps, 0, 60, 1000.0)

        for event in result["events"]:
            if event["signals"]["1pp"] != "NEUTRAL":
                assert event["signals"]["1pp"] == "SHORT"

    def test_noisy_no_signal(self):
        """Noisy price around flat produces no signals."""
        prices = [100.0 if i % 2 == 0 else 100.01 for i in range(60)]
        timestamps = [i * 60000 for i in range(60)]

        result = run_htf_5m_experiment(prices, timestamps, 0, 60, 1000.0)

        for event in result["events"]:
            assert event["signals"]["1pp"] == "NEUTRAL"
            assert event["signals"]["2pp"] == "NEUTRAL"


# Manual verification test
def test_manual_calculation():
    """
    Manual verification:
    - Start: 1000 capital, price 100
    - LONG: 250 margin, 3x = 750 qty at 100
    - SHORT: 250 margin, 3x = 750 qty at 100
    - VIRTUAL: 500 USDT
    - Price moves to 110 (+10%)
    - LONG value: 250 + 7.5*10 = 325 (32.5%)
    - SHORT value: 250 - 7.5*10 = 175 (17.5%)
    - VIRTUAL: 500 (50%)
    - excess_long = 32.5% - 25% = 7.5pp
    - 1pp signal: LONG -> targets 26/24/50
    - 2pp signal: LONG -> targets 27/23/50
    """
    portfolio = Virtual5MPortfolio(Decimal('1000'))
    portfolio.initialize(Decimal('100'))
    vals = portfolio.revalue(Decimal('110'))

    assert vals["long"] == Decimal('325')
    assert vals["short"] == Decimal('175')
    assert vals["virtual"] == Decimal('500')
    assert vals["total"] == Decimal('1000')

    w_long, w_short, w_virt = portfolio.get_shares(Decimal('110'))
    assert float(w_long) == 0.325
    assert float(w_short) == 0.175
    assert float(w_virt) == 0.5

    excess_long = (w_long - Decimal('0.25')) * Decimal('100')
    assert float(excess_long) == 7.5

    event = portfolio.measure_and_reset(Decimal('110'), 0, 0, 4)
    assert event["signals"]["1pp"] == "LONG"
    assert event["signals"]["2pp"] == "LONG"
    assert event["targets"]["1pp"]["LONG"] == 0.26
    assert event["targets"]["2pp"]["LONG"] == 0.27