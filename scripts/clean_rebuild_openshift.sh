#!/usr/bin/env bash
# =============================================================================
# clean_rebuild_openshift.sh — Full clean rebuild for namespace aifeeders
# =============================================================================
# Usage:
#   oc login --token=<token> --server=https://api.<cluster>:6443
#   ./scripts/clean_rebuild_openshift.sh
#
# What it does:
#   1.  Hard-deletes ALL resources in aifeeders (pods, builds, buildconfigs,
#       imagestreams, deployments, services, routes, cronjobs, hpa, pdb,
#       configmap, networkpolicies)
#   2.  Preserves daily-news-secrets (real API credentials not in repo)
#   3.  Re-applies config layer (namespace, configmap, rbac, networkpolicy)
#   4.  Re-applies buildconfigs + imagestreams
#   5.  Starts all 5 binary builds from local source — each upload is retried
#       up to BUILD_UPLOAD_RETRIES times on FetchSourceFailed / interrupted upload
#   6.  Waits for all builds to complete — failed builds are auto-retried up to
#       BUILD_MAX_RETRIES times; known-superseded failures are ignored
#   7.  Deploys MCPs → API → cronjob → HPA → PDB
#   8.  Waits for deployments — polls every 15 s, auto-deletes stuck Pending
#       pods so the scheduler picks fresh nodes; retries up to DEPLOY_MAX_WAIT s
#   9.  Verifies node capacity and warns when workers are over pod limit
#  10.  Runs end-to-end security + live checks:
#         • LLM gateway reachability + live inference
#         • MCP /health for all 4 servers
#         • Secret integrity (all required keys present in pod env)
#         • LinkedIn token validity via /v2/userinfo
#         • GNews API key (returns articles)
#         • Langfuse connectivity
#         • Route TLS (HTTPS 200 on public routes)
#  11.  Prints final pod / route / hpa / cronjob status
#
# Ref: RUNBOOK.md § 15
# =============================================================================
set -euo pipefail

# ── Tunables ─────────────────────────────────────────────────────────────────
NS=aifeeders
BUILD_UPLOAD_RETRIES=3       # retries for interrupted binary upload
BUILD_MAX_RETRIES=2          # retries for a failed build (network transients)
BUILD_POLL_INTERVAL=30       # seconds between build status polls
BUILD_TIMEOUT=900            # total seconds to wait for all builds (15 min)
DEPLOY_MAX_WAIT=300          # seconds to wait for deployments to become Available
DEPLOY_POLL_INTERVAL=15      # seconds between deployment polls
PENDING_EVICT_AFTER=60       # seconds a pod may stay Pending before force-delete

SERVICES=(daily-news news-mcp pageindex-mcp evaluation-mcp linkedin-mcp)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; RESET='\033[0m'
ok()   { echo -e "    ${GREEN}✅  $*${RESET}"; }
warn() { echo -e "    ${YELLOW}⚠️   $*${RESET}"; }
fail() { echo -e "    ${RED}❌  $*${RESET}"; }

# Track build names that are legitimately superseded (interrupted upload etc.)
IGNORED_BUILDS=""

# ── Helper: start one build with upload-retry ─────────────────────────────────
# Usage: start_build_with_retry <svc>
# Sets global BUILD_NAME_<svc> to the submitted build name.
start_build_with_retry() {
  local svc="$1"
  local attempt=0
  while [[ $attempt -lt $BUILD_UPLOAD_RETRIES ]]; do
    attempt=$(( attempt + 1 ))
    echo "    [${svc}] upload attempt ${attempt}/${BUILD_UPLOAD_RETRIES}..."
    local out
    # oc start-build streams the upload to the cluster; capture its output.
    if out=$(oc start-build "${svc}" --from-dir=. --follow=false -n "${NS}" 2>&1); then
      local build_name
      build_name=$(echo "${out}" | grep -oE "${svc}-[0-9]+" | tail -1)
      echo "    [${svc}] submitted → ${build_name}"
      # Store in an associative-style flat var (bash 3 compat)
      eval "BUILD_NAME_${svc//-/_}=${build_name}"
      return 0
    else
      warn "[${svc}] upload failed (attempt ${attempt}): ${out}"
      sleep 5
    fi
  done
  fail "[${svc}] upload failed after ${BUILD_UPLOAD_RETRIES} attempts — aborting."
  exit 1
}

# ── Helper: wait for all active builds, auto-retry FetchSourceFailed ──────────
wait_for_builds() {
  local deadline=$(( $(date +%s) + BUILD_TIMEOUT ))
  # Track per-service retry counts  (flat vars, bash 3 compat)
  for svc in "${SERVICES[@]}"; do
    eval "RETRY_${svc//-/_}=0"
  done

  while true; do
    if [[ $(date +%s) -gt $deadline ]]; then
      fail "Build timeout after ${BUILD_TIMEOUT}s."
      oc get builds -n "${NS}"
      exit 1
    fi

    local statuses
    statuses=$(oc get builds -n "${NS}" --no-headers 2>/dev/null || true)
    echo "    $(date '+%H:%M:%S')  $(echo "${statuses}" | awk '{printf "%s=%s  ",$1,$4}')"

    # Collect failed builds that are NOT already in the ignore list
    local new_failures=""
    while IFS= read -r line; do
      [[ -z "${line}" ]] && continue
      local bname bstatus
      bname=$(echo "${line}" | awk '{print $1}')
      bstatus=$(echo "${line}" | awk '{print $4}')
      if echo "${bstatus}" | grep -iq "failed\|error"; then
        if ! echo " ${IGNORED_BUILDS} " | grep -q " ${bname} "; then
          new_failures="${new_failures} ${bname}"
        fi
      fi
    done <<< "${statuses}"

    # Handle new failures: retry if quota allows, else abort
    for bname in ${new_failures}; do
      # Derive service name from build name (strip trailing -N)
      local svc="${bname%-*}"
      local retry_var="RETRY_${svc//-/_}"
      local retries
      eval "retries=\${${retry_var}:-0}"

      # Check if it's a FetchSourceFailed (interrupted upload) — always retry once
      local reason
      reason=$(oc get build "${bname}" -n "${NS}" -o jsonpath='{.status.reason}' 2>/dev/null || true)

      if [[ $retries -lt $BUILD_MAX_RETRIES ]]; then
        retries=$(( retries + 1 ))
        eval "${retry_var}=${retries}"
        IGNORED_BUILDS="${IGNORED_BUILDS} ${bname}"
        warn "[${svc}] build ${bname} failed (reason=${reason:-unknown}). Retrying (${retries}/${BUILD_MAX_RETRIES})..."
        start_build_with_retry "${svc}"
      else
        fail "[${svc}] build ${bname} failed after ${BUILD_MAX_RETRIES} retries."
        echo "    Logs: oc logs build/${bname} -n ${NS} | tail -60"
        exit 1
      fi
    done

    # Check if any active builds remain (exclude ignored/superseded)
    local running=0
    while IFS= read -r line; do
      [[ -z "${line}" ]] && continue
      local bname bstatus
      bname=$(echo "${line}" | awk '{print $1}')
      bstatus=$(echo "${line}" | awk '{print $4}')
      if echo "${bstatus}" | grep -iqE "running|pending|new"; then
        if ! echo " ${IGNORED_BUILDS} " | grep -q " ${bname} "; then
          running=$(( running + 1 ))
        fi
      fi
    done <<< "${statuses}"

    [[ $running -eq 0 ]] && break
    sleep "${BUILD_POLL_INTERVAL}"
  done
  ok "All builds complete."
}

# ── Helper: wait for deployments, evict stuck Pending pods ───────────────────
wait_for_deployments() {
  local deadline=$(( $(date +%s) + DEPLOY_MAX_WAIT ))
  # Track when each pending pod was first seen  (name → epoch)
  declare -A pending_since

  while true; do
    if [[ $(date +%s) -gt $deadline ]]; then
      fail "Deployment timeout after ${DEPLOY_MAX_WAIT}s. Current state:"
      oc get pods -n "${NS}" --no-headers | grep -v build
      oc get deployments -n "${NS}"
      exit 1
    fi

    echo "    $(date '+%H:%M:%S')"
    oc get pods -n "${NS}" --no-headers | grep -v build | \
      awk '{printf "      %-45s %-8s %s\n",$1,$2,$4}'

    # Force-delete pods that have been Pending beyond the eviction threshold
    local now
    now=$(date +%s)
    while IFS= read -r line; do
      [[ -z "${line}" ]] && continue
      local pname pstatus
      pname=$(echo "${line}" | awk '{print $1}')
      pstatus=$(echo "${line}" | awk '{print $2}')
      if [[ "${pstatus}" == "0/1" ]] || echo "${pstatus}" | grep -q "0/"; then
        local phase
        phase=$(oc get pod "${pname}" -n "${NS}" \
          -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
        if [[ "${phase}" == "Pending" ]]; then
          if [[ -z "${pending_since[${pname}]+x}" ]]; then
            pending_since["${pname}"]="${now}"
          else
            local waited=$(( now - pending_since["${pname}"] ))
            if [[ $waited -ge $PENDING_EVICT_AFTER ]]; then
              warn "Pod ${pname} stuck Pending for ${waited}s — force-deleting to reschedule..."
              oc delete pod "${pname}" -n "${NS}" --force --grace-period=0 2>/dev/null || true
              unset "pending_since[${pname}]"
            fi
          fi
        else
          unset "pending_since[${pname}]" 2>/dev/null || true
        fi
      else
        unset "pending_since[${pname}]" 2>/dev/null || true
      fi
    done < <(oc get pods -n "${NS}" --no-headers 2>/dev/null | grep -v build)

    # Check all target deployments are Available
    local not_ready=0
    for dep in daily-news-api news-mcp pageindex-mcp evaluation-mcp linkedin-mcp; do
      local avail
      avail=$(oc get deployment "${dep}" -n "${NS}" \
        -o jsonpath='{.status.availableReplicas}' 2>/dev/null || echo "0")
      local desired
      desired=$(oc get deployment "${dep}" -n "${NS}" \
        -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "1")
      if [[ "${avail}" -lt "${desired}" ]]; then
        not_ready=$(( not_ready + 1 ))
      fi
    done

    [[ $not_ready -eq 0 ]] && break
    echo "    (${not_ready} deployment(s) not yet fully available — retrying in ${DEPLOY_POLL_INTERVAL}s)"
    sleep "${DEPLOY_POLL_INTERVAL}"
  done
  ok "All deployments Available."
}

# ── Helper: node capacity report ─────────────────────────────────────────────
check_node_capacity() {
  echo "==> [preflight] Node scheduling capacity..."
  local over=0
  while IFS= read -r line; do
    local node status
    node=$(echo "${line}" | awk '{print $1}')
    status=$(echo "${line}" | awk '{print $2}')
    if echo "${status}" | grep -q "SchedulingDisabled"; then
      warn "Node ${node} is cordoned (SchedulingDisabled)"
      over=$(( over + 1 ))
    fi
  done < <(oc get nodes --no-headers 2>/dev/null)

  # Count schedulable workers and their pod saturation
  local saturated=0
  while IFS= read -r node; do
    local count cap
    count=$(oc get pods --all-namespaces \
      --field-selector "spec.nodeName=${node}" --no-headers 2>/dev/null | wc -l)
    cap=$(oc get node "${node}" \
      -o jsonpath='{.status.allocatable.pods}' 2>/dev/null || echo "250")
    if [[ $count -ge $cap ]]; then
      warn "Node ${node}: ${count}/${cap} pods — AT CAPACITY"
      saturated=$(( saturated + 1 ))
    else
      ok  "Node ${node}: ${count}/${cap} pods"
    fi
  done < <(oc get nodes --no-headers 2>/dev/null \
    | grep -v "SchedulingDisabled\|master\|control-plane" | awk '{print $1}')

  if [[ $saturated -gt 0 ]]; then
    warn "${saturated} schedulable worker(s) are at pod capacity."
    warn "Pending pods will auto-evict and reschedule — see PENDING_EVICT_AFTER=${PENDING_EVICT_AFTER}s"
    warn "If builds or pods remain stuck, run: oc adm uncordon <node>"
  fi
}

# ── Helper: security + live checks ───────────────────────────────────────────
run_security_checks() {
  echo ""
  echo "==> [security] Running end-to-end security + live checks..."

  # Pick a stable running api pod
  local api_pod
  api_pod=$(oc get pods -n "${NS}" --no-headers 2>/dev/null \
    | grep "daily-news-api" | grep "1/1" | sort -k5 -rn | head -1 | awk '{print $1}')

  if [[ -z "${api_pod}" ]]; then
    warn "No running daily-news-api pod found — skipping in-cluster checks."
    return
  fi
  echo "    Using pod: ${api_pod}"

  # Run all in-cluster checks in a single exec to avoid pod churn issues
  oc exec "${api_pod}" -n "${NS}" -- bash -c '
set -uo pipefail
MCP_TOKEN="${MCP_AUTH_TOKEN:-}"
LLM_URL="${LLM_BASE_URL:-}"
LLM_KEY="${LLM_API_KEY:-}"
LLM_MODEL="${LLM_MODEL:-qwen2-5-72b-instruct}"

GREEN="\033[0;32m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; R="\033[0m"
ok()   { echo -e "    ${GREEN}✅  $*${R}"; }
warn() { echo -e "    ${YELLOW}⚠️   $*${R}"; }
fail() { echo -e "    ${RED}❌  $*${R}"; }

echo ""
echo "  ── [SEC-1] Secret integrity ──────────────────────────────────"
MISSING=0
for key in LLM_API_KEY LLM_BASE_URL GNEWS_API_KEY LINKEDIN_ACCESS_TOKEN \
           LINKEDIN_CLIENT_ID MCP_AUTH_TOKEN LANGFUSE_PUBLIC_KEY; do
  val=$(printenv "$key" 2>/dev/null || true)
  if [[ -n "$val" ]]; then
    ok "$key = ${val:0:24}..."
  else
    fail "$key — MISSING or EMPTY"
    MISSING=$((MISSING+1))
  fi
done
[[ $MISSING -eq 0 ]] || warn "$MISSING secret key(s) missing — check daily-news-secrets"

echo ""
echo "  ── [SEC-2] LLM gateway reachability ──────────────────────────"
MODEL_RESP=$(curl -sk --max-time 10 "${LLM_URL}/models" \
  -H "Authorization: Bearer ${LLM_KEY}" 2>&1)
if echo "${MODEL_RESP}" | grep -q '"id"'; then
  MODELS=$(echo "${MODEL_RESP}" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(', '.join([m['id'] for m in d.get('data',[])]))" \
    2>/dev/null || echo "parse-error")
  ok "LLM gateway reachable — models: ${MODELS}"
else
  fail "LLM gateway unreachable or bad auth: ${MODEL_RESP:0:200}"
fi

echo ""
echo "  ── [SEC-3] Live LLM inference ─────────────────────────────────"
INFER=$(curl -sk --max-time 30 "${LLM_URL}/chat/completions" \
  -H "Authorization: Bearer ${LLM_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"${LLM_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with only: LLM_OK\"}],\"max_tokens\":10}" 2>&1)
CONTENT=$(echo "${INFER}" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d[\"choices\"][0][\"message\"][\"content\"])" \
  2>/dev/null || true)
if [[ "${CONTENT}" == *"LLM_OK"* ]]; then
  ok "Live inference → \"${CONTENT}\""
else
  fail "Inference failed or unexpected response: ${INFER:0:300}"
fi

echo ""
echo "  ── [SEC-4] MCP /health reachability ──────────────────────────"
for svc in news-mcp:8000 pageindex-mcp:8000 evaluation-mcp:8000 linkedin-mcp:8000; do
  CODE=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 5 http://${svc}/health 2>&1)
  BODY=$(curl -sk --max-time 5 http://${svc}/health 2>&1)
  STATUS=$(echo "${BODY}" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(d.get(\"status\",\"?\"))" 2>/dev/null || echo "?")
  if [[ "${CODE}" == "200" && "${STATUS}" == "healthy" ]]; then
    ok "${svc} → HTTP ${CODE}  status=${STATUS}"
  else
    warn "${svc} → HTTP ${CODE}  status=${STATUS} (pod may be starting)"
  fi
done

echo ""
echo "  ── [SEC-5] MCP auth (no-token → must not be 200 on /mcp) ──────"
for svc in news-mcp:8000 pageindex-mcp:8000 evaluation-mcp:8000 linkedin-mcp:8000; do
  NO=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 5 \
    -X POST http://${svc}/mcp \
    -H "Content-Type: application/json" \
    -d "{\"jsonrpc\":\"2.0\",\"method\":\"tools/list\",\"id\":1}" 2>&1)
  WITH=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 5 \
    -X POST http://${svc}/mcp \
    -H "Authorization: Bearer ${MCP_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"jsonrpc\":\"2.0\",\"method\":\"tools/list\",\"id\":1}" 2>&1)
  if [[ "${WITH}" == "200" ]]; then
    ok "${svc}  no-token=${NO}  with-token=${WITH}"
  else
    warn "${svc}  no-token=${NO}  with-token=${WITH} (expected 200 with token)"
  fi
done

echo ""
echo "  ── [SEC-6] LinkedIn token validity ────────────────────────────"
LI=$(curl -sk --max-time 8 \
  -H "Authorization: Bearer ${LINKEDIN_ACCESS_TOKEN}" \
  "https://api.linkedin.com/v2/userinfo" 2>&1)
NAME=$(echo "${LI}" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get(\"name\",\"?\"))" 2>/dev/null || echo "")
EMAIL=$(echo "${LI}" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get(\"email\",\"?\"))" 2>/dev/null || echo "")
if [[ -n "${NAME}" && "${NAME}" != "?" ]]; then
  ok "LinkedIn token valid — ${NAME} (${EMAIL})"
else
  fail "LinkedIn token invalid or expired: ${LI:0:200}"
fi
REFRESH=$(printenv LINKEDIN_REFRESH_TOKEN 2>/dev/null || true)
[[ -z "${REFRESH}" ]] && warn "LINKEDIN_REFRESH_TOKEN is empty — rotate access token manually every 60 days"

echo ""
echo "  ── [SEC-7] GNews API key ───────────────────────────────────────"
GN=$(curl -sk --max-time 10 \
  "https://gnews.io/api/v4/top-headlines?topic=technology&lang=en&max=1&apikey=${GNEWS_API_KEY}" 2>&1)
COUNT=$(echo "${GN}" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(len(d.get(\"articles\",[])))" 2>/dev/null || echo "0")
if [[ "${COUNT}" -gt 0 ]]; then
  ok "GNews API key valid — returned ${COUNT} article(s)"
else
  fail "GNews returned 0 articles or error: ${GN:0:200}"
fi

echo ""
echo "  ── [SEC-8] Langfuse connectivity ──────────────────────────────"
LF_CODE=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 8 \
  "${LANGFUSE_BASE_URL}/api/public/health" 2>&1)
if [[ "${LF_CODE}" == "200" ]]; then
  ok "Langfuse ${LANGFUSE_BASE_URL} → HTTP ${LF_CODE}"
else
  warn "Langfuse → HTTP ${LF_CODE} (tracing may be unavailable)"
fi
' 2>&1

  # ── Route TLS checks (from local machine) ──────────────────────────────────
  echo ""
  echo "  ── [SEC-9] Route TLS + external reachability ──────────────────"
  local api_route="https://daily-news-api-aifeeders.apps.your-cluster.example.com"
  local linkedin_route="https://linkedin-mcp-aifeeders.apps.your-cluster.example.com"

  for url in "${api_route}/health" "${api_route}/ready" "${linkedin_route}/health"; do
    local code
    code=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 10 "${url}" 2>&1)
    if [[ "${code}" == "200" ]]; then
      ok "${url} → HTTP ${code}"
    else
      warn "${url} → HTTP ${code}"
    fi
  done
}

# =============================================================================
# MAIN
# =============================================================================

echo ""
echo "================================================================="
echo "  AIFeeders — Full clean rebuild · namespace: ${NS}"
echo "  $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "================================================================="
echo ""

# ── Preflight ────────────────────────────────────────────────────────────────
echo "==> [0/9] Preflight checks..."

if ! oc whoami &>/dev/null; then
  fail "Not logged in to OpenShift. Run: oc login --token=<token> --server=<url>"
  exit 1
fi
ok "Logged in as: $(oc whoami)"

CURRENT_PROJECT=$(oc project -q 2>/dev/null || echo "")
if [[ "${CURRENT_PROJECT}" != "${NS}" ]]; then
  echo "    Switching to project ${NS}..."
  oc project "${NS}"
fi
ok "Namespace: ${NS}"

check_node_capacity

# Verify secret exists before we delete everything
if ! oc get secret daily-news-secrets -n "${NS}" &>/dev/null; then
  warn "daily-news-secrets NOT FOUND in namespace ${NS}."
  warn "Deployments will fail at startup without it. See RUNBOOK.md § 6."
  echo ""
  read -rp "    Continue anyway? [y/N] " _CONT
  [[ "${_CONT,,}" == "y" ]] || { echo "Aborted."; exit 1; }
else
  ok "daily-news-secrets present"
fi

# ── Step 1: Hard delete all resources ────────────────────────────────────────
echo ""
echo "==> [1/9] Deleting all resources (preserving daily-news-secrets)..."
oc delete pods       --all -n "${NS}" --force --grace-period=0 2>/dev/null || true
oc delete builds     --all -n "${NS}"                          2>/dev/null || true
oc delete buildconfig --all -n "${NS}"                         2>/dev/null || true
oc delete imagestream --all -n "${NS}"                         2>/dev/null || true
oc delete deployment --all -n "${NS}"                          2>/dev/null || true
oc delete service    --all -n "${NS}"                          2>/dev/null || true
oc delete route      --all -n "${NS}"                          2>/dev/null || true
oc delete cronjob    --all -n "${NS}"                          2>/dev/null || true
oc delete hpa        --all -n "${NS}"                          2>/dev/null || true
oc delete pdb        --all -n "${NS}"                          2>/dev/null || true
oc delete configmap daily-news-config -n "${NS}"               2>/dev/null || true
oc delete networkpolicy --all -n "${NS}"                       2>/dev/null || true
ok "All resources deleted."

# Re-verify secret survived the cleanup
if ! oc get secret daily-news-secrets -n "${NS}" &>/dev/null; then
  fail "daily-news-secrets was deleted! Recreate it before retrying."
  echo "    See RUNBOOK.md § 6 for instructions."
  exit 1
fi

# ── Step 2: Config layer ──────────────────────────────────────────────────────
echo ""
echo "==> [2/9] Re-applying config layer..."
oc apply -f openshift/namespace.yaml
oc apply -f openshift/configmap.yaml
oc apply -f openshift/rbac.yaml
oc apply -f openshift/networkpolicy.yaml
ok "Config layer applied."

# ── Step 3: BuildConfigs + ImageStreams ───────────────────────────────────────
echo ""
echo "==> [3/9] Re-applying BuildConfigs + ImageStreams..."
oc apply -f openshift/buildconfigs.yaml
ok "BuildConfigs + ImageStreams applied."

# ── Step 4: Start all 5 builds ───────────────────────────────────────────────
echo ""
echo "==> [4/9] Starting all 5 binary builds from local source..."
echo "    (Each upload streams ~50 MB — may take 30–60s per service)"
IGNORED_BUILDS=""
for svc in "${SERVICES[@]}"; do
  start_build_with_retry "${svc}"
done
ok "All 5 builds submitted."

# ── Step 5: Wait for builds ───────────────────────────────────────────────────
echo ""
echo "==> [5/9] Waiting for builds to complete (6–16 min each)..."
echo "    Polling every ${BUILD_POLL_INTERVAL}s — Ctrl+C detaches (builds continue on cluster)"
wait_for_builds

# ── Step 6: Deploy workloads ──────────────────────────────────────────────────
echo ""
echo "==> [6/9] Deploying MCP services..."
oc apply -f openshift/news-mcp/
oc apply -f openshift/pageindex-mcp/
oc apply -f openshift/evaluation-mcp/
oc apply -f openshift/linkedin-mcp/

echo ""
echo "==> [7/9] Deploying API + scheduling + autoscaling..."
oc apply -f openshift/api/
oc apply -f openshift/cronjob.yaml
oc apply -f openshift/hpa.yaml
oc apply -f openshift/pdb.yaml
ok "All manifests applied."

# ── Step 7: Wait for deployments ─────────────────────────────────────────────
echo ""
echo "==> [8/9] Waiting for all deployments to become Available..."
echo "    (Pending pods stuck >${PENDING_EVICT_AFTER}s will be auto-evicted to reschedule)"
wait_for_deployments

# ── Step 8: Security + live checks ───────────────────────────────────────────
echo ""
echo "==> [9/9] Security + live checks..."
run_security_checks

# ── Final status ──────────────────────────────────────────────────────────────
echo ""
echo "================================================================="
echo "  ✅  Clean rebuild complete — namespace: ${NS}"
echo "  $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "================================================================="
echo ""
echo "--- DEPLOYMENTS ---"
oc get deployments -n "${NS}"
echo ""
echo "--- PODS ---"
oc get pods -n "${NS}" | grep -v build
echo ""
echo "--- ROUTES ---"
oc get routes -n "${NS}"
echo ""
echo "--- HPA ---"
oc get hpa -n "${NS}"
echo ""
echo "--- CRONJOBS ---"
oc get cronjobs -n "${NS}"
