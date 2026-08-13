#!/usr/bin/env python3
"""Refresh CUSTOM provider api_key with a Databricks bearer token.

Prefer a durable SP PAT via JUDGE_PROVIDER_TOKEN (or config.provider.api_key_env).
Falls back to the current CLI OAuth access token (expires ~1h).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as scripts/refresh_provider_token.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))

from common import (  # noqa: E402
    UcClient,
    fq_provider,
    load_config,
    resolve_auth,
    resolve_provider_token,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None)
    parser.add_argument("--profile", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    profile = args.profile or cfg.get("profile")
    if not profile:
        raise SystemExit("Set profile in config or pass --profile")

    host, workspace_id, cli_token = resolve_auth(profile)
    api_key, source = resolve_provider_token(cfg, cli_token)
    if "cli-oauth" in source:
        print(f"WARNING: using {source}")

    client = UcClient(host, workspace_id, cli_token)
    name = fq_provider(cfg)
    path = f"/api/2.1/unity-catalog/model-provider-services/{name}"
    st, body = client.get(path)
    if not (200 <= st < 300):
        print(json.dumps(body, indent=2)[:1000], file=sys.stderr)
        raise SystemExit(f"GET provider failed: {st}")

    config = body.get("config") or {}
    custom = config.setdefault("custom", {}).setdefault("direct", {})
    if not custom.get("base_url"):
        raise SystemExit("Provider has no base_url — run bootstrap.py first")
    custom["api_key"] = {"plaintext": api_key}

    st2, body2 = client.patch(
        f"{path}?update_mask=config",
        {"config": config, "etag": body.get("etag")},
    )
    if not (200 <= st2 < 300):
        print(json.dumps(body2, indent=2)[:1000], file=sys.stderr)
        raise SystemExit(f"PATCH provider failed: {st2}")

    print(f"Refreshed api_key on {name} (source={source})")


if __name__ == "__main__":
    main()
