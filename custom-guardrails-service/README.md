# Custom LLM judge for Unity AI Gateway

Profile example: `DEFAULT`

## Goal

A **custom LLM judge service** executed by Unity AI Gateway **LLM-as-a-Judge** service policies.

Start with [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the problem,
runtime topology, and the external-FastAPI option. The detailed external
integration guide is [docs/external-fastapi-uaig-guide.md](docs/external-fastapi-uaig-guide.md).

## Stand up on a fresh workspace

Follow **[docs/DEPLOY.md](docs/DEPLOY.md)** — Apps CLI for the judge, then:

```bash
cp deploy/config.example.yaml deploy/config.yaml
# edit profile / catalog / destination / dry_run
export JUDGE_PROVIDER_TOKEN='<durable-sp-pat>'

databricks apps deploy guardrail-judge --profile <profile>
python deploy/bootstrap.py --config deploy/config.yaml
python deploy/smoke_test.py --config deploy/config.yaml
```

## Architecture

```text
Client → guardrail_pilot
            │  invoke_llm_judge (pre_call, post_call)
            ▼
         guardrail_judge  (Evaluator Model Service)
            │
            ▼
         guardrail_judge_provider  (CUSTOM)
            │
            ▼
         Databricks App guardrail-judge
         returns {"flagged","confidence","reason"}

         chat destination (example): system.ai.databricks-glm-5-2
```

## Repo layout

| Path | Purpose |
|---|---|
| `app.py`, `judge/` | FastAPI OpenAI-compatible judge |
| `app.yaml` | Databricks App process config |
| `deploy/` | Idempotent UC bootstrap + smoke |
| `scripts/refresh_provider_token.py` | Refresh provider bearer if using CLI OAuth |
| `policies/` | Desired-state snapshots (docs; bootstrap applies live) |
| `eval/` | Labeled cases + persona traffic results |

## Auth caveat

The CUSTOM provider stores a bearer used to call the Databricks App edge.

- Prefer a **service principal PAT** via `JUDGE_PROVIDER_TOKEN`
- CLI OAuth tokens expire (~1h) and break the evaluator until refreshed

```bash
python scripts/refresh_provider_token.py --config deploy/config.yaml
```

## Local development

```bash
cd custom-guardrails-service
source .venv/bin/activate
export JUDGE_API_KEY=  # empty when relying on App-edge Databricks auth
uvicorn app:app --port 8080
pytest -q
```
