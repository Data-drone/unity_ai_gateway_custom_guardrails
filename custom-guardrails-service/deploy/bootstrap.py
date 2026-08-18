#!/usr/bin/env python3
"""Idempotent bootstrap for UAIG custom-judge wiring.

Order:
  1. Ensure schema
  2. CUSTOM provider → App /v1/chat/completions
  3. Evaluator Model Service → provider
  4. Pilot Model Service → OSS/FM destination + service policy
  5. Print summary

App deploy is a separate prerequisite:
  databricks apps deploy <app.name> --profile <profile>
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (
    UcClient,
    chat_completions_url,
    discover_app_url,
    fq_evaluator,
    fq_llm_evaluator,
    fq_pilot,
    fq_provider,
    has_llm_policy,
    llm_policy_options,
    llm_target_model,
    load_config,
    policy_options,
    resolve_auth,
    resolve_provider_token,
)


def _ok(status: int) -> bool:
    return 200 <= int(status) < 300


def _die(msg: str, payload: Any = None) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    if payload is not None:
        print(json.dumps(payload, indent=2)[:2000], file=sys.stderr)
    raise SystemExit(1)


def ensure_schema(client: UcClient, cfg: dict[str, Any]) -> None:
    catalog = cfg["catalog"]
    schema = cfg["schema"]
    path = f"/api/2.1/unity-catalog/schemas/{catalog}.{schema}"
    st, body = client.get(path)
    if _ok(st):
        print(f"[ok] schema {catalog}.{schema}")
        return
    if not cfg.get("bootstrap", {}).get("create_schema_if_missing", True):
        _die(f"Schema {catalog}.{schema} missing and create_schema_if_missing=false", body)
    st, body = client.post(
        "/api/2.1/unity-catalog/schemas",
        {"name": schema, "catalog_name": catalog, "comment": "Custom guardrail resources"},
    )
    if not _ok(st):
        _die(f"Failed to create schema {catalog}.{schema}", body)
    print(f"[created] schema {catalog}.{schema}")


def provider_config(cfg: dict[str, Any], base_url: str, api_key: str) -> dict[str, Any]:
    targets = [
        {
            "model": cfg["provider"]["target_model"],
            "native_api_types": ["openai/v1/chat/completions"],
        }
    ]
    if has_llm_policy(cfg):
        targets.append(
            {
                "model": llm_target_model(cfg),
                "native_api_types": ["openai/v1/chat/completions"],
            }
        )
    return {
        "provider_type": "EXTERNAL_MODEL_PROVIDER_TYPE_CUSTOM",
        "forward_unmanaged_paths": True,
        "allow_all_targets": False,
        "custom": {
            "direct": {
                "base_url": base_url,
                "api_key": {"plaintext": api_key},
            }
        },
        "targets": targets,
    }


def ensure_provider(
    client: UcClient, cfg: dict[str, Any], base_url: str, api_key: str
) -> str:
    name = fq_provider(cfg)
    parent = f"schemas/{cfg['catalog']}.{cfg['schema']}"
    get_path = f"/api/2.1/unity-catalog/model-provider-services/{name}"
    st, body = client.get(get_path)
    config = provider_config(cfg, base_url, api_key)
    comment = cfg["provider"].get("comment")

    if _ok(st):
        etag = body.get("etag")
        st2, body2 = client.patch(
            f"{get_path}?update_mask=config",
            {"config": config, "etag": etag, "comment": comment},
        )
        # Some workspaces reject comment on PATCH — retry config-only.
        if not _ok(st2) and "comment" in json.dumps(body2).lower():
            st2, body2 = client.patch(
                f"{get_path}?update_mask=config",
                {"config": config, "etag": etag},
            )
        if not _ok(st2):
            _die(f"Failed to update provider {name}", body2)
        print(f"[updated] provider {name}")
        return name

    st, body = client.post(
        f"/api/2.1/unity-catalog/model-provider-services"
        f"?parent={parent}&model_provider_service_id={cfg['provider']['id']}",
        {"config": config, "comment": comment},
    )
    if not _ok(st):
        _die(f"Failed to create provider {name}", body)
    print(f"[created] provider {name}")
    return name


def _evaluator_routing(cfg: dict[str, Any], target_model: str) -> dict[str, Any]:
    return {
        "routing": {
            "destinations": [
                {
                    "destination_type": "DESTINATION_TYPE_EXTERNAL_FOUNDATION_MODEL",
                    "name": target_model,
                    "traffic_percentage": 100,
                    "external_model_config": {
                        "model_provider_service": f"model-provider-services/{fq_provider(cfg)}",
                        "target": {
                            "model": target_model,
                            "native_api_types": ["openai/v1/chat/completions"],
                        },
                    },
                }
            ]
        }
    }


def evaluator_config(cfg: dict[str, Any]) -> dict[str, Any]:
    return _evaluator_routing(cfg, cfg["provider"]["target_model"])


def llm_evaluator_config(cfg: dict[str, Any]) -> dict[str, Any]:
    return _evaluator_routing(cfg, llm_target_model(cfg))


def _ensure_model_service(
    client: UcClient,
    *,
    fq_name: str,
    service_id: str,
    parent: str,
    config: dict[str, Any],
    comment: str | None,
    label: str,
) -> str:
    get_path = f"/api/2.1/unity-catalog/model-services/{fq_name}"
    st, body = client.get(get_path)

    if _ok(st):
        st2, body2 = client.patch(
            f"{get_path}?update_mask=config",
            {"config": config, "etag": body.get("etag"), "comment": comment},
        )
        if not _ok(st2):
            st2, body2 = client.patch(
                f"{get_path}?update_mask=config",
                {"config": config, "etag": body.get("etag")},
            )
        if not _ok(st2):
            _die(f"Failed to update {label} {fq_name}", body2)
        print(f"[updated] {label} {fq_name}")
        return fq_name

    st, body = client.post(
        f"/api/2.1/unity-catalog/model-services"
        f"?parent={parent}&model_service_id={service_id}",
        {"config": config, "comment": comment},
    )
    if not _ok(st):
        _die(f"Failed to create {label} {fq_name}", body)
    print(f"[created] {label} {fq_name}")
    return fq_name


def ensure_evaluator(client: UcClient, cfg: dict[str, Any]) -> str:
    return _ensure_model_service(
        client,
        fq_name=fq_evaluator(cfg),
        service_id=cfg["evaluator"]["id"],
        parent=f"schemas/{cfg['catalog']}.{cfg['schema']}",
        config=evaluator_config(cfg),
        comment=cfg["evaluator"].get("comment"),
        label="evaluator",
    )


def ensure_llm_evaluator(client: UcClient, cfg: dict[str, Any]) -> str:
    llm_eval = cfg.get("llm_evaluator") or {}
    service_id = llm_eval.get("id") or "guardrail_judge_llm"
    return _ensure_model_service(
        client,
        fq_name=fq_llm_evaluator(cfg),
        service_id=service_id,
        parent=f"schemas/{cfg['catalog']}.{cfg['schema']}",
        config=llm_evaluator_config(cfg),
        comment=llm_eval.get("comment") or "LLM financial-advice judge evaluator.",
        label="llm evaluator",
    )


def pilot_config(cfg: dict[str, Any]) -> dict[str, Any]:
    dest = cfg["pilot"]["destination"].strip()
    # Accept system.ai.X or bare X
    dest_name = dest if dest.startswith("system.ai.") else f"system.ai.{dest}"
    model_ref = f"models/{dest_name}"
    policies: list[dict[str, Any]] = [
        {
            "name": cfg["policy"]["name"],
            "policy_type": "POLICY_TYPE_BUILTIN",
            "handler": "system.ai.invoke_llm_judge",
            "rank": int(cfg["policy"].get("rank", 10)),
            "is_deleted": False,
            "options": policy_options(cfg),
        }
    ]
    if has_llm_policy(cfg):
        pol = cfg["llm_policy"]
        policies.append(
            {
                "name": pol["name"],
                "policy_type": "POLICY_TYPE_BUILTIN",
                "handler": "system.ai.invoke_llm_judge",
                "rank": int(pol.get("rank", 20)),
                "is_deleted": False,
                "options": llm_policy_options(cfg),
            }
        )
    return {
        "routing": {
            "destinations": [
                {
                    "destination_type": "DESTINATION_TYPE_PAY_PER_TOKEN_FOUNDATION_MODEL",
                    "name": dest_name,
                    "pay_per_token_config": {"model": model_ref},
                    "traffic_percentage": 100,
                }
            ]
        },
        "service_policies": policies,
    }


def ensure_pilot(client: UcClient, cfg: dict[str, Any]) -> str:
    name = fq_pilot(cfg)
    parent = f"schemas/{cfg['catalog']}.{cfg['schema']}"
    get_path = f"/api/2.1/unity-catalog/model-services/{name}"
    st, body = client.get(get_path)
    config = pilot_config(cfg)
    comment = cfg["pilot"].get("comment")

    if _ok(st):
        # Preserve inference-table settings already configured on the pilot.
        existing = (body.get("config") or {}).get("inference_table")
        if isinstance(existing, dict) and existing:
            # Drop read-only / derived fields that reject on write.
            preserved = {
                k: v
                for k, v in existing.items()
                if k
                in {
                    "disabled",
                    "table_name_prefix",
                    "parent",
                    "catalog_name",
                    "schema_name",
                }
                or (k == "is_deleted" and v is False)
            }
            if preserved:
                config["inference_table"] = preserved
        st2, body2 = client.patch(
            f"{get_path}?update_mask=config",
            {"config": config, "etag": body.get("etag"), "comment": comment},
        )
        if not _ok(st2):
            st2, body2 = client.patch(
                f"{get_path}?update_mask=config",
                {"config": config, "etag": body.get("etag")},
            )
        if not _ok(st2):
            _die(f"Failed to update pilot MS {name}", body2)
        print(f"[updated] pilot {name}")
        return name

    st, body = client.post(
        f"/api/2.1/unity-catalog/model-services"
        f"?parent={parent}&model_service_id={cfg['pilot']['id']}",
        {"config": config, "comment": comment},
    )
    if not _ok(st):
        _die(f"Failed to create pilot MS {name}", body)
    print(f"[created] pilot {name}")
    return name


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None, help="Path to deploy/config.yaml")
    parser.add_argument("--profile", default=None, help="Override config.profile")
    parser.add_argument(
        "--skip-schema",
        action="store_true",
        help="Do not create/check schema",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    profile = args.profile or cfg.get("profile")
    if not profile:
        _die("Set profile in config or pass --profile")

    host, workspace_id, cli_token = resolve_auth(profile)
    api_key, key_source = resolve_provider_token(cfg, cli_token)
    if "cli-oauth" in key_source:
        print(f"WARNING: provider api_key source = {key_source}")

    app_cfg = cfg.get("app") or {}
    app_url = discover_app_url(profile, app_cfg["name"], app_cfg.get("url"))
    base_url = chat_completions_url(app_url)
    print(f"[ok] app {app_cfg['name']} → {base_url}")

    client = UcClient(host, workspace_id, cli_token)

    if not args.skip_schema:
        ensure_schema(client, cfg)

    ensure_provider(client, cfg, base_url, api_key)
    ensure_evaluator(client, cfg)
    if has_llm_policy(cfg):
        ensure_llm_evaluator(client, cfg)
    ensure_pilot(client, cfg)

    settle = int((cfg.get("bootstrap") or {}).get("settle_seconds", 15))
    if settle > 0:
        print(f"[wait] settling {settle}s…")
        time.sleep(settle)

    dry = policy_options(cfg)["dry_run"]
    print("\n=== Bootstrap complete ===")
    print(f"profile:     {profile}")
    print(f"host:        {host}")
    print(f"provider:    {fq_provider(cfg)}")
    print(f"evaluator:   {fq_evaluator(cfg)}")
    if has_llm_policy(cfg):
        print(f"llm eval:    {fq_llm_evaluator(cfg)}")
    print(f"pilot:       {fq_pilot(cfg)}")
    print(f"destination: {cfg['pilot']['destination']}")
    print(f"policy:      {cfg['policy']['name']} (dry_run={dry})")
    if has_llm_policy(cfg):
        llm_dry = llm_policy_options(cfg)["dry_run"]
        print(f"llm policy:  {cfg['llm_policy']['name']} (dry_run={llm_dry})")
    print(f"api_key:     {key_source}")
    print("\nNext: python deploy/smoke_test.py --config <config>")


if __name__ == "__main__":
    main()
