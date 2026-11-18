"""Blocks a git commit when a real secret would be committed.

Checks every staged file (or, with --all, every file git would add) for:
  - the real VALUES from your .env (API keys, tokens, IDs) and from secrets/*.json (tokens, private keys,
    client IDs/secrets, service-account e-mail)
  - common key shapes (OpenAI sk-..., Google AIza.../ya29.../1//..., Facebook EAA..., private keys, GitHub tokens)
  - files that must never be committed (.env, secrets/, token/credential JSON)
Prints only WHERE and WHAT KIND, never the secret itself.

Installed as a git hook by:  git config core.hooksPath .githooks
Run by hand:                 .venv\\Scripts\\python.exe scripts\\check_secrets.py --all
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRET_JSON_KEYS = {"private_key", "private_key_id", "client_email", "client_id", "client_secret", "refresh_token",
                    "token", "access_token", "project_id", "id_token"}
PATTERNS = {
    "OpenAI key": r"sk-[A-Za-z0-9_-]{20,}",
    "Google API key": r"AIza[0-9A-Za-z_-]{30,}",
    "Google access token": r"ya29\.[0-9A-Za-z_-]{20,}",
    "Google refresh token": r"1//0[0-9A-Za-z_-]{30,}",
    "Facebook/Instagram token": r"EAA[A-Za-z0-9]{40,}",
    "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "GitHub token": r"gh[pousr]_[A-Za-z0-9]{30,}",
}
NEVER = re.compile(r"(^|/)(\.env|secrets/.*|.*token.*\.json|.*credentials.*\.json|client_secret.*\.json|"
                   r"service_account.*\.json|cookies.*\.txt)$", re.I)


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8").stdout


def secret_values() -> dict:
    vals = {}
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" not in line or line.strip().startswith("#"):
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            # settings that are not secret: file paths, model names, hosts, browser names
            if len(v) < 8 or "/" in v or "\\" in v or v.startswith(("gpt-", "gemini-")) or v.endswith(".com") \
                    or k.endswith(("_FILE", "_MODEL", "_HOST", "_BROWSER", "_CLIENT", "_PROVIDER")):
                continue
            vals[f".env {k}"] = v

    def walk(obj, where):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in SECRET_JSON_KEYS and isinstance(v, str) and len(v) >= 8:
                    vals[f"{where} {k}"] = v
                walk(v, where)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, where)

    for f in (ROOT / "secrets").glob("*.json"):
        try:
            walk(json.loads(f.read_text(encoding="utf-8")), f"secrets/{f.name}")
        except (OSError, ValueError):
            pass
    return vals


def check(files) -> list:
    vals, problems = secret_values(), []
    for f in files:
        if NEVER.search(f.replace("\\", "/")) and not f.endswith(".env.example"):
            problems.append(f"{f}: this file must never be committed")
            continue
        p = ROOT / f
        try:
            text = p.read_text(encoding="utf-8", errors="ignore") if p.stat().st_size < 5_000_000 else ""
        except OSError:
            continue
        problems += [f"{f}: contains the value of {name}" for name, v in vals.items() if v in text]
        problems += [f"{f}: looks like a {name}" for name, rx in PATTERNS.items() if re.search(rx, text)]
    return problems


def main():
    if "--all" in sys.argv:
        files = _git("ls-files", "--cached", "--others", "--exclude-standard").splitlines()
    else:
        files = _git("diff", "--cached", "--name-only", "--diff-filter=ACM").splitlines()
    problems = check(files)
    for p in problems:
        print(f"[SECRET] {p}")
    if problems:
        print("Commit blocked: remove the secret (keep it in .env / secrets/, which git ignores).")
        return 1
    print(f"secret check: {len(files)} file(s) clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
