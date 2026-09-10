from decimal import Decimal
from datetime import datetime, timezone
import pytest

from huntloop.criteria.schema import CompensationFloor
from huntloop.discovery.normalize.compensation import (
    normalize_compensation, compare_to_floor, FloorComparison, NormalizedCompensation
)
from huntloop.discovery.normalize.timestamps import to_utc

class DummyListing:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

class TestCompensation:
    def test_structured_ashby(self):
        listing = DummyListing(comp_min=Decimal("150000"), comp_max=None, comp_currency="USD", comp_period="annual", comp_raw=None)
        comp = normalize_compensation(listing)
        assert comp.source == "structured"
        assert comp.minimum == Decimal("150000")
        assert comp.currency == "USD"
        assert comp.period == "annual"

    def test_text_basic_range(self):
        listing = DummyListing(description_plain="We pay $150,000 - $190,000 per year.")
        comp = normalize_compensation(listing)
        assert comp.source == "text"
        assert comp.minimum == Decimal("150000")
        assert comp.maximum == Decimal("190000")
        assert comp.currency == "USD"
        assert comp.period == "annual"

    def test_text_inr_lakhs(self):
        listing = DummyListing(description_plain="₹40,00,000 per annum")
        comp = normalize_compensation(listing)
        assert comp.currency == "INR"
        assert comp.minimum == Decimal("4000000")

    def test_text_k_suffix_euro(self):
        listing = DummyListing(description_plain="Salary: €90k–€120k.")
        comp = normalize_compensation(listing)
        assert comp.currency == "EUR"
        assert comp.minimum == Decimal("90000")
        assert comp.maximum == Decimal("120000")

    def test_text_hourly_gbp(self):
        listing = DummyListing(description_plain="Pay is £60 per hour")
        comp = normalize_compensation(listing)
        assert comp.currency == "GBP"
        assert comp.period == "hourly"

    def test_text_absent(self):
        listing = DummyListing(description_plain="Competitive salary")
        comp = normalize_compensation(listing)
        assert comp.source == "absent"
        assert comp.minimum is None

    def test_compare_above(self):
        comp = NormalizedCompensation(minimum=Decimal("150000"), maximum=None, currency="USD", period="annual", raw=None, source="structured")
        floor = CompensationFloor(amount=Decimal("120000"), currency="USD", period="annual")
        assert compare_to_floor(comp, floor) == FloorComparison.ABOVE

    def test_compare_below(self):
        comp = NormalizedCompensation(minimum=Decimal("90000"), maximum=None, currency="USD", period="annual", raw=None, source="structured")
        floor = CompensationFloor(amount=Decimal("120000"), currency="USD", period="annual")
        assert compare_to_floor(comp, floor) == FloorComparison.BELOW

    def test_compare_not_comparable_currency(self):
        comp = NormalizedCompensation(minimum=Decimal("4000000"), maximum=None, currency="INR", period="annual", raw=None, source="structured")
        floor = CompensationFloor(amount=Decimal("120000"), currency="USD", period="annual")
        assert compare_to_floor(comp, floor) == FloorComparison.NOT_COMPARABLE

    def test_compare_no_data_absent(self):
        comp = NormalizedCompensation(minimum=None, maximum=None, currency=None, period=None, raw=None, source="absent")
        floor = CompensationFloor(amount=Decimal("120000"), currency="USD", period="annual")
        assert compare_to_floor(comp, floor) == FloorComparison.NO_DATA

    def test_compare_not_comparable_period(self):
        comp = NormalizedCompensation(minimum=Decimal("60"), maximum=None, currency="USD", period="hourly", raw=None, source="structured")
        floor = CompensationFloor(amount=Decimal("120000"), currency="USD", period="annual")
        assert compare_to_floor(comp, floor) == FloorComparison.NOT_COMPARABLE

    def test_compare_no_data_no_floor(self):
        comp = NormalizedCompensation(minimum=Decimal("60"), maximum=None, currency="USD", period="hourly", raw=None, source="structured")
        floor = CompensationFloor(amount=None)
        assert compare_to_floor(comp, floor) == FloorComparison.NO_DATA

    def test_compare_cross_currency_never_below_even_if_numerically_smaller(self):
        # Even if comp is INR 40,00,000 and floor is USD 120,000 (so 40,00,000 > 120,000), it's NOT_COMPARABLE
        comp = NormalizedCompensation(minimum=Decimal("4000000"), maximum=None, currency="INR", period="annual", raw=None, source="structured")
        floor = CompensationFloor(amount=Decimal("120000"), currency="USD", period="annual")
        assert compare_to_floor(comp, floor) == FloorComparison.NOT_COMPARABLE
        
        # Or if comp is INR 50,000 and floor is USD 120,000 (so 50,000 < 120,000), it's NOT_COMPARABLE, not BELOW
        comp = NormalizedCompensation(minimum=Decimal("50000"), maximum=None, currency="INR", period="annual", raw=None, source="structured")
        floor = CompensationFloor(amount=Decimal("120000"), currency="USD", period="annual")
        assert compare_to_floor(comp, floor) == FloorComparison.NOT_COMPARABLE


class TestTimestamps:
    def test_to_utc_iso_offset(self):
        dt = to_utc("2026-08-01T10:00:00+05:30")
        assert dt.tzinfo is not None
        assert dt.hour == 4
        assert dt.minute == 30

    def test_to_utc_epoch_ms(self):
        # 1754035200000 is some date
        dt = to_utc(1754035200000, epoch_unit="ms")
        assert dt.tzinfo is not None

    def test_to_utc_naive(self):
        dt = to_utc("2026-08-01T10:00:00")
        assert dt.tzinfo is not None
        assert dt.hour == 10

    def test_to_utc_none_or_invalid(self):
        assert to_utc(None) is None
        assert to_utc("not a date") is None
