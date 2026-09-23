# AIFeeders — AI Daily News Platform

> An autonomous, multi-agent system that wakes up every morning, reads the internet for AI news, thinks about it from five different human perspectives, safety-checks everything, and publishes a polished post to LinkedIn — all without anyone pressing a button.

---

## What Does It Actually Do?

Every day at 10:00 AM UTC (3:30 PM IST), a scheduled job fires up. Here's what happens next, in plain English:

1. **It searches the internet.** Six different searches go out to GNews: AI technology, AI business, AI jobs, AI policy, enterprise automation, and product launches.
2. **It picks one story.** Out of everything found, it selects the single most relevant AI news story.
3. **It reads and understands the article.** An LLM (IBM's `qwen2-5-72b-instruct`) writes a structured summary — headline, key points, business impact, workforce impact, tech impact, policy impact.
4. **Five different "minds" react to it.** Five AI personas, each with a distinct voice and worldview, write their perspective on the news. Not generic summaries — genuine reactions from a business leader, a working professional, a policy analyst, an early-career person, and a tech strategist.
5. **A safety panel reviews it.** Six independent guardrails check for hallucinations, made-up facts, private data (emails, phone numbers), toxic content, political bias, and formatting violations.
6. **If it passes, it goes live on LinkedIn.** One structured post, with all five perspectives embedded, under 3000 characters, goes out.

The whole pipeline takes 1–3 minutes. Zero human involvement.

---

## What the LinkedIn Post Looks Like

Here's an actual post published by this system:

```
🤖  AI NEWS

AI Assistants Belong in Your Ears, Not on Your Face

Gizmodo argues that earbuds, not smart glasses, are the ideal wearables for
AI assistants due to their ubiquity and privacy benefits.

📌  Key Points
  • Earbuds have been a common accessory for over a decade
  • Smart glasses face significant privacy concerns and social acceptance issues
  • Earbuds provide a more discreet and user-friendly interface for AI interactions

📈  Business Impact   —  Companies focusing on earbud tech may see increased investment...
👷  Workforce Impact  —  The shift towards earbud-based AI could create new audio roles...
🔬  Tech Impact       —  Advancements in audio processing will be crucial...

🔗  Source: https://lnkd.in/dBki-z9s

──────────────────
🧵  Perspectives

💼  Capitalist Mind
Earbuds have a decade-long head start and better privacy — they're a safer bet
for AI assistants. Smart glasses face too many hurdles. Where will the next
big investment round go?

👷  Working Professional Mind
The article makes a strong case for earbuds as the future of AI assistants.
If this trend continues, the question I'd ask my manager is: are we building
for voice-first interactions yet?

🏛️  Government Mind
The article highlights earbuds' ubiquity and privacy advantages. Any wearable
with always-on microphones will attract regulatory attention — GDPR's ambient
audio provisions are the framework to watch here.

🎓  Young / Fresher Mind
Earbuds are already part of daily life — that's the article's key point.
If you're starting out in tech, focus on audio interfaces and voice recognition.
These skills will be in demand well before smart glasses go mainstream.

──────────────────
🧠  Tech Strategist Mind
The article highlights audio processing as the more mature platform. For teams
evaluating voice AI integrations, earbud APIs are the lower-risk path right now
— the question is whether your architecture can swap the input modality later.

──────────────────
⚠️ These are AI-simulated perspectives — not verified opinions or professional advice.
🤖 Built with AIFeeders · Powered by Agentic AI

#AI #AgenticAI #LLM #GenerativeAI #AINews #TechNews #AIStrategy
#MachineLearning #AIInnovation #DigitalTransformation #AILeadership
```

---

## Who Is This For?

| You are... | This helps you... |
|------------|-------------------|
| **A developer** who wants to understand agentic AI systems | See how LangGraph, MCP servers, and LLM chains work together in production |
| **A DevOps / platform engineer** | Deploy and operate a real multi-service AI workload on OpenShift |
| **A product person** who follows AI | Get a daily, multi-perspective AI news digest without reading five different Substacks |
| **Someone learning AI engineering** | A working, production example of: agents, guardrails, observability, and multi-platform deployment |

---

## How It Was Built — From Idea to Production

### The problem we started with

AI news moves fast. There's too much of it, it's scattered across dozens of sources, and most coverage gives you one perspective. A business leader cares about a different angle than a developer or a policy wonk. Reading all of it manually every morning isn't realistic.

### The idea

What if an AI system could do the reading, synthesise it, and present it from multiple human perspectives — automatically, every day?

### Phase 1 — Proof of concept (3 nodes, one weekend)

We started with the simplest possible thing: a LangGraph graph with three steps:
1. Fetch news from GNews
2. Summarise with an LLM
3. Post to LinkedIn

It worked. A post appeared on LinkedIn. That was enough to know the idea was viable.

**What we learned the hard way:**
- GNews free plan gives you 100 requests/day. Burn through them in testing and nothing works till midnight UTC
- The IBM LLM gateway uses a self-signed certificate — every `httpx` call needs `verify=False`
- Uvicorn silences the root Python logger by default — you see nothing in pod logs without `logging.basicConfig()`

### Phase 2 — MCP microservices architecture

A monolith works for a POC. For production, we needed each concern to be independently deployable, testable, and replaceable. We split everything into four **MCP (Model Context Protocol) servers**:

- **`news-mcp`** — wraps the GNews API. The main workflow never calls GNews directly.
- **`pageindex-mcp`** — an in-memory document store. Articles get indexed here so the LLM can retrieve relevant sections when writing summaries and personas.
- **`evaluation-mcp`** — the safety layer. Six independent guardrail checks run here.
- **`linkedin-mcp`** — wraps the LinkedIn API. Handles OAuth, token storage, and publishing.

Each MCP server is a FastAPI app that accepts tool calls over HTTP at `POST /call`. The main workflow treats them like external services — if one is down, the error is contained.

**What we learned:**
- LinkedIn's Comments API requires a separate product approval ("Community Management API"). We didn't have it. Solution: embed all five personas directly in the post body, and treat comment failures as soft skips — not pipeline errors.
- `pageindex-mcp` uses in-memory storage. Run more than one replica and each pod has a different view of the data. Must be `replicas: 1`.

### Phase 3 — Making it actually reliable

The POC posted. Phase 2 split concerns properly. But a dozen subtle bugs stood between "it sometimes works" and "it reliably works every day at 10 AM." Here's what we fixed:

| Bug | Root Cause | Fix |
|-----|-----------|-----|
| Evaluation always returned `REGENERATE` | GNews free tier truncates article content to ~250 chars, so the guardrail saw persona text about things the source "never mentioned" | Added `_enrich_source()` — combines article content with all summary fields before evaluation |
| LinkedIn feed card showed blank | `🤖 AI NEWS \| <headline>` on one line — mobile clipped everything after the emoji | Put headline on its own line (line 2) |
| Same post published twice on re-run | `publication_key` didn't include `run_id`, so two runs same day collided | Appended last 8 chars of `run_id` to the key |
| Five error logs per run for comments | `PERMISSION_ERROR` on each of 5 comment attempts | First 403 sets `_comments_blocked=True`, logs once, skips the rest |
| GNews quota hit mid-day | 100 req/day free limit, one key | Added second GNews key with automatic failover on HTTP 403 |
| REGENERATE loop never ended | `retry_count` was read but never incremented | Increment inside the `evaluate` node |

### Phase 4 — Persona voices

Generic LLM output sounds like a press release. We wanted five distinct human voices. We built `skills.md` — a document that describes each persona's worldview, what they notice first, how they speak, and concrete examples of their voice. Each persona prompt loads from its own `.txt` file:

- `prompts/capitalist.txt` — founder/operator, P&L lens, contractions, ends with a pointed question
- `prompts/labor.txt` — Slack-message energy, concrete and actionable, no HR-speak
- `prompts/policy.txt` — policy memo precision, names specific regulations, no vague "could influence"
- `prompts/genz.txt` — group chat energy, says exactly why something matters, no "big deal for us"
- `prompts/linkedin.txt` — practitioner precision, names the trade-off, no "has implications"

Each prompt includes **anti-patterns** — exact examples of the corporate voice the LLM should never produce — and **good examples** showing the target voice. The `persona` enum value (`"business"`, `"linkedin"` etc.) is always injected from the agent context, never trusted from the LLM output, to prevent enum mismatch errors.

---

## System Architecture

```
                    ┌─────────────────────────────────────────┐
                    │         OpenShift — namespace: aifeeders│
                    │                                         │
  ┌──────────┐      │  ┌──────────────┐    ┌──────────────┐  │
  │  CronJob │─────▶│  │ LangGraph    │    │ daily-news   │  │
  │ 10AM UTC │      │  │ Workflow     │◀───│ API (2 pods) │  │
  └──────────┘      │  │ (9 nodes)    │    └──────────────┘  │
                    │  └──────┬───────┘                       │
                    │         │ HTTP POST /call                │
                    │   ┌─────┼──────────────┐                │
                    │   ▼     ▼     ▼         ▼               │
                    │ news  page  eval  linkedin               │
                    │  mcp   idx   mcp    mcp                  │
                    └───┬─────┼─────┼──────┬──┘
                        │     │     │      │
                       GNews  │    LLM   LinkedIn
                         (in-memory)  Gateway   API
                              │
                           (articles
                            indexed
                            here)
```

The nine workflow nodes run in sequence:
`discover_news` → `deduplicate` → `fetch_articles` → `index_pageindex` → `select_stories` → `summarize` → `generate_personas` → `evaluate` → `publish`

The `evaluate` node can loop back to `summarize` up to 2 times if guardrails fail (REGENERATE decision).

---

## The Five Personas — How `skills.md` Shapes Them

The personas aren't just different system prompts. Each one has a documented voice, worldview, and set of anti-patterns in [`skills.md`](skills.md). Here's what makes each one distinct:

| Persona | Voice | Lens | Closes With |
|---------|-------|------|-------------|
| 💼 **Capitalist Mind** | Founder/operator, seen hype cycles before | P&L, margins, moats | A question a decision-maker loses sleep over |
| 👷 **Working Professional Mind** | Trusted coworker on Slack | Day-to-day impact, skill half-life | Something actionable you could do this week |
| 🏛️ **Government Mind** | Policy memo excerpt, measured | Named regulations, enforcement signals | Specific compliance action with a deadline |
| 🎓 **Young / Fresher Mind** | Group chat, direct and honest | Entry points, signal vs. hype | A concrete next step: tool to try, community to join |
| 🧠 **Tech Strategist Mind** | RFC comment from a staff engineer | Architecture, build-vs-buy, lock-in risk | One question a technical decision-maker should ask today |

The `skills.md` document describes each persona in detail — their instincts, evidence style, and how they sound compared to generic AI output. When you see the post looking corporate, come back to `skills.md` and the prompt anti-patterns.

---

## Project Structure

```
AINewsfeederLinkedin/
├── src/
│   └── daily_news/
│       ├── agents/
│       │   ├── summary_agent.py        # LLM → NewsSummary
│       │   ├── persona_agent.py        # 5× parallel LLM calls → PersonaSetOutput
│       │   ├── evaluation_agent.py     # Calls evaluation-mcp, applies thresholds
│       │   └── publisher_agent.py      # Composes + posts to LinkedIn (no LLM)
│       ├── workflows/
│       │   └── daily_news_graph.py     # LangGraph StateGraph (9 nodes)
│       ├── mcp/                        # HTTP clients for each MCP server
│       ├── models/                     # Pydantic models (NewsSummary, PersonaOutput…)
│       ├── config/settings.py          # Reads env vars
│       └── api/                        # FastAPI (manual trigger endpoints)
│
├── mcp_servers/
│   ├── news_mcp/server.py              # GNews adapter + key rotation
│   ├── pageindex_mcp/server.py         # In-memory doc store
│   ├── evaluation_mcp/server.py        # 6-layer guardrail pipeline
│   └── linkedin_mcp/server.py          # OAuth + Posts API
│
├── prompts/
│   ├── summary.txt                     # LLM prompt for news summary
│   ├── capitalist.txt                  # Capitalist Mind persona
│   ├── labor.txt                       # Working Professional Mind
│   ├── policy.txt                      # Government Mind
│   ├── genz.txt                        # Young / Fresher Mind
│   └── linkedin.txt                    # Tech Strategist Mind
│
├── openshift/                          # All Kubernetes manifests
│   ├── configmap.yaml                  # Non-secret config (thresholds, URLs)
│   ├── secrets.yaml                    # Secret template (never commit real values)
│   ├── cronjob.yaml                    # Scheduled daily run
│   ├── api/                            # Deployment + Service + Route for main API
│   ├── news-mcp/                       # Deployment + Service + Route
│   ├── pageindex-mcp/
│   ├── evaluation-mcp/
│   └── linkedin-mcp/
│
├── deploy/
│   ├── deploy.sh                       # Multi-platform deploy script
│   └── helm/aifeeders/                 # Helm chart (OpenShift / EKS / AKS)
│
├── skills.md                           # Persona voice & characteristics reference
├── Dockerfile                          # Main app image
├── .dockerignore                       # Excludes .venv (270MB) — keeps builds fast
├── docker-compose.yaml                 # Local dev: all 5 services + Langflow
└── pyproject.toml                      # Python dependencies
```

---

## Prerequisites — What You Need Before Starting

### Accounts and credentials

| What | Where to get it | Notes |
|------|----------------|-------|
| **IBM LLM Gateway API key** | IBM internal | The `Authorization: Bearer` token for `qwen2-5-72b-instruct` |
| **GNews API key** | [gnews.io](https://gnews.io) — free plan | 100 requests/day. Optionally get a second key for auto-failover |
| **LinkedIn Developer App** | [linkedin.com/developers](https://www.linkedin.com/developers/apps/) | Needs "Sign In with LinkedIn" + "Share on LinkedIn" products |
| **Langfuse keys** (optional) | [langfuse.com](https://langfuse.com) | For LLM trace logging. Works without it |

### Tools

| Tool | Version | Install |
|------|---------|---------|
| `python` | 3.11+ | `brew install python@3.11` |
| `oc` (OpenShift CLI) | 4.x | Download from your cluster's "?" menu → Command Line Tools |
| `git` | any | `brew install git` |
| `docker` or `podman` | any | For local development only |

---

## Local Development Setup

If you want to run the full pipeline on your laptop before deploying to OpenShift:

### Step 1 — Clone the repo

```bash
git clone https://github.com/k-nishant09/AINewsFeederLinkedin.git
cd AINewsFeederLinkedin
```

### Step 2 — Create a Python virtual environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Step 3 — Set up your environment variables

```bash
cp .env.example .env
# Open .env in your editor and fill in your real values:
#   LLM_API_KEY=<your IBM LLM gateway token>
#   GNEWS_API_KEY=<your GNews key>
#   GNEWS_API_KEY_2=<optional second GNews key>
#   LINKEDIN_CLIENT_ID=<from LinkedIn Developer Portal>
#   LINKEDIN_CLIENT_SECRET=<from LinkedIn Developer Portal>
#   LINKEDIN_ACCESS_TOKEN=<after OAuth flow below>
```

### Step 4 — Start all services

```bash
# Start all 5 MCP servers + the main API
docker-compose up -d
```

### Step 5 — Authorise LinkedIn (first time)

Open this URL in your browser:
```
http://localhost:8004/oauth/start
```
LinkedIn will show a permission screen. Sign in and click Allow. You'll be redirected to a success page. Your access token is now stored in the `linkedin-mcp` container.

### Step 6 — Test without publishing (safe)

```bash
# This runs the full pipeline but does NOT post to LinkedIn
PUBLISHING_ENABLED=false python -m daily_news.workflow_runner
```

You'll see the full log output: news discovery, summarisation, persona generation, evaluation scores, and the composed post text — all without actually posting anything.

### Step 7 — Run for real

```bash
# This posts to LinkedIn
PUBLISHING_ENABLED=true python -m daily_news.workflow_runner
```

---

## OpenShift Deployment — Step by Step

> **First time?** Follow every step in order. **Subsequent deployments?** Jump to [Rebuild and Redeploy](#rebuild-and-redeploy).

### Step 1 — Log in to your cluster

```bash
oc login --token=<your-token> --server=https://api.<your-cluster>:6443
oc new-project aifeeders  # skip if it already exists
```

### Step 2 — Create the ConfigMap (non-secret configuration)

The ConfigMap holds settings that don't need to be secret — LLM model name, evaluation thresholds, service URLs:

```bash
# Review openshift/configmap.yaml first — the LLM_BASE_URL must point to your gateway
cat openshift/configmap.yaml

oc apply -f openshift/configmap.yaml -n aifeeders
```

### Step 3 — Create the Secrets

```bash
# Open openshift/secrets.yaml in your editor
# Fill in EVERY field marked <placeholder> with your real values
# NEVER commit the file with real values — only commit <placeholder> versions
nano openshift/secrets.yaml

oc apply -f openshift/secrets.yaml -n aifeeders
```

The required secrets are:

| Secret Key | What It Is |
|-----------|-----------|
| `LLM_API_KEY` | IBM LLM Gateway bearer token |
| `GNEWS_API_KEY` | Primary GNews API key |
| `GNEWS_API_KEY_2` | Secondary GNews key (optional but recommended) |
| `LINKEDIN_CLIENT_ID` | LinkedIn app client ID |
| `LINKEDIN_CLIENT_SECRET` | LinkedIn app client secret |
| `LINKEDIN_ACCESS_TOKEN` | LinkedIn member access token (from OAuth) |
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key (optional) |
| `LANGFUSE_SECRET_KEY` | Langfuse secret key (optional) |

### Step 4 — Apply RBAC

```bash
oc apply -f openshift/rbac.yaml -n aifeeders
```

### Step 5 — Create ImageStreams and BuildConfigs (first time only)

```bash
for svc in daily-news news-mcp pageindex-mcp evaluation-mcp linkedin-mcp; do
  oc create imagestream $svc -n aifeeders 2>/dev/null || true
  oc new-build --name=$svc --binary --strategy=docker -n aifeeders 2>/dev/null || true
done
```

### Step 6 — Build the images

Each service gets its own image. The main app (`daily-news`) contains all the agents and workflow logic:

```bash
# Main app — agents, workflow, API, prompts
oc start-build daily-news --from-dir=. -n aifeeders --follow

# MCP servers — each has its own source directory
oc start-build news-mcp       --from-dir=mcp_servers/news_mcp       -n aifeeders --follow
oc start-build pageindex-mcp  --from-dir=mcp_servers/pageindex_mcp  -n aifeeders --follow
oc start-build evaluation-mcp --from-dir=mcp_servers/evaluation_mcp -n aifeeders --follow
oc start-build linkedin-mcp   --from-dir=mcp_servers/linkedin_mcp   -n aifeeders --follow
```

> **Why builds are fast now:** A `.dockerignore` file excludes `.venv` (270 MB) and `__pycache__`. Builds upload ~140 KB instead of 270 MB and complete in under 2 minutes.

### Step 7 — Deploy all services

```bash
oc apply -f openshift/news-mcp/       -n aifeeders
oc apply -f openshift/pageindex-mcp/  -n aifeeders
oc apply -f openshift/evaluation-mcp/ -n aifeeders
oc apply -f openshift/linkedin-mcp/   -n aifeeders
oc apply -f openshift/api/            -n aifeeders
```

### Step 8 — Apply network policies, autoscaler, and pod disruption budget

```bash
oc apply -f openshift/networkpolicy.yaml -n aifeeders
oc apply -f openshift/hpa.yaml           -n aifeeders
oc apply -f openshift/pdb.yaml           -n aifeeders
```

### Step 9 — Create the CronJob

```bash
oc apply -f openshift/cronjob.yaml -n aifeeders
```

### Step 10 — Authorise LinkedIn

Open this URL in your browser. Replace `<your-cluster>` with your actual cluster hostname:

```
https://linkedin-mcp-aifeeders.apps.<your-cluster>/oauth/start
```

LinkedIn will ask you to sign in and approve the app. After clicking Allow, you'll see a success page. Verify the token is working:

```bash
curl https://linkedin-mcp-aifeeders.apps.<your-cluster>/oauth/status
# Expected: {"status": "ok", "expired": false, "scopes_sufficient": true}
```

### Step 11 — Verify everything is healthy

```bash
oc get pods -n aifeeders
# You should see 8 Running pods:
# 2× daily-news-api
# 2× news-mcp
# 1× pageindex-mcp   ← must be exactly 1
# 2× evaluation-mcp
# 1× linkedin-mcp    ← must be exactly 1
```

### Step 12 — Run a smoke test (no publishing)

```bash
# Temporarily disable publishing
oc patch configmap daily-news-config -n aifeeders \
  --patch '{"data":{"PUBLISHING_ENABLED":"false"}}'

# Trigger a manual run
oc create job --from=cronjob/daily-ai-news smoke-test-1 -n aifeeders
oc logs -f job/smoke-test-1 -n aifeeders

# Re-enable publishing
oc patch configmap daily-news-config -n aifeeders \
  --patch '{"data":{"PUBLISHING_ENABLED":"true"}}'
```

A successful smoke test ends with:
```
Workflow complete — status=EVALUATED published=0 errors=0
```
(0 published because publishing is disabled — that's correct.)

### Step 13 — Trigger a real live run

```bash
oc create job --from=cronjob/daily-ai-news live-run-$(date +%s) -n aifeeders
oc logs -f job/live-run-<id> -n aifeeders
```

A successful live run ends with:
```
post published post_urn=urn:li:share:XXXXXXXXX status=published
Workflow complete — status=PUBLISHED published=1 errors=0
```

---

## Rebuild and Redeploy

When you change code, prompts, or configuration:

```bash
# Rebuild the main image (src/ + prompts/)
oc start-build daily-news --from-dir=. -n aifeeders --follow

# Roll the deployment to the new image
oc rollout restart deployment/daily-news-api -n aifeeders
oc rollout status deployment/daily-news-api -n aifeeders

# Verify the new code is actually running
oc exec -n aifeeders deployment/daily-news-api -- \
  python3 -c "from daily_news.agents.persona_agent import _load_prompt; \
              print(_load_prompt('capitalist')[:80])"
```

---

## Environment Variables Reference

### ConfigMap (`openshift/configmap.yaml`) — no secrets here

| Variable | Default | What It Controls |
|----------|---------|-----------------|
| `LLM_BASE_URL` | `https://...` | IBM LLM Gateway base URL |
| `LLM_MODEL` | `qwen2-5-72b-instruct` | Model name for all LLM calls |
| `NEWS_MCP_URL` | `http://news-mcp:8000` | Internal service URL |
| `PAGEINDEX_MCP_URL` | `http://pageindex-mcp:8000` | Internal service URL |
| `EVALUATION_MCP_URL` | `http://evaluation-mcp:8000` | Internal service URL |
| `LINKEDIN_MCP_URL` | `http://linkedin-mcp:8000` | Internal service URL |
| `PUBLISHING_ENABLED` | `true` | Set `false` for smoke tests |
| `FACTUALITY_THRESHOLD` | `0.50` | Minimum factuality score to pass |
| `GROUNDEDNESS_THRESHOLD` | `0.50` | Minimum groundedness score to pass |
| `HALLUCINATION_THRESHOLD` | `0.85` | Maximum hallucination score to pass |
| `LANGFUSE_BASE_URL` | `https://us.cloud.langfuse.com` | Langfuse endpoint |

### Secrets (`openshift/secrets.yaml`) — keep these private

| Variable | Required | Description |
|----------|---------|-------------|
| `LLM_API_KEY` | ✅ | IBM LLM Gateway bearer token |
| `GNEWS_API_KEY` | ✅ | Primary GNews API key |
| `GNEWS_API_KEY_2` | Optional | Secondary key — auto-failover on primary 403 |
| `LINKEDIN_CLIENT_ID` | ✅ | LinkedIn OAuth app client ID |
| `LINKEDIN_CLIENT_SECRET` | ✅ | LinkedIn OAuth app client secret |
| `LINKEDIN_ACCESS_TOKEN` | ✅ | Member access token (rotates every 60 days) |
| `LINKEDIN_REDIRECT_URI` | ✅ | Must match LinkedIn Developer Portal exactly |
| `MCP_AUTH_TOKEN` | ✅ | Shared bearer token for all MCP server calls |
| `LANGFUSE_PUBLIC_KEY` | Optional | Langfuse project public key |
| `LANGFUSE_SECRET_KEY` | Optional | Langfuse project secret key |

---

## Health Checks

Every service exposes `/health`. Use this to verify everything is running:

```bash
CLUSTER="apps.<your-cluster>"

# Check all services at once
for svc in daily-news-api news-mcp pageindex-mcp evaluation-mcp linkedin-mcp; do
  echo "=== $svc ==="
  curl -sf https://${svc}-aifeeders.${CLUSTER}/health | python3 -m json.tool
done
```

Key things to look for:

**`news-mcp` health — watch for key rotation:**
```json
{
  "status": "healthy",
  "keys_configured": 2,
  "active_key_index": 1,
  "active_key_prefix": "abc123..."
}
```
If `active_key_index` is `2`, the primary key has hit its quota and the system has auto-rotated to the backup key. Normal behaviour — no action needed until tomorrow.

---

## Troubleshooting

### No post appeared on LinkedIn today

```bash
# Check if the CronJob ran
oc get jobs -n aifeeders --sort-by=.metadata.creationTimestamp | tail -5

# Check the last run's logs
oc logs -l app=daily-news-worker --tail=100 -n aifeeders
```

Look for `Workflow complete — status=PUBLISHED published=1` at the end of the logs.

### LinkedIn returns 401 or 403

The access token has expired (60-day lifetime) or was issued without `w_member_social` scope.

```bash
# Re-run the OAuth flow
open https://linkedin-mcp-aifeeders.apps.<your-cluster>/oauth/start

# Persist the new token
oc patch secret daily-news-secrets -n aifeeders \
  --patch '{"stringData":{"LINKEDIN_ACCESS_TOKEN":"<new-token>"}}'
oc rollout restart deployment/linkedin-mcp -n aifeeders
```

### Post publishes but looks blank in LinkedIn feed

The headline is on the same line as the emoji. This is a known LinkedIn mobile rendering issue. The fix is already in the code: `🤖  AI NEWS` is on line 1 and the headline is on line 2.

If you are running an old image, rebuild:
```bash
oc start-build daily-news --from-dir=. -n aifeeders --follow
oc rollout restart deployment/daily-news-api -n aifeeders
```

### Evaluation always returns REGENERATE

The LLM is scoring persona content as hallucinated. This usually means the source text passed to the evaluator is too short (GNews truncates content to ~250 chars on the free plan).

Check whether `_enrich_source()` is running in `evaluation_agent.py`. The enriched source should include the full summary, key points, and impact fields — not just the raw article content.

### GNews returns nothing / 500 errors from news-mcp

```bash
oc exec -n aifeeders deployment/news-mcp -- \
  curl -sf http://localhost:8000/health | python3 -m json.tool
```

If `keys_configured` is 1 and `active_key_index` is 2, you've exhausted both keys. Wait until midnight UTC for the quota to reset, or add a third key.

### Build takes too long (>5 minutes)

Check that `.dockerignore` exists in the repo root and excludes `.venv`:
```bash
cat .dockerignore | grep venv
# Should output: .venv/
```
Without `.dockerignore`, the build uploads the entire `.venv` directory (~270 MB). With it, uploads are ~140 KB.

### Namespace full of Error/Completed pods

```bash
# Delete all failed and completed pods at once
oc delete pods -n aifeeders --field-selector=status.phase=Failed
oc delete pods -n aifeeders --field-selector=status.phase=Succeeded

# Delete old manual test jobs
oc get jobs -n aifeeders --no-headers | grep -v "daily-ai-news$" | \
  awk '{print $1}' | xargs -r oc delete job -n aifeeders
```

---

## Monitoring

### Langfuse (LLM traces)

If you've configured Langfuse keys, every LLM call is traced. View traces at `https://us.cloud.langfuse.com`. Each workflow run creates a session with `session_id=run_id` — you can see all five persona LLM calls, their prompts, outputs, and latency side by side.

### LinkedIn audit log

```bash
curl -H "Authorization: Bearer ${MCP_AUTH_TOKEN}" \
  http://linkedin-mcp.aifeeders.svc.cluster.local:8000/mcp \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"linkedin_get_audit","arguments":{}}}'
```

Shows every post attempt, status, URN, and timestamp.

---

## Changelog

| Version | What Changed |
|---------|-------------|
| **v1.4** | Humanised persona voices with anti-pattern prompts; `skills.md` persona reference; removed persona numbers (1/4…4/4); stronger voice in all 5 prompt files |
| **v1.3** | Beautified post layout: Tech Strategist Mind as standalone section; `_clip_at_sentence()` to prevent mid-sentence truncation; short disclaimer; `.dockerignore` for fast builds |
| **v1.2** | GNews key pool rotation; `_enrich_source()` in evaluation; `_comments_blocked` soft-skip; headline on own line for feed card fix |
| **v1.1** | MCP microservices architecture; 6-layer guardrail pipeline; Langfuse tracing; multi-platform Helm chart |
| **v1.0** | Initial POC: 3-node LangGraph graph → first LinkedIn post |
