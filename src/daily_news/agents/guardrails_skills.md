# guardrails — Skills Reference

## Purpose

`guardrails.py` provides two deterministic, zero-LLM safety validators:

- **`InputGuardrail`** — validates raw news article content before it enters the pipeline
- **`OutputGuardrail`** — validates final composed post text before LinkedIn publish

Both return a `GuardrailCheckResult` Pydantic model. Neither makes network calls.

---

## `GuardrailCheckResult` (Pydantic model)

| Field | Type | Meaning |
|---|---|---|
| `is_safe` | `bool` | `True` only when ALL checks pass |
| `prompt_injection_detected` | `bool` | Injection pattern found in input |
| `pii_detected` | `bool` | SSN or credit card pattern found |
| `malicious_content_detected` | `bool` | Set equal to `prompt_injection_detected` |
| `violations` | `list[str]` | Human-readable violation descriptions |
| `sanitized_text` | `str` | Content with control characters stripped |

---

## `InputGuardrail`

### `inspect_article(article: dict) → GuardrailCheckResult`

Validates a raw GNews article dict before it enters the Jev/LLM pipeline.

**Checks performed (in order):**

1. **Prompt injection** — scans `title + "\n" + content` against 9 regex patterns:
   - `ignore (all) previous instructions`
   - `system: you are`
   - `you are now unrestricted`
   - `jailbreak`
   - `bypass all filters/guardrails/safety`
   - `disregard the above/previous/system`
   - `output the secret/system prompt/api key`
   - `<system>`
   - `[system prompt]`
   - First match short-circuits — stops scanning after one hit.

2. **PII — SSN** — regex `\b\d{3}-\d{2}-\d{4}\b`

3. **PII — Credit card** — regex `\b(?:\d{4}[-\s]?){3}\d{4}\b`

4. **Control character sanitisation** — strips `\x00–\x08`, `\x0B`, `\x0C`,
   `\x0E–\x1F`, `\x7F` from content text.

`is_safe = not injection_found and not pii_found`

---

## `OutputGuardrail`

### `inspect_output(post_text: str, evidence_text: str = "") → GuardrailCheckResult`

Validates the final composed LinkedIn post string before it is published.

**Checks performed (in order):**

1. **Empty text** — returns `GuardrailCheckResult(is_safe=True)` immediately.

2. **`[BANNED_CONTENT:…]` sentinel** — injected by `PublisherAgent._compose_main_post()`
   when the deterministic banned-phrase scanner fires. This sentinel is *never*
   publishable. Extracts the persona/phrase detail from the bracket for the violation log.
   Sets `banned_content = True` and logs a `WARNING`.

3. **PII — SSN** — same pattern as `InputGuardrail`.

4. **PII — Credit card** — same pattern as `InputGuardrail`.

5. **Fake quotation check (soft)** — finds all `"..."` substrings ≥ 15 chars;
   if `evidence_text` is provided and a long direct quote (≥ 6 words) is not found
   verbatim in the evidence, logs a `DEBUG` warning. Does **not** set `is_safe=False`
   (soft check only).

`is_safe = not pii_found and not fake_quote_risk and not banned_content`

---

## Integration points

| Caller | What it calls | Why |
|---|---|---|
| `PublisherAgent.publish()` | `OutputGuardrail.inspect_output()` | Final gate before `final_post` is returned |
| `daily_news_graph.py` node `validate_input` | `InputGuardrail.inspect_article()` | Blocks injected/PII articles before any agent sees them |

---

## Invariants

- **No network calls** — all checks are pure regex + string operations.
- **Never raises** — always returns a `GuardrailCheckResult`. Validation failures
  are captured as violations, not exceptions.
- **Idempotent** — calling either method twice with the same input returns the same result.
- **The `[BANNED_CONTENT:]` sentinel is the bridge to the retry loop** — `OutputGuardrail`
  detects it; `EvaluationAgent` sees `is_safe=False`; the graph routes to `REGENERATE`.

---

## Adding new checks

Add a pattern to `_INJECTION_PATTERNS` for new injection variants (input).
Add a new `if re.search(...)` block inside the appropriate method for new PII types.
Both paths follow the same `violations.append(...)` → `is_safe = not flag` pattern.
