from pathlib import Path
import yaml
from sqlalchemy import select, func, update
from sqlalchemy.orm import Session

from huntloop.db.models import Criteria, CriteriaSource
from huntloop.criteria.schema import CriteriaPayload


def load_criteria_yaml(path: Path) -> CriteriaPayload:
    if not path.exists():
        raise FileNotFoundError(f"Criteria file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        data = {}
    return CriteriaPayload.model_validate(data)

def save_new_criteria_version(session: Session, payload: CriteriaPayload,
                              source: CriteriaSource = CriteriaSource.MANUAL_EDIT) -> int:
    """Insert a NEW criteria row at max(version)+1, mark it active, deactivate the rest.
    Returns the new version number. Never UPDATEs an existing criteria row. 
    INTK-07 (Phase 4) requires every change to create a new version; this CLI path is 
    the first writer to that table and must establish the behaviour."""
    current_max = session.execute(select(func.max(Criteria.version))).scalar()
    new_version = (current_max or 0) + 1
    session.execute(update(Criteria).values(is_active=False))   # deactivate all prior
    session.add(Criteria(
        version=new_version,
        is_active=True,
        payload=payload.model_dump(mode="json"),
        source=source
    ))
    session.flush()
    return new_version

def get_active_criteria(session: Session) -> tuple[int, CriteriaPayload] | None:
    row = session.execute(select(Criteria).where(Criteria.is_active == True)).scalar_one_or_none()
    if row is None:
        return None
    return row.version, CriteriaPayload.model_validate(row.payload)
