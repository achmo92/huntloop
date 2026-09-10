from datetime import datetime, timezone
from typing import Literal

PLATFORM_EPOCH_UNIT = {"lever": "ms", "greenhouse": "s", "ashby": "s"}

def to_utc(value: str | int | float | datetime | None, *, epoch_unit: Literal["s", "ms"] = "s") -> datetime | None:
    """Return a tz-aware UTC datetime, or None. Naive input is assumed UTC."""
    if value is None:
        return None
        
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
        
    if isinstance(value, (int, float)):
        if epoch_unit == "ms":
            value = value / 1000.0
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
            
    if isinstance(value, str):
        # Handle ISO with trailing Z
        v_str = value.replace('Z', '+00:00')
        try:
            dt = datetime.fromisoformat(v_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass
            
        # Fallbacks
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%f%z"):
            try:
                dt = datetime.strptime(v_str, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except ValueError:
                pass
                
    return None
