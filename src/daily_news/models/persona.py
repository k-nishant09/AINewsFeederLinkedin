"""Persona output Pydantic models."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class PersonaType(str, Enum):
    BUSINESS = "business"
    POLICY   = "policy"
    GENZ     = "genz"      # display: Generalist Mind
    LINKEDIN = "linkedin"  # display: Tech & Workforce Mind (merged labor + tech strategist)


class PersonaOutput(BaseModel):
    persona: PersonaType
    perspective: str
    evidence: list[str]
    article_id: str


class PersonaSetOutput(BaseModel):
    article_id: str
    business: PersonaOutput
    policy:   PersonaOutput
    genz:     PersonaOutput
    linkedin: PersonaOutput
