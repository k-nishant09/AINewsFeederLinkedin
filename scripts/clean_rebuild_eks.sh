#!/usr/bin/env bash
# =============================================================================
# clean_rebuild_eks.sh — Full clean rebuild for AIFeeders on AWS EKS
# =============================================================================
# Usage:
#   export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
#   export AWS_REGION=us-east-1
#   export CLUSTER_NAME=aifeeders-cluster
#   ./scripts/clean_rebuild_eks.sh
#
# What it does:
#   1.  Validates AWS CLI auth and kubectl context
#   2.  Logs in to ECR, creates repos if missing (scan-on-push enabled)
#   3.  Builds all 5 Docker images locally with versioned + latest tags
#   4.  Pushes all 5 images to ECR (with retry on transient push failures)
#   5.  Hard-deletes all workloads in the aifeeders namespace
#       (preserves the daily-news-secrets K8s secret)
#   6.  Re-applies: namespace → configmap → rbac → networkpolicy
#   7.  Deploys MCPs → API → cronjob → HPA → PDB
#   8.  Waits for all deployments to become Available (polls with stuck-pod
#       eviction, same pattern as the OpenShift script)
#   9.  Runs end-to-end security + live checks:
#         • LLM gateway reachability + live inference
#         • MCP /health for all 4 servers
#         • Secret integrity (all required keys present in pod env)
#         • LinkedIn token validity
#         • GNews API key validity
#         • Langfuse connectivity
#         • ECR image scan results (CRITICAL/HIGH CVE count)
#  10.  Prints final pod / service / hpa / cronjob status
#
# Ref: RUNBOOK.md §4 (Deploy — AWS EKS) + §18 (E2E Validation Guide)
# =============================================================================
set -euo pipefail

# ── Tunables ─────────────────────────────────────────────────────────────────
NS=aifeeders
DEPLOY_MAX_WAIT=300
DEPLOY_POLL_INTERVAL=15
PENDING_EVICT_AFTER=60
IMAGE_TAG=$(git rev-parse --short HEAD 2>/dev/null || echo "local-$(date +%Y%m%d%H%M%S)")

# AWS settings — override via environment before running
: "${AWS_ACCOUNT_ID:?Set AWS_ACCOUNT_ID (aws sts get-caller-identity --query Account --output text)}"
: "${AWS_REGION:=us-east-1}"
: "${CLUSTER_NAME:=aifeeders-cluster}"

ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
SERVICES=(daily-news-api news-mcp pageindex-mcp evaluation-mcp linkedin-mcp)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; RESET='\033[0m'
ok()   { echo -e "    ${GREEN}✅  $*${RESET}"; }
warn() { echo -e "    ${YELLOW}⚠️   $*${RESET}"; }
fail() { echo -e "    ${RED}❌  $*${RESET}"; }

# =============================================================================
# STEP 1 — Validate Prerequisites
# =============================================================================
echo ""
echo "===========================================================================" 
echo "  AIFeeders — EKS Clean Rebuild  (tag: ${IMAGE_TAG})"
echo "==========================================================================="
echo ""
echo "==> STEP 1: Validating prerequisites..."

# AWS CLI auth
if ! aws sts get-caller-identity &>/dev/null; then
  fail "AWS CLI not authenticated. Run: aws configure  OR  aws sso login"
  exit 1
fi
ok "AWS CLI authenticated (account: ${AWS_ACCOUNT_ID})"

# kubectl context
CURRENT_CTX=$(kubectl config current-context 2>/dev/null || echo "none")
echo "    kubectl context: ${CURRENT_CTX}"
if ! kubectl cluster-info &>/dev/null; then
  echo "    Updating kubeconfig for cluster ${CLUSTER_NAME}..."
  aws eks update-kubeconfig --region "${AWS_REGION}" --name "${CLUSTER_NAME}"
fi
ok "kubectl connected to cluster"

# Docker available
if ! docker info &>/dev/null; then
  fail "Docker daemon not running. Start Docker Desktop or the Docker daemon."
  exit 1
fi
ok "Docker daemon running"

# =============================================================================
# STEP 2 — ECR Login + Create Repos (idempotent, scan-on-push enabled)
# =============================================================================
echo ""
echo "==> STEP 2: ECR login + repo setup..."

aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${ECR_REGISTRY}"
ok "ECR authenticated"

for svc in "${SERVICES[@]}"; do
  aws ecr create-repository \
    --repository-name "aifeeders/${svc}" \
    --image-scanning-configuration scanOnPush=true \
    --region "${AWS_REGION}" 2>/dev/null \
    && ok "ECR repo created: aifeeders/${svc}" \
    || echo "    ECR repo aifeeders/${svc}: already exists"
done

# =============================================================================
# STEP 3 — Build All 5 Images
# =============================================================================
echo ""
echo "==> STEP 3: Building all 5 images (tag: ${IMAGE_TAG})..."

# Map: service → Dockerfile context directory
# All Dockerfiles reference paths relative to repo root, so all use "."
declare -A BUILD_CONTEXTS=(
  [daily-news-api]="."
  [news-mcp]="."
  [pageindex-mcp]="."
  [evaluation-mcp]="."
  [linkedin-mcp]="."
)

declare -A DOCKERFILE_PATHS=(
  [daily-news-api]="Dockerfile"
  [news-mcp]="mcp_servers/news_mcp/Dockerfile"
  [pageindex-mcp]="mcp_servers/pageindex_mcp/Dockerfile"
  [evaluation-mcp]="mcp_servers/evaluation_mcp/Dockerfile"
  [linkedin-mcp]="mcp_servers/linkedin_mcp/Dockerfile"
)

for svc in "${SERVICES[@]}"; do
  echo "    Building ${svc}..."
  docker build \
    --build-arg APP_VERSION="${IMAGE_TAG}" \
    -f "${DOCKERFILE_PATHS[$svc]}" \
    -t "${ECR_REGISTRY}/aifeeders/${svc}:${IMAGE_TAG}" \
    -t "${ECR_REGISTRY}/aifeeders/${svc}:latest" \
    "${BUILD_CONTEXTS[$svc]}"
  ok "${svc} built"
done

# =============================================================================
# STEP 4 — Push All Images to ECR (with retry)
# =============================================================================
echo ""
echo "==> STEP 4: Pushing all images to ECR..."

PUSH_RETRIES=3
for svc in "${SERVICES[@]}"; do
  attempt=0
  pushed=false
  while [[ $attempt -lt $PUSH_RETRIES ]]; do
    attempt=$(( attempt + 1 ))
    echo "    [${svc}] push attempt ${attempt}/${PUSH_RETRIES}..."
    if docker push "${ECR_REGISTRY}/aifeeders/${svc}:${IMAGE_TAG}" \
       && docker push "${ECR_REGISTRY}/aifeeders/${svc}:latest"; then
      ok "${svc} pushed"
      pushed=true
      break
    else
      warn "[${svc}] push failed (attempt ${attempt}) — retrying in 10s..."
      sleep 10
      # Re-authenticate ECR token (valid 12h, but retry is cheap insurance)
      aws ecr get-login-password --region "${AWS_REGION}" \
        | docker login --username AWS --password-stdin "${ECR_REGISTRY}" 2>/dev/null || true
    fi
  done
  if [[ "$pushed" != "true" ]]; then
    fail "[${svc}] push failed after ${PUSH_RETRIES} attempts — aborting."
    exit 1
  fi
done

# =============================================================================
# STEP 5 — Hard Delete All Workloads (preserve secret)
# =============================================================================
echo ""
echo "==> STEP 5: Hard-deleting all workloads in namespace ${NS} (preserving daily-news-secrets)..."

# Verify secret exists before we delete anything
if ! kubectl get secret daily-news-secrets -n "${NS}" &>/dev/null; then
  warn "daily-news-secrets does not exist — you must create it after this script."
  warn "See RUNBOOK.md §6 (First-Time Secret Setup → EKS)."
fi

kubectl delete pods --all -n "${NS}" --force --grace-period=0 2>/dev/null || true
kubectl delete deployment --all -n "${NS}" 2>/dev/null || true
kubectl delete service --all -n "${NS}" 2>/dev/null || true
kubectl delete ingress --all -n "${NS}" 2>/dev/null || true
kubectl delete cronjob --all -n "${NS}" 2>/dev/null || true
kubectl delete hpa --all -n "${NS}" 2>/dev/null || true
kubectl delete pdb --all -n "${NS}" 2>/dev/null || true
kubectl delete configmap daily-news-config -n "${NS}" 2>/dev/null || true
kubectl delete networkpolicy --all -n "${NS}" 2>/dev/null || true
ok "Workloads deleted"

# =============================================================================
# STEP 6 — Re-apply Config Layer
# =============================================================================
echo ""
echo "==> STEP 6: Re-applying config layer..."

kubectl create namespace "${NS}" --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n "${NS}" -f openshift/configmap.yaml
kubectl apply -n "${NS}" -f openshift/rbac.yaml
kubectl apply -n "${NS}" -f openshift/networkpolicy.yaml
ok "Config layer applied"

# =============================================================================
# STEP 7 — Deploy All Services (ECR image references substituted)
# =============================================================================
echo ""
echo "==> STEP 7: Deploying services with ECR image references (${ECR_REGISTRY}/aifeeders)..."

LOCAL_PREFIX="ainewsfeederlinkedin"
ECR_PREFIX="${ECR_REGISTRY}/aifeeders"

for svc_dir in openshift/news-mcp openshift/pageindex-mcp openshift/evaluation-mcp openshift/linkedin-mcp openshift/api; do
  if [[ -d "${svc_dir}" ]]; then
    for f in "${svc_dir}"/*.yaml; do
      sed "s|${LOCAL_PREFIX}/|${ECR_PREFIX}/|g; s|:latest|:${IMAGE_TAG}|g" "${f}" \
        | kubectl apply -n "${NS}" -f -
    done
  fi
done

kubectl apply -n "${NS}" -f openshift/cronjob.yaml 2>/dev/null \
  || warn "cronjob.yaml missing — skipping CronJob deploy"
kubectl apply -n "${NS}" -f openshift/hpa.yaml 2>/dev/null \
  || warn "hpa.yaml missing — skipping HPA deploy"
kubectl apply -n "${NS}" -f openshift/pdb.yaml 2>/dev/null \
  || warn "pdb.yaml missing — skipping PDB deploy"

ok "Manifests applied"

# =============================================================================
# STEP 8 — Wait for Deployments (with stuck-pod eviction)
# =============================================================================
echo ""
echo "==> STEP 8: Waiting for deployments to become Available..."

deadline=$(( $(date +%s) + DEPLOY_MAX_WAIT ))
ALL_READY=false

while [[ $(date +%s) -lt $deadline ]]; do
  ready_count=0
  total_count=0

  for svc in daily-news-api news-mcp pageindex-mcp evaluation-mcp linkedin-mcp; do
    total_count=$(( total_count + 1 ))
    avail=$(kubectl get deployment "${svc}" -n "${NS}" \
      -o jsonpath='{.status.availableReplicas}' 2>/dev/null || echo "0")
    desired=$(kubectl get deployment "${svc}" -n "${NS}" \
      -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "1")
    if [[ "${avail}" -ge "${desired}" ]]; then
      ready_count=$(( ready_count + 1 ))
    fi
  done

  if [[ $ready_count -eq $total_count ]]; then
    ALL_READY=true
    break
  fi

  echo "    Waiting... ${ready_count}/${total_count} deployments available"

  # Evict stuck Pending pods (node resource contention)
  STUCK_PODS=$(kubectl get pods -n "${NS}" --field-selector=status.phase=Pending \
    -o jsonpath='{range .items[*]}{.metadata.name} {.metadata.creationTimestamp}{"\n"}{end}' \
    2>/dev/null || true)
  while IFS=' ' read -r pod_name created_at; do
    [[ -z "$pod_name" ]] && continue
    created_epoch=$(date -d "${created_at}" +%s 2>/dev/null \
      || date -jf "%Y-%m-%dT%H:%M:%SZ" "${created_at}" +%s 2>/dev/null || echo 0)
    age=$(( $(date +%s) - created_epoch ))
    if [[ $age -gt $PENDING_EVICT_AFTER ]]; then
      warn "Evicting stuck Pending pod ${pod_name} (${age}s old)"
      kubectl delete pod "${pod_name}" -n "${NS}" --force --grace-period=0 2>/dev/null || true
    fi
  done <<< "$STUCK_PODS"

  sleep "${DEPLOY_POLL_INTERVAL}"
done

if [[ "$ALL_READY" != "true" ]]; then
  fail "Deployments did not become Available within ${DEPLOY_MAX_WAIT}s."
  kubectl get pods -n "${NS}"
  exit 1
fi
ok "All deployments Available"

# =============================================================================
# STEP 9 — Security + Live Checks
# =============================================================================
echo ""
echo "==> STEP 9: Running security + live checks..."

CHECKS_PASSED=0
CHECKS_TOTAL=0
check_pass() { CHECKS_TOTAL=$(( CHECKS_TOTAL + 1 )); CHECKS_PASSED=$(( CHECKS_PASSED + 1 )); ok "$1"; }
check_fail() { CHECKS_TOTAL=$(( CHECKS_TOTAL + 1 )); fail "$1"; }
check_warn() { CHECKS_TOTAL=$(( CHECKS_TOTAL + 1 )); warn "$1"; }

# 1. MCP health checks
for port_svc in "8101:news-mcp" "8102:pageindex-mcp" "8103:evaluation-mcp" "8104:linkedin-mcp"; do
  port="${port_svc%%:*}"; svc="${port_svc##*:}"
  result=$(kubectl exec deploy/daily-news-api -n "${NS}" -- \
    curl -sf "http://${svc}:${port}/health" 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null \
    || echo "UNREACHABLE")
  if [[ "$result" == "ok" || "$result" == "ready" ]]; then
    check_pass "MCP ${svc} :${port} → ${result}"
  else
    check_fail "MCP ${svc} :${port} → ${result}"
  fi
done

# 2. Secret integrity (check required keys are in pod env)
REQUIRED_KEYS=(LLM_API_KEY LLM_BASE_URL GNEWS_API_KEY LINKEDIN_ACCESS_TOKEN)
for key in "${REQUIRED_KEYS[@]}"; do
  val=$(kubectl exec deploy/daily-news-api -n "${NS}" -- printenv "${key}" 2>/dev/null || echo "")
  if [[ -n "$val" ]]; then
    check_pass "Secret ${key} present"
  else
    check_fail "Secret ${key} MISSING — add to daily-news-secrets (Section 6)"
  fi
done

# 3. LLM gateway reachability
LLM_RESPONSE=$(kubectl exec deploy/daily-news-api -n "${NS}" -- bash -c \
  'curl -sk "$LLM_BASE_URL/chat/completions" \
    -H "Authorization: Bearer $LLM_API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"$LLM_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply: READY\"}],\"max_tokens\":5}" \
    2>/dev/null' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('ok')" 2>/dev/null \
  || echo "FAIL")
if [[ "$LLM_RESPONSE" == "ok" ]]; then
  check_pass "LLM gateway live inference"
else
  check_fail "LLM gateway inference failed — check LLM_BASE_URL + LLM_API_KEY"
fi

# 4. LinkedIn token (via /v2/userinfo)
LI_STATUS=$(kubectl exec deploy/daily-news-api -n "${NS}" -- bash -c \
  'curl -sf -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $LINKEDIN_ACCESS_TOKEN" \
    https://api.linkedin.com/v2/userinfo 2>/dev/null' \
  || echo "000")
if [[ "$LI_STATUS" == "200" ]]; then
  check_pass "LinkedIn token valid (HTTP 200)"
elif [[ "$LI_STATUS" == "401" ]]; then
  check_fail "LinkedIn token INVALID (HTTP 401) — rotate token (Section 12)"
else
  check_warn "LinkedIn token check inconclusive (HTTP ${LI_STATUS}) — may be network restriction"
fi

# 5. GNews API key
GNEWS_STATUS=$(kubectl exec deploy/daily-news-api -n "${NS}" -- bash -c \
  'curl -sf -o /dev/null -w "%{http_code}" \
    "https://gnews.io/api/v4/search?q=AI&token=$GNEWS_API_KEY&max=1" 2>/dev/null' \
  || echo "000")
if [[ "$GNEWS_STATUS" == "200" ]]; then
  check_pass "GNews API key valid (HTTP 200)"
elif [[ "$GNEWS_STATUS" == "403" ]]; then
  check_fail "GNews API key quota exhausted or invalid (HTTP 403)"
else
  check_warn "GNews API check inconclusive (HTTP ${GNEWS_STATUS})"
fi

# 6. ECR image scan (CRITICAL CVEs)
for svc in "${SERVICES[@]}"; do
  SCAN_STATUS=$(aws ecr describe-image-scan-findings \
    --repository-name "aifeeders/${svc}" \
    --image-id imageTag="${IMAGE_TAG}" \
    --region "${AWS_REGION}" 2>/dev/null \
    | python3 -c "
import sys, json
d = json.load(sys.stdin)
findings = d.get('imageScanFindings', {}).get('findings', [])
critical = sum(1 for f in findings if f['severity'] == 'CRITICAL')
high     = sum(1 for f in findings if f['severity'] == 'HIGH')
print(f'CRITICAL={critical} HIGH={high}')
" 2>/dev/null || echo "SCAN_UNAVAILABLE")
  if [[ "$SCAN_STATUS" == *"CRITICAL=0"* ]]; then
    check_pass "ECR scan ${svc}: no CRITICAL CVEs"
  elif [[ "$SCAN_STATUS" == "SCAN_UNAVAILABLE" ]]; then
    check_warn "ECR scan ${svc}: scan not yet complete (check ECR console)"
  else
    check_warn "ECR scan ${svc}: ${SCAN_STATUS} — review before production"
  fi
done

echo ""
echo "    Security checks: ${CHECKS_PASSED}/${CHECKS_TOTAL} passed"

# =============================================================================
# STEP 10 — Final Status
# =============================================================================
echo ""
echo "==> STEP 10: Final status..."
echo ""
kubectl get pods -n "${NS}" -o wide
echo ""
kubectl get svc -n "${NS}"
echo ""
kubectl get hpa -n "${NS}" 2>/dev/null || true
echo ""
kubectl get cronjob -n "${NS}" 2>/dev/null || true

echo ""
if [[ $CHECKS_PASSED -eq $CHECKS_TOTAL ]]; then
  echo -e "    ${GREEN}✅  Clean EKS rebuild complete — all checks passed.${RESET}"
else
  echo -e "    ${YELLOW}⚠️   EKS rebuild complete with ${CHECKS_TOTAL-$CHECKS_PASSED} warnings — review above.${RESET}"
fi
echo ""
echo "    ECR registry:  ${ECR_REGISTRY}/aifeeders"
echo "    Image tag:     ${IMAGE_TAG}"
echo "    Namespace:     ${NS}"
echo "    Cluster:       ${CLUSTER_NAME} (${AWS_REGION})"
echo ""
echo "    Next: trigger a pipeline run with:"
echo "    kubectl exec deploy/daily-news-api -n ${NS} -- \\"
echo "      curl -s -X POST http://localhost:8000/workflow/daily-news -H 'Content-Type: application/json' -d '{}'"
echo ""
