import pytest

def test_greenhouse_cobaltio_jobs(ats_fixture):
    data = ats_fixture("greenhouse_cobaltio_jobs")
    assert "jobs" in data
    assert len(data["jobs"]) > 0
    # Must preserve HTML entity encoding
    import json
    raw = json.dumps(data)
    assert "&lt;" in raw

def test_lever_spotify_postings(ats_fixture):
    data = ats_fixture("lever_spotify_postings")
    assert isinstance(data, list)
    assert len(data) > 0

def test_greenhouse_empty(ats_fixture):
    data = ats_fixture("greenhouse_empty")
    assert data["jobs"] == []
    assert data.get("meta", {}).get("total") == 0

def test_lever_empty(ats_fixture):
    data = ats_fixture("lever_empty")
    assert data == []

def test_ashby_empty(ats_fixture):
    data = ats_fixture("ashby_empty")
    assert data["jobs"] == []

def test_newrocket_careers(html_fixture):
    html = html_fixture("newrocket_careers")
    assert "boards-api.greenhouse.io/v1/boards/highmetric/jobs" in html

def test_cobaltio_careers(html_fixture):
    html = html_fixture("cobaltio_careers")
    assert "greenhouse.io/cobaltio" in html

def test_ramp_careers(html_fixture):
    html = html_fixture("ramp_careers")
    assert "ashbyhq.com/ramp/" in html

def test_lifeatspotify_careers(html_fixture):
    html = html_fixture("lifeatspotify_careers")
    assert "lever.co" not in html.lower()

def test_generic_nonats_careers(html_fixture):
    html = html_fixture("generic_nonats_careers").lower()
    assert "greenhouse" not in html
    assert "lever.co" not in html
    assert "ashbyhq" not in html
