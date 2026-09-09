from huntloop.db.models import AtsPlatform
from huntloop.discovery.ats import ashby, greenhouse, lever
from huntloop.discovery.ats.base import AtsAdapter


class UnsupportedPlatform(KeyError):
    """Raised for an AtsPlatform value that has no Phase 2 adapter (workday, smartrecruiters)."""


ADAPTERS: dict[str, AtsAdapter] = {
    AtsPlatform.GREENHOUSE.value: greenhouse.ADAPTER,
    AtsPlatform.LEVER.value: lever.ADAPTER,
    AtsPlatform.ASHBY.value: ashby.ADAPTER,
}


def get_adapter(platform: str) -> AtsAdapter:
    try:
        return ADAPTERS[platform]
    except KeyError as exc:
        raise UnsupportedPlatform(
            f"No Phase 2 ATS adapter for {platform!r}. Supported: {sorted(ADAPTERS)}. "
            "Workday and SmartRecruiters are explicitly v2 scope (DISC-07/DISC-08)."
        ) from exc
