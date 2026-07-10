"""Pydantic contracts for every stage of the pipeline.

AppRecord is the research contract: every researched field is a claim with its
own evidence URL and confidence. Deterministic facts (Composio registry, MCP
registry) are merged in later by the orchestrator, never asserted by an LLM.
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low"]

RESEARCHED_FIELDS = ["description", "auth_methods", "credential_access", "api_surface", "buildability"]


class AuthMethodsClaim(BaseModel):
    value: list[Literal["oauth2", "api_key", "basic", "token", "jwt", "other", "none_public"]] = Field(
        description="Every auth method the public API supports"
    )
    evidence_url: str = Field(description="URL of the auth docs page supporting this")
    confidence: Confidence = "medium"
    note: str = ""


class CredentialAccessClaim(BaseModel):
    value: Literal["self_serve_free", "self_serve_paid_trial", "admin_approval", "partner_gated", "unclear"] = Field(
        description="Can a developer get API credentials themselves for free/trial, or is it gated"
    )
    evidence_url: str
    confidence: Confidence = "medium"
    note: str = ""


class ApiSurfaceClaim(BaseModel):
    value: Literal["rest", "graphql", "both", "sdk_only", "none_public"]
    breadth: Literal["broad", "moderate", "narrow", "unknown"] = Field(
        description="broad = most product objects covered; narrow = a handful of endpoints"
    )
    docs_url: str = Field(description="Root URL of the public API reference")
    confidence: Confidence = "medium"
    note: str = ""


class BuildabilityClaim(BaseModel):
    verdict: Literal["buildable_today", "buildable_with_key_signup", "blocked"]
    main_blocker: Literal[
        "none", "partner_gate", "no_public_api", "insufficient_docs", "enterprise_only", "paid_only", "other"
    ] = "none"
    note: str = ""


class ResearchMeta(BaseModel):
    queries: list[str] = Field(default_factory=list, description="Search queries actually used")
    insufficient_public_docs: bool = Field(
        default=False,
        description="True when no public developer docs could be found; evidence_url fields may then be empty",
    )


class AppRecord(BaseModel):
    slug: str
    name: str
    category: str
    description: str = Field(description="What the app does, in one line")
    auth_methods: AuthMethodsClaim
    credential_access: CredentialAccessClaim
    api_surface: ApiSurfaceClaim
    buildability: BuildabilityClaim
    research_meta: ResearchMeta = Field(default_factory=ResearchMeta)


class FieldVerdict(BaseModel):
    field: Literal["description", "auth_methods", "credential_access", "api_surface", "buildability"]
    verdict: Literal["confirmed", "contradicted", "unsupported", "dead_url"]
    corrected_value: str = Field(default="", description="Correct value if contradicted, else empty")
    better_evidence_url: str = ""
    reasoning: str = ""


class VerificationReport(BaseModel):
    slug: str
    verdicts: list[FieldVerdict]


class JudgeFieldScore(BaseModel):
    field: str
    faithful: bool = Field(description="Does the cited evidence URL actually support the recorded value")
    reasoning: str = ""


class JudgeReport(BaseModel):
    slug: str
    scores: list[JudgeFieldScore]


if __name__ == "__main__":
    from pathlib import Path

    out = Path(__file__).resolve().parent.parent / "schema"
    out.mkdir(exist_ok=True)
    (out / "app_record.schema.json").write_text(json.dumps(AppRecord.model_json_schema(), indent=2))
    (out / "verification.schema.json").write_text(json.dumps(VerificationReport.model_json_schema(), indent=2))
    print("schemas written")
