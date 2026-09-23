# AIFeeders — Operations Runbook

**Who this is for:** Anyone who needs to set up, run, fix, or understand AIFeeders — whether you're technical or not. If you can follow numbered steps, you can operate this system.

**What this document covers:** Everything from "what is this thing?" to "it broke at 3am, here's how to fix it."

---

## Table of Contents

1. [What Is AIFeeders, In Plain English](#1-what-is-aifeeders-in-plain-english)
2. [The Story of How It Was Built](#2-the-story-of-how-it-was-built)
3. [What's Running and Where](#3-whats-running-and-where)
4. [Your Normal Day (Usually Nothing to Do)](#4-your-normal-day-usually-nothing-to-do)
5. [First-Time Setup on a New Cluster](#5-first-time-setup-on-a-new-cluster)
6. [Rebuilding and Redeploying Code](#6-rebuilding-and-redeploying-code)
7. [LinkedIn Login — First Time and Re-authorising](#7-linkedin-login--first-time-and-re-authorising)
8. [Triggering a Run Manually](#8-triggering-a-run-manually)
9. [How to Read the Logs](#9-how-to-read-the-logs)
10. [Checking That Every Service Is Healthy](#10-checking-that-every-service-is-healthy)
11. [How the Workflow Works Step by Step](#11-how-the-workflow-works-step-by-step)
12. [The Safety Checks — What Gets Blocked and Why](#12-the-safety-checks--what-gets-blocked-and-why)
13. [What the LinkedIn Post Looks Like](#13-what-the-linkedin-post-looks-like)
14. [How the Personas Get Their Voices — skills.md](#14-how-the-personas-get-their-voices--skillsmd)
15. [Changing Settings Without Rebuilding](#15-changing-settings-without-rebuilding)
16. [Rotating API Keys and Tokens](#16-rotating-api-keys-and-tokens)
17. [Cleaning Up a Messy Namespace](#17-cleaning-up-a-messy-namespace)
18. [What Can Go Wrong and How to Fix It](#18-what-can-go-wrong-and-how-to-fix-it)
19. [Things That Are Known Limitations (Not Bugs)](#19-things-that-are-known-limitations-not-bugs)
20. [Glossary — Words Used in This Document](#20-glossary--words-used-in-this-document)

---

## 1. What Is AIFeeders, In Plain English

Think of AIFeeders as a tiny automated editorial team that works every morning without you doing anything.

Here's the full story of what happens every day at 10:00 AM UTC:

**Step 1: It goes looking for news.**
The system sends six search queries to a news API called GNews: AI technology, AI business, AI jobs, AI policy, enterprise automation, and product launches. It collects everything published in the last 48 hours.

**Step 2: It picks one story.**
From everything it found, it selects the single most relevant AI news article.

**Step 3: It reads and understands the article.**
An AI language model (IBM's `qwen2-5-72b-instruct`) reads the article and writes a structured summary: the headline, three key takeaways, and the impact on business, the workforce, and technology.

**Step 4: Five different people "react" to it.**
Five AI personas — each representing a different type of person — write their take on the news. A business leader. A working professional. A policy analyst. An early-career person. A tech strategist. Each has a distinct voice and point of view. These aren't generic summaries — they're genuine reactions from five different worldviews.

**Step 5: A safety panel reviews everything.**
Before anything goes out, six independent checks run: Is anything made up? Does it contain personal data like phone numbers? Is it toxic or politically biased? Does it fit LinkedIn's formatting rules?

**Step 6: If it passes, it goes on LinkedIn.**
One polished post, with all five perspectives embedded, goes live. The whole process takes 1–3 minutes.

**The outcome:** A daily AI news post on LinkedIn, written and published automatically, with no one pressing a button.

---

## 2. The Story of How It Was Built

Understanding the journey explains a lot of the design decisions. Nothing was overengineered from the start — every complexity was added to solve a real problem that appeared when the simpler version failed.

### Weekend 1 — "Does this even work?"

We started with the absolute minimum: a three-step program.
1. Fetch one AI news article from GNews
2. Ask an LLM to summarise it
3. Post the summary to LinkedIn

It worked. A post appeared on LinkedIn. That proved the idea was worth continuing.

**Problems we hit immediately:**
- GNews gives you 100 free requests per day. We burned through them in one afternoon of testing. Nothing worked again until midnight UTC.
- The IBM LLM gateway uses a self-signed security certificate. Every connection attempt failed until we added `verify=False` to bypass the check.
- The application logs were completely silent inside the OpenShift pod. Uvicorn (our web server) was eating all the log output. We had to add one line of Python to fix it.

### Week 2 — "Let's make each part independently replaceable"

A single monolithic script is fine for testing. For production, if the LinkedIn API changes, you don't want to redeploy the entire application. So we split everything into four independent **MCP servers** — small services, each responsible for one thing:

- **`news-mcp`:** All GNews API calls go here. The main workflow never talks to GNews directly.
- **`pageindex-mcp`:** A simple document storage service. Articles are stored here so the LLM can look up specific sections when writing summaries.
- **`evaluation-mcp`:** The safety checker. All six content guardrails live here.
- **`linkedin-mcp`:** All LinkedIn API calls, OAuth token management, and publishing go here.

Each service is a small web app that accepts requests over HTTP. The main workflow talks to them like it would talk to any external service. If one is down, only that one thing fails — the rest keeps running.

**What we learned:**
- LinkedIn's comment-posting API requires a special approval from LinkedIn that we didn't have. Rather than blocking everything, we embedded all five persona perspectives directly in the post body instead, and made comment failures silently skip — not crash.
- The document store (`pageindex-mcp`) keeps all data in memory. If you run two copies of it, each copy has a different set of documents and the workflow breaks. It must always run as exactly one instance.

### Month 1 — "Why does it keep failing in weird ways?"

Getting it to work once is different from getting it to work reliably every single day. Here's every subtle bug we found and fixed:

| What broke | Why it broke | How we fixed it |
|-----------|-------------|-----------------|
| The safety check always said "hallucination" | GNews free tier truncates article text to ~250 characters. The AI was checking a 500-word persona perspective against a 250-character source — of course it looked "made up" | We now enrich the source with the full summary, key points, and impact fields before checking |
| LinkedIn showed a blank post | The badge `🤖 AI NEWS` and the headline were on the same line. LinkedIn's mobile app clips everything after an emoji | We put the headline on its own line, after the badge |
| Running it twice posted the same article twice | The system uses a "publication key" to prevent duplicates. The key didn't include a unique run identifier, so two runs on the same day produced the same key | We added the last 8 characters of the run ID to the key |
| Five error messages every run | The LinkedIn Comments API was refusing every attempt with a 403 error — all five persona comment attempts failed with error logs | We detect the first refusal, log it once as an informational note, and skip the remaining attempts silently |
| GNews ran out of quota mid-morning | 100 free requests per day per key | We added support for a second GNews key. When the first key's quota runs out, the system automatically switches to the second |
| The workflow retried forever | When safety checks failed, the workflow re-ran the summarisation step. But the retry counter was never being updated | Fixed: the counter now increments inside the evaluation step |

### The persona voice problem

The personas were working technically — five outputs, all grammatically correct. But they all sounded the same. Corporate. Generic. Like a press release written by a committee.

The output looked like this (bad):
> *"This suggests that manufacturers could see a significant boost in sales and market share if they invest in integrating AI assistants."*

What we actually wanted looked like this (good):
> *"Earbuds have a decade-long head start and better privacy — they're a safer bet for AI assistants. Smart glasses face too many hurdles. Where will the next big investment round go?"*

The solution was to write `skills.md` — a document that describes each persona's worldview, their conversational style, and crucially, **anti-patterns**: exact examples of the corporate voice the AI should never produce, taken directly from bad outputs in live runs. We also rewrote all five persona prompt files to include these examples.

---

## 3. What's Running and Where

Everything runs in the `aifeeders` namespace on OpenShift.

### Always-running services

| Service | How many copies | Purpose |
|---------|----------------|---------|
| `daily-news-api` | 2 (auto-scales to 10) | Web API for manually triggering runs and checking status |
| `news-mcp` | 2 | Fetches news from GNews. Has 2-key rotation built in |
| `pageindex-mcp` | **1 only** | Stores article content in memory. Must be 1 — cannot scale |
| `evaluation-mcp` | 2 | Runs the six safety checks |
| `linkedin-mcp` | **1 only** | Manages LinkedIn login and posting. Must be 1 — stores token in memory |

### The daily job

| Job | When | What it does |
|-----|------|-------------|
| `daily-ai-news` CronJob | Every day at 10:00 AM UTC | Runs `python -m daily_news.workflow_runner` |

### External services it depends on

| Service | What for | Important limits |
|---------|----------|-----------------|
| GNews API | Finding news articles | 100 requests/day free. Resets at midnight UTC |
| IBM LLM Gateway | Writing summaries and persona perspectives | Self-signed certificate — connections use `verify=False` |
| LinkedIn API | Publishing posts | 3000 character limit per post |
| Langfuse | Optional: logging AI traces | Works fine if absent — just no traces |

---

## 4. Your Normal Day (Usually Nothing to Do)

On a normal day, the CronJob fires at 10:00 AM UTC (3:30 PM IST) and a post appears on LinkedIn about 2–5 minutes later. You don't need to do anything.

**Things worth checking occasionally:**

- **LinkedIn profile at ~10:15 AM UTC** — confirm a new post appeared. If not, check the logs.
- **GNews quota** — the free plan gives 100 requests/day per key. With 2 keys, you have 200/day. The workflow uses 6 requests per run. This is very comfortable.
- **LinkedIn token expiry** — tokens last 60 days. Set a calendar reminder to re-authorise before expiry.

**How to quickly check if today's run succeeded:**

```bash
oc logs -l app=daily-news-worker --tail=10 -n aifeeders
```

The last line should say:
```
Workflow complete — status=PUBLISHED published=1 errors=0
```

---

## 5. First-Time Setup on a New Cluster

Follow this exactly. Don't skip steps.

### Step 1 — Install the tools you need on your laptop

You need two command-line tools:
- **`oc`** (OpenShift CLI): Download from your cluster's web console → top right "?" menu → "Command Line Tools"
- **`git`**: Install from https://git-scm.com or run `brew install git` on Mac

### Step 2 — Log in to the cluster

```bash
# Get your login token from the cluster web console:
# Top right → your username → "Copy Login Command"
oc login --token=<paste-token-here> --server=https://api.<your-cluster>:6443

# Create the project (or skip if it exists)
oc new-project aifeeders
```

### Step 3 — Clone the code

```bash
git clone https://github.com/k-nishant09/AINewsFeederLinkedin.git
cd AINewsFeederLinkedin
```

### Step 4 — Edit the ConfigMap (settings)

The ConfigMap holds non-secret configuration. You probably only need to change the `LLM_BASE_URL` to point to your IBM LLM gateway.

```bash
# Open and review
cat openshift/configmap.yaml

# Apply it
oc apply -f openshift/configmap.yaml -n aifeeders
```

### Step 5 — Edit and apply the Secrets

This is the most important step. Open `openshift/secrets.yaml` and replace every `<placeholder>` value with your real credentials.

```bash
# Open in editor (or use nano/vim)
nano openshift/secrets.yaml
```

Fill in these values:

| What to fill in | Where to get it |
|----------------|----------------|
| `LLM_API_KEY` | IBM internal — your LLM gateway bearer token |
| `GNEWS_API_KEY` | Sign up free at https://gnews.io |
| `GNEWS_API_KEY_2` | Optional — a second GNews key for failover |
| `LINKEDIN_CLIENT_ID` | LinkedIn Developer Portal → your app → Auth tab |
| `LINKEDIN_CLIENT_SECRET` | Same place as above |
| `LINKEDIN_ACCESS_TOKEN` | Leave blank for now — you'll get this in Step 10 |
| `LINKEDIN_REDIRECT_URI` | `https://linkedin-mcp-aifeeders.apps.<your-cluster>/oauth/callback` |
| `MCP_AUTH_TOKEN` | Make up any long random string, e.g. `aifeeders-mcp-token-2026` |

**⚠️ Important:** Never commit `secrets.yaml` with real values. Only commit `<placeholder>` versions.

```bash
oc apply -f openshift/secrets.yaml -n aifeeders
```

### Step 6 — Apply RBAC (permissions)

```bash
oc apply -f openshift/rbac.yaml -n aifeeders
```

### Step 7 — Create build configurations (first time only)

This tells OpenShift how to build each service's container image.

```bash
for svc in daily-news news-mcp pageindex-mcp evaluation-mcp linkedin-mcp; do
  oc create imagestream $svc -n aifeeders 2>/dev/null || true
  oc new-build --name=$svc --binary --strategy=docker -n aifeeders 2>/dev/null || true
done
```

### Step 8 — Build the container images

This step downloads your code into OpenShift and builds Docker images from it. Each one takes about 2 minutes.

```bash
# Main application (agents, workflow, API, prompts)
oc start-build daily-news --from-dir=. -n aifeeders --follow

# The four MCP servers
oc start-build news-mcp       --from-dir=mcp_servers/news_mcp       -n aifeeders --follow
oc start-build pageindex-mcp  --from-dir=mcp_servers/pageindex_mcp  -n aifeeders --follow
oc start-build evaluation-mcp --from-dir=mcp_servers/evaluation_mcp -n aifeeders --follow
oc start-build linkedin-mcp   --from-dir=mcp_servers/linkedin_mcp   -n aifeeders --follow
```

You should see `Push successful` at the end of each build.

### Step 9 — Deploy all services

```bash
oc apply -f openshift/news-mcp/       -n aifeeders
oc apply -f openshift/pageindex-mcp/  -n aifeeders
oc apply -f openshift/evaluation-mcp/ -n aifeeders
oc apply -f openshift/linkedin-mcp/   -n aifeeders
oc apply -f openshift/api/            -n aifeeders
oc apply -f openshift/networkpolicy.yaml -n aifeeders
oc apply -f openshift/hpa.yaml           -n aifeeders
oc apply -f openshift/pdb.yaml           -n aifeeders
oc apply -f openshift/cronjob.yaml       -n aifeeders
```

### Step 10 — Check all pods are running

```bash
oc get pods -n aifeeders
```

Expected output (wait 2–3 minutes for all pods to start):
```
daily-news-api-xxxxx   1/1   Running   0   2m
daily-news-api-xxxxx   1/1   Running   0   2m
news-mcp-xxxxx         1/1   Running   0   2m
news-mcp-xxxxx         1/1   Running   0   2m
pageindex-mcp-xxxxx    1/1   Running   0   2m
evaluation-mcp-xxxxx   1/1   Running   0   2m
evaluation-mcp-xxxxx   1/1   Running   0   2m
linkedin-mcp-xxxxx     1/1   Running   0   2m
```

If any pod shows `Error` or `CrashLoopBackOff`, check its logs:
```bash
oc logs <pod-name> -n aifeeders
```

### Step 11 — Authorise LinkedIn

Open this URL in your web browser (replace `<your-cluster>` with your cluster's hostname):
```
https://linkedin-mcp-aifeeders.apps.<your-cluster>/oauth/start
```

LinkedIn will ask you to sign in and approve the app permissions. Click Allow. You'll see a success page.

Now save the token so it survives pod restarts:
```bash
# Get the token the pod just stored
NEW_TOKEN="$(oc exec -n aifeeders deployment/linkedin-mcp -- printenv LINKEDIN_ACCESS_TOKEN)"

# Save it to the secret
oc patch secret daily-news-secrets -n aifeeders \
  --patch "{\"stringData\":{\"LINKEDIN_ACCESS_TOKEN\":\"${NEW_TOKEN}\"}}"
```

Verify the token is working:
```bash
curl https://linkedin-mcp-aifeeders.apps.<your-cluster>/oauth/status
```
Expected: `{"status": "ok", "expired": false, "scopes_sufficient": true}`

### Step 12 — Smoke test (run without posting)

```bash
# Turn off publishing temporarily
oc patch configmap daily-news-config -n aifeeders \
  --patch '{"data":{"PUBLISHING_ENABLED":"false"}}'

# Trigger a run
oc create job --from=cronjob/daily-ai-news smoke-test-1 -n aifeeders

# Watch the logs
oc logs -f job/smoke-test-1 -n aifeeders

# Turn publishing back on
oc patch configmap daily-news-config -n aifeeders \
  --patch '{"data":{"PUBLISHING_ENABLED":"true"}}'
```

A successful smoke test ends with:
```
Workflow complete — status=EVALUATED published=0 errors=0
```

### Step 13 — First real live run

```bash
oc create job --from=cronjob/daily-ai-news first-live-run -n aifeeders
oc logs -f job/first-live-run -n aifeeders
```

A successful live run ends with:
```
post published post_urn=urn:li:share:XXXXXXXXX status=published
Workflow complete — status=PUBLISHED published=1 errors=0
```

Check your LinkedIn profile — the post should be there within 30 seconds.

---

## 6. Rebuilding and Redeploying Code

### When do you need to rebuild?

Rebuild when you change:
- Any Python file in `src/`
- Any prompt file in `prompts/`
- Any MCP server file in `mcp_servers/`
- `Dockerfile` or `pyproject.toml`

No rebuild needed when you only change:
- `openshift/configmap.yaml` — just run `oc apply`
- `openshift/secrets.yaml` — run `oc apply`, then restart the affected service
- The CronJob schedule in `openshift/cronjob.yaml` — just run `oc apply`

### How to rebuild the main application

```bash
# 1. Build the new image (uploads ~140 KB, takes ~2 minutes)
oc start-build daily-news --from-dir=. -n aifeeders --follow

# 2. Deploy the new image
oc rollout restart deployment/daily-news-api -n aifeeders

# 3. Wait for it to finish
oc rollout status deployment/daily-news-api -n aifeeders

# 4. Verify the right code is running
oc exec -n aifeeders deployment/daily-news-api -- \
  python3 -c "from daily_news.agents.persona_agent import _load_prompt; \
              print(_load_prompt('capitalist')[:100])"
```

### How to rebuild an MCP server

```bash
# Example: rebuild news-mcp
oc start-build news-mcp --from-dir=mcp_servers/news_mcp -n aifeeders --follow
oc rollout restart deployment/news-mcp -n aifeeders
oc rollout status deployment/news-mcp -n aifeeders
```

### Why builds are fast (< 2 minutes)

The `.dockerignore` file at the root of the repo excludes the `.venv` directory (which is 270 MB of Python packages). Without it, every build would upload 270 MB and take 10–15 minutes. With it, the upload is ~140 KB.

---

## 7. LinkedIn Login — First Time and Re-authorising

### Why this is needed

LinkedIn access tokens expire after 60 days. When expired, every post attempt fails with `401 AUTH_ERROR`. You need to re-authorise.

### How to re-authorise (takes 2 minutes)

**Step 1:** Open this URL in your web browser:
```
https://linkedin-mcp-aifeeders.apps.<your-cluster>/oauth/start
```

**Step 2:** LinkedIn shows you a permissions screen. Sign in as the account owner. Click Allow.

**Step 3:** You see a success page. The pod now has a new token in memory.

**Step 4:** Save the token so it survives pod restarts:
```bash
NEW_TOKEN="$(oc exec -n aifeeders deployment/linkedin-mcp -- printenv LINKEDIN_ACCESS_TOKEN)"

oc patch secret daily-news-secrets -n aifeeders \
  --patch "{\"stringData\":{\"LINKEDIN_ACCESS_TOKEN\":\"${NEW_TOKEN}\"}}"

oc rollout restart deployment/linkedin-mcp -n aifeeders
```

**Step 5:** Verify:
```bash
curl https://linkedin-mcp-aifeeders.apps.<your-cluster>/oauth/status
# Expected: {"status": "ok", "expired": false, "scopes_sufficient": true}
```

### What scopes does LinkedIn need?

| Scope | What it's for |
|-------|-------------|
| `openid` | Identity verification |
| `profile` | Get your LinkedIn person ID |
| `email` | Secondary identity |
| `w_member_social` | **Required to post** — without this, every post returns 403 |

If `w_member_social` is missing, you need to go to the LinkedIn Developer Portal → your app → Products → request "Share on LinkedIn" again, then re-authorise.

---

## 8. Triggering a Run Manually

### Via CLI (most common)

```bash
# Create a job from the CronJob template
oc create job --from=cronjob/daily-ai-news manual-$(date +%s) -n aifeeders

# Watch the logs in real time
oc logs -f job/manual-<id> -n aifeeders
```

### Via the REST API

```bash
# Trigger via API (no body needed)
curl -X POST https://daily-news-api-aifeeders.apps.<your-cluster>/workflow/daily-news

# Returns: {"run_id": "RUN-XXXX", "status": "STARTED"}

# Check status
curl https://daily-news-api-aifeeders.apps.<your-cluster>/workflow/RUN-XXXX
```

### Run without posting (safe test)

```bash
# Disable publishing
oc patch configmap daily-news-config -n aifeeders \
  --patch '{"data":{"PUBLISHING_ENABLED":"false"}}'

# Run
oc create job --from=cronjob/daily-ai-news nopost-$(date +%s) -n aifeeders
oc logs -f job/nopost-<id> -n aifeeders

# Re-enable publishing
oc patch configmap daily-news-config -n aifeeders \
  --patch '{"data":{"PUBLISHING_ENABLED":"true"}}'
```

---

## 9. How to Read the Logs

Every log line follows this format:
```
2026-09-23 10:40:25 INFO  daily_news.agents.publisher_agent — [RUN-E11162872227] publishing main post
```

Fields: `timestamp  level  module — [run_id] message`

### What a healthy run looks like

```
[RUN-xxx] discover_news started
[RUN-xxx] discovered 15 raw articles
[RUN-xxx] deduplicated: 15 → 15
[RUN-xxx] selected 1 story for summarisation
[RUN-xxx] summarised 1 articles
[RUN-xxx] eval article=news-abc decision=PASS factuality=0.95 groundedness=0.95 hallucination=0.20
[RUN-xxx] publishing main post article=news-abc key=news-abc:2026-09-23:78173e...:xxxx
[RUN-xxx] post published post_urn=urn:li:share:7508479385549697024 status=published
[RUN-xxx] Comments API not available (PERMISSION_ERROR) — personas are embedded in post body. Skipping remaining comment attempts.
Workflow complete — status=PUBLISHED published=1 errors=0
```

The `PERMISSION_ERROR` line is expected and normal — LinkedIn's comment API isn't approved, so personas live in the post body.

### What a safety retry looks like

```
[RUN-xxx] eval article=news-abc decision=REGENERATE factuality=0.42 groundedness=0.31
[RUN-xxx] REGENERATE decision — retry 1/2
[RUN-xxx] summarised 1 articles    ← summarising again
[RUN-xxx] eval article=news-abc decision=PASS factuality=0.81 groundedness=0.79
[RUN-xxx] post published post_urn=urn:li:share:xxx status=published
```

Up to 2 retries happen automatically. If it fails after 2 retries, any articles that did pass get published, and failed ones are skipped.

### What a blocked post looks like

```
[RUN-xxx] eval article=news-abc decision=BLOCK ...
[RUN-xxx] publish_eligible=false (decision=BLOCK) — skipping article=news-abc
Workflow complete — status=PUBLISHED published=0 errors=0
```

This is the safety system doing its job. `published=0` with `errors=0` means everything worked correctly — the content just didn't pass.

### Useful log commands

```bash
# Latest CronJob run
oc logs -l app=daily-news-worker --tail=100 -n aifeeders

# Follow a manual job in real time
oc logs -f job/manual-<id> -n aifeeders

# API service logs
oc logs -l app=daily-news-api --tail=50 -n aifeeders

# LinkedIn MCP (publishing errors)
oc logs -l app=linkedin-mcp --tail=50 -n aifeeders

# Evaluation MCP (safety scores)
oc logs -l app=evaluation-mcp --tail=50 -n aifeeders

# News MCP (key rotation)
oc logs -l app=news-mcp --tail=50 -n aifeeders
```

---

## 10. Checking That Every Service Is Healthy

Every service has a `/health` endpoint. Check them all at once:

```bash
CLUSTER="apps.<your-cluster>"
for svc in daily-news-api news-mcp pageindex-mcp evaluation-mcp linkedin-mcp; do
  echo "=== $svc ==="
  curl -sf https://${svc}-aifeeders.${CLUSTER}/health | python3 -m json.tool
done
```

### What to look for

**`news-mcp`** — check GNews key status:
```json
{
  "status": "healthy",
  "keys_configured": 2,
  "active_key_index": 1,
  "active_key_prefix": "abc123..."
}
```
- `keys_configured: 2` means both keys are set up ✅
- `active_key_index: 1` means primary key is still working ✅
- `active_key_index: 2` means primary ran out of quota and auto-rotated to secondary — normal behaviour, no action needed

**`linkedin-mcp`** — check token validity:
```json
{
  "status": "healthy",
  "oauth_configured": true,
  "token_present": true
}
```
If `token_present: false`, run the OAuth flow (see Section 7).

---

## 11. How the Workflow Works Step by Step

The workflow is a nine-step pipeline. Each step hands its output to the next.

```
Step 1: discover_news
  → Sends 6 queries to GNews (AI tech, business, jobs, policy, automation, products)
  → Collects all articles from the last 48 hours

Step 2: deduplicate
  → Removes articles with the same URL or title
  → Usually reduces from ~30 articles to ~15 unique ones

Step 3: fetch_articles
  → Fetches full content for each article (where available)

Step 4: index_pageindex
  → Stores each article in the in-memory document store
  → Allows the LLM to retrieve specific sections later

Step 5: select_stories
  → Picks 1 article to process (the most relevant AI story)

Step 6: summarize
  → LLM reads the article + stored sections
  → Produces: headline, summary, 3 key points, business/workforce/tech/policy impacts

Step 7: generate_personas
  → 5 LLM calls run in parallel (one per persona)
  → Each uses its own prompt file from prompts/*.txt
  → Produces a perspective + 2 evidence bullets per persona
  → Persona enum value always injected from agent context (never trusted from LLM output)

Step 8: evaluate
  → Sends summary + personas to evaluation-mcp
  → 6 safety layers check everything
  → Decision: PASS / REGENERATE / BLOCK / HUMAN_REVIEW
  → If REGENERATE: loops back to Step 6 (max 2 times)

Step 9: publish
  → Composes the full post text (deterministic — no LLM)
  → Calls linkedin-mcp to post
  → Returns post URN and full audit record
```

---

## 12. The Safety Checks — What Gets Blocked and Why

The safety system runs before anything is published. It has six layers:

| Layer | What it checks | What fails |
|-------|---------------|-----------|
| 1. Prompt injection | Does the text try to override the AI's instructions? | Anything with jailbreak patterns |
| 2. Factuality | Are the claims grounded in the source article? | Score below 0.50 triggers a retry |
| 3. Hallucination | Does the text mention things not in the source? | Score above 0.85 triggers a retry |
| 4. PII | Does it contain emails, phone numbers, SSNs, card numbers? | Any PII detected = permanent block |
| 5. Policy | Is it toxic, politically biased, or brand-unsafe? | Toxicity > 0.30 = block; political bias = human review |
| 6. Format | Is it under 3000 characters? Any unsafe URLs? | Character limit exceeded = retry |

**Why hallucination check used to always fail:** GNews free plan truncates article content to ~250 characters. The AI was writing 500-word persona perspectives, then the safety checker compared them against the 250-character source and flagged everything as "made up." 

**The fix:** Before checking, we build an "enriched source" that combines the article snippet with the full summary, key points, and impact fields we generated. This gives the safety checker enough context to make accurate judgements.

---

## 13. What the LinkedIn Post Looks Like

Each post follows this exact structure:

```
🤖  AI NEWS                          ← Badge on its own line (critical for mobile feed card)

<Headline>                           ← Headline on its own line

<2-3 sentence summary>

📌  Key Points
  • First key takeaway
  • Second key takeaway
  • Third key takeaway

📈  Business Impact   —  <one sentence>
👷  Workforce Impact  —  <one sentence>
🔬  Tech Impact       —  <one sentence>
🏛️  Policy Impact     —  <one sentence, only when relevant>

🔗  Source: <url>

──────────────────
🧵  Perspectives

💼  Capitalist Mind
<2-3 sentence perspective in founder/operator voice>
    ↳  <direct quote or fact from article>
    ↳  <second supporting fact>

👷  Working Professional Mind
<2-3 sentence perspective in Slack-message voice>

🏛️  Government Mind
<2-3 sentences from a policy analyst>

🎓  Young / Fresher Mind
<2-3 sentences in group chat voice>

──────────────────
🧠  Tech Strategist Mind
<2-3 sentences from a staff engineer / architect>
    ↳  <technical fact from article>

──────────────────
⚠️ These are AI-simulated perspectives — not verified opinions or professional advice.
🤖 Built with AIFeeders · Powered by Agentic AI

#AI #AgenticAI #LLM #GenerativeAI #AINews #TechNews #AIStrategy
#MachineLearning #AIInnovation #DigitalTransformation #AILeadership
```

**Why the badge is on its own line:** LinkedIn's mobile app clips the first line of a post after an emoji. If `🤖 AI NEWS` and the headline are on the same line, mobile users only see the emoji. Putting the headline on line 2 ensures it always appears in the feed card preview.

**Why no sentence is ever cut off:** Each persona section is clipped at the last complete sentence (`.`, `!`, or `?`) before the character budget runs out — never mid-sentence.

---

## 14. How the Personas Get Their Voices — skills.md

The five personas are defined in [`skills.md`](skills.md). This document describes each persona's worldview, what they notice first in a news story, how they sound, and crucially — **anti-patterns** showing exactly what generic AI output looks like vs. what the target voice should be.

Each persona has its own prompt file that gets loaded at runtime:

| Persona | Prompt file | Voice |
|---------|------------|-------|
| 💼 Capitalist Mind | `prompts/capitalist.txt` | Founder/operator, P&L lens, ends with a pointed question |
| 👷 Working Professional Mind | `prompts/labor.txt` | Slack-message energy, no HR-speak, ends with something actionable |
| 🏛️ Government Mind | `prompts/policy.txt` | Policy memo precision, names specific regulations, no vague "could influence" |
| 🎓 Young / Fresher Mind | `prompts/genz.txt` | Group chat energy, says exactly why it matters, no "big deal for us" |
| 🧠 Tech Strategist Mind | `prompts/linkedin.txt` | RFC comment style, names the trade-off, no "has implications" |

Each prompt file includes:
- **✗ Anti-patterns** — exact examples of the corporate voice taken from actual bad outputs in live runs
- **✓ Good examples** — the target voice, taken from `skills.md`

When a persona sounds generic or corporate, the fix is to update the relevant `.txt` file's anti-patterns with the bad output as a `✗` example, then rebuild.

---

## 15. Changing Settings Without Rebuilding

These settings live in the ConfigMap and take effect immediately — no rebuild needed.

```bash
# Check current settings
oc get configmap daily-news-config -n aifeeders -o yaml

# Change a setting (example: lower hallucination threshold)
oc patch configmap daily-news-config -n aifeeders \
  --patch '{"data":{"EVAL_HALLUCINATION_THRESHOLD":"0.90"}}'

# The change takes effect on the next workflow run — no restart needed
```

Common settings to change:

| Setting | Default | When to change |
|---------|---------|---------------|
| `PUBLISHING_ENABLED` | `true` | Set to `false` for safe smoke tests |
| `EVAL_FACTUALITY_THRESHOLD` | `0.50` | Raise if too many articles pass; lower if too many are blocked |
| `EVAL_HALLUCINATION_THRESHOLD` | `0.85` | Lower (e.g. 0.75) for stricter checks |

---

## 16. Rotating API Keys and Tokens

### GNews API key has exhausted its quota

```bash
# Check which key is active
oc exec -n aifeeders deployment/news-mcp -- \
  curl -sf http://localhost:8000/health | python3 -m json.tool

# Patch in a new second key
oc patch secret daily-news-secrets -n aifeeders \
  --patch '{"stringData":{"GNEWS_API_KEY_2":"<new-key>"}}'

# Restart news-mcp to pick up the new key
oc rollout restart deployment/news-mcp -n aifeeders

# Verify: should show keys_configured: 2
oc exec -n aifeeders deployment/news-mcp -- \
  curl -sf http://localhost:8000/health | python3 -m json.tool
```

### LinkedIn token has expired (60-day rotation)

See Section 7 for the full re-authorisation flow.

Quick version:
```bash
# 1. Open in browser and complete OAuth
open https://linkedin-mcp-aifeeders.apps.<your-cluster>/oauth/start

# 2. Save the new token
NEW_TOKEN="$(oc exec -n aifeeders deployment/linkedin-mcp -- printenv LINKEDIN_ACCESS_TOKEN)"
oc patch secret daily-news-secrets -n aifeeders \
  --patch "{\"stringData\":{\"LINKEDIN_ACCESS_TOKEN\":\"${NEW_TOKEN}\"}}"
oc rollout restart deployment/linkedin-mcp -n aifeeders
```

### LLM API key rotation

```bash
oc patch secret daily-news-secrets -n aifeeders \
  --patch '{"stringData":{"LLM_API_KEY":"<new-key>"}}'

# Restart all services that use the LLM
oc rollout restart deployment/daily-news-api -n aifeeders
oc rollout restart deployment/evaluation-mcp -n aifeeders
```

---

## 17. Cleaning Up a Messy Namespace

After many test runs, the namespace accumulates dead pods and old jobs. Clean them all in one go:

```bash
# Delete all Error/Failed pods
oc delete pods -n aifeeders --field-selector=status.phase=Failed

# Delete all Completed pods
oc delete pods -n aifeeders --field-selector=status.phase=Succeeded

# Delete manual test jobs (keeps the daily-ai-news CronJob)
oc get jobs -n aifeeders --no-headers | grep -v "daily-ai-news$" | \
  awk '{print $1}' | xargs -r oc delete job -n aifeeders

# Optional: prune old builds, keep last 2 per service
for svc in daily-news evaluation-mcp linkedin-mcp news-mcp pageindex-mcp; do
  builds=$(oc get builds -n aifeeders -l buildconfig=$svc \
    --sort-by=.metadata.creationTimestamp --no-headers | awk '{print $1}')
  count=$(echo "$builds" | wc -l | tr -d ' ')
  if [[ $count -gt 2 ]]; then
    echo "$builds" | head -n $((count - 2)) | \
      xargs -r oc delete build -n aifeeders
  fi
done
```

---

## 18. What Can Go Wrong and How to Fix It

### No post appeared today

```bash
# Check if the CronJob even ran
oc get jobs -n aifeeders --sort-by=.metadata.creationTimestamp | tail -5

# Check the last run's logs
oc logs -l app=daily-news-worker --tail=50 -n aifeeders
```

Common causes: GNews quota exhausted (see key rotation in Section 16), LinkedIn token expired (see Section 7), or a service is down (check health — Section 10).

### LinkedIn returns 401 or 403

- **401:** Token expired. Re-authorise (Section 7).
- **403 on posts:** Token is missing `w_member_social` scope. Re-authorise and check the LinkedIn Developer Portal has "Share on LinkedIn" product enabled.
- **403 on comments:** Expected. The Comments API needs a separate approval. This is handled gracefully — personas are in the post body.

### Post is published but looks wrong

**Blank feed card on mobile:** Rebuild with the latest code — the headline-on-own-line fix is already applied.

**Personas sound generic/corporate:** The prompt anti-patterns need updating. Add the bad output as a `✗` example to the relevant prompt file, rebuild, and redeploy.

**Post is cut off:** Check that `_clip_at_sentence()` is running. The post should never cut mid-sentence — it clips at the last `.`/`!`/`?` before the character budget runs out.

### Evaluation always blocks/regenerates

```bash
# Check the evaluation scores in the logs
oc logs -l app=daily-news-worker --tail=50 -n aifeeders | grep "eval article"
```

If hallucination scores are consistently above 0.85, the source enrichment may not be working. Check that `_enrich_source()` is present in `evaluation_agent.py`.

If factuality/groundedness are consistently below 0.50, the LLM may be fabricating too much. Check the GNews key — if it's exhausted, searches return very few articles, and the LLM has to work with poor source material.

### Build takes more than 5 minutes

Check the `.dockerignore` file:
```bash
cat .dockerignore | grep -E "venv|__pycache__"
```
Should show `.venv/` and `**/__pycache__/`. If missing, the build is uploading 270 MB of Python packages.

### A service pod won't start

```bash
oc describe pod <pod-name> -n aifeeders
oc logs <pod-name> -n aifeeders --previous
```

Most common causes: missing secret key (check `openshift/secrets.yaml`), wrong image tag, or insufficient memory on the node.

---

## 19. Things That Are Known Limitations (Not Bugs)

| Limitation | Explanation |
|-----------|------------|
| **LinkedIn Comments API disabled** | Requires "Community Management API" product approval from LinkedIn. Pending. Personas are in the post body as the workaround. |
| **GNews free plan: 100 req/day** | The workflow uses 6 requests per run. With 2 keys, you have 200/day — comfortable. But heavy testing can exhaust keys quickly. |
| **One article per run** | Only 1 story is processed per CronJob run. This is intentional — it keeps costs low and quality high. Increase `[:1]` to `[:3]` in `select_stories` to change this. |
| **`pageindex-mcp` must be 1 replica** | In-memory document store. No shared state. If you run 2 replicas, each pod has different data and the workflow breaks. |
| **`linkedin-mcp` must be 1 replica** | In-memory token storage and idempotency registry. Same issue. |
| **LLM gateway self-signed cert** | All IBM LLM Gateway calls use `verify=False`. Fine for internal use; would need a real cert for external deployment. |

---

## 20. Glossary — Words Used in This Document

| Word | What it means in this context |
|------|-------------------------------|
| **Agent** | A piece of code that calls an LLM and processes the output. Not autonomous — it always follows a fixed algorithm. |
| **CronJob** | A Kubernetes scheduler. Like a cron tab entry, but for containers. Runs `daily-ai-news` every day at 10:00 AM UTC. |
| **Evaluation / Guardrails** | The safety pipeline that checks content before it's published. Six layers. Deterministic outcome. |
| **`_enrich_source()`** | A function in `evaluation_agent.py` that builds a richer source text for the safety checker by combining the raw article with the full summary fields. Fixes false hallucination scores. |
| **`_clip_at_sentence()`** | A function in `publisher_agent.py` that trims text at the last sentence boundary before the character limit. Prevents mid-sentence truncation. |
| **`_comments_blocked`** | A flag in `publisher_agent.py` set to `True` after the first LinkedIn Comments API 403. Skips all remaining comment attempts silently. |
| **GNews key rotation** | Automatic switch from `GNEWS_API_KEY` to `GNEWS_API_KEY_2` when the primary key returns HTTP 403 (quota exhausted). |
| **`publication_key`** | A unique string combining article ID, date, and run ID. Used to prevent publishing the same post twice. |
| **MCP server** | Model Context Protocol server. A small FastAPI app that wraps one external API or capability. Accepts tool calls via `POST /call`. |
| **LangGraph** | The Python framework used to build the 9-node workflow. Each node is a function; edges define the flow. |
| **`run_id`** | A unique ID for each workflow execution, e.g. `RUN-E11162872227`. All log lines from one run share this ID. |
| **`skills.md`** | A reference document describing each persona's voice, worldview, and anti-patterns. Used when tuning prompt files. |
| **`PASS / REGENERATE / BLOCK / HUMAN_REVIEW`** | The four possible outcomes from the safety pipeline. Only `PASS` results in publishing. |
| **`verify=False`** | A flag on HTTP connections that disables TLS certificate verification. Used for IBM's LLM gateway which uses a self-signed cert. |
| **ImageStream** | An OpenShift concept. A named reference to a container image that gets updated every time you run a build. |
| **BuildConfig** | An OpenShift concept. Defines how to build a container image from source code. |
| **`post_urn`** | The unique identifier LinkedIn returns for every published post. Looks like `urn:li:share:7508479385549697024`. Confirms the post exists. |
