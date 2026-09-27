#!/usr/bin/env bash
# =============================================================================
# clean_rebuild_openshift.sh — Full clean rebuild for namespace aifeeders
# =============================================================================
# Usage:
#   oc login --token=<token> --server=https://api.<cluster>:6443
#   ./scripts/clean_rebuild_openshift.sh
#
# What it does:
#   1. Hard-deletes ALL resources in aifeeders (pods, builds, buildconfigs,
#      imagestreams, deployments, services, routes, cronjobs, hpa, pdb,
#      configmap, networkpolicies)
#   2. Preserves daily-news-secrets (real API credentials not in repo)
#   3. Re-applies config layer (namespace, configmap, rbac, networkpolicy)
#   4. Re-applies buildconfigs + imagestreams
#   5. Starts all 5 binary builds from local source
#   6. Waits for all builds to complete (up to 15 min each)
#   7. Deploys MCPs → API → cronjob → HPA → PDB
#   8. Waits for all deployments to become Available
#   9. Prints final pod + route status
#
# Ref: RUNBOOK.md § 15
# =============================================================================
set -euo pipefail

NS=aifeeders
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_ROOT}"

# ── Preflight ────────────────────────────────────────────────────────────────
if ! oc whoami &>/dev/null; then
  echo "❌  Not logged in to OpenShift. Run: oc login --token=<token> --server=<url>"
  exit 1
fi

CURRENT_PROJECT=$(oc project -q)
if [[ "${CURRENT_PROJECT}" != "${NS}" ]]; then
  echo "Switching to project ${NS}..."
  oc project "${NS}"
fi

echo ""
echo "================================================================="
echo "  AIFeeders — Full clean rebuild · namespace: ${NS}"
echo "================================================================="
echo ""

# ── Step 1: Hard delete all resources (preserve secret) ──────────────────────
echo "==> [1/8] Deleting all resources (preserving daily-news-secrets)..."
oc delete pods --all -n "${NS}" --force --grace-period=0 2>/dev/null || true
oc delete builds --all          -n "${NS}" 2>/dev/null || true
oc delete buildconfig --all     -n "${NS}" 2>/dev/null || true
oc delete imagestream --all     -n "${NS}" 2>/dev/null || true
oc delete deployment --all      -n "${NS}" 2>/dev/null || true
oc delete service --all         -n "${NS}" 2>/dev/null || true
oc delete route --all           -n "${NS}" 2>/dev/null || true
oc delete cronjob --all         -n "${NS}" 2>/dev/null || true
oc delete hpa --all             -n "${NS}" 2>/dev/null || true
oc delete pdb --all             -n "${NS}" 2>/dev/null || true
oc delete configmap daily-news-config -n "${NS}" 2>/dev/null || true
oc delete networkpolicy --all   -n "${NS}" 2>/dev/null || true
echo "    Done."

# Verify secret still intact
if ! oc get secret daily-news-secrets -n "${NS}" &>/dev/null; then
  echo ""
  echo "⚠️  WARNING: daily-news-secrets not found in namespace ${NS}."
  echo "    Deployments will fail at startup without it."
  echo "    See RUNBOOK.md § 6 to recreate it."
  echo ""
fi

# ── Step 2: Config layer ──────────────────────────────────────────────────────
echo "==> [2/8] Re-applying config layer..."
oc apply -f openshift/namespace.yaml
oc apply -f openshift/configmap.yaml
oc apply -f openshift/rbac.yaml
oc apply -f openshift/networkpolicy.yaml

# ── Step 3: BuildConfigs + ImageStreams ───────────────────────────────────────
echo "==> [3/8] Re-applying BuildConfigs + ImageStreams..."
oc apply -f openshift/buildconfigs.yaml

# ── Step 4: Start all 5 builds ───────────────────────────────────────────────
echo "==> [4/8] Starting all 5 binary builds from local source..."
for svc in daily-news news-mcp pageindex-mcp evaluation-mcp linkedin-mcp; do
  echo "    Starting build: ${svc}"
  oc start-build "${svc}" --from-dir=. --follow=false -n "${NS}"
done
echo "    All builds submitted."

# ── Step 5: Wait for builds ───────────────────────────────────────────────────
echo "==> [5/8] Waiting for builds to complete (this takes 6–16 min each)..."
echo "    Polling every 30s — Ctrl+C to detach (builds continue on cluster)"
while true; do
  STATUSES=$(oc get builds -n "${NS}" --no-headers 2>/dev/null \
    | awk '{print $1 "=" $4}')
  echo "    $(date '+%H:%M:%S')  $(echo "${STATUSES}" | tr '\n' '  ')"

  FAILED=$(oc get builds -n "${NS}" --no-headers 2>/dev/null \
    | grep -i "failed\|error" | awk '{print $1}' || true)
  if [[ -n "${FAILED}" ]]; then
    echo ""
    echo "❌  Build(s) failed: ${FAILED}"
    echo "    Check logs with: oc logs build/<name> -n ${NS} | tail -50"
    echo "    If caused by 'Network is unreachable' (transient), re-run:"
    echo "      oc start-build <name> --from-dir=. --follow=false -n ${NS}"
    exit 1
  fi

  RUNNING=$(oc get builds -n "${NS}" --no-headers 2>/dev/null \
    | grep -i "running\|pending\|new" | wc -l || echo "0")
  if [[ "${RUNNING}" -eq 0 ]]; then
    break
  fi
  sleep 30
done
echo "    All builds complete ✅"

# ── Step 6: Deploy workloads ──────────────────────────────────────────────────
echo "==> [6/8] Deploying MCP services..."
oc apply -f openshift/news-mcp/
oc apply -f openshift/pageindex-mcp/
oc apply -f openshift/evaluation-mcp/
oc apply -f openshift/linkedin-mcp/

echo "==> [7/8] Deploying API + scheduling + autoscaling..."
oc apply -f openshift/api/
oc apply -f openshift/cronjob.yaml
oc apply -f openshift/hpa.yaml
oc apply -f openshift/pdb.yaml

# ── Step 7: Wait for deployments ─────────────────────────────────────────────
echo "==> [8/8] Waiting for all deployments to become Available..."
oc wait deployment/daily-news-api deployment/news-mcp deployment/pageindex-mcp \
         deployment/evaluation-mcp deployment/linkedin-mcp \
  --for=condition=Available -n "${NS}" --timeout=120s

# ── Final status ──────────────────────────────────────────────────────────────
echo ""
echo "================================================================="
echo "  ✅  Clean rebuild complete — namespace: ${NS}"
echo "================================================================="
echo ""
echo "--- PODS ---"
oc get pods -n "${NS}"
echo ""
echo "--- ROUTES ---"
oc get routes -n "${NS}"
echo ""
echo "--- HPA ---"
oc get hpa -n "${NS}"
echo ""
echo "--- CRONJOBS ---"
oc get cronjobs -n "${NS}"
