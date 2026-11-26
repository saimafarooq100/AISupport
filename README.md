# UOWD AI Support Assistant

The UOWD AI Support Assistant is a reproducible student-and-staff support system combining supervised department routing, permission-filtered RAG, controlled agent tools, and observable container deployment. The repository implements **Phases 1–5**: a UV-managed FastAPI foundation, independent department routers and RAG releases, an authenticated LangGraph/Ollama chatbot, and a Docker Compose/Prometheus/Grafana deployment.

## Demo video

Not available yet. The final submission will include a public, no-sign-in demo link.

## Prerequisites

- [UV](https://docs.astral.sh/uv/) 0.11.23 or compatible
- Git
- Docker Desktop or Docker Engine with Compose v2
- At least 15 GB free disk space for the unified image, service images, caches, and model weights
- macOS or Linux; Windows users can run the demo bootstrap in PowerShell and should use WSL
  for the other documented shell commands

UV installs the pinned Python 3.12 runtime when needed. PyCharm can use `.venv/bin/python` after synchronization.

## Set up and run the pipeline

From a clean clone:

```bash
uv sync --locked
```

The checked-in DVC pointers identify the exact synthetic source and evaluation datasets. The development remote is local and intentionally not committed, so a teammate can recreate the same source files from their fixed seeds:

```bash
uv run ai-support data generate --audience all --rows 600
uv run dvc add data/router/student/tickets.csv data/router/staff/tickets.csv
```

Run both router pipelines end to end, including deterministic generation, preprocessing, training, validation, MLflow tracking, candidate registration, and prediction output:

```bash
uv run ai-support routers run-all --generate --rows 600
```

Run the independent Metaflow workflows individually:

```bash
uv run ai-support router train --audience student
uv run ai-support router train --audience staff
```

Equivalent direct Metaflow commands are:

```bash
uv run python -m ai_support.pipelines.student_router_flow run
uv run python -m ai_support.pipelines.staff_router_flow run
```

Passing runs receive the `candidate` alias. Promotion to `champion` is deliberately manual:

```bash
uv run ai-support router approve --audience student --model-reference candidate
uv run ai-support router approve --audience staff --model-reference candidate
```

Start the API after approved champion versions exist:

```bash
uv run uvicorn ai_support.api.app:app --reload
```

Open `http://127.0.0.1:8000/docs` for its OpenAPI interface. Detailed pipeline, reproduction, and rollback instructions are in `docs/router-pipeline.md`.

## Build the RAG releases

The synthetic document collections and independent benchmark sets are deterministic and DVC-versioned:

```bash
uv run ai-support rag generate-sample-data --audience all
uv run dvc add data/rag/student data/rag/staff
```

Run the two audience-specific Metaflow workflows:

```bash
uv run ai-support rag build --audience student
uv run ai-support rag build --audience staff
```

The first real build downloads the configured, revision-pinned Sentence Transformers model; CI tests inject a deterministic local embedder and make no model download. Set `AI_SUPPORT_DATABASE_URL` to a PostgreSQL database with pgvector to persist an index. Without it, the experiment uses the in-memory repository and still evaluates and records a release manifest.

See `docs/rag-pipeline.md` for metadata, experiments, metrics, SQL setup, and release restoration.

## Run the authenticated chatbot

Copy `.env.example` to `.env`, replace the synthetic login code and session-secret placeholders, and export the variables into the shell running the API. The backend—not the Streamlit audience selector—verifies the identity and derives the available router, knowledge base, profile, and tools.

Start the required local services and an already-installed Ollama model. The application never downloads model weights automatically.

```bash
uv run uvicorn ai_support.api.app:app --reload
uv run streamlit run src/ai_support/ui/app.py
```

Synthetic demo identities are `S100001`/`S100002` for students and `E100001`/`E100002` for staff. All use the configured `AI_SUPPORT_DEMO_LOGIN_CODE`. The chatbot supports session chat history, cited retrieval answers, authenticated profiles, student course progress, reviewable department-email drafts, explicit Mailpit confirmation, human handoff, and helpful/not-helpful feedback.

Detailed security, tool, Ollama, Mailpit, and API instructions are in `docs/chatbot-agents.md`.

## Run tests and quality checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run dvc status
```

The automated suite trains small deterministic models and uses a temporary real MLflow SQLite registry for registration, alias, and version-loading tests. It mocks LLM responses and does not require Ollama or external model downloads. An opt-in real test uses an already-installed model:

```bash
AI_SUPPORT_TEST_OLLAMA_MODEL=llama3.1:latest \
  uv run pytest -q tests/agents/test_adapters.py::test_real_ollama_demo_model --no-cov
```

## Build and run the container

Copy `.env.example` to `.env` and replace all placeholder credentials. The complete clean-clone
bootstrap is one command. On macOS/Linux (or in WSL):

```bash
./scripts/bootstrap_demo.sh --pull-model
```

On Windows PowerShell:

```powershell
.\scripts\bootstrap_demo.ps1 -PullModel
```

`--pull-model` (Bash) or `-PullModel` (PowerShell) explicitly authorizes downloading the configured
Ollama model; omit it on a later verification when the model exists in the persistent volume.
Existing selected immutable RAG releases are reused and the acceptance probe confirms their database
indexes are still present. The command builds the images, starts infrastructure, trains and approves
both routers, builds both PostgreSQL RAG indexes, starts the application/monitoring
services, and runs an authenticated cited-answer acceptance probe. It stops immediately if a gate or
verification fails.

The lockfile-based API image runs as non-root UID/GID 10001. The chatbot is at
`http://127.0.0.1:8501`; FastAPI is at `http://127.0.0.1:8000`. Administrative services bind
only to loopback and PostgreSQL is not published. Every published host port has an
`AI_SUPPORT_*_PORT` override in `.env.example`, which permits an isolated second stack without
stopping an existing demo.

Runtime metrics are exposed at `/metrics`, scraped by Prometheus, and displayed in the
provisioned Grafana dashboard. Build the historical experiment catalog or measure a running
health endpoint with:

```bash
uv run ai-support catalog build
MLFLOW_DISABLE_AGENT_HINT=1 uv run ai-support evidence export --output docs/evidence
uv run ai-support benchmark-api --requests 100 \
  --output docs/performance-baseline.json
```

See `docs/operations-report.md` for the complete architecture, release, scalability, monitoring,
maintenance, security, cost, limitations, rollback, and reflection record. The shorter
`docs/deployment-monitoring.md` remains the operator quick reference.

## Call the service

Health request:

```bash
curl --fail http://127.0.0.1:8000/health
```

Authenticate first:

```bash
curl --fail -X POST http://127.0.0.1:8000/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"S100001","login_code":"your-synthetic-code"}'
```

Use the returned bearer token for router, RAG, chat, action-confirmation, and feedback requests. The server rejects a student token on staff routes and vice versa.

Router request:

```bash
curl --fail -X POST http://127.0.0.1:8000/v1/router/student/predict \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"ticket_subject":"Timetable missing","ticket_description":"I cannot see my class timetable"}'
```

Example response from the measured Phase 2 champion:

```json
{
  "audience": "student",
  "department": "Registry",
  "confidence": 0.851066861553833,
  "top_departments": [
    {"department": "Registry", "probability": 0.851066861553833},
    {"department": "IT", "probability": 0.04904855415621885},
    {"department": "Careers", "probability": 0.04454449506213565}
  ],
  "model_name": "student-department-router",
  "model_reference": "champion"
}
```

The RAG endpoint also requires the matching server-issued session:

```bash
curl --fail -X POST http://127.0.0.1:8000/v1/rag/student/search \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"query":"I cannot see my timetable","department":"Registry"}'
```

## Repository layout

```text
.
├── .dvc/                         # DVC project and local-remote configuration
├── .github/workflows/ci.yml      # Locked install, lint, and test automation
├── configs/rag/                  # Separate chunking/embedding/retrieval settings
├── configs/router/               # Separate student/staff model and gate settings
├── data/app/                     # Seeded synthetic profiles
├── data/rag/                     # DVC-versioned documents and benchmarks
├── data/router/                  # DVC pointers; CSV contents remain out of Git
├── deployment/                   # PostgreSQL, MinIO, Prometheus, and Grafana configuration
├── docs/
│   ├── architecture/overview.md  # Target architecture and phase boundaries
│   ├── chatbot-agents.md         # Authentication, tools, UI, Ollama, Mailpit
│   ├── rag-pipeline.md           # RAG experiments, releases, and restoration
│   ├── router-pipeline.md        # Execution, tracking, reproduction, rollback
│   ├── implementation-log.md     # Changes from the Assessment 2 design
│   ├── deployment-monitoring.md  # Compose, telemetry, performance, and rollback
│   ├── operations-report.md       # Complete Assessment 3 deployment/operations report
│   ├── evidence/                  # Tracked DVC, MLflow, Metaflow, and run provenance
│   └── reference/                # Supplied assessment PDFs
├── src/ai_support/
│   ├── api/                      # FastAPI contracts and endpoints
│   ├── agents/                   # LangGraph, tools, profiles, actions, adapters
│   ├── pipelines/                # Four independent Metaflow workflows
│   ├── rag/                      # Ingestion, chunking, retrieval, evaluation
│   ├── router/                   # Data, preprocessing, model, metrics, registry
│   ├── security/                 # Signed synthetic sessions
│   └── ui/                       # Streamlit app and API client
├── tests/agents/                 # Authorization, graph, adapter, and API tests
├── tests/deployment/             # Metrics, catalog, rollback, and performance helpers
├── tests/rag/                    # RAG policy, adapter, pipeline, and API tests
├── tests/router/                 # Router unit, API, and registry tests
├── scripts/bootstrap_demo.sh     # One-command clean-clone bootstrap and acceptance
├── pyproject.toml                # Dependencies and tool configuration
├── compose.yaml                  # Private-network assessment service stack
├── Dockerfile                    # UV-locked non-root application image
└── uv.lock                       # Reproducible dependency lock
```

## Tools used and why

- **UV** manages Python, environments, dependencies, commands, and the lockfile.
- **DVC** records source and chronological evaluation dataset hashes, with a local development remote.
- **Metaflow** makes ingestion, preprocessing, training, evaluation/gating, and registration visible as independent executable steps.
- **MLflow** records parameters, metrics, artifacts, provenance, model versions, and `candidate`/`champion` aliases.
- **scikit-learn** provides the explainable TF-IDF plus logistic-regression baseline.
- **sentence-transformers** provides revision-pinned dense embeddings and an optional CrossEncoder reranker.
- **PostgreSQL/pgvector** provides persistent dense search alongside PostgreSQL full-text search; RRF combines their ranked lists.
- **LangGraph** executes a controlled, allow-listed workflow; policy code, not the LLM, chooses tools.
- **Ollama** generates evidence-grounded text with a structured-output schema.
- **Streamlit** provides authenticated session chat, citations, confirmation, and feedback controls.
- **Mailpit SMTP** receives only confirmed messages addressed to synthetic `example.test` recipients.
- **FastAPI and Pydantic** expose typed authentication, routing, retrieval, chat, action, and feedback contracts.
- **pytest, HTTPX2, and Ruff** provide automated behavioral, integration, lint, formatting, and coverage checks.
- **Prometheus and Grafana** collect genuine runtime telemetry and provision a versioned dashboard.
- **Docker Compose** runs the assessment services with named storage and private backend networking.
- **GitHub Actions** repeats the locked installation, quality checks, Compose validation, real image build, non-root assertion, and container health smoke test on pushes and pull requests.

The assessment deployment is single-host and intentionally does not claim production TLS,
institutional SSO, managed secrets, multi-node availability, or automatic rollback.

## Team and contributions

- Saima Farooq: Assessment 2 solution architecture and project owner.
- Additional team members: add names and specific code, documentation, testing, demo, and presentation contributions before submission.

The Git history and this section must agree before the final assessment submission.
