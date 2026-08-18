# Unity AI Gateway — Custom Guardrails

Centralized LLM safety decisions for Databricks **Unity AI Gateway**, via a custom
OpenAI-compatible judge service used by **LLM-as-a-Judge** service policies.

Applications call one governed Pilot Model Service. Gateway runs pre/post-call
evaluation against a FastAPI judge that returns `flagged`, `confidence`, and
`reason`. When enforcement is on, unsafe traffic is blocked before (or after)
the foundation-model destination.

## Architecture (short)

```text
Client → Pilot Model Service
            │  invoke_llm_judge ×2 (fraud regex + financial-advice LLM)
            ▼
         Evaluator Model Services
            │
            ▼
         CUSTOM provider → Databricks App
            │                 ├ guardrail-judge     (regex fraud/phishing)
            │                 └ guardrail-judge-llm (mini-LLM advice boundary)
            └ returns {"flagged","confidence","reason"}

         Destination (example): system.ai.databricks-glm-5-2
```

Details: [custom-guardrails-service/docs/ARCHITECTURE.md](custom-guardrails-service/docs/ARCHITECTURE.md)

## Repository layout

| Path | Purpose |
|---|---|
| [`custom-guardrails-service/`](custom-guardrails-service/) | FastAPI judge, Databricks App config, UC bootstrap, policies, tests |
| [`custom-guardrails-service/docs/`](custom-guardrails-service/docs/) | Architecture, deploy runbook, external FastAPI / UAIG guide |
| [`custom-guardrails-service/deploy/`](custom-guardrails-service/deploy/) | Idempotent bootstrap + smoke tests |
| [`custom-guardrails-service/policies/`](custom-guardrails-service/policies/) | Desired-state policy snapshots (docs; bootstrap is authoritative) |
| [`custom-guardrails-service/eval/`](custom-guardrails-service/eval/) | Labeled cases and traffic eval artifacts |

## Quick start

### Prerequisites

- Python 3.10+
- Databricks CLI authenticated to the target workspace
- Unity AI Gateway / Service Policies available in that workspace
- A catalog you can write to, plus a foundation-model destination

### Local judge

```bash
cd custom-guardrails-service
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export JUDGE_API_KEY=   # empty for local App-edge-style auth convenience
uvicorn app:app --port 8080
pytest -q
```

### Deploy to a workspace

Full runbook: [custom-guardrails-service/docs/DEPLOY.md](custom-guardrails-service/docs/DEPLOY.md)

```bash
cd custom-guardrails-service
cp deploy/config.example.yaml deploy/config.yaml
# edit profile / catalog / destination / dry_run
export JUDGE_PROVIDER_TOKEN='<durable-sp-pat>'

databricks apps deploy guardrail-judge --profile <profile>
python deploy/bootstrap.py --config deploy/config.yaml
python deploy/smoke_test.py --config deploy/config.yaml
```

Prefer a **service principal PAT** for `JUDGE_PROVIDER_TOKEN`. CLI OAuth tokens
expire (~1h) and will break the evaluator until refreshed
(`scripts/refresh_provider_token.py`).

## Auth notes

- `JUDGE_API_KEY` — optional bearer expected by the FastAPI app when set
- `JUDGE_PROVIDER_TOKEN` — bearer the CUSTOM provider uses to call the App edge

Never commit `deploy/config.yaml` if it contains secrets (see `.gitignore`).

## Docs

| Doc | When to read |
|---|---|
| [Architecture](custom-guardrails-service/docs/ARCHITECTURE.md) | Problem, topology, component ownership |
| [Deploy](custom-guardrails-service/docs/DEPLOY.md) | Fresh workspace stand-up |
| [External FastAPI / UAIG guide](custom-guardrails-service/docs/external-fastapi-uaig-guide.md) | Integrating an external judge with Gateway |
| [Service README](custom-guardrails-service/README.md) | Day-to-day service commands |

## License

[MIT](LICENSE)
