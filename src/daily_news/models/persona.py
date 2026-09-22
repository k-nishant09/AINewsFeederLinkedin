"""Persona output Pydantic models."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class PersonaType(str, Enum):
    BUSINESS = "business"
    LABOR = "labor"
    POLICY = "policy"
    GENZ = "genz"
    LINKEDIN = "linkedin"


class PersonaOutput(BaseModel):
    persona: PersonaType
    perspective: str
    evidence: list[str]
    article_id: str


class PersonaSetOutput(BaseModel):
    article_id: str
    business: PersonaOutput
    labor: PersonaOutput
    policy: PersonaOutput
    genz: PersonaOutput
    linkedin: PersonaOutput
