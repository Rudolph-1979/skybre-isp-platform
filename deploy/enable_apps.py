"""Register the apps added since build 32 in an existing settings.py / urls.py.

Despite the name (kept so the build 33 instructions still work), this now
enables both the `audit` app (build 33) and the `sales` app (build 34). It
is a superset of what it did before, so running the command from either
build's deploy notes does the right thing.

Why a patch script rather than shipping those two config files: the
deployed config is allowed to have diverged -- ALLOWED_HOSTS, CORS
origins, proxy flags, anything tuned on the box. Overwriting settings.py
to add four lines would take all of that with it, and the failure would
arrive as the whole platform refusing to start rather than as a missing
feature.

Safe to run more than once, and safe to run when only half of it has been
applied: every edit checks for itself first.

    python3 deploy/enable_apps.py backend/config     # from the repo root
"""
import re
import sys
from pathlib import Path

# (INSTALLED_APPS entry, url include or None) in the order they should be
# added. Order matters for the audit app -- it registers signal receivers
# in ready(), so it loads after the apps whose models it tracks.
APPS = [
    ("audit", 'path("api/", include("audit.urls")),'),
    ("sales", 'path("api/", include("sales.urls")),'),
]
MIDDLEWARE = '"audit.middleware.AuditActorMiddleware",'


def _append_into_block(text, block_name, line):
    match = re.search(rf"({block_name}\s*=\s*\[)(.*?)(\n\])", text, re.S)
    if not match:
        return None
    return text[: match.end(2)] + f"\n    {line}" + text[match.end(2) :]


def patch_settings(path: Path) -> list:
    text = path.read_text()
    done = []

    for app, _ in APPS:
        if f'"{app}"' in text:
            continue
        updated = _append_into_block(text, "INSTALLED_APPS", f'"{app}",')
        if updated is None:
            return ["FAILED: couldn't find INSTALLED_APPS"]
        text = updated
        done.append(f"INSTALLED_APPS += {app}")

    if "AuditActorMiddleware" not in text:
        # Last in MIDDLEWARE, i.e. innermost, so the request it binds is
        # still bound while the view runs.
        updated = _append_into_block(text, "MIDDLEWARE", MIDDLEWARE)
        if updated is None:
            return ["FAILED: couldn't find MIDDLEWARE"]
        text = updated
        done.append("MIDDLEWARE += AuditActorMiddleware")

    if not done:
        return ["settings.py: already up to date, nothing to do"]
    path.write_text(text)
    return [f"settings.py: {item}" for item in done]


def patch_urls(path: Path) -> list:
    text = path.read_text()
    done = []
    for app, include_line in APPS:
        if include_line is None or f'include("{app}.urls")' in text:
            continue
        matches = list(re.finditer(r'\n(\s*)path\("api/", include\("[a-z_]+\.urls"\)\),', text))
        if not matches:
            return ["FAILED: couldn't find the api url includes"]
        last = matches[-1]
        indent = last.group(1)
        text = text[: last.end()] + f"\n{indent}{include_line}" + text[last.end() :]
        done.append(f"urls.py: added the {app} API routes")
    if not done:
        return ["urls.py: already up to date, nothing to do"]
    path.write_text(text)
    return done


def main():
    base = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(__file__).resolve().parent.parent / "backend" / "config"
    )
    settings = base / "settings.py"
    urls = base / "urls.py"
    for f in (settings, urls):
        if not f.exists():
            print(f"FAILED: {f} not found. Pass the path to your config directory as an argument.")
            raise SystemExit(1)
    for line in patch_settings(settings) + patch_urls(urls):
        print(line)
        if line.startswith("FAILED"):
            raise SystemExit(1)
    print("Done. Rebuild the backend container, then run migrate.")


if __name__ == "__main__":
    main()
