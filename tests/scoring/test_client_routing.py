import os
import pytest
from pathlib import Path
from pydantic import BaseModel
import openai
from unittest.mock import patch
from huntloop.config import ConfigError
from huntloop.llm.client import get_llm_client, complete_json, LlmResponseError, LlmUsage

class DummySchema(BaseModel):
    name: str
    score: int

class FakeUsage:
    def __init__(self, pt, ct):
        self.prompt_tokens = pt
        self.completion_tokens = ct

class FakeMessage:
    def __init__(self, content):
        self.content = content

class FakeChoice:
    def __init__(self, content):
        self.message = FakeMessage(content)

class FakeResponse:
    def __init__(self, content, pt=10, ct=5):
        self.choices = [FakeChoice(content)]
        self.usage = FakeUsage(pt, ct)

class FakeCompletions:
    def __init__(self):
        self.kwargs = {}
        self.responses = []
        
    def create(self, **kwargs):
        self.kwargs = kwargs
        if not self.responses:
            raise openai.APIConnectionError(request=None)
        res = self.responses.pop(0)
        if isinstance(res, Exception):
            raise res
        return res

class FakeChat:
    def __init__(self):
        self.completions = FakeCompletions()

class FakeClient:
    def __init__(self):
        self.chat = FakeChat()

def test_get_llm_client_base_url(credentials_session, monkeypatch):
    monkeypatch.setenv("HUNTLOOP_OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("HUNTLOOP_OPENAI_API_KEY", "dummy")
    client = get_llm_client(credentials_session)
    assert str(client.base_url) == "http://localhost:11434/v1/"
    assert client.max_retries == 0

def test_get_llm_client_no_key(credentials_session, monkeypatch):
    monkeypatch.delenv("HUNTLOOP_OPENAI_API_KEY", raising=False)
    # Ensure no db key
    with pytest.raises(ConfigError):
        get_llm_client(credentials_session)

def test_complete_json_success():
    client = FakeClient()
    client.chat.completions.responses.append(FakeResponse('{"name": "test", "score": 5}', pt=20, ct=10))
    
    call = complete_json(client, model="m", system="s", user="u", schema=DummySchema)
    assert call.model == "m"
    assert call.content == {"name": "test", "score": 5}
    assert call.usage.prompt_tokens == 20
    assert call.usage.completion_tokens == 10
    assert client.chat.completions.kwargs["temperature"] == 0.0
    assert client.chat.completions.kwargs["response_format"] == {"type": "json_object"}

def test_complete_json_validation_error():
    client = FakeClient()
    client.chat.completions.responses.append(FakeResponse('{"name": "test"}')) # Missing score
    
    with pytest.raises(LlmResponseError, match="response did not match DummySchema"):
        complete_json(client, model="m", system="s", user="u", schema=DummySchema)

def test_complete_json_invalid_json():
    client = FakeClient()
    client.chat.completions.responses.append(FakeResponse('not json'))
    
    with pytest.raises(LlmResponseError, match="returned non-JSON content"):
        complete_json(client, model="m", system="s", user="u", schema=DummySchema)

def test_complete_json_retry_success():
    client = FakeClient()
    # First fails with connection error, second succeeds
    client.chat.completions.responses.append(openai.APIConnectionError(request=None))
    client.chat.completions.responses.append(FakeResponse('{"name": "test", "score": 5}'))
    
    with patch("huntloop.llm.client.wait_exponential_jitter", return_value=lambda x: 0):
        call = complete_json(client, model="m", system="s", user="u", schema=DummySchema)
    assert call.content == {"name": "test", "score": 5}

def test_complete_json_no_retry_on_bad_request():
    client = FakeClient()
    import httpx
    response = httpx.Response(status_code=400, request=httpx.Request("GET", "http://localhost"))
    client.chat.completions.responses.append(openai.BadRequestError("bad", response=response, body=None))
    
    with pytest.raises(openai.BadRequestError):
        complete_json(client, model="m", system="s", user="u", schema=DummySchema)

def test_openai_import_is_confined_to_llm_client():
    src_dir = Path(__file__).parent.parent.parent / "src" / "huntloop"
    assert src_dir.exists()
    
    bad_files = []
    for py_file in src_dir.rglob("*.py"):
        if py_file.name == "client.py" and py_file.parent.name == "llm":
            continue
        text = py_file.read_text()
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("import openai") or line.startswith("from openai"):
                bad_files.append(str(py_file))
                break
                
    assert not bad_files, f"openai imported outside huntloop/llm/client.py: {bad_files}"
