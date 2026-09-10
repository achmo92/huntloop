import pytest
import json
from pathlib import Path
from huntloop.discovery.normalize.location import normalize_location, RemoteScopeGuess

def test_normalize_location_basic():
    loc = normalize_location("Bengaluru, India")
    assert loc.country_code == "IN"
    assert loc.city == "Bengaluru"
    assert loc.is_remote is False or loc.is_remote is None
    assert loc.ambiguous is False

def test_normalize_location_remote_us():
    loc = normalize_location("Remote — US")
    assert loc.is_remote is True
    assert loc.country_code == "US"
    assert loc.remote_scope == RemoteScopeGuess.COUNTRY

def test_normalize_location_remote_emea():
    loc = normalize_location("Remote (EMEA)")
    assert loc.is_remote is True
    assert loc.region == "EMEA"
    assert loc.remote_scope == RemoteScopeGuess.REGION
    assert loc.country_code is None

def test_normalize_location_remote_ambiguous():
    loc = normalize_location("Remote")
    assert loc.is_remote is True
    assert loc.remote_scope == RemoteScopeGuess.UNSPECIFIED
    assert loc.ambiguous is True

def test_normalize_location_worldwide():
    loc = normalize_location("Fully remote, worldwide")
    assert loc.is_remote is True
    assert loc.remote_scope == RemoteScopeGuess.GLOBAL
    assert loc.ambiguous is False

def test_normalize_location_hybrid():
    loc = normalize_location("Hybrid — New York, NY")
    assert loc.is_remote is False
    assert loc.city == "New York"
    assert loc.region == "NY"
    assert loc.country_code == "US"

def test_normalize_location_us_city_state():
    loc = normalize_location("Austin, TX")
    assert loc.country_code == "US"
    assert loc.region == "TX"
    assert loc.city == "Austin"

def test_normalize_location_gibberish():
    loc = normalize_location("Zzzz Qqqq")
    assert loc.ambiguous is True
    assert loc.country_code is None

def test_normalize_location_none():
    loc = normalize_location(None)
    assert loc.ambiguous is True
    assert loc.raw is None
    assert loc.display == ""

def test_normalize_location_platform_override():
    loc = normalize_location("Somewhere odd", platform_country="GB", platform_is_remote=True, platform_workplace_type="Remote")
    assert loc.country_code == "GB"
    assert loc.is_remote is True
    assert loc.remote_scope == RemoteScopeGuess.COUNTRY
    assert loc.ambiguous is False

def test_normalize_location_secondary_locations():
    loc = normalize_location("Remote", platform_country="US", platform_is_remote=True, secondary_locations=["London, UK", "Berlin, Germany"])
    assert loc.remote_scope == RemoteScopeGuess.COUNTRY
    assert "GB" in loc.display
    assert "DE" in loc.display

def test_normalize_location_pycountry_alias():
    loc = normalize_location("United Kingdom")
    assert loc.country_code == "GB"

def test_normalize_location_remote_scope_never_none():
    loc = normalize_location("Some place")
    assert loc.remote_scope is not None

def test_fixtures_location_parsing():
    import json
    fixtures_dir = Path(__file__).parent.parent / "fixtures" / "ats"
    
    locations = []
    
    lever_file = fixtures_dir / "lever_spotify_postings.json"
    if lever_file.exists():
        data = json.loads(lever_file.read_text())
        for d in data:
            loc = (d.get("categories") or {}).get("location")
            if loc: locations.append(loc)
            
    ashby_file = fixtures_dir / "ashby_ramp_jobs.json"
    if ashby_file.exists():
        data = json.loads(ashby_file.read_text())
        for job in data.get("jobs", []):
            loc = job.get("location")
            if loc: locations.append(loc)
            
    for l in locations:
        res = normalize_location(l)
        assert res.remote_scope is not None
