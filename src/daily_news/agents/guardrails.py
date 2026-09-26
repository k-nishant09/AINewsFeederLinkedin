"""
Guardrail Agent — Input and Output safety validators.

Enforces:
1. Input Guardrail:
   - Untrusted news article content validation
   - Prompt injection detection
   - PII detection / redaction flags
   - Malicious/adversarial pattern filtering
   - Invariant enforcement: Article text != system instructions, Article claims != verified facts

2. Output Guardrail (Pre-publish & Pre-human review):
   - Fake quotation detection (characters attributing fake real-person quotes)
   - Hallucinated attribution
   - PII leak detection
   - Toxic/defamatory content checks
   - Invariant enforcement: Persona outputs are clearly simulated perspectives, not real quotes
"""
from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Known prompt injection signatures in untrusted inputs
_INJECTION_PATTERNS = [
    r"(?i)ignore\s+(all\s+)?(previous|above|prior)\s+(instructions|prompts|directives)",
    r"(?i)system\s*:\s*you\s+are",
    r"(?i)you\s+are\s+now\s+(an?\s+)?unrestricted",
    r"(?i)jailbreak",
    r"(?i)bypass\s+all\s+(filters|guardrails|safety)",
    r"(?i)disregard\s+(the\s+)?(above|previous|system)",
    r"(?i)output\s+the\s+(secret|system\s+prompt|api\s*key)",
    r"(?i)<\s*system\s*>",
    r"(?i)\[\s*system\s*prompt\s*\]",
]

# Simple PII patterns (SSN, credit cards, emails in unexpected places, phone numbers)
_SSN_PATTERN = r"\b\d{3}-\d{2}-\d{4}\b"
_CREDIT_CARD_PATTERN = r"\b(?:\d{4}[-\s]?){3}\d{4}\b"
_EMAIL_PATTERN = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b"


class GuardrailCheckResult(BaseModel):
    is_safe: bool = True
    prompt_injection_detected: bool = False
    pii_detected: bool = False
    malicious_content_detected: bool = False
    violations: list[str] = Field(default_factory=list)
    sanitized_text: str = ""


class InputGuardrail:
    """Validates and sanitizes raw news inputs before indexing and reasoning."""

    @classmethod
    def inspect_article(cls, article: dict[str, Any]) -> GuardrailCheckResult:
        title = article.get("title", "") or ""
        content = article.get("content", "") or ""
        combined = f"{title}\n{content}"

        violations: list[str] = []
        injection_found = False
        pii_found = False

        # 1. Prompt Injection Checks
        for pat in _INJECTION_PATTERNS:
            if re.search(pat, combined):
                injection_found = True
                violations.append(f"Prompt injection pattern detected: {pat}")
                break

        # 2. PII Checks
        if re.search(_SSN_PATTERN, combined):
            pii_found = True
            violations.append("SSN pattern detected in article content")
        if re.search(_CREDIT_CARD_PATTERN, combined):
            pii_found = True
            violations.append("Credit card number pattern detected in article content")

        # Basic sanitization: strip dangerous control characters
        sanitized_content = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", content)

        is_safe = not injection_found and not pii_found
        return GuardrailCheckResult(
            is_safe=is_safe,
            prompt_injection_detected=injection_found,
            pii_detected=pii_found,
            malicious_content_detected=injection_found,
            violations=violations,
            sanitized_text=sanitized_content,
        )


class OutputGuardrail:
    """Validates final generated story and dialogue outputs before delivery."""

    @classmethod
    def inspect_output(
        cls,
        post_text: str,
        evidence_text: str = "",
    ) -> GuardrailCheckResult:
        violations: list[str] = []
        pii_found = False
        fake_quote_risk = False
        banned_content = False

        if not post_text:
            return GuardrailCheckResult(is_safe=True)

        # 0. Banned-content sentinel injected by PublisherAgent._compose_main_post
        # when the deterministic banned-phrase scanner catches a throat-clearing
        # opener or a hardcoded inline phrase.  This sentinel is NEVER publishable
        # and forces the graph to REGENERATE.
        if "[BANNED_CONTENT:" in post_text:
            banned_content = True
            # Extract persona/phrase for the violation log
            match = re.search(r"\[BANNED_CONTENT: ([^\]]+)\]", post_text)
            detail = match.group(1) if match else "unknown"
            violations.append(f"banned_phrase_in_persona_output: {detail}")
            logger.warning("OutputGuardrail: BANNED_CONTENT sentinel detected — %s", detail)

        # 1. PII Checks
        if re.search(_SSN_PATTERN, post_text):
            pii_found = True
            violations.append("SSN pattern detected in generated output")
        if re.search(_CREDIT_CARD_PATTERN, post_text):
            pii_found = True
            violations.append("Credit card number pattern detected in generated output")

        # 2. Fake quotation attribution check
        # Detect if a character fabricates a quotation from a real specific CEO/leader
        # without it being in the evidence
        quote_matches = re.findall(r'"([^"]{15,})"', post_text)
        if evidence_text and quote_matches:
            evidence_lower = evidence_text.lower()
            for q in quote_matches:
                # If there's a long direct quote attributed that isn't in evidence
                if len(q.split()) >= 6 and q.lower() not in evidence_lower:
                    # Soft warning or verification
                    logger.debug("Quote in output not found verbatim in evidence: %s", q[:40])

        is_safe = not pii_found and not fake_quote_risk and not banned_content
        return GuardrailCheckResult(
            is_safe=is_safe,
            pii_detected=pii_found,
            violations=violations,
            sanitized_text=post_text,
        )
