# Architecture overview: custom guardrails for Unity AI Gateway

This project lets a team put a consistent safety decision in front of a
Databricks-hosted foundation model without embedding guardrail logic in every
client application. It is designed for a banking-assistant pilot, but the
pattern is reusable for other policies.

## The problem it solves

Without this layer, every application that calls an LLM must decide for itself
which prompts or responses are unsafe. That produces duplicated rules,
inconsistent enforcement, and no safe way to move from observation to blocking.

This architecture centralises the decision:

- applications call one protected **Pilot Model Service**;
- Unity AI Gateway evaluates the configured policy before and/or after the
  model call;
- a dedicated judge returns a small, auditable decision:
  `flagged`, `confidence`, and `reason`;
- the gateway either continues to the foundation-model destination or blocks
  the request.

The initial stack ships two judges behind the same FastAPI contract:

- a **deterministic** fraud/phishing regex engine in `judge/engine.py`;
- an **LLM** personal financial advice judge in `judge/llm_engine.py` (Haiku /
  GPT-mini class via OpenAI-compatible serving).

Both return `flagged`, `confidence`, and `reason`. Gateway topology stays the
same when you add or swap judges; only provider targets, evaluators, and
service policies change.

## Runtime architecture

```text
Client / agent
  |
  | OpenAI-compatible chat request
  v
Pilot Model Service (Unity AI Gateway)
  |  destination: a governed foundation model
  |  policies: system.ai.invoke_llm_judge (rank 10 fraud + rank 20 advice)
  |
  +-- pre_call and/or post_call --> Evaluator Model Services
                                      |
                                      | routes to a CUSTOM provider
                                      | (model = guardrail-judge | guardrail-judge-llm)
                                      v
                                  Judge FastAPI service
                                      |
                                      | OpenAI-compatible response;
                                      | assistant content is decision JSON
                                      v
                           {"flagged": true|false,
                            "confidence": 0..1, "reason": "..."}

If flagged and dry_run=false: Gateway blocks the protected request.
Otherwise: Gateway calls (or returns from) the foundation-model destination.
```

### What each component owns

| Component | Responsibility |
|---|---|
| Client / agent | Calls the pilot service only; it does not call the destination directly. |
| Pilot Model Service | Public, governed model entry point. Holds routing to the destination and the service policies. |
| `invoke_llm_judge` policies | Decide when to inspect traffic (`pre_call`, `post_call`), pass the policy instruction to the evaluator, and apply `block` when enforcement is enabled. Rank 10 = fraud regex; rank 20 = financial advice LLM. |
| Evaluator Model Services | Stable Databricks routing objects for each judge target (`guardrail-judge`, `guardrail-judge-llm`). |
| CUSTOM provider | Stores the judge URL and credential, and translates evaluator calls into OpenAI Chat Completions requests for each target model. |
| FastAPI judge | Authenticates the provider request, normalizes Chat Completions/Responses input, dispatches by model name, and returns the decision in an OpenAI-compatible envelope. |
| Foundation-model destination | The actual LLM that answers allowed requests, such as a `system.ai.*` pay-per-token model. |
| Mini model (LLM judge only) | Databricks foundation / serving endpoint. Auth is the **App service principal** with `CAN_QUERY` on a declared `serving-endpoint` resource. Optional `LLM_JUDGE_API_KEY` is local/dev only. |

## How the pieces fit together

The bootstrap script creates and updates the Databricks objects in dependency
order:

1. A Unity Catalog schema namespaces the resources.
2. A CUSTOM provider records the judge's full
   `https://host/v1/chat/completions` URL and bearer secret, with targets for
   both `guardrail-judge` and `guardrail-judge-llm` when `llm_policy` is set.
3. Evaluator Model Services route to that provider (one per judge target).
4. The pilot Model Service routes to the foundation-model destination and owns
   the `invoke_llm_judge` policies (fraud + optional financial advice).

At request time, the order reverses from the client's perspective: client →
pilot → evaluator/provider → judge → pilot decision → destination (when
allowed). The client sees a normal model response for an allowed request and a
policy-attributed error for a blocked one.

Start with `policy.dry_run: true` and `llm_policy.dry_run: true`. The judges
are called and can be measured, but they cannot interrupt callers. After
evaluating representative labelled traffic, set each to `false` and rerun
bootstrap to enforce.

## Judge API contract

The service exposes `POST /v1/chat/completions` (with aliases for convenience)
and accepts an OpenAI-shaped request. It returns a normal chat completion; the
first assistant message's `content` is a JSON string such as:

```json
{"flagged": true, "confidence": 0.95, "reason": "Request asks for phishing guidance."}
```

This envelope is important: a bare `{"flagged": true}` response is not an
OpenAI Chat Completions response and should not be used as the provider target.

Dispatch:

| Request `model` | Engine | Criteria usage |
|---|---|---|
| `guardrail-judge` | Deterministic regex (`judge/engine.py`) | Gateway instruction is guidance; matching is hard-coded |
| `guardrail-judge-llm` | Mini LLM (`judge/llm_engine.py`) | Gateway `instruction` is the system prompt (personal financial advice boundary) |

Both paths fail closed for empty input or internal faults. The LLM path also
fails closed on timeout, HTTP errors, missing serving URL / App identity, or
unparseable model JSON. The financial-advice judge is an **operational pilot
heuristic**, not a legal determination under the Corporations Act / ASIC RG 255.

On Databricks Apps, configure the mini model by adding a **Serving endpoint**
resource with **Can query**, inject it as `SERVING_ENDPOINT` (`valueFrom:
serving-endpoint`), and optionally set `AI_GATEWAY_URL`. Do not rely on a
separate model API key in production.

## Hosting options

The architecture above is unchanged by where FastAPI runs. Only the CUSTOM
provider's URL and credential change.

| | Databricks App (repository default) | External FastAPI |
|---|---|---|
| Judge host | Databricks Apps | Existing Kubernetes, ECS, VM, API gateway, or on-prem service |
| Provider URL | Discovered App URL + `/v1/chat/completions` | Your full HTTPS URL + `/v1/chat/completions` |
| Provider credential | Databricks service-principal PAT is preferred | A dedicated long-lived `JUDGE_API_KEY` from your secret manager |
| FastAPI application code | This repository's `app.py` and `judge/` | The same code/contract, or an adapter around an existing service |
| Evaluator, pilot, policy, and destination | Same | Same |

## Adapting to FastAPI outside Databricks

You do **not** need to migrate an existing FastAPI service into Databricks.
Expose an HTTPS endpoint that Databricks can reach and keep the judge contract
above.

1. Deploy the service behind TLS and make `POST /v1/chat/completions`
   reachable from the Databricks workspace (public ingress with WAF/IP controls
   or approved private connectivity).
2. Set a long, random `JUDGE_API_KEY` in the external service's secret store.
   Require `Authorization: Bearer <key>` on judge routes.
3. Put that identical value in the shell only while running bootstrap, for
   example `JUDGE_PROVIDER_TOKEN`. This is the `api_key` stored in the
   CUSTOM provider. Do not use a personal or short-lived CLI OAuth token.
4. Set `app.url` in `deploy/config.yaml` to the external service origin or its
   full chat-completions URL. The bootstrap normalizes it to the required full
   `/v1/chat/completions` path; `app.name` may remain as an identifier but no
   Databricks App deployment is needed when `app.url` is set.
5. Run `python deploy/bootstrap.py --config deploy/config.yaml`, first with
   `policy.dry_run: true`; then use `deploy/smoke_test.py` and the labelled
   cases under `eval/` before enforcing.

For an external service, provider-to-judge authentication is a service secret
you control. Databricks still requires a separately selected CLI profile for
bootstrap authentication, but that CLI token is not the credential the
provider should send to your service.

See [external-fastapi-uaig-guide.md](external-fastapi-uaig-guide.md) for the
detailed setup, network/security checklist, and troubleshooting. See
[DEPLOY.md](DEPLOY.md) for the Databricks Apps deployment runbook.

## Operational boundaries

- Keep clients on the pilot Model Service; direct calls to the destination
  bypass this guardrail topology.
- Protect and rotate the provider-to-judge secret. Do not log bearer tokens or
  unredacted sensitive prompts.
- Monitor judge availability and latency: a broken evaluator can block or
  degrade protected traffic depending on policy configuration.
- Treat policy text, judge-code changes, and the `dry_run` switch as controlled
  production changes. Validate them against `eval/` cases and smoke tests.
