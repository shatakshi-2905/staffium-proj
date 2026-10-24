"""Application settings, loaded from environment variables (and a local .env file in development).

Secrets are never hard-coded and never logged. In production the app refuses to start with
settings that would weaken security (missing CORS origin, test-only embedding provider, etc.).
"""
from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["development", "production", "test"] = "development"

    # --- Database (Supabase Postgres connection string; use the session pooler, port 5432) -----
    database_url: str
    db_pool_min: int = 1
    db_pool_max: int = 10

    # --- Supabase Auth --------------------------------------------------------------------------
    supabase_url: str                       # https://<project-ref>.supabase.co
    supabase_anon_key: str                  # used server-side only, to call Supabase Auth
    supabase_jwt_secret: str | None = None  # legacy HS256 projects; leave empty to use JWKS (asymmetric keys)

    # --- Frontend / CORS --------------------------------------------------------------------------
    frontend_origin: str = "http://localhost:5500"   # exact origin of the static frontend, no trailing slash

    # --- AWS ----------------------------------------------------------------------------------------
    aws_region: str = "ap-south-1"
    aws_access_key_id: str | None = None             # omit on AWS-hosted compute with an IAM role
    aws_secret_access_key: str | None = None
    s3_bucket_name: str = ""
    bedrock_region: str | None = None                # defaults to aws_region
    bedrock_model_id: str = "apac.amazon.nova-lite-v1:0"
    llm_enabled: bool = True

    # --- Email (AWS SES). When disabled, outbox rows are marked SKIPPED instead of sent. -----------
    email_enabled: bool = False
    ses_from_email: str = ""
    ses_configuration_set: str | None = None

    # --- Embeddings ----------------------------------------------------------------------------------
    embeddings_provider: Literal["fastembed", "hash"] = "fastembed"   # "hash" is for tests only
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: str = "./.models"

    # --- Limits ----------------------------------------------------------------------------------------
    max_resume_bytes: int = 5 * 1024 * 1024
    max_json_body_bytes: int = 256 * 1024
    worker_enabled: bool = True
    worker_interval_seconds: int = 20

    rate_limit_auth: str = "5/minute"
    rate_limit_search: str = "10/minute"
    rate_limit_upload: str = "10/hour"
    rate_limit_default: str = "120/minute"

    @field_validator("frontend_origin", "supabase_url")
    @classmethod
    def strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @model_validator(mode="after")
    def production_guards(self) -> "Settings":
        if self.environment == "production":
            problems = []
            if self.embeddings_provider != "fastembed":
                problems.append("EMBEDDINGS_PROVIDER must be 'fastembed' in production")
            if not self.frontend_origin.startswith("https://"):
                problems.append("FRONTEND_ORIGIN must be an https:// origin in production")
            if not self.s3_bucket_name:
                problems.append("S3_BUCKET_NAME is required")
            if self.email_enabled and not self.ses_from_email:
                problems.append("SES_FROM_EMAIL is required when EMAIL_ENABLED=true")
            if problems:
                raise ValueError("; ".join(problems))
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def supabase_issuer(self) -> str:
        return f"{self.supabase_url}/auth/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
