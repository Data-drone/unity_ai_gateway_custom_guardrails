# Deploy runbook — Custom LLM judge on Unity AI Gateway

Fresh laptop + fresh workspace. No Terraform. Python SDK/REST bootstrap + Apps CLI.

## What this repo deploys

| Layer | Resource | How |
|---|---|---|
| Judge service | Databricks App (`app.name`) | `databricks apps deploy` |
| CUSTOM provider | `….<provider.id>` | `deploy/bootstrap.py` |
| Evaluator Model Service | `….<evaluator.id>` | `deploy/bootstrap.py` |
| Pilot Model Service + policy | `….<pilot.id>` + `pilot_safety_judge` | `deploy/bootstrap.py` |

Desired-state JSON snapshots live under `policies/` (documentation; bootstrap is authoritative).

## Prerequisites

1. Databricks CLI installed and on `PATH`
2. Python 3.10+
3. UAIG / Service Policies beta available in the target workspace
4. A catalog you can write to (default: `brian_agent_governance`)
5. A pay-per-token destination that exists as a UC Model Service destination  
   (default: `system.ai.databricks-glm-5-2` — verify in your workspace)
6. **Durable auth for the App edge (recommended):** a service principal PAT that can call the Databricks App. Put it in env `JUDGE_PROVIDER_TOKEN`.  
   Without this, bootstrap falls back to CLI OAuth (~1h expiry).

## Execution order

### 0. Auth + config

```bash
databricks auth login --profile <profile>

cd custom-guardrails-service
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp deploy/config.example.yaml deploy/config.yaml
# edit: profile, catalog, schema, destination, dry_run
export JUDGE_PROVIDER_TOKEN='<sp-pat-or-long-lived-bearer>'
```

### 1. Local unit tests

```bash
pytest -q
```

### 2. Deploy the App

From this directory (contains `app.yaml` + FastAPI app):

```bash
databricks apps deploy guardrail-judge --profile <profile>
databricks apps get guardrail-judge --profile <profile> -o json
```

Confirm the App is `RUNNING` and note its `url`.

Optional direct App smoke (with a Databricks bearer):

```bash
# POST {url}/v1/chat/completions with messages; expect JSON {"flagged":...}
```

### 3. Bootstrap UC wiring

```bash
python deploy/bootstrap.py --config deploy/config.yaml
```

Internal order (do not reorder):

1. Ensure schema  
2. Create/update CUSTOM provider (`base_url` = `{app_url}/v1/chat/completions`)  
3. Create/update evaluator Model Service → provider  
4. Create/update pilot Model Service → destination + `invoke_llm_judge` policy  

### 4. Smoke

```bash
python deploy/smoke_test.py --config deploy/config.yaml
```

Expect:

- **allow** prompt → HTTP 200  
- **deny** phishing prompt → HTTP 400 blocked by `pilot_safety_judge`  
  (if `dry_run: true`, deny may return 200 — that is expected)

### 5. Enforce

1. Set `policy.dry_run: false` in `deploy/config.yaml`
2. Re-run `python deploy/bootstrap.py --config deploy/config.yaml`
3. Re-run `python deploy/smoke_test.py --config deploy/config.yaml`

### 6. Day-2: token refresh (only if using CLI OAuth)

```bash
python scripts/refresh_provider_token.py --config deploy/config.yaml
```

Prefer fixing auth with a durable SP PAT instead of refreshing OAuth every hour.

## Layout

```text
deploy/
  config.example.yaml   # copy → config.yaml
  common.py             # auth + UC HTTP helpers
  bootstrap.py          # idempotent wiring
  smoke_test.py         # allow/deny gate
scripts/
  refresh_provider_token.py
policies/               # desired-state snapshots
docs/DEPLOY.md          # this file
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Evaluator / policy `302` | App edge auth failed / expired provider token | Set `JUDGE_PROVIDER_TOKEN` or run refresh script |
| `provider service(s) not live` | Provider deleted/recreated; MS stale | Re-run bootstrap (updates evaluator binding) |
| Destination model does not exist | FM not registered as UC destination | Pick a destination that works in this workspace (see config comment) |
| Deny returns 200 | `dry_run: true` | Flip to `false` after calibration |
| App URL 404 on provider | `base_url` missing `/v1/chat/completions` | Bootstrap always appends it; don't strip it manually |

## Out of scope (by design)

- Terraform for UC model services / providers / policies (no first-class resources yet)
- Full persona eval suite as a deploy gate (see `eval/` for manual calibration)
