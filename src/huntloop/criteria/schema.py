from decimal import Decimal
from typing import Literal
import pycountry
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SENIORITY_LADDER = ["intern", "junior", "mid", "senior", "staff", "principal", "director", "vp", "c_level"]

class DimensionWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role_fit: float
    seniority_fit: float
    employer_fit: float
    trajectory: float

    @field_validator("role_fit", "seniority_fit", "employer_fit", "trajectory")
    @classmethod
    def weight_ge_zero(cls, v: float) -> float:
        if v < 0:
            raise ValueError("Weight must be >= 0")
        return v

    @model_validator(mode="after")
    def sum_gt_zero(self) -> "DimensionWeights":
        if self.role_fit + self.seniority_fit + self.employer_fit + self.trajectory == 0:
            raise ValueError("dimension_weights sum must be > 0")
        return self

class CompensationFloor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: Decimal | None = None
    currency: str | None = None
    period: Literal["annual", "monthly", "hourly"] = "annual"

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v_upper = v.upper()
        if pycountry.currencies.get(alpha_3=v_upper) is None:
            raise ValueError(f"Invalid currency code: {v}")
        return v_upper

class LocationCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")
    eligible_countries: list[str] = Field(default_factory=list)
    eligible_regions: list[str] = Field(default_factory=list)
    preferred_cities: list[str] = Field(default_factory=list)

    @field_validator("eligible_countries")
    @classmethod
    def valid_countries(cls, v: list[str]) -> list[str]:
        out = []
        for code in v:
            c = code.upper()
            if pycountry.countries.get(alpha_2=c) is None:
                raise ValueError(f"Invalid country code: {code}")
            out.append(c)
        return out

    @field_validator("eligible_regions")
    @classmethod
    def valid_regions(cls, v: list[str]) -> list[str]:
        valid = {"EMEA", "APAC", "LATAM", "NA", "EU"}
        out = []
        for r in v:
            r_up = r.upper()
            if r_up not in valid:
                raise ValueError(f"Invalid region: {r}")
            out.append(r_up)
        return out

class Exclusions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title_keywords: list[str] = Field(default_factory=list)
    employers: list[str] = Field(default_factory=list)

class WorkAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid")
    countries_authorized: list[str] = Field(default_factory=list)
    requires_sponsorship: bool = False

    @field_validator("countries_authorized")
    @classmethod
    def valid_countries(cls, v: list[str]) -> list[str]:
        out = []
        for code in v:
            c = code.upper()
            if pycountry.countries.get(alpha_2=c) is None:
                raise ValueError(f"Invalid country code: {code}")
            out.append(c)
        return out

class CriteriaPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_summary: str
    seniority_min: str | None = None
    seniority_max: str | None = None
    posting_age_days: int = Field(default=30, ge=1)
    locations: LocationCriteria = Field(default_factory=LocationCriteria)
    compensation_floor: CompensationFloor = Field(default_factory=CompensationFloor)
    exclusions: Exclusions = Field(default_factory=Exclusions)
    work_authorization: WorkAuthorization = Field(default_factory=WorkAuthorization)
    dimension_weights: DimensionWeights

    @field_validator("seniority_min", "seniority_max")
    @classmethod
    def valid_seniority(cls, v: str | None) -> str | None:
        if v is not None and v not in SENIORITY_LADDER:
            raise ValueError(f"Invalid seniority: {v}")
        return v

    @model_validator(mode="after")
    def min_le_max(self) -> "CriteriaPayload":
        if self.seniority_min and self.seniority_max:
            idx_min = SENIORITY_LADDER.index(self.seniority_min)
            idx_max = SENIORITY_LADDER.index(self.seniority_max)
            if idx_min > idx_max:
                raise ValueError(f"seniority_min ({self.seniority_min}) ranked above seniority_max ({self.seniority_max})")
        return self
