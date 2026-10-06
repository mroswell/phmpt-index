"""Shared helpers for uploading FOIA documents to Cloudflare R2.

Reads credentials from the gitignored .r2.env at repo root. Never logs or
returns secret values. Public read is a bucket-level setting on R2 — we do
NOT pass per-object ACLs (R2 rejects them).
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".r2.env"
LEDGER = ROOT / "data" / "r2_uploads.json"

_REQUIRED = ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_PUBLIC_BASE"]


def load_env() -> dict:
    env = {}
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    env = {k: env.get(k) or os.environ.get(k) for k in _REQUIRED}
    missing = [k for k in _REQUIRED if not env[k]]
    if missing:
        raise SystemExit(f"Missing R2 credentials: {', '.join(missing)} (set them in {ENV})")
    return env


def client(env: dict):
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3",
        endpoint_url=f"https://{env['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=env["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=env["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(retries={"max_attempts": 10, "mode": "adaptive"}),
    )


def safe_name(name: str) -> str:
    """URL/console-safe object name: collapse anything outside [A-Za-z0-9._-]."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    return re.sub(r"_+", "_", s).strip("_") or "file"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_type(name: str) -> str:
    """So browsers view PDFs/docs inline instead of force-downloading them."""
    ct, _ = mimetypes.guess_type(name)
    return ct or "application/octet-stream"


def public_url(env: dict, key: str) -> str:
    return env["R2_PUBLIC_BASE"].rstrip("/") + "/" + key.lstrip("/")


def load_ledger() -> dict:
    if LEDGER.exists():
        return json.loads(LEDGER.read_text())
    return {"schema_version": 1, "individual": {}, "datasets": {}}


def save_ledger(ledger: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ledger, indent=2))
    os.replace(tmp, LEDGER)
