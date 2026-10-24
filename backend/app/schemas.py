"""Request models. Every model forbids unknown fields (no mass assignment), bounds every string,
and validates URLs to http(s) only, so values like `javascript:` can't be stored and later rendered."""
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import Query
from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def S(max_len: int, min_len: int = 0):
    return Annotated[str, StringConstraints(strip_whitespace=True, min_length=min_len, max_length=max_len)]


Name = S(100, 1)
Url = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500, pattern=r"^https?://[^\s<>\"']+$")]
HttpsUrl = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500, pattern=r"^https://[^\s<>\"']+$")]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\+?[0-9 ()-]{7,20}$")]


# --- Pagination ----------------------------------------------------------------------------------
class Page:
    def __init__(self, limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=10_000)):
        self.limit, self.offset = limit, offset


def paged(rows, page: Page) -> dict:
    total = rows[0]["total_count"] if rows else 0
    items = [{k: v for k, v in dict(r).items() if k != "total_count"} for r in rows]
    return {"items": items, "total": total, "limit": page.limit, "offset": page.offset}


# --- Auth ------------------------------------------------------------------------------------------
Portal = Literal["applicant", "recruiter"]
Password = Annotated[str, StringConstraints(min_length=8, max_length=72)]


class SignupIn(Strict):
    email: EmailStr
    password: Password
    portal: Portal

    @field_validator("password")
    @classmethod
    def strength(cls, v: str) -> str:
        if v.lower() == v or v.upper() == v or not any(c.isdigit() for c in v):
            raise ValueError("Use at least 8 characters with upper and lower case letters and a number")
        return v


class LoginIn(Strict):
    email: EmailStr
    password: Annotated[str, StringConstraints(min_length=1, max_length=72)]
    portal: Portal


class RefreshIn(Strict):
    refresh_token: S(2000, 1)


class ForgotPasswordIn(Strict):
    email: EmailStr
    portal: Portal


class ResetPasswordIn(Strict):
    access_token: S(4000, 1)
    password: Password


# --- Applicant profile ---------------------------------------------------------------------------------
class ApplicantProfileIn(Strict):
    first_name: Name
    last_name: Name
    headline: S(160) | None = None
    phone: Phone | None = None
    location: S(150) | None = None
    date_of_birth: date | None = None
    linkedin_url: Url | None = None
    github_url: Url | None = None
    portfolio_url: Url | None = None
    notice_period_days: int | None = Field(None, ge=0, le=365)
    is_discoverable: bool = True

    @field_validator("date_of_birth")
    @classmethod
    def dob(cls, v):
        if v and not (date(1920, 1, 1) <= v <= date.today()):
            raise ValueError("Enter a valid date of birth")
        return v


class ApplicantProfilePatch(Strict):
    first_name: Name | None = None
    last_name: Name | None = None
    headline: S(160) | None = None
    phone: Phone | None = None
    location: S(150) | None = None
    date_of_birth: date | None = None
    linkedin_url: Url | None = None
    github_url: Url | None = None
    portfolio_url: Url | None = None
    notice_period_days: int | None = Field(None, ge=0, le=365)
    is_discoverable: bool | None = None


Proficiency = Literal["BEGINNER", "INTERMEDIATE", "ADVANCED", "EXPERT"]


class CandidateSkillIn(Strict):
    skill_id: int = Field(gt=0)
    proficiency_level: Proficiency | None = None
    years_experience: Decimal | None = Field(None, ge=0, le=60, decimal_places=1)


class EducationIn(Strict):
    institution: S(200, 1)
    degree: S(120, 1)
    field_of_study: S(120) | None = None
    start_year: int | None = Field(None, ge=1950, le=2100)
    end_year: int | None = Field(None, ge=1950, le=2100)
    cgpa: Decimal | None = Field(None, ge=0, le=10, decimal_places=2)


class ExperienceIn(Strict):
    company_name: S(200, 1)
    designation: S(150, 1)
    start_date: date
    end_date: date | None = None
    description: S(4000) | None = None
    is_current: bool = False

    @model_validator(mode="after")
    def dates(self):
        if self.is_current:
            self.end_date = None
        if self.end_date and self.end_date < self.start_date:
            raise ValueError("End date must be after the start date")
        if self.start_date > date.today():
            raise ValueError("Start date can't be in the future")
        return self


class ProjectIn(Strict):
    title: S(200, 1)
    description: S(4000) | None = None
    technologies: list[S(60, 1)] = Field(default_factory=list, max_length=15)
    start_date: date | None = None
    end_date: date | None = None
    project_url: Url | None = None


class CertificationIn(Strict):
    name: S(200, 1)
    issuer: S(200) | None = None
    issue_date: date | None = None
    expiry_date: date | None = None
    credential_url: Url | None = None


# --- Companies ------------------------------------------------------------------------------------------
class CompanyCreateIn(Strict):
    company_name: S(200, 1)
    industry: S(100) | None = None
    description: S(4000) | None = None
    location: S(150) | None = None
    website: Url | None = None
    first_name: Name
    last_name: Name
    designation: S(100) | None = None


class JoinCompanyIn(Strict):
    invite_code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=16, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")]
    first_name: Name
    last_name: Name
    designation: S(100) | None = None


class CompanyPatch(Strict):
    company_name: S(200, 1) | None = None
    industry: S(100) | None = None
    description: S(4000) | None = None
    location: S(150) | None = None
    website: Url | None = None


# --- Jobs -------------------------------------------------------------------------------------------------
EmploymentType = Literal["FULL_TIME", "PART_TIME", "CONTRACT", "INTERNSHIP"]


class JobSkillIn(Strict):
    skill_id: int = Field(gt=0)
    is_required: bool = True
    importance_weight: Decimal = Field(Decimal("1.0"), gt=0, le=1)


class JobIn(Strict):
    title: S(200, 1)
    department: S(100) | None = None
    description: S(20000, 30)
    location: S(150) | None = None
    employment_type: EmploymentType = "FULL_TIME"
    is_remote: bool = False
    min_experience: Decimal = Field(Decimal(0), ge=0, le=50)
    max_experience: Decimal | None = Field(None, ge=0, le=50)
    min_salary: Decimal | None = Field(None, ge=0, le=Decimal("10000000000"))
    max_salary: Decimal | None = Field(None, ge=0, le=Decimal("10000000000"))
    closing_date: date | None = None
    status: Literal["DRAFT", "OPEN"] = "DRAFT"
    skills: list[JobSkillIn] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def ranges(self):
        if self.max_experience is not None and self.max_experience < self.min_experience:
            raise ValueError("Maximum experience must be at least the minimum")
        if self.min_salary is not None and self.max_salary is not None and self.max_salary < self.min_salary:
            raise ValueError("Maximum salary must be at least the minimum")
        if self.closing_date and self.closing_date < date.today():
            raise ValueError("Closing date can't be in the past")
        if len({s.skill_id for s in self.skills}) != len(self.skills):
            raise ValueError("A skill is listed twice")
        return self


class JobPatch(Strict):
    title: S(200, 1) | None = None
    department: S(100) | None = None
    description: S(20000, 30) | None = None
    location: S(150) | None = None
    employment_type: EmploymentType | None = None
    is_remote: bool | None = None
    min_experience: Decimal | None = Field(None, ge=0, le=50)
    max_experience: Decimal | None = Field(None, ge=0, le=50)
    min_salary: Decimal | None = Field(None, ge=0)
    max_salary: Decimal | None = Field(None, ge=0)
    closing_date: date | None = None
    status: Literal["DRAFT", "OPEN", "PAUSED", "CLOSED"] | None = None
    skills: list[JobSkillIn] | None = Field(None, max_length=30)


# --- Pipeline -----------------------------------------------------------------------------------------------
class StatusIn(Strict):
    status: Literal["SCREENING", "SHORTLISTED", "REJECTED"]


class SearchFilters(Strict):
    must_have_skill_ids: list[int] = Field(default_factory=list, max_length=10)
    min_experience: Decimal | None = Field(None, ge=0, le=50)
    location: S(100) | None = None
    max_notice_days: int | None = Field(None, ge=0, le=365)


class SearchIn(Strict):
    title: S(200) = ""
    description: S(20000, 30)
    job_id: int | None = Field(None, gt=0)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    use_ai: bool = True


class ScreenIn(Strict):
    use_ai: bool = True


InterviewType = Literal["PHONE", "VIDEO", "ONSITE", "TECHNICAL", "HR"]


def _aware(v: datetime | None) -> datetime | None:
    if v is not None and v.tzinfo is None:
        raise ValueError("Include a timezone offset, e.g. 2026-11-02T10:00:00+05:30")
    return v


class InterviewIn(Strict):
    application_id: int = Field(gt=0)
    interviewer_id: int | None = Field(None, gt=0)
    scheduled_at: datetime
    duration_minutes: int = Field(60, ge=15, le=480)
    interview_type: InterviewType = "VIDEO"
    meeting_link: HttpsUrl | None = None
    notes: S(4000) | None = None

    @field_validator("scheduled_at")
    @classmethod
    def tz_aware(cls, v):
        return _aware(v)


class InterviewPatch(Strict):
    scheduled_at: datetime | None = None
    duration_minutes: int | None = Field(None, ge=15, le=480)
    interviewer_id: int | None = Field(None, gt=0)
    interview_type: InterviewType | None = None
    meeting_link: HttpsUrl | None = None
    notes: S(4000) | None = None
    status: Literal["CANCELLED", "COMPLETED"] | None = None

    @field_validator("scheduled_at")
    @classmethod
    def tz_aware(cls, v):
        return _aware(v)


class InterviewFeedbackIn(Strict):
    technical_score: int = Field(ge=1, le=10)
    communication_score: int = Field(ge=1, le=10)
    problem_solving_score: int = Field(ge=1, le=10)
    overall_score: int = Field(ge=1, le=10)
    recommendation: Literal["STRONG_HIRE", "HIRE", "NO_HIRE", "STRONG_NO_HIRE"]
    comments: S(4000) | None = None


class OfferIn(Strict):
    application_id: int = Field(gt=0)
    salary: Decimal = Field(gt=0, le=Decimal("10000000000"))
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] = "INR"
    joining_date: date
    expires_in_days: int = Field(7, ge=1, le=60)
    notes: S(4000) | None = None


class OfferResponseIn(Strict):
    decision: Literal["ACCEPTED", "REJECTED"]
