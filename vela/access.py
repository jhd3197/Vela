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


# ---------------------------------------------------------------- quick unlock
#
# Quick unlock is reauthentication for one already authenticated session, not a
# second way to start one. Its verifier is salted and compared in constant time,
# exactly like the password, and the secret itself is never stored or logged.

PIN_LENGTH = 6
PATTERN_MIN_DOTS = 4
PATTERN_DOTS = 9

# The midpoint a straight move passes over, for the moves that have one. A 3 by
# 3 grid is small enough to state the rule directly: a move crosses a dot when
# both its rows and both its columns have the same parity.
_MIDPOINTS = {
    frozenset((a, b)): (a + b) // 2
    for a in range(PATTERN_DOTS)
    for b in range(PATTERN_DOTS)
    if a != b
    and (a // 3 + b // 3) % 2 == 0
    and (a % 3 + b % 3) % 2 == 0
}


def normalize_pattern(sequence):
    """Canonical dot order for a 3 by 3 pattern, or None when it is not valid.

    Mirrored by `web/src/pattern.js`. The server re-runs this on whatever the
    client sends, so a hand-written request cannot enroll a two-dot pattern.
    """
    if not isinstance(sequence, (list, tuple)) or not 2 <= len(sequence) <= PATTERN_DOTS:
        return None
    dots = []
    for value in sequence:
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        if not 0 <= value < PATTERN_DOTS or value in dots:
            return None
        previous = dots[-1] if dots else None
        if previous is not None:
            middle = _MIDPOINTS.get(frozenset((previous, value)))
            if middle is not None and middle not in dots:
                dots.append(middle)
        if value in dots:
            return None
        dots.append(value)
    return dots if len(dots) >= PATTERN_MIN_DOTS else None


def normalize_secret(method, secret):
    """The exact bytes a verifier is built from, or None when unacceptable."""
    if method == "pin":
        if not isinstance(secret, str) or len(secret) != PIN_LENGTH or not secret.isdecimal():
            return None
        # `isdecimal` accepts other scripts' digits; the stored form is ASCII so
        # a leading zero and an Arabic-Indic five cannot be two different PINs.
        if not all("0" <= character <= "9" for character in secret):
            return None
        return f"pin:{secret}".encode()
    if method == "pattern":
        dots = normalize_pattern(secret)
        return None if dots is None else ("pattern:" + "-".join(map(str, dots))).encode()
    return None


def build_verifier(method, secret):
    """`(salt, digest)` for an acceptable secret, or None. Memory only."""
    material = normalize_secret(method, secret)
    if material is None:
        return None
    salt = secrets.token_bytes(16)
    return salt, hashlib.scrypt(material, salt=salt, n=16384, r=8, p=1).hex()


def verify_secret(method, secret, salt, digest):
    material = normalize_secret(method, secret)
    if material is None:
        return False
    candidate = hashlib.scrypt(material, salt=salt, n=16384, r=8, p=1).hex()
    return secrets.compare_digest(candidate, digest)
