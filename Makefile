# =============================================================================
# AI Daily News Platform — Makefile
# =============================================================================

.PHONY: install dev lint test build push deploy clean

# ── Python env ──────────────────────────────────────────────────────────────
install:
	pip install -e ".[dev]"

# ── Local dev ────────────────────────────────────────────────────────────────
dev:
	docker compose up --build

dev-api:
	uvicorn daily_news.api.main:app --reload --port 8000

dev-news-mcp:
	python -m mcp_servers.news_mcp.server

dev-pageindex-mcp:
	python -m mcp_servers.pageindex_mcp.server

dev-evaluation-mcp:
	python -m mcp_servers.evaluation_mcp.server

dev-linkedin-mcp:
	python -m mcp_servers.linkedin_mcp.server

# ── Quality ──────────────────────────────────────────────────────────────────
lint:
	ruff check src mcp_servers tests
	ruff format --check src mcp_servers tests

format:
	ruff format src mcp_servers tests

typecheck:
	mypy src mcp_servers

# ── Tests ─────────────────────────────────────────────────────────────────────
test:
	pytest tests/unit -v

test-integration:
	pytest tests/integration -v

test-mcp:
	pytest tests/mcp -v

test-workflow:
	pytest tests/workflow -v

test-all:
	pytest tests/ -v

# ── Container build ─────────────────────────────────────────────────────────
IMAGE_REGISTRY ?= image-registry.openshift-image-registry.svc:5000
IMAGE_NAMESPACE ?= ai-news-prod
IMAGE_TAG       ?= latest

build:
	docker build -t $(IMAGE_REGISTRY)/$(IMAGE_NAMESPACE)/daily-news:$(IMAGE_TAG) .

build-news-mcp:
	docker build -f mcp_servers/news_mcp/Dockerfile \
	  -t $(IMAGE_REGISTRY)/$(IMAGE_NAMESPACE)/news-mcp:$(IMAGE_TAG) .

build-pageindex-mcp:
	docker build -f mcp_servers/pageindex_mcp/Dockerfile \
	  -t $(IMAGE_REGISTRY)/$(IMAGE_NAMESPACE)/pageindex-mcp:$(IMAGE_TAG) .

build-evaluation-mcp:
	docker build -f mcp_servers/evaluation_mcp/Dockerfile \
	  -t $(IMAGE_REGISTRY)/$(IMAGE_NAMESPACE)/evaluation-mcp:$(IMAGE_TAG) .

build-linkedin-mcp:
	docker build -f mcp_servers/linkedin_mcp/Dockerfile \
	  -t $(IMAGE_REGISTRY)/$(IMAGE_NAMESPACE)/linkedin-mcp:$(IMAGE_TAG) .

build-all: build build-news-mcp build-pageindex-mcp build-evaluation-mcp build-linkedin-mcp

push-all:
	docker push $(IMAGE_REGISTRY)/$(IMAGE_NAMESPACE)/daily-news:$(IMAGE_TAG)
	docker push $(IMAGE_REGISTRY)/$(IMAGE_NAMESPACE)/news-mcp:$(IMAGE_TAG)
	docker push $(IMAGE_REGISTRY)/$(IMAGE_NAMESPACE)/pageindex-mcp:$(IMAGE_TAG)
	docker push $(IMAGE_REGISTRY)/$(IMAGE_NAMESPACE)/evaluation-mcp:$(IMAGE_TAG)
	docker push $(IMAGE_REGISTRY)/$(IMAGE_NAMESPACE)/linkedin-mcp:$(IMAGE_TAG)

# ── OpenShift deploy ─────────────────────────────────────────────────────────
deploy-namespace:
	oc apply -f openshift/namespace.yaml

deploy-config:
	oc apply -f openshift/configmap.yaml
	oc apply -f openshift/rbac.yaml
	oc apply -f openshift/networkpolicy.yaml

deploy-mcps:
	oc apply -f openshift/news-mcp/
	oc apply -f openshift/pageindex-mcp/
	oc apply -f openshift/evaluation-mcp/
	oc apply -f openshift/linkedin-mcp/

deploy-api:
	oc apply -f openshift/api/

deploy-cronjob:
	oc apply -f openshift/cronjob.yaml

deploy-all: deploy-namespace deploy-config deploy-mcps deploy-api deploy-cronjob

# ── Verify MCP servers and APIs ───────────────────────────────────────────────
verify:
	python scripts/verify_mcp.py --dry-run

verify-full:
	python scripts/verify_mcp.py

verify-publish:
	python scripts/verify_mcp.py --publish

# ── Workflow (manual run) ─────────────────────────────────────────────────────
run-workflow:
	python -m daily_news.workflow_runner

# ── Clean ─────────────────────────────────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .mypy_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
	find . -name "*.pyc" -delete
