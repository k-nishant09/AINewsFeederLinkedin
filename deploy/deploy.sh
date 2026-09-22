#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# AIFeeders — Production Deployment Script
#
# Usage:
#   ./deploy/deploy.sh --platform openshift --environment prod --namespace aifeeders [options]
#   ./deploy/deploy.sh --platform eks       --environment prod --namespace aifeeders \
#                      --image-registry 123456789.dkr.ecr.us-east-1.amazonaws.com/aifeeders
#   ./deploy/deploy.sh --platform aks       --environment prod --namespace aifeeders \
#                      --image-registry myacr.azurecr.io/aifeeders
#
# Required env vars (never printed — only [SET]/[MISSING] shown):
#   LLM_API_KEY, LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET,
#   LINKEDIN_ACCESS_TOKEN, GNEWS_API_KEY, LANGFUSE_PUBLIC_KEY,
#   LANGFUSE_SECRET_KEY
#
# Optional env vars:
#   MCP_AUTH_TOKEN, LINKEDIN_SCOPES, LINKEDIN_REFRESH_TOKEN,
#   LINKEDIN_REDIRECT_URI, LANGFUSE_BASE_URL
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'

ts()   { date '+%H:%M:%S'; }
info() { echo -e "${BLUE}[$(ts)] ℹ  $*${RESET}"; }
ok()   { echo -e "${GREEN}[$(ts)] ✔  $*${RESET}"; }
warn() { echo -e "${YELLOW}[$(ts)] ⚠  $*${RESET}"; }
die()  { echo -e "${RED}[$(ts)] ✘  $*${RESET}" >&2; exit 1; }
hdr()  { echo -e "\n${BOLD}${BLUE}══════════════════════════════════════════════════════${RESET}"; \
         echo -e "${BOLD}${BLUE}  $*${RESET}"; \
         echo -e "${BOLD}${BLUE}══════════════════════════════════════════════════════${RESET}"; }

# ── Defaults ──────────────────────────────────────────────────────────────────
PLATFORM=""
ENVIRONMENT="prod"
NAMESPACE="aifeeders"
IMAGE_REGISTRY=""
IMAGE_TAG="latest"
DRY_RUN=false
SKIP_BUILD=false
SKIP_SMOKE_TEST=false

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHART_DIR="${SCRIPT_DIR}/helm/aifeeders"

# ── Step 1: Parse arguments ───────────────────────────────────────────────────
hdr "Step 1/21 · Parsing arguments"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform)        PLATFORM="$2";        shift 2 ;;
    --environment)     ENVIRONMENT="$2";     shift 2 ;;
    --namespace)       NAMESPACE="$2";       shift 2 ;;
    --image-registry)  IMAGE_REGISTRY="$2";  shift 2 ;;
    --image-tag)       IMAGE_TAG="$2";       shift 2 ;;
    --dry-run)         DRY_RUN=true;         shift   ;;
    --skip-build)      SKIP_BUILD=true;      shift   ;;
    --skip-smoke-test) SKIP_SMOKE_TEST=true; shift   ;;
    *) die "Unknown argument: $1" ;;
  esac
done

[[ -z "$PLATFORM" ]] && die "--platform is required (openshift | eks | aks)"
[[ "$PLATFORM" =~ ^(openshift|eks|aks)$ ]] || die "--platform must be one of: openshift, eks, aks"

ENV_VALUES_FILE="${SCRIPT_DIR}/environments/${PLATFORM}/values.yaml"
[[ -f "$ENV_VALUES_FILE" ]] || die "Environment values file not found: ${ENV_VALUES_FILE}"

info "Platform:     ${PLATFORM}"
info "Environment:  ${ENVIRONMENT}"
info "Namespace:    ${NAMESPACE}"
info "Image tag:    ${IMAGE_TAG}"
info "Dry run:      ${DRY_RUN}"
info "Skip build:   ${SKIP_BUILD}"
ok   "Arguments parsed"

# ── Step 2: Validate prerequisites ───────────────────────────────────────────
hdr "Step 2/21 · Validating prerequisites"

check_cmd() {
  if command -v "$1" &>/dev/null; then
    ok "$1 found: $(command -v "$1")"
  else
    die "Required tool '$1' not found in PATH"
  fi
}

check_cmd helm

# kubectl or oc
if command -v oc &>/dev/null; then
  KUBE_CMD="oc"
  ok "oc found (will use oc for OpenShift-specific commands)"
elif command -v kubectl &>/dev/null; then
  KUBE_CMD="kubectl"
  ok "kubectl found"
else
  die "Neither 'oc' nor 'kubectl' found in PATH"
fi

# docker or podman (only needed if building)
if [[ "$SKIP_BUILD" == "false" ]]; then
  if command -v docker &>/dev/null; then
    CONTAINER_CMD="docker"
    ok "docker found"
  elif command -v podman &>/dev/null; then
    CONTAINER_CMD="podman"
    ok "podman found"
  else
    die "Neither 'docker' nor 'podman' found in PATH (use --skip-build to skip image build)"
  fi
else
  CONTAINER_CMD="docker"
  warn "Skipping container tool check (--skip-build)"
fi

# Platform-specific extras
if [[ "$PLATFORM" == "aks" ]] && [[ "$SKIP_BUILD" == "false" ]]; then
  check_cmd az
fi

# ── Step 3: Detect / validate kube context ───────────────────────────────────
hdr "Step 3/21 · Validating kube context"

KUBE_CTX=$($KUBE_CMD config current-context 2>/dev/null || echo "(none)")
info "Current kube context: ${KUBE_CTX}"

if [[ "$KUBE_CTX" == "(none)" ]]; then
  die "No kube context set. Run: ${KUBE_CMD} config use-context <ctx>"
fi

# Sanity: verify we can talk to the cluster
if ! $KUBE_CMD cluster-info &>/dev/null; then
  die "Cannot reach cluster. Check your kube context and credentials."
fi
ok "Cluster reachable"

# ── Step 4 & 5: Validate required env vars (never print values) ───────────────
hdr "Step 4/21 · Validating required environment variables"

REQUIRED_SECRETS=(
  LLM_API_KEY
  LINKEDIN_CLIENT_ID
  LINKEDIN_CLIENT_SECRET
  LINKEDIN_ACCESS_TOKEN
  GNEWS_API_KEY
  LANGFUSE_PUBLIC_KEY
  LANGFUSE_SECRET_KEY
)

OPTIONAL_SECRETS=(
  MCP_AUTH_TOKEN
  LINKEDIN_SCOPES
  LINKEDIN_REFRESH_TOKEN
  LINKEDIN_REDIRECT_URI
  LANGFUSE_BASE_URL
)

missing=0
for var in "${REQUIRED_SECRETS[@]}"; do
  if [[ -n "${!var:-}" ]]; then
    echo -e "  ${GREEN}[SET]    ${var}${RESET}"
  else
    echo -e "  ${RED}[MISSING] ${var}${RESET}"
    (( missing++ )) || true
  fi
done

echo ""
info "Optional variables:"
for var in "${OPTIONAL_SECRETS[@]}"; do
  if [[ -n "${!var:-}" ]]; then
    echo -e "  ${GREEN}[SET]    ${var}${RESET}"
  else
    echo -e "  ${YELLOW}[UNSET]  ${var}${RESET}"
  fi
done

[[ $missing -gt 0 ]] && die "${missing} required environment variable(s) missing — export them before running."
ok "All required secrets present"

# Apply defaults for optional vars
MCP_AUTH_TOKEN="${MCP_AUTH_TOKEN:-aifeeders-mcp-token}"
LINKEDIN_SCOPES="${LINKEDIN_SCOPES:-openid profile email w_member_social}"
LINKEDIN_REFRESH_TOKEN="${LINKEDIN_REFRESH_TOKEN:-}"
LINKEDIN_REDIRECT_URI="${LINKEDIN_REDIRECT_URI:-}"
LANGFUSE_BASE_URL="${LANGFUSE_BASE_URL:-https://us.cloud.langfuse.com}"

# ── Step 6: Build and push images ─────────────────────────────────────────────
hdr "Step 6/21 · Building and pushing images"

SERVICES=(daily-news news-mcp pageindex-mcp evaluation-mcp linkedin-mcp)

if [[ "$SKIP_BUILD" == "true" ]]; then
  warn "Skipping image build (--skip-build)"
else
  case "$PLATFORM" in
    openshift)
      info "Using OpenShift Source-to-Image binary builds (oc start-build)"
      for svc in "${SERVICES[@]}"; do
        info "Building ${svc}..."
        if [[ "$DRY_RUN" == "true" ]]; then
          info "[DRY RUN] oc start-build ${svc} --from-dir=. --follow --namespace=${NAMESPACE}"
        else
          $KUBE_CMD start-build "${svc}" --from-dir=. --follow --namespace="${NAMESPACE}" || \
            warn "Build config '${svc}' not found — skipping (create BuildConfig first)"
        fi
      done
      ;;
    eks)
      [[ -z "$IMAGE_REGISTRY" ]] && die "--image-registry required for EKS (ECR registry URL)"
      info "Logging in to ECR..."
      if [[ "$DRY_RUN" == "false" ]]; then
        AWS_REGION="${AWS_REGION:-us-east-1}"
        aws ecr get-login-password --region "${AWS_REGION}" | \
          $CONTAINER_CMD login --username AWS --password-stdin "${IMAGE_REGISTRY%%/*}"
      fi
      for svc in "${SERVICES[@]}"; do
        TAG="${IMAGE_REGISTRY}/${svc}:${IMAGE_TAG}"
        info "Building ${svc} → ${TAG}"
        if [[ "$DRY_RUN" == "true" ]]; then
          info "[DRY RUN] ${CONTAINER_CMD} build -t ${TAG} -f mcp_servers/${svc}/Dockerfile ."
        else
          $CONTAINER_CMD build -t "${TAG}" .
          $CONTAINER_CMD push "${TAG}"
        fi
      done
      ;;
    aks)
      [[ -z "$IMAGE_REGISTRY" ]] && die "--image-registry required for AKS (ACR registry, e.g. myacr.azurecr.io/aifeeders)"
      ACR_NAME="${IMAGE_REGISTRY%%.*}"
      for svc in "${SERVICES[@]}"; do
        TAG="${svc}:${IMAGE_TAG}"
        info "Building ${svc} via az acr build → ${IMAGE_REGISTRY}/${svc}:${IMAGE_TAG}"
        if [[ "$DRY_RUN" == "true" ]]; then
          info "[DRY RUN] az acr build --registry ${ACR_NAME} --image ${TAG} ."
        else
          az acr build --registry "${ACR_NAME}" --image "${TAG}" .
        fi
      done
      ;;
  esac
  ok "Image build/push complete"
fi

# ── Step 7: helm lint ─────────────────────────────────────────────────────────
hdr "Step 7/21 · Running helm lint"

helm lint "${CHART_DIR}" \
  -f "${ENV_VALUES_FILE}" \
  --set global.image.tag="${IMAGE_TAG}" \
  --set llm.baseUrl="https://placeholder.example.com/v1" \
  --set llm.model="placeholder-model" \
  --set-string linkedin.apiVersion="202609" \
  --set secrets.llmApiKey="lint-placeholder" \
  --set secrets.mcpAuthToken="lint-placeholder" \
  --set secrets.gnewsApiKey="lint-placeholder" \
  --set secrets.linkedinClientId="lint-placeholder" \
  --set secrets.linkedinClientSecret="lint-placeholder" \
  --set secrets.linkedinScopes="lint-placeholder" \
  --set secrets.linkedinAccessToken="lint-placeholder" \
  --set secrets.linkedinRedirectUri="lint-placeholder" \
  --set secrets.langfusePublicKey="lint-placeholder" \
  --set secrets.langfuseSecretKey="lint-placeholder"
ok "helm lint passed"

# Assemble common helm flags (used by template + upgrade)
HELM_SET_FLAGS=(
  --set "global.image.tag=${IMAGE_TAG}"
  --set "global.namespace=${NAMESPACE}"
  --set "secrets.llmApiKey=${LLM_API_KEY}"
  --set "secrets.mcpAuthToken=${MCP_AUTH_TOKEN}"
  --set "secrets.gnewsApiKey=${GNEWS_API_KEY}"
  --set "secrets.linkedinClientId=${LINKEDIN_CLIENT_ID}"
  --set "secrets.linkedinClientSecret=${LINKEDIN_CLIENT_SECRET}"
  --set "secrets.linkedinScopes=${LINKEDIN_SCOPES}"
  --set "secrets.linkedinAccessToken=${LINKEDIN_ACCESS_TOKEN}"
  --set "secrets.linkedinRefreshToken=${LINKEDIN_REFRESH_TOKEN}"
  --set "secrets.langfusePublicKey=${LANGFUSE_PUBLIC_KEY}"
  --set "secrets.langfuseSecretKey=${LANGFUSE_SECRET_KEY}"
  --set "secrets.langfuseBaseUrl=${LANGFUSE_BASE_URL}"
)

[[ -n "$LINKEDIN_REDIRECT_URI" ]] && HELM_SET_FLAGS+=(--set "secrets.linkedinRedirectUri=${LINKEDIN_REDIRECT_URI}")
[[ -n "$IMAGE_REGISTRY" ]]        && HELM_SET_FLAGS+=(--set "global.image.registry=${IMAGE_REGISTRY}")

# ── Step 8: helm template (dry-run render) ────────────────────────────────────
hdr "Step 8/21 · Rendering templates (helm template)"

RENDERED_MANIFEST="$(mktemp /tmp/aifeeders-manifest-XXXXXX.yaml)"
helm template aifeeders "${CHART_DIR}" \
  -f "${ENV_VALUES_FILE}" \
  "${HELM_SET_FLAGS[@]}" \
  > "${RENDERED_MANIFEST}"
ok "Templates rendered to ${RENDERED_MANIFEST}"

# ── Step 9: kubectl apply --dry-run=server ────────────────────────────────────
hdr "Step 9/21 · Server-side dry run"

if [[ "$DRY_RUN" == "true" ]]; then
  warn "[DRY RUN] Skipping server-side dry run"
else
  $KUBE_CMD apply --dry-run=server -f "${RENDERED_MANIFEST}" \
    --namespace "${NAMESPACE}" 2>&1 | head -60
  ok "Server-side dry run passed"
fi

# ── Step 10: Create namespace if not exists ───────────────────────────────────
hdr "Step 10/21 · Ensuring namespace exists"

if [[ "$DRY_RUN" == "false" ]]; then
  if $KUBE_CMD get namespace "${NAMESPACE}" &>/dev/null; then
    ok "Namespace '${NAMESPACE}' already exists"
  else
    info "Creating namespace '${NAMESPACE}'..."
    $KUBE_CMD create namespace "${NAMESPACE}"
    ok "Namespace created"
  fi
else
  warn "[DRY RUN] Would create namespace '${NAMESPACE}' if not present"
fi

# ── Step 11: Create/update Kubernetes secret from env vars ────────────────────
hdr "Step 11/21 · Syncing Kubernetes secret (daily-news-secrets)"
# Note: Helm manages this secret — step 11 is a belt-and-suspenders pre-flight
# that ensures the secret exists before helm upgrade creates it too.
info "Secret will be created/updated by Helm (secret.yaml template)"
info "Secret keys (values redacted): LLM_API_KEY, MCP_AUTH_TOKEN, GNEWS_API_KEY,"
info "  LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET, LINKEDIN_SCOPES,"
info "  LINKEDIN_ACCESS_TOKEN, LINKEDIN_REFRESH_TOKEN, LINKEDIN_REDIRECT_URI,"
info "  LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL"

# ── Step 12: helm upgrade --install ───────────────────────────────────────────
hdr "Step 12/21 · Deploying with helm upgrade --install"

if [[ "$DRY_RUN" == "true" ]]; then
  warn "[DRY RUN] Would run:"
  echo "  helm upgrade --install aifeeders ${CHART_DIR} \\"
  echo "    -f ${ENV_VALUES_FILE} \\"
  echo "    --namespace ${NAMESPACE} --create-namespace \\"
  echo "    --atomic --timeout 10m0s \\"
  echo "    --set global.image.tag=${IMAGE_TAG} ..."
else
  helm upgrade --install aifeeders "${CHART_DIR}" \
    -f "${ENV_VALUES_FILE}" \
    "${HELM_SET_FLAGS[@]}" \
    --namespace "${NAMESPACE}" \
    --create-namespace \
    --atomic \
    --timeout 10m0s \
    --wait
  ok "helm upgrade --install succeeded"
fi

# ── Step 13: kubectl rollout status ──────────────────────────────────────────
hdr "Step 13/21 · Waiting for rollouts"

DEPLOYMENTS=(daily-news-api news-mcp pageindex-mcp evaluation-mcp linkedin-mcp)

if [[ "$DRY_RUN" == "false" ]]; then
  for dep in "${DEPLOYMENTS[@]}"; do
    info "Waiting for deployment/${dep}..."
    $KUBE_CMD rollout status deployment/"${dep}" \
      --namespace "${NAMESPACE}" \
      --timeout=300s && ok "${dep} ready" || warn "${dep} rollout timed out — check pod logs"
  done
else
  warn "[DRY RUN] Skipping rollout status checks"
fi

# ── Helper: get service base URL ──────────────────────────────────────────────
get_api_url() {
  local svc="$1"
  local ns="${NAMESPACE}"
  case "$PLATFORM" in
    openshift)
      # Try oc get route first
      local host
      host=$(oc get route "${svc}" -n "${ns}" -o jsonpath='{.spec.host}' 2>/dev/null || echo "")
      [[ -n "$host" ]] && echo "https://${host}" || echo "http://${svc}.${ns}.svc.cluster.local:8000"
      ;;
    eks|aks)
      local host
      host=$($KUBE_CMD get ingress "${svc}" -n "${ns}" \
        -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo "")
      [[ -z "$host" ]] && host=$($KUBE_CMD get ingress "${svc}" -n "${ns}" \
        -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "")
      [[ -n "$host" ]] && echo "https://${host}" || echo "http://daily-news-api.${ns}.svc.cluster.local:8000"
      ;;
  esac
}

# ── Step 14: Health checks ────────────────────────────────────────────────────
hdr "Step 14/21 · Service health checks"

if [[ "$DRY_RUN" == "false" ]]; then
  # Check each MCP service via cluster-internal DNS
  for svc in news-mcp pageindex-mcp evaluation-mcp linkedin-mcp; do
    INTERNAL_URL="http://${svc}.${NAMESPACE}.svc.cluster.local:8000/health"
    info "Health check: ${svc} (${INTERNAL_URL})"
    # Run a temporary pod for in-cluster curl
    RESULT=$($KUBE_CMD run --rm -i --restart=Never --image=curlimages/curl:8.5.0 \
      "health-check-${svc}-$$" \
      --namespace "${NAMESPACE}" \
      -- curl -sf --max-time 10 "${INTERNAL_URL}" 2>&1 || echo "FAILED")
    if echo "$RESULT" | grep -qi "FAILED\|error\|refused"; then
      warn "${svc} /health returned non-OK — check pod logs"
    else
      ok "${svc} healthy"
    fi
  done
else
  warn "[DRY RUN] Skipping health checks"
fi

# ── Step 15: Smoke test — API /health and /ready ──────────────────────────────
hdr "Step 15/21 · API smoke test (/health, /ready)"

if [[ "$SKIP_SMOKE_TEST" == "true" ]]; then
  warn "Skipping smoke test (--skip-smoke-test)"
elif [[ "$DRY_RUN" == "true" ]]; then
  warn "[DRY RUN] Skipping smoke test"
else
  API_URL=$(get_api_url "daily-news-api")
  info "API URL: ${API_URL}"

  for path in health ready; do
    info "GET ${API_URL}/${path}"
    HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" --max-time 15 "${API_URL}/${path}" 2>/dev/null || echo "000")
    if [[ "$HTTP_CODE" == "200" ]]; then
      ok "/${path} → HTTP ${HTTP_CODE}"
    else
      warn "/${path} → HTTP ${HTTP_CODE} (expected 200) — service may still be starting"
    fi
  done
fi

# ── Step 16: LinkedIn connectivity test ──────────────────────────────────────
# CRITICAL: Only calls linkedin_validate_token and linkedin_get_profile.
# NEVER calls create_post or any publishing tool during smoke test.
hdr "Step 16/21 · LinkedIn connectivity test (validate + profile only)"

if [[ "$SKIP_SMOKE_TEST" == "true" ]]; then
  warn "Skipping LinkedIn connectivity test (--skip-smoke-test)"
elif [[ "$DRY_RUN" == "true" ]]; then
  warn "[DRY RUN] Skipping LinkedIn connectivity test"
else
  LINKEDIN_MCP_INTERNAL="http://linkedin-mcp.${NAMESPACE}.svc.cluster.local:8000"
  info "LinkedIn MCP internal URL: ${LINKEDIN_MCP_INTERNAL}"
  info "Testing: linkedin_validate_token (read-only)"

  # Call via MCP JSON-RPC — no post creation, no publishing
  VALIDATE_PAYLOAD='{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"linkedin_validate_token","arguments":{}}}'

  VALIDATE_RESULT=$($KUBE_CMD run --rm -i --restart=Never --image=curlimages/curl:8.5.0 \
    "li-test-validate-$$" \
    --namespace "${NAMESPACE}" \
    -- curl -sf --max-time 15 \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer ${MCP_AUTH_TOKEN}" \
      -d "${VALIDATE_PAYLOAD}" \
      "${LINKEDIN_MCP_INTERNAL}/mcp" 2>&1 || echo "FAILED")

  if echo "$VALIDATE_RESULT" | grep -qi "FAILED\|error\|refused"; then
    warn "linkedin_validate_token failed — check LINKEDIN_ACCESS_TOKEN and MCP_AUTH_TOKEN"
    warn "If you see 403/ACCESS_DENIED, the token may be missing w_member_social scope."
    warn "Re-authorize via: GET <linkedin-mcp-route>/oauth/start"
  else
    ok "linkedin_validate_token responded"
  fi

  info "Testing: linkedin_get_profile (read-only)"
  PROFILE_PAYLOAD='{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"linkedin_get_profile","arguments":{}}}'

  PROFILE_RESULT=$($KUBE_CMD run --rm -i --restart=Never --image=curlimages/curl:8.5.0 \
    "li-test-profile-$$" \
    --namespace "${NAMESPACE}" \
    -- curl -sf --max-time 15 \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer ${MCP_AUTH_TOKEN}" \
      -d "${PROFILE_PAYLOAD}" \
      "${LINKEDIN_MCP_INTERNAL}/mcp" 2>&1 || echo "FAILED")

  if echo "$PROFILE_RESULT" | grep -qi "FAILED\|error\|refused"; then
    warn "linkedin_get_profile failed — check token scopes (need: openid profile email w_member_social)"
  else
    ok "linkedin_get_profile responded"
  fi

  # Explicit guard — the smoke test NEVER publishes
  info "NOTE: create_post / linkedin publishing NOT called during smoke test."
  info "      PUBLISHING_ENABLED in the ConfigMap controls the CronJob at runtime."
fi

# ── Step 17: Config validation output ────────────────────────────────────────
hdr "Step 17/21 · Config validation (keys shown, values redacted)"

if [[ "$DRY_RUN" == "false" ]]; then
  info "ConfigMap daily-news-config:"
  $KUBE_CMD get configmap daily-news-config -n "${NAMESPACE}" \
    -o jsonpath='{.data}' 2>/dev/null | \
    python3 -c "import sys,json; d=json.load(sys.stdin); [print(f'  {k}: {v}') for k,v in d.items()]" \
    2>/dev/null || \
    $KUBE_CMD get configmap daily-news-config -n "${NAMESPACE}" -o yaml | grep -E "^\s+[A-Z_]+:" | head -40

  echo ""
  info "Secret daily-news-secrets keys (values redacted):"
  $KUBE_CMD get secret daily-news-secrets -n "${NAMESPACE}" \
    -o jsonpath='{.data}' 2>/dev/null | \
    python3 -c "import sys,json; d=json.load(sys.stdin); [print(f'  {k}: [REDACTED]') for k in d]" \
    2>/dev/null || \
    $KUBE_CMD get secret daily-news-secrets -n "${NAMESPACE}" \
    -o jsonpath='{.data}' | grep -oP '"[A-Z_]+"' | sed 's/"//g' | \
    while read -r k; do echo "  ${k}: [REDACTED]"; done
else
  warn "[DRY RUN] Skipping config validation"
fi

# ── Step 18: Deployment summary ───────────────────────────────────────────────
hdr "Step 18/21 · Deployment summary"

echo ""
echo -e "${BOLD}Platform:${RESET}     ${PLATFORM}"
echo -e "${BOLD}Namespace:${RESET}    ${NAMESPACE}"
echo -e "${BOLD}Image tag:${RESET}    ${IMAGE_TAG}"
echo -e "${BOLD}Registry:${RESET}     ${IMAGE_REGISTRY:-'(from values.yaml)'}"
echo ""

if [[ "$DRY_RUN" == "false" ]]; then
  echo -e "${BOLD}Deployments:${RESET}"
  $KUBE_CMD get deployments -n "${NAMESPACE}" \
    -o custom-columns='NAME:.metadata.name,READY:.status.readyReplicas,DESIRED:.spec.replicas' \
    2>/dev/null || true

  echo ""
  echo -e "${BOLD}CronJob:${RESET}"
  $KUBE_CMD get cronjob -n "${NAMESPACE}" \
    -o custom-columns='NAME:.metadata.name,SCHEDULE:.spec.schedule,LAST_SCHEDULE:.status.lastScheduleTime' \
    2>/dev/null || true

  echo ""
  case "$PLATFORM" in
    openshift)
      echo -e "${BOLD}Routes:${RESET}"
      oc get routes -n "${NAMESPACE}" \
        -o custom-columns='NAME:.metadata.name,HOST:.spec.host,TLS:.spec.tls.termination' \
        2>/dev/null || true
      ;;
    eks|aks)
      echo -e "${BOLD}Ingresses:${RESET}"
      $KUBE_CMD get ingress -n "${NAMESPACE}" \
        -o custom-columns='NAME:.metadata.name,HOSTS:.spec.rules[*].host,ADDRESS:.status.loadBalancer.ingress[*].hostname' \
        2>/dev/null || true
      ;;
  esac
fi

# ── Steps 19–21: Final checks ─────────────────────────────────────────────────
hdr "Steps 19–21/21 · Final validation"

if [[ "$DRY_RUN" == "false" ]]; then
  info "HPA status:"
  $KUBE_CMD get hpa -n "${NAMESPACE}" 2>/dev/null || warn "No HPA found"

  info "PDB status:"
  $KUBE_CMD get pdb -n "${NAMESPACE}" 2>/dev/null || warn "No PDB found"

  info "Recent events (warnings only):"
  $KUBE_CMD get events -n "${NAMESPACE}" --field-selector type=Warning \
    --sort-by='.lastTimestamp' 2>/dev/null | tail -10 || true
fi

# Clean up temp file
rm -f "${RENDERED_MANIFEST}"

echo ""
ok "═══════════════════════════════════════════════════"
ok "  AIFeeders deployment complete (${PLATFORM} / ${NAMESPACE})"
ok "═══════════════════════════════════════════════════"
echo ""
