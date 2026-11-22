import hashlib
import json
from datetime import datetime, timezone

from app.config import RULE_VERSION


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def make_manifest(data, sample, elapsed_ms, *, records=None, prefix=None):
    input_sha256 = hashlib.sha256(canonical(data).encode()).hexdigest()
    scope = f"{prefix}:" if prefix else ""
    digest = hashlib.sha256(f"{RULE_VERSION}:{scope}{input_sha256}".encode()).hexdigest()
    return {
        "digest": digest, "input_sha256": input_sha256, "version": RULE_VERSION,
        "sample": sample, "elapsed_ms": elapsed_ms,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "timezone": "UTC", "records": len(data["receipts"]) if records is None else records,
    }
