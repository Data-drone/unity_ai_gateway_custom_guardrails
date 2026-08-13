"""Shared helpers for guardrail deploy scripts."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "PyYAML is required. Run: pip install -r requirements.txt"
    ) from exc

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "deploy" / "config.yaml"
EXAMPLE_CONFIG = ROOT / "deploy" / "config.example.yaml"


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    if not cfg_path.exists():
        raise SystemExit(
            f"Missing config: {cfg_path}\n"
            f"Copy {EXAMPLE_CONFIG} → {DEFAULT_CONFIG} and edit it."
        )
    with cfg_path.open() as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise SystemExit(f"Config must be a mapping: {cfg_path}")
    return data


def resolve_auth(profile: str) -> tuple[str, str, str]:
    """Return (host, workspace_id, access_token) for a CLI profile."""
    describe = json.loads(
        subprocess.check_output(
            ["databricks", "auth", "describe", "--profile", profile, "-o", "json"],
            text=True,
        )
    )
    details = describe.get("details") or {}
    host = (details.get("host") or "").rstrip("/")
    conf = details.get("configuration") or {}
    workspace_id = ((conf.get("workspace_id") or {}).get("value")) or ""
    if not host or not workspace_id:
        raise SystemExit(
            f"Could not resolve host/workspace_id for profile {profile!r}. "
            "Run: databricks auth login --profile <profile>"
        )
    token = json.loads(
        subprocess.check_output(
            ["databricks", "auth", "token", "--profile", profile, "-o", "json"],
            text=True,
        )
    )["access_token"]
    return host, str(workspace_id), token


def resolve_provider_token(cfg: dict[str, Any], cli_token: str) -> tuple[str, str]:
    """Return (token, source_label). Prefer durable env PAT over CLI OAuth."""
    env_name = ((cfg.get("provider") or {}).get("api_key_env")) or "JUDGE_PROVIDER_TOKEN"
    env_tok = os.environ.get(env_name, "").strip()
    if env_tok:
        return env_tok, f"env:{env_name}"
    plaintext = ((cfg.get("provider") or {}).get("api_key_plaintext") or "").strip()
    if plaintext and plaintext != "REPLACE_WITH_DATABRICKS_BEARER_TOKEN":
        return plaintext, "config.provider.api_key_plaintext"
    return cli_token, "cli-oauth (expires ~1h — set durable PAT via api_key_env)"


def discover_app_url(profile: str, app_name: str, explicit: str | None = None) -> str:
    if explicit:
        return explicit.rstrip("/")
    raw = subprocess.check_output(
        ["databricks", "apps", "get", app_name, "--profile", profile, "-o", "json"],
        text=True,
    )
    url = (json.loads(raw).get("url") or "").rstrip("/")
    if not url:
        raise SystemExit(
            f"App {app_name!r} has no URL. Deploy it first:\n"
            f"  databricks apps deploy {app_name} --profile {profile}"
        )
    return url


def chat_completions_url(app_url: str) -> str:
    base = app_url.rstrip("/")
    if base.endswith("/v1/chat/completions"):
        return base
    return f"{base}/v1/chat/completions"


class UcClient:
    def __init__(self, host: str, workspace_id: str, token: str):
        self.host = host.rstrip("/")
        self.workspace_id = workspace_id
        self.token = token

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout: int = 90,
    ) -> tuple[int, Any]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "x-databricks-workspace-id": self.workspace_id,
        }
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            self.host + path, data=data, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode()
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            try:
                parsed: Any = json.loads(raw) if raw else {"message": raw}
            except json.JSONDecodeError:
                parsed = {"message": raw}
            return e.code, parsed

    def get(self, path: str) -> tuple[int, Any]:
        return self.request("GET", path)

    def post(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        return self.request("POST", path, body)

    def patch(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        return self.request("PATCH", path, body)

    def delete(self, path: str) -> tuple[int, Any]:
        return self.request("DELETE", path)


def fq_provider(cfg: dict[str, Any]) -> str:
    return f"{cfg['catalog']}.{cfg['schema']}.{cfg['provider']['id']}"


def fq_evaluator(cfg: dict[str, Any]) -> str:
    return f"{cfg['catalog']}.{cfg['schema']}.{cfg['evaluator']['id']}"


def fq_pilot(cfg: dict[str, Any]) -> str:
    return f"{cfg['catalog']}.{cfg['schema']}.{cfg['pilot']['id']}"


def policy_options(cfg: dict[str, Any]) -> dict[str, str]:
    pol = cfg["policy"]
    dry = pol.get("dry_run", True)
    dry_s = "true" if dry in (True, "true", "True", 1) else "false"
    return {
        "action": str(pol.get("action", "block")),
        "dry_run": dry_s,
        "instruction": str(pol["instruction"]).strip(),
        "model_service": f"model-services/{fq_evaluator(cfg)}",
        "phases": str(pol.get("phases", "pre_call,post_call")),
    }
