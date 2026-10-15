"""
API keys for iconforge: the repo-root .env (KEY=value lines, git-ignored) first, then the process
environment. Values are never printed or logged; callers only learn whether a key is set.
(Copied from meridian-unreal/tools/aigen/env.py.)
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"


def _read_env_file() -> dict:
    out = {}
    if not ENV_FILE.exists():
        return out
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k] = v
    return out


def get(name: str):
    """The key's value, or None when it isn't set anywhere."""
    return _read_env_file().get(name) or os.environ.get(name) or None


def require(name: str) -> str:
    value = get(name)
    if not value:
        raise SystemExit("%s is not set: add it to %s (never commit it)" % (name, ENV_FILE))
    return value
