import json
from dataclasses import dataclass
from pydantic import BaseModel, ValidationError
import openai
from tenacity import Retrying, stop_after_attempt, wait_exponential_jitter, retry_if_exception_type

from huntloop.config import load_config, resolve_llm_api_key

"""OPS-06. This is the ONLY module in HuntLoop permitted to import `openai` or construct a model client. Triage (SCOR-05), dimension scoring (SCOR-06) and non-ATS careers-page extraction (DISC-05) all route through `complete_json` so a user who points `HUNTLOOP_OPENAI_BASE_URL` at their own endpoint moves every model call at once."""

class LlmResponseError(RuntimeError):
    """The model returned something that is not a valid instance of the requested schema."""

@dataclass(frozen=True)
class LlmUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""

@dataclass(frozen=True)
class LlmCall:
    content: dict
    usage: LlmUsage
    model: str

def get_llm_client(credentials_session) -> openai.OpenAI:
    cfg = load_config()
    return openai.OpenAI(
        base_url=cfg.openai_base_url,
        api_key=resolve_llm_api_key(credentials_session),
        timeout=60.0,
        max_retries=0,   # tenacity owns retries here, not the SDK, so the policy is one place
    )

def complete_json(client: openai.OpenAI, *, model: str, system: str, user: str,
                  schema: type[BaseModel], temperature: float = 0.0,
                  max_tokens: int | None = None) -> LlmCall:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user}
    ]
    kwargs = {"top_p": 1.0}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
        
    for attempt in Retrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=20),
        retry=retry_if_exception_type((openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError)),
        reraise=True
    ):
        with attempt:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                response_format={"type": "json_object"},
                **kwargs
            )
            
    content = response.choices[0].message.content
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        raise LlmResponseError(f"{model} returned non-JSON content: {content[:200]!r}")
        
    try:
        validated = schema.model_validate(parsed)
    except ValidationError as exc:
        raise LlmResponseError(f"{model} response did not match {schema.__name__}: {exc}") from exc
        
    pt = getattr(response.usage, "prompt_tokens", 0) or 0
    ct = getattr(response.usage, "completion_tokens", 0) or 0
    usage = LlmUsage(prompt_tokens=pt, completion_tokens=ct, model=model)
    
    return LlmCall(content=validated.model_dump(), usage=usage, model=model)
