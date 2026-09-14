"""Password hashing for authenticated HTTPS clients; no plaintext password store."""
import hashlib
import json
import secrets
from pathlib import Path


def set_password(path: Path, password: str):
    if not 12 <= len(password) <= 256:
        raise ValueError("Use a password between 12 and 256 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"salt": salt.hex(), "hash": digest}), encoding="utf-8")
    temporary.replace(path)


def verify_password(path: Path, password: str):
    if not 12 <= len(password) <= 256: return False
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(record["salt"]), n=16384, r=8, p=1).hex()
        return secrets.compare_digest(digest, record["hash"])
    except (OSError, ValueError, KeyError):
        return False
