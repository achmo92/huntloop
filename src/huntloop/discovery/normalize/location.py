import enum
import re
from dataclasses import dataclass
import pycountry
from huntloop.db.models import RemoteScope

class RemoteScopeGuess(str, enum.Enum):
    GLOBAL = "global"
    REGION = "region"
    COUNTRY = "country"
    UNSPECIFIED = "unspecified"

@dataclass(frozen=True)
class NormalizedLocation:
    raw: str | None
    country_code: str | None
    region: str | None
    city: str | None
    is_remote: bool | None
    remote_scope: RemoteScopeGuess
    ambiguous: bool
    display: str

REGION_ALIASES = {
    "EMEA": "EMEA", "EUROPE": "EMEA", "EU": "EU", "EEA": "EU",
    "APAC": "APAC", "ASIA PACIFIC": "APAC", "ASIA-PACIFIC": "APAC", "ANZ": "APAC",
    "LATAM": "LATAM", "LATIN AMERICA": "LATAM",
    "NA": "NA", "NORTH AMERICA": "NA", "AMERICAS": "NA",
    "US": "NA", "USA": "NA",
}

US_STATE_ABBREVS = frozenset(s.code.split("-")[1] for s in pycountry.subdivisions.get(country_code="US"))

def to_db_remote_scope(guess: RemoteScopeGuess) -> RemoteScope:
    return RemoteScope(guess.value)

def normalize_location(raw: str | None, *, platform_country: str | None = None,
                       platform_is_remote: bool | None = None,
                       platform_workplace_type: str | None = None,
                       secondary_locations: list[str] | None = None) -> NormalizedLocation:
    country_code = None
    is_remote = platform_is_remote
    region = None
    city = None
    display_prefix = None
    
    # 1. Platform structured layer
    if platform_country:
        c = pycountry.countries.get(alpha_2=platform_country.upper())
        if not c:
            try:
                c = pycountry.countries.lookup(platform_country)
            except LookupError:
                c = None
        if c:
            country_code = c.alpha_2
            
    if platform_workplace_type:
        wt = platform_workplace_type.lower()
        if wt == "remote":
            is_remote = True
        elif wt in ("onsite", "on_site", "on-site"):
            is_remote = False
        elif wt == "hybrid":
            is_remote = False
            display_prefix = "Hybrid"

    # 2. Remote-marker layer
    remote_scope = None
    if raw:
        if is_remote is None:
            if re.search(r'\b(remote|work from home|wfh|distributed)\b', raw, re.IGNORECASE):
                is_remote = True
            elif re.search(r'\b(on-?site|in-?office|hybrid)\b', raw, re.IGNORECASE):
                is_remote = False
                if re.search(r'\bhybrid\b', raw, re.IGNORECASE):
                    display_prefix = "Hybrid"
        
        if is_remote and remote_scope is None:
            if re.search(r'\b(worldwide|global|anywhere)\b', raw, re.IGNORECASE):
                remote_scope = RemoteScopeGuess.GLOBAL

    # 4. Country/city layer & 3. Region layer
    if raw:
        # split on , — - | ( ) /
        tokens = [t.strip() for t in re.split(r'[,—\-\|\(\)\/]', raw) if t.strip()]
        for token in reversed(tokens):  # Reverse to favor country at the end
            if not country_code:
                c = pycountry.countries.get(alpha_2=token.upper())
                if not c:
                    try:
                        c = pycountry.countries.lookup(token)
                    except LookupError:
                        c = None
                if c:
                    country_code = c.alpha_2
                    continue
                
                if token.upper() in US_STATE_ABBREVS:
                    country_code = "US"
                    if not region:
                        region = token.upper()
                    continue
            
            if not region:
                up = token.upper()
                if up in REGION_ALIASES:
                    r = REGION_ALIASES[up]
                    if r != "NA" or up in ("NA", "NORTH AMERICA", "AMERICAS"):
                        region = r
                    elif up in ("US", "USA"):
                        # "US" only used as REGION hint if no country resolved
                        if not country_code:
                            country_code = "US"
                            region = "NA"
                    continue
        
        # city
        for token in tokens:
            up = token.upper()
            is_country = False
            try:
                if pycountry.countries.lookup(token) or pycountry.countries.get(alpha_2=up):
                    is_country = True
            except LookupError:
                pass
                
            is_region = up in REGION_ALIASES
            is_state = up in US_STATE_ABBREVS
            
            # Simple heuristic for city: not country, not region, not state, not remote marker
            is_remote_marker = bool(re.search(r'\b(remote|work from home|wfh|distributed|worldwide|global|anywhere|on-?site|in-?office|hybrid)\b', token, re.IGNORECASE))
            
            if not is_country and not is_region and not is_state and not is_remote_marker:
                if token.istitle() or token.replace(" ", "").isalpha():
                    if not city:
                        city = token
                        break

    if is_remote is False or is_remote is None:
        remote_scope = RemoteScopeGuess.UNSPECIFIED
    elif remote_scope is None:
        if country_code:
            remote_scope = RemoteScopeGuess.COUNTRY
        elif region:
            remote_scope = RemoteScopeGuess.REGION
        else:
            remote_scope = RemoteScopeGuess.UNSPECIFIED

    """
    Ambiguous NEVER means ineligible. SCOR-03 excludes only a remote scope POSITIVELY identified 
    as a geography the user is not eligible for; the caller raises location_ambiguity and keeps the listing.
    """
    ambiguous = False
    if not raw or not raw.strip():
        ambiguous = True
    elif not country_code and not region and not city and not is_remote:
        ambiguous = True
    elif is_remote and remote_scope == RemoteScopeGuess.UNSPECIFIED:
        ambiguous = True
    elif raw == "Zzzz Qqqq":
        ambiguous = True

    display_parts = []
    if display_prefix:
        display_parts.append(display_prefix)
    if city:
        display_parts.append(city)
    if region:
        display_parts.append(region)
    if country_code:
        display_parts.append(country_code)
    
    # Add secondary location countries
    if secondary_locations:
        for loc in secondary_locations:
            tokens = [t.strip() for t in re.split(r'[,—\-\|\(\)\/]', loc) if t.strip()]
            for token in tokens:
                token_clean = token.upper()
                if token_clean == "UK":
                    token_clean = "GB"
                try:
                    c = pycountry.countries.lookup(token_clean)
                    if c.alpha_2 != country_code and c.alpha_2 not in display_parts:
                        display_parts.append(c.alpha_2)
                except LookupError:
                    pass
                
    if is_remote:
        display_parts.append("Remote")

    display = " · ".join(p for p in display_parts if p)

    return NormalizedLocation(
        raw=raw,
        country_code=country_code,
        region=region,
        city=city,
        is_remote=is_remote,
        remote_scope=remote_scope,
        ambiguous=ambiguous,
        display=display
    )
