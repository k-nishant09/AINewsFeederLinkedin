# AIFeeders — Deployment Guide

Production deployment for the AI Daily News Multi-Agent Platform.
Supports OpenShift, Amazon EKS, and Azure AKS via a single Helm chart.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Required Environment Variables](#required-environment-variables)
3. [Quick Start](#quick-start)
4. [Secret Management](#secret-management)
5. [LinkedIn Re-authorization](#linkedin-re-authorization)
6. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### All platforms

| Tool | Minimum version | Notes |
|------|----------------|-------|
| `helm` | 3.12+ | `brew install helm` / package manager |
| `kubectl` | 1.28+ | Or `oc` on OpenShift |

### OpenShift

| Tool | Notes |
|------|-------|
| `oc` | OpenShift CLI — replaces `kubectl` |
| BuildConfig | One BuildConfig per service must exist in the namespace |
| `oc login` | Must be authenticated before running the script |

```bash
# Log in
oc login --token=<token> --server=https://<cluster-api>:6443

# Verify
oc whoami && oc project aifeeders
```

### EKS (Amazon)

| Tool | Notes |
|------|-------|
| `aws` CLI | 2.x — for ECR login |
| `docker` or `podman` | Image build and push |
| AWS Load Balancer Controller | Required for ALB Ingress |
| `eks` kube context | `aws eks update-kubeconfig --region us-east-1 --name <cluster>` |

### AKS (Azure)

| Tool | Notes |
|------|-------|
| `az` CLI | 2.50+ |
| `docker` or `podman` | Image build |
| nginx Ingress Controller | `helm install ingress-nginx ingress-nginx/ingress-nginx` |
| `aks` kube context | `az aks get-credentials --resource-group <rg> --name <cluster>` |

---

## Required Environment Variables

Export **all** of the following before running `deploy.sh`.  
The script will display `[SET]` or `[MISSING]` — it **never prints values**.

| Variable | Description |
|----------|-------------|
| `LLM_API_KEY` | Bearer token for the LLM Gateway (IBM Model Gateway) |
| `LINKEDIN_CLIENT_ID` | LinkedIn OAuth app client ID |
| `LINKEDIN_CLIENT_SECRET` | LinkedIn OAuth app client secret |
| `LINKEDIN_ACCESS_TOKEN` | LinkedIn member access token (rotate before 60-day expiry) |
| `GNEWS_API_KEY` | GNews API key for news fetching |
| `LANGFUSE_PUBLIC_KEY` | Langfuse project public key |
| `LANGFUSE_SECRET_KEY` | Langfuse project secret key |

### Optional variables (defaults applied if unset)

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_AUTH_TOKEN` | `aifeeders-mcp-token` | Shared Bearer token for MCP server auth |
| `LINKEDIN_SCOPES` | `openid profile email w_member_social` | OAuth scopes — **must include `w_member_social`** |
| `LINKEDIN_REFRESH_TOKEN` | _(empty)_ | Programmatic refresh token, if enabled |
| `LINKEDIN_REDIRECT_URI` | _(from values.yaml)_ | Must match LinkedIn Developer Portal exactly |
| `LANGFUSE_BASE_URL` | `https://us.cloud.langfuse.com` | Self-hosted Langfuse endpoint |

---

## Quick Start

### OpenShift

```bash
# Export secrets
export LLM_API_KEY="..."
export LINKEDIN_CLIENT_ID="..."
export LINKEDIN_CLIENT_SECRET="..."
export LINKEDIN_ACCESS_TOKEN="..."
export GNEWS_API_KEY="..."
export LANGFUSE_PUBLIC_KEY="..."
export LANGFUSE_SECRET_KEY="..."

# Deploy
./deploy/deploy.sh \
  --platform openshift \
  --environment prod \
  --namespace aifeeders

# Dry run first
./deploy/deploy.sh \
  --platform openshift \
  --environment prod \
  --namespace aifeeders \
  --dry-run
```

### EKS

```bash
export LLM_API_KEY="..." # (+ all others above)
export AWS_REGION="us-east-1"

./deploy/deploy.sh \
  --platform eks \
  --environment prod \
  --namespace aifeeders \
  --image-registry 123456789.dkr.ecr.us-east-1.amazonaws.com/aifeeders \
  --image-tag v1.2.3
```

### AKS

```bash
export LLM_API_KEY="..." # (+ all others above)

./deploy/deploy.sh \
  --platform aks \
  --environment prod \
  --namespace aifeeders \
  --image-registry myacr.azurecr.io/aifeeders \
  --image-tag v1.2.3
```

### Skip image build (re-deploy existing tag)

```bash
./deploy/deploy.sh \
  --platform openshift \
  --namespace aifeeders \
  --skip-build \
  --image-tag v1.2.3
```

### Helm only (no script)

```bash
helm upgrade --install aifeeders deploy/helm/aifeeders \
  -f deploy/environments/openshift/values.yaml \
  --namespace aifeeders \
  --create-namespace \
  --atomic \
  --set global.image.tag=latest \
  --set secrets.llmApiKey="${LLM_API_KEY}" \
  --set secrets.gnewsApiKey="${GNEWS_API_KEY}" \
  --set secrets.linkedinClientId="${LINKEDIN_CLIENT_ID}" \
  --set secrets.linkedinClientSecret="${LINKEDIN_CLIENT_SECRET}" \
  --set secrets.linkedinScopes="openid profile email w_member_social" \
  --set secrets.linkedinAccessToken="${LINKEDIN_ACCESS_TOKEN}" \
  --set secrets.linkedinRedirectUri="https://linkedin-mcp.apps.<cluster>/oauth/callback" \
  --set secrets.langfusePublicKey="${LANGFUSE_PUBLIC_KEY}" \
  --set secrets.langfuseSecretKey="${LANGFUSE_SECRET_KEY}" \
  --set secrets.mcpAuthToken="${MCP_AUTH_TOKEN}"
```

---

## Secret Management

### OpenShift — native secrets + External Secrets Operator

Secrets are managed by the Helm chart's [`secret.yaml`](helm/aifeeders/templates/secret.yaml) template
and injected at deploy time via `--set secrets.*`.

For automated pipelines, use the
[External Secrets Operator](https://external-secrets.io/) with an IBM Secrets Manager or
HashiCorp Vault backend:

```yaml
# ExternalSecret example
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: daily-news-secrets
  namespace: aifeeders
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: daily-news-secrets
  data:
    - secretKey: LLM_API_KEY
      remoteRef:
        key: aifeeders/prod
        property: llm_api_key
    # ... (repeat for all secret keys)
```

### EKS — AWS Secrets Manager + Secrets Store CSI Driver

1. Store secrets in AWS Secrets Manager under `aifeeders/prod`.
2. Install the [Secrets Store CSI Driver](https://secrets-store-csi-driver.sigs.k8s.io/) and AWS provider.
3. Create a `SecretProviderClass` that mounts the secrets as a Kubernetes secret named `daily-news-secrets`.
4. Pass `--skip-secrets` and let ESO manage secret sync instead of the Helm template.

Alternatively, use [AWS Secrets Manager + External Secrets Operator](https://external-secrets.io/latest/provider/aws-secrets-manager/).

### AKS — Azure Key Vault + CSI Driver

1. Store secrets in Azure Key Vault.
2. Install the [Secrets Store CSI Driver for Azure](https://azure.github.io/secrets-store-csi-driver-provider-azure/).
3. Create a `SecretProviderClass` with `objectType: secret` entries for each key.
4. The CSI driver syncs secrets into a Kubernetes secret named `daily-news-secrets`.

---

## LinkedIn Re-authorization

Access tokens expire after ~60 days. Follow these steps when the token expires or
if you receive `403 ACCESS_DENIED` on publishing calls.

### Step 1 — Check token status

```bash
# Via LinkedIn MCP OAuth status endpoint
curl -sf https://linkedin-mcp.apps.<cluster>/oauth/status
```

### Step 2 — Initiate re-authorization

Open the OAuth flow in a browser:

```
https://linkedin-mcp.apps.<cluster>/oauth/start
```

This redirects to LinkedIn's authorization page. Log in and approve the requested scopes.
**Ensure `w_member_social` is listed in the scope consent screen** — without it, all
post and comment calls return `403 ACCESS_DENIED`.

### Step 3 — Confirm scopes on the new token

After the OAuth callback completes, call:

```bash
curl -sf https://linkedin-mcp.apps.<cluster>/oauth/status | python3 -m json.tool
```

Verify the response includes `"w_member_social"` in the granted scopes list.

### Step 4 — Rotate the secret

```bash
# Extract the new token from the OAuth callback (stored in the pod)
NEW_TOKEN="$(oc exec -n aifeeders deploy/linkedin-mcp -- printenv LINKEDIN_ACCESS_TOKEN)"

# Update the Kubernetes secret
kubectl create secret generic daily-news-secrets \
  -n aifeeders \
  --from-literal=LINKEDIN_ACCESS_TOKEN="${NEW_TOKEN}" \
  --dry-run=client -o yaml | kubectl apply -f -

# Or re-run the full deploy with the new token
export LINKEDIN_ACCESS_TOKEN="${NEW_TOKEN}"
./deploy/deploy.sh --platform openshift --namespace aifeeders --skip-build
```

---

## Troubleshooting

### 403 ACCESS_DENIED on LinkedIn posts/comments

**Symptom:** `linkedin_create_post` or `linkedin_comment` returns HTTP 403 with `ACCESS_DENIED`.

**Root cause:** The access token was issued without the `w_member_social` scope.  
This happens when the OAuth flow was completed before `w_member_social` was added
to the LinkedIn app's approved products, or the scope was omitted from the authorization URL.

**Fix:**
1. In the [LinkedIn Developer Portal](https://www.linkedin.com/developers/apps) → your app →
   **Products** tab → request **Share on LinkedIn** (grants `w_member_social`).
2. Wait for approval (usually instant for personal apps).
3. Re-authorize via `GET /oauth/start` to get a new token with the correct scope.
4. Rotate the secret (see [LinkedIn Re-authorization](#linkedin-re-authorization) above).

---

### Pods stuck in `ImagePullBackOff`

```bash
kubectl describe pod -n aifeeders <pod-name>
```

- **OpenShift:** Verify the `ImageStream` and `BuildConfig` exist and the latest build succeeded.
- **EKS:** Check ECR permissions — the node's IAM role needs `ecr:GetDownloadUrlForLayer`, `ecr:BatchGetImage`.
- **AKS:** Ensure the AKS cluster's managed identity has `AcrPull` on the ACR.

---

### CronJob not triggering

```bash
kubectl get cronjob daily-ai-news -n aifeeders
kubectl get jobs -n aifeeders --sort-by=.metadata.creationTimestamp | tail -5
```

- Verify `PUBLISHING_ENABLED` is `"true"` in the ConfigMap.
- Check `concurrencyPolicy: Forbid` — if the previous job is still running, the new one is skipped.
- `activeDeadlineSeconds: 7200` (2 hours) — if the job takes longer, it will be killed.

---

### `helm upgrade` fails with `required ... is required`

One or more `--set secrets.*` values were not passed. Ensure all variables listed in
[Required Environment Variables](#required-environment-variables) are exported before running the script.

---

### NetworkPolicy blocking MCP calls

The `allow-api-to-mcps` NetworkPolicy allows only pods with label `app: daily-news-api`
or `app: daily-news-worker` to reach MCP servers on port 8000.

- API pods must have `app: daily-news-api`.
- CronJob pods must have `app: daily-news-worker` (set in [`cronjob.yaml`](helm/aifeeders/templates/cronjob.yaml)).
- Manually created debug pods will be blocked — add the label explicitly:
  ```bash
  kubectl run debug-pod --image=curlimages/curl:8.5.0 -n aifeeders \
    --labels="app=daily-news-worker" --rm -it -- sh
  ```

---

### Token introspection returns `active: false`

LinkedIn tokens become inactive if the member revokes access or the token expires.

```bash
# Introspect via LinkedIn MCP
curl -H "Authorization: Bearer ${MCP_AUTH_TOKEN}" \
  http://linkedin-mcp.aifeeders.svc.cluster.local:8000/mcp \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"linkedin_validate_token","arguments":{}}}'
```

If `active: false`, follow the [LinkedIn Re-authorization](#linkedin-re-authorization) steps.
