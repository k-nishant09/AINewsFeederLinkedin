#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# AIFeeders — End-to-End Deployment Script
#
# Supports: OpenShift (primary) · EKS · AKS
#
# Zero-argument OpenShift quick-start (uses hard-coded cluster defaults):
#   ./deploy/deploy.sh
#
# Full usage:
#   ./deploy/deploy.sh [options]
#
# Key options:
#   --platform      openshift | eks | aks            (default: openshift)
#   --namespace     <ns>                             (default: aifeeders)
#   --image-tag     <tag>                            (default: latest)
#   --oc-token      <sha256~...>                     OpenShift login token
#   --oc-server     <https://api.host:6443>          OpenShift API server
#   --image-registry <registry>                      EKS/AKS only
#   --skip-build    skip image build (re-deploy existing tag)
#   --skip-smoke-test  skip /health + LinkedIn checks
#   --dry-run       print commands without executing
#
# Required env vars (never printed — only [SET]/[MISSING] shown):
#   LLM_API_KEY, LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET,
#   LINKEDIN_ACCESS_TOKEN, GNEWS_API_KEY, LANGFUSE_PUBLIC_KEY,
#   LANGFUSE_SECRET_KEY
#
# Optional env vars:
#   MCP_AUTH_TOKEN, LINKEDIN_SCOPES, LINKEDIN_REFRESH_TOKEN,
#   LINKEDIN_REDIRECT_URI, LANGFUSE_BASE_URL, GNEWS_API_KEY_2
#
# Embedded defaults for the IBM OpenShift cluster (override via --oc-* flags):
#   OC_DEFAULT_TOKEN  = sha256~3uwKwXiiefh4FXTE6IglEM0L58Nyk_IahBEzgxASCwU
#   OC_DEFAULT_SERVER = https://api.your-cluster.example.com:6443
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────────────────
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

# ── Embedded cluster defaults (IBM OpenShift fusion cluster) ──────────────────
OC_DEFAULT_TOKEN="sha256~3uwKwXiiefh4FXTE6IglEM0L58Nyk_IahBEzgxASCwU"
OC_DEFAULT_SERVER="https://api.your-cluster.example.com:6443"

# ── CLI defaults ──────────────────────────────────────────────────────────────
PLATFORM="openshift"
ENVIRONMENT="prod"
NAMESPACE="aifeeders"
IMAGE_REGISTRY=""
IMAGE_TAG="latest"
DRY_RUN=false
SKIP_BUILD=false
SKIP_SMOKE_TEST=false
OC_TOKEN="${OC_DEFAULT_TOKEN}"
OC_SERVER="${OC_DEFAULT_SERVER}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHART_DIR="${SCRIPT_DIR}/helm/aifeeders"

# ── Step 1: Parse arguments ───────────────────────────────────────────────────
hdr "Step 1 · Parsing arguments"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform)        PLATFORM="$2";        shift 2 ;;
    --environment)     ENVIRONMENT="$2";     shift 2 ;;
    --namespace)       NAMESPACE="$2";       shift 2 ;;
    --image-registry)  IMAGE_REGISTRY="$2";  shift 2 ;;
    --image-tag)       IMAGE_TAG="$2";       shift 2 ;;
    --oc-token)        OC_TOKEN="$2";        shift 2 ;;
    --oc-server)       OC_SERVER="$2";       shift 2 ;;
    --dry-run)         DRY_RUN=true;         shift   ;;
    --skip-build)      SKIP_BUILD=true;      shift   ;;
    --skip-smoke-test) SKIP_SMOKE_TEST=true; shift   ;;
    *) die "Unknown argument: $1. Run with --help for usage." ;;
  esac
done

[[ "$PLATFORM" =~ ^(openshift|eks|aks)$ ]] || die "--platform must be: openshift | eks | aks"

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
hdr "Step 2 · Validating prerequisites"

check_cmd() {
  if command -v "$1" &>/dev/null; then
    ok "$1 found: $(command -v "$1")"
  else
    die "Required tool '$1' not found in PATH"
  fi
}

check_cmd helm

if command -v oc &>/dev/null; then
  KUBE_CMD="oc"
  ok "oc found (OpenShift CLI)"
elif command -v kubectl &>/dev/null; then
  KUBE_CMD="kubectl"
  ok "kubectl found"
else
  die "Neither 'oc' nor 'kubectl' found in PATH"
fi

if [[ "$SKIP_BUILD" == "false" ]]; then
  if command -v docker &>/dev/null; then
    CONTAINER_CMD="docker"
    ok "docker found"
  elif command -v podman &>/dev/null; then
    CONTAINER_CMD="podman"
    ok "podman found"
  else
    if [[ "$PLATFORM" == "openshift" ]]; then
      # OpenShift uses oc start-build (server-side) — no local container tool needed
      CONTAINER_CMD="none"
      warn "No docker/podman found — using server-side oc start-build for OpenShift"
    else
      die "Neither 'docker' nor 'podman' found in PATH (use --skip-build to skip image build)"
    fi
  fi
else
  CONTAINER_CMD="none"
  warn "Skipping container tool check (--skip-build)"
fi

if [[ "$PLATFORM" == "aks" ]] && [[ "$SKIP_BUILD" == "false" ]]; then
  check_cmd az
fi

# ── Step 3: OpenShift login ───────────────────────────────────────────────────
hdr "Step 3 · Cluster authentication"

if [[ "$PLATFORM" == "openshift" ]] && command -v oc &>/dev/null; then
  info "Logging in to OpenShift: ${OC_SERVER}"
  if [[ "$DRY_RUN" == "true" ]]; then
    info "[DRY RUN] oc login --token=<redacted> --server=${OC_SERVER}"
  else
    oc login --token="${OC_TOKEN}" --server="${OC_SERVER}" \
      --insecure-skip-tls-verify=false 2>&1 | grep -v "^$" || \
      oc login --token="${OC_TOKEN}" --server="${OC_SERVER}" \
        --insecure-skip-tls-verify=true
    ok "Logged in as: $(oc whoami)"
  fi
else
  KUBE_CTX=$($KUBE_CMD config current-context 2>/dev/null || echo "(none)")
  info "Current kube context: ${KUBE_CTX}"
  [[ "$KUBE_CTX" == "(none)" ]] && die "No kube context set."
fi

# Verify cluster reachability
if [[ "$DRY_RUN" == "false" ]]; then
  $KUBE_CMD cluster-info &>/dev/null || die "Cannot reach cluster. Check your credentials."
  ok "Cluster reachable"
fi

# Ensure we are working in the correct namespace / project
if [[ "$DRY_RUN" == "false" ]]; then
  if command -v oc &>/dev/null && [[ "$PLATFORM" == "openshift" ]]; then
    oc project "${NAMESPACE}" 2>/dev/null || {
      info "Project '${NAMESPACE}' does not exist — creating..."
      oc new-project "${NAMESPACE}" || true
    }
    ok "Active project: $(oc project -q)"
  fi
fi

# ── Step 4: Validate required environment variables ───────────────────────────
hdr "Step 4 · Validating environment variables"

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
  GNEWS_API_KEY_2
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
GNEWS_API_KEY_2="${GNEWS_API_KEY_2:-}"

# ── Step 5: Delete old, dead, and failed pods ─────────────────────────────────
hdr "Step 5 · Cleaning up old / dead / failed pods"

if [[ "$DRY_RUN" == "false" ]]; then
  # Delete pods in terminal states: Completed, Error, OOMKilled, Evicted, CrashLoopBackOff
  info "Scanning namespace '${NAMESPACE}' for dead pods..."

  # Evicted pods
  EVICTED=$($KUBE_CMD get pods -n "${NAMESPACE}" \
    --field-selector=status.reason=Evicted \
    -o name 2>/dev/null || true)
  if [[ -n "$EVICTED" ]]; then
    echo "$EVICTED" | xargs -r $KUBE_CMD delete -n "${NAMESPACE}"
    ok "Deleted evicted pods"
  else
    info "No evicted pods found"
  fi

  # Completed / Failed / OOMKilled / Error / CrashLoopBackOff pods
  DEAD_PODS=$($KUBE_CMD get pods -n "${NAMESPACE}" \
    --no-headers 2>/dev/null | \
    awk '
      $3 == "Completed"        ||
      $3 == "Error"            ||
      $3 == "OOMKilled"        ||
      $3 == "CrashLoopBackOff" ||
      $3 == "Init:Error"       ||
      $3 == "Init:OOMKilled"   ||
      ($3 == "Terminating" && $5 != "<none>") {print $1}
    ' || true)

  if [[ -n "$DEAD_PODS" ]]; then
    info "Dead pods to delete:"
    echo "$DEAD_PODS" | while read -r p; do info "  - ${p}"; done
    echo "$DEAD_PODS" | xargs -r $KUBE_CMD delete pod -n "${NAMESPACE}" --force --grace-period=0
    ok "Deleted dead pods"
  else
    info "No dead pods found"
  fi

  # Show remaining pods
  info "Current pods in '${NAMESPACE}':"
  $KUBE_CMD get pods -n "${NAMESPACE}" 2>/dev/null || info "(namespace may not exist yet)"
else
  warn "[DRY RUN] Would delete Evicted/Error/CrashLoopBackOff pods in '${NAMESPACE}'"
fi

# ── Step 6: Build and push images ─────────────────────────────────────────────
hdr "Step 6 · Building images"

# All service names — must match BuildConfig names on OpenShift
SERVICES=(daily-news news-mcp pageindex-mcp evaluation-mcp linkedin-mcp)

if [[ "$SKIP_BUILD" == "true" ]]; then
  warn "Skipping image build (--skip-build)"
else
  case "$PLATFORM" in
    openshift)
      info "Platform: OpenShift — using 'oc start-build' (server-side binary builds)"
      BUILD_FAILURES=0

      for svc in "${SERVICES[@]}"; do
        info "Starting build for '${svc}'..."
        if [[ "$DRY_RUN" == "true" ]]; then
          info "[DRY RUN] oc start-build ${svc} --from-dir=. --follow --namespace=${NAMESPACE}"
        else
          # Check if BuildConfig exists before attempting
          if $KUBE_CMD get buildconfig "${svc}" -n "${NAMESPACE}" &>/dev/null; then
            if oc start-build "${svc}" \
                --from-dir="${SCRIPT_DIR}/.." \
                --follow \
                --wait \
                --namespace="${NAMESPACE}"; then
              ok "Build '${svc}' succeeded"
            else
              warn "Build '${svc}' failed — deployment will use existing image tag"
              (( BUILD_FAILURES++ )) || true
            fi
          else
            warn "BuildConfig '${svc}' not found in '${NAMESPACE}' — skipping build"
            warn "  Create it with: oc apply -f openshift/buildconfigs.yaml"
            (( BUILD_FAILURES++ )) || true
          fi
        fi
      done

      if [[ $BUILD_FAILURES -gt 0 ]]; then
        warn "${BUILD_FAILURES} build(s) skipped or failed — continuing with existing images"
      else
        ok "All OpenShift builds completed"
      fi
      ;;

    eks)
      [[ -z "$IMAGE_REGISTRY" ]] && die "--image-registry is required for EKS (ECR URL)"
      AWS_REGION="${AWS_REGION:-us-east-1}"
      info "Logging in to ECR (region: ${AWS_REGION})..."
      if [[ "$DRY_RUN" == "false" ]]; then
        aws ecr get-login-password --region "${AWS_REGION}" | \
          ${CONTAINER_CMD} login --username AWS --password-stdin "${IMAGE_REGISTRY%%/*}"
        ok "ECR login successful"
      fi
      for svc in "${SERVICES[@]}"; do
        TAG="${IMAGE_REGISTRY}/${svc}:${IMAGE_TAG}"
        info "Building ${svc} → ${TAG}"
        if [[ "$DRY_RUN" == "true" ]]; then
          info "[DRY RUN] ${CONTAINER_CMD} build -t ${TAG} ."
          info "[DRY RUN] ${CONTAINER_CMD} push ${TAG}"
        else
          ${CONTAINER_CMD} build -t "${TAG}" "${SCRIPT_DIR}/.."
          ${CONTAINER_CMD} push "${TAG}"
          ok "Pushed ${TAG}"
        fi
      done
      ;;

    aks)
      [[ -z "$IMAGE_REGISTRY" ]] && die "--image-registry is required for AKS (ACR URL)"
      ACR_NAME="${IMAGE_REGISTRY%%.*}"
      for svc in "${SERVICES[@]}"; do
        TAG="${svc}:${IMAGE_TAG}"
        info "Building ${svc} via az acr build → ${IMAGE_REGISTRY}/${svc}:${IMAGE_TAG}"
        if [[ "$DRY_RUN" == "true" ]]; then
          info "[DRY RUN] az acr build --registry ${ACR_NAME} --image ${TAG} ."
        else
          az acr build --registry "${ACR_NAME}" --image "${TAG}" "${SCRIPT_DIR}/.."
          ok "ACR build ${svc} complete"
        fi
      done
      ;;
  esac

  ok "Image build phase complete"
fi

# ── Step 7: helm lint ─────────────────────────────────────────────────────────
hdr "Step 7 · Helm lint"

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

# ── Assemble helm flags (shared by template + upgrade) ────────────────────────
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
[[ -n "$GNEWS_API_KEY_2" ]]       && HELM_SET_FLAGS+=(--set "secrets.gnewsApiKey2=${GNEWS_API_KEY_2}")

# ── Step 8: helm template dry-run render ─────────────────────────────────────
hdr "Step 8 · Rendering Helm templates"

RENDERED_MANIFEST="$(mktemp /tmp/aifeeders-manifest-XXXXXX.yaml)"
helm template aifeeders "${CHART_DIR}" \
  -f "${ENV_VALUES_FILE}" \
  "${HELM_SET_FLAGS[@]}" \
  > "${RENDERED_MANIFEST}"
ok "Templates rendered → ${RENDERED_MANIFEST}"

# ── Step 9: Server-side dry run ───────────────────────────────────────────────
hdr "Step 9 · Server-side dry run (kubectl apply --dry-run=server)"

if [[ "$DRY_RUN" == "true" ]]; then
  warn "[DRY RUN] Skipping server-side dry run"
else
  $KUBE_CMD apply --dry-run=server -f "${RENDERED_MANIFEST}" \
    --namespace "${NAMESPACE}" 2>&1 | head -60
  ok "Server-side dry run passed"
fi

# ── Step 10: Ensure namespace exists ─────────────────────────────────────────
hdr "Step 10 · Ensuring namespace / project exists"

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

# ── Step 11: Helm upgrade --install ──────────────────────────────────────────
hdr "Step 11 · Deploying with 'helm upgrade --install'"

if [[ "$DRY_RUN" == "true" ]]; then
  warn "[DRY RUN] Would run helm upgrade --install aifeeders"
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

# ── Step 12: Force rollout restart (pick up new images) ───────────────────────
hdr "Step 12 · Rolling restart of all deployments"

DEPLOYMENTS=(daily-news-api news-mcp pageindex-mcp evaluation-mcp linkedin-mcp)

if [[ "$DRY_RUN" == "false" ]]; then
  for dep in "${DEPLOYMENTS[@]}"; do
    if $KUBE_CMD get deployment "${dep}" -n "${NAMESPACE}" &>/dev/null; then
      info "Rolling restart: deployment/${dep}"
      $KUBE_CMD rollout restart deployment/"${dep}" --namespace "${NAMESPACE}"
    else
      warn "Deployment '${dep}' not found — skipping restart"
    fi
  done
  ok "Rollout restarts issued"
else
  warn "[DRY RUN] Would restart: ${DEPLOYMENTS[*]}"
fi

# ── Step 13: Wait for rollouts to complete ────────────────────────────────────
hdr "Step 13 · Waiting for rollouts to complete"

if [[ "$DRY_RUN" == "false" ]]; then
  for dep in "${DEPLOYMENTS[@]}"; do
    if $KUBE_CMD get deployment "${dep}" -n "${NAMESPACE}" &>/dev/null; then
      info "Waiting for deployment/${dep} (timeout 5m)..."
      $KUBE_CMD rollout status deployment/"${dep}" \
        --namespace "${NAMESPACE}" \
        --timeout=300s && ok "${dep} ready" || \
        warn "${dep} rollout timed out — check: $KUBE_CMD logs -n ${NAMESPACE} -l app=${dep} --tail=50"
    fi
  done
else
  warn "[DRY RUN] Skipping rollout status"
fi

# ── Step 14: Show live pod status ────────────────────────────────────────────
hdr "Step 14 · Live pod status"

if [[ "$DRY_RUN" == "false" ]]; then
  echo ""
  $KUBE_CMD get pods -n "${NAMESPACE}" \
    -o wide \
    --sort-by='.metadata.creationTimestamp' 2>/dev/null || true
fi

# ── Helper: derive service URL ────────────────────────────────────────────────
get_svc_url() {
  local svc="$1" ns="${NAMESPACE}"
  case "$PLATFORM" in
    openshift)
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
      [[ -n "$host" ]] && echo "https://${host}" || echo "http://${svc}.${ns}.svc.cluster.local:8000"
      ;;
  esac
}

# ── Step 15: In-cluster health checks ────────────────────────────────────────
hdr "Step 15 · MCP service health checks (in-cluster curl)"

if [[ "$DRY_RUN" == "false" ]] && [[ "$SKIP_SMOKE_TEST" == "false" ]]; then
  for svc in news-mcp pageindex-mcp evaluation-mcp linkedin-mcp; do
    INTERNAL_URL="http://${svc}.${NAMESPACE}.svc.cluster.local:8000/health"
    info "Health check: ${svc} → ${INTERNAL_URL}"
    RESULT=$($KUBE_CMD run "hc-${svc}-$$" \
      --rm -i --restart=Never \
      --image=curlimages/curl:8.5.0 \
      --namespace "${NAMESPACE}" \
      --labels="app=daily-news-worker" \
      -- curl -sf --max-time 10 "${INTERNAL_URL}" 2>&1 || echo "FAILED")
    if echo "$RESULT" | grep -qi "FAILED\|error\|refused\|000"; then
      warn "${svc} /health — non-OK (service may still be starting)"
    else
      ok "${svc} healthy"
    fi
  done
else
  warn "[DRY RUN / skip-smoke-test] Skipping in-cluster health checks"
fi

# ── Step 16: API smoke test ───────────────────────────────────────────────────
hdr "Step 16 · API smoke test (/health + /ready)"

if [[ "$SKIP_SMOKE_TEST" == "false" ]] && [[ "$DRY_RUN" == "false" ]]; then
  API_URL=$(get_svc_url "daily-news-api")
  info "API base URL: ${API_URL}"
  for path in health ready; do
    HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" --max-time 15 \
      "${API_URL}/${path}" 2>/dev/null || echo "000")
    if [[ "$HTTP_CODE" == "200" ]]; then
      ok "/${path} → HTTP ${HTTP_CODE}"
    else
      warn "/${path} → HTTP ${HTTP_CODE} (expected 200) — service may still be starting"
    fi
  done
else
  warn "[DRY RUN / skip-smoke-test] Skipping API smoke test"
fi

# ── Step 17: LinkedIn connectivity test (read-only) ──────────────────────────
# NEVER calls create_post or any publishing tool during smoke test.
hdr "Step 17 · LinkedIn connectivity test (validate + profile — read-only)"

if [[ "$SKIP_SMOKE_TEST" == "false" ]] && [[ "$DRY_RUN" == "false" ]]; then
  LI_URL="http://linkedin-mcp.${NAMESPACE}.svc.cluster.local:8000"

  for test_name in linkedin_validate_token linkedin_get_profile; do
    PAYLOAD="{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"${test_name}\",\"arguments\":{}}}"
    info "Testing: ${test_name}"
    RESULT=$($KUBE_CMD run "li-${test_name##*_}-$$" \
      --rm -i --restart=Never \
      --image=curlimages/curl:8.5.0 \
      --namespace "${NAMESPACE}" \
      --labels="app=daily-news-worker" \
      -- curl -sf --max-time 15 \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${MCP_AUTH_TOKEN}" \
        -d "${PAYLOAD}" \
        "${LI_URL}/mcp" 2>&1 || echo "FAILED")
    if echo "$RESULT" | grep -qi "FAILED\|error\|refused"; then
      warn "${test_name} failed — check LINKEDIN_ACCESS_TOKEN / MCP_AUTH_TOKEN"
    else
      ok "${test_name} responded"
    fi
  done
  info "NOTE: linkedin create_post NOT called during smoke test (controlled by PUBLISHING_ENABLED ConfigMap)"
else
  warn "[DRY RUN / skip-smoke-test] Skipping LinkedIn connectivity test"
fi

# ── Step 18: Config & secret key validation ───────────────────────────────────
hdr "Step 18 · Config validation (values shown, secret keys redacted)"

if [[ "$DRY_RUN" == "false" ]]; then
  info "ConfigMap daily-news-config:"
  $KUBE_CMD get configmap daily-news-config -n "${NAMESPACE}" \
    -o jsonpath='{.data}' 2>/dev/null | \
    python3 -c "import sys,json; d=json.load(sys.stdin); [print(f'  {k}: {v}') for k,v in d.items()]" \
    2>/dev/null || warn "ConfigMap 'daily-news-config' not found"

  echo ""
  info "Secret daily-news-secrets keys (values REDACTED):"
  $KUBE_CMD get secret daily-news-secrets -n "${NAMESPACE}" \
    -o jsonpath='{.data}' 2>/dev/null | \
    python3 -c "import sys,json; d=json.load(sys.stdin); [print(f'  {k}: [REDACTED]') for k in d]" \
    2>/dev/null || warn "Secret 'daily-news-secrets' not found"
else
  warn "[DRY RUN] Skipping config validation"
fi

# ── Step 19: Deployment summary ───────────────────────────────────────────────
hdr "Step 19 · Deployment summary"

echo ""
echo -e "${BOLD}Platform:${RESET}     ${PLATFORM}"
echo -e "${BOLD}Namespace:${RESET}    ${NAMESPACE}"
echo -e "${BOLD}Image tag:${RESET}    ${IMAGE_TAG}"
echo -e "${BOLD}Registry:${RESET}     ${IMAGE_REGISTRY:-'(from values.yaml)'}"
echo ""

if [[ "$DRY_RUN" == "false" ]]; then
  echo -e "${BOLD}Deployments:${RESET}"
  $KUBE_CMD get deployments -n "${NAMESPACE}" \
    -o custom-columns='NAME:.metadata.name,READY:.status.readyReplicas,DESIRED:.spec.replicas,IMAGE:.spec.template.spec.containers[0].image' \
    2>/dev/null || true

  echo ""
  echo -e "${BOLD}CronJob:${RESET}"
  $KUBE_CMD get cronjob -n "${NAMESPACE}" \
    -o custom-columns='NAME:.metadata.name,SCHEDULE:.spec.schedule,LAST_RUN:.status.lastScheduleTime,ACTIVE:.status.active' \
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

# ── Step 20: Final cluster health checks ─────────────────────────────────────
hdr "Step 20 · Final cluster health (HPA, PDB, warnings)"

if [[ "$DRY_RUN" == "false" ]]; then
  info "HPA status:"
  $KUBE_CMD get hpa -n "${NAMESPACE}" 2>/dev/null || warn "No HPA found"

  echo ""
  info "PDB status:"
  $KUBE_CMD get pdb -n "${NAMESPACE}" 2>/dev/null || warn "No PDB found"

  echo ""
  info "Recent warning events (last 10):"
  $KUBE_CMD get events -n "${NAMESPACE}" \
    --field-selector type=Warning \
    --sort-by='.lastTimestamp' 2>/dev/null | tail -10 || true
fi

# ── Cleanup ───────────────────────────────────────────────────────────────────
rm -f "${RENDERED_MANIFEST}"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
ok "══════════════════════════════════════════════════════"
ok "  AIFeeders deployment COMPLETE"
ok "  Platform:   ${PLATFORM}"
ok "  Namespace:  ${NAMESPACE}"
ok "  Image tag:  ${IMAGE_TAG}"
ok "══════════════════════════════════════════════════════"
echo ""
