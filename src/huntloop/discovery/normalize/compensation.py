import enum
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
import pycountry
from huntloop.criteria.schema import CompensationFloor

@dataclass(frozen=True)
class NormalizedCompensation:
    minimum: Decimal | None
    maximum: Decimal | None
    currency: str | None
    period: str | None
    raw: str | None
    source: Literal["structured", "text", "absent"]

class FloorComparison(str, enum.Enum):
    ABOVE = "above"
    BELOW = "below"
    NOT_COMPARABLE = "not_comparable"
    NO_DATA = "no_data"

CURRENCY_SYMBOLS = {
    "$": "USD", "US$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", 
    "¥": "JPY", "C$": "CAD", "A$": "AUD", "SGD": "SGD", "CHF": "CHF"
}

PERIOD_PATTERNS = {
    r'per year|per annum|annually|/yr|/year|p\.a\.': 'annual',
    r'per month|monthly|/mo': 'monthly',
    r'per hour|hourly|/hr|/hour': 'hourly'
}

def normalize_compensation(listing) -> NormalizedCompensation:
    if getattr(listing, 'comp_min', None) is not None or getattr(listing, 'comp_max', None) is not None:
        return NormalizedCompensation(
            minimum=listing.comp_min,
            maximum=listing.comp_max,
            currency=listing.comp_currency,
            period=listing.comp_period,
            raw=listing.comp_raw,
            source="structured"
        )
        
    text = getattr(listing, 'description_plain', None) or getattr(listing, 'description_html', None) or ""
    if not text:
        return NormalizedCompensation(None, None, None, None, None, "absent")
        
    # Match currency symbol/code, then number (with optional commas/lakh grouping), optional k/K, optional range and second number
    symbol_opts = "|".join(re.escape(s) for s in CURRENCY_SYMBOLS.keys())
    # Add 3-letter codes
    symbol_pattern = f"({symbol_opts}|[A-Z]{{3}})"
    
    number_pattern = r"(\d{1,3}(?:[,]\d{2,3})*(?:\.\d+)?|\d+(?:\.\d+)?)([kK])?"
    range_pattern = rf"(?:\s*[-–—to]+\s*{symbol_pattern}?\s*{number_pattern})?"
    
    full_pattern = rf"{symbol_pattern}?\s*{number_pattern}{range_pattern}"
    
    for match in re.finditer(full_pattern, text):
        raw_match = match.group(0)
        # Verify it's actually a money pattern (has a symbol or looks explicitly like salary)
        sym1 = match.group(1)
        num1 = match.group(2)
        mult1 = match.group(3)
        sym2 = match.group(4)
        num2 = match.group(5)
        mult2 = match.group(6)
        
        if not sym1 and not sym2:
            # If no symbol, maybe it's just a number. Check if period follows closely.
            # But let's check pycountry if it was a 3-letter code
            pass
            
        currency = None
        if sym1:
            if sym1 in CURRENCY_SYMBOLS:
                currency = CURRENCY_SYMBOLS[sym1]
            elif pycountry.currencies.get(alpha_3=sym1.upper()):
                currency = sym1.upper()
        if not currency and sym2:
            if sym2 in CURRENCY_SYMBOLS:
                currency = CURRENCY_SYMBOLS[sym2]
            elif pycountry.currencies.get(alpha_3=sym2.upper()):
                currency = sym2.upper()
                
        if not currency:
            continue
            
        try:
            val1 = Decimal(num1.replace(",", ""))
            if mult1 and mult1.lower() == 'k':
                val1 *= 1000
                
            val2 = None
            if num2:
                val2 = Decimal(num2.replace(",", ""))
                if mult2 and mult2.lower() == 'k':
                    val2 *= 1000
                    
            period = None
            end_pos = match.end()
            lookahead = text[end_pos:end_pos+40].lower()
            for pat, per in PERIOD_PATTERNS.items():
                if re.search(pat, lookahead):
                    period = per
                    break
                    
            return NormalizedCompensation(
                minimum=val1,
                maximum=val2,
                currency=currency,
                period=period,
                raw=raw_match,
                source="text"
            )
        except Exception:
            continue
            
    return NormalizedCompensation(None, None, None, None, None, "absent")


def compare_to_floor(comp: NormalizedCompensation, floor: CompensationFloor) -> FloorComparison:
    """
    There is no currency conversion in HuntLoop v1. A cross-currency or cross-period pair 
    returns NOT_COMPARABLE, which the caller turns into an advisory flag — never into a 
    filter decision. SCOR-04 forbids comparing wrongly; 02-CONTEXT.md defers conversion entirely.
    """
    if floor.amount is None or comp.source == "absent" or comp.minimum is None:
        return FloorComparison.NO_DATA
    if floor.currency is None or comp.currency is None or comp.currency != floor.currency:
        return FloorComparison.NOT_COMPARABLE
    if comp.period is None or comp.period != floor.period:
        return FloorComparison.NOT_COMPARABLE
    return FloorComparison.ABOVE if comp.minimum >= floor.amount else FloorComparison.BELOW
