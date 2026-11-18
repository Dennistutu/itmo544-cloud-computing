#!/usr/bin/env python3
"""Add the final project's new settings to your existing .env, keeping every value you
already have. Lines that only the old RDS version used are reported, not deleted."""
from pathlib import Path

from stack.config import load_dotenv

OLD_ONLY = {"DB_INSTANCE_ID", "DB_INSTANCE_CLASS", "DB_ALLOCATED_STORAGE", "DB_PORT", "DB_NAME",
            "DB_USER", "DB_SG_NAME", "DB_SUBNET_GROUP_NAME", "DB_PASSWORD_PARAM", "CACHE_SECONDS"}


# Recommended values that changed after a live test. Updated only if you never edited them.
CHANGED = {"REPORT_LIMIT": ("20", "60"), "RECOVERY_SLO_SECONDS": ("300", "180")}


def refresh_defaults(path: Path, current: dict) -> list[str]:
    """Replace KEY=old_default with KEY=new_default; leave values you chose yourself."""
    lines, changed = path.read_text().splitlines(), []
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in CHANGED and current.get(key) == CHANGED[key][0]:
            lines[i] = f"{key}={CHANGED[key][1]}"
            changed.append(f"{key} {CHANGED[key][0]} -> {CHANGED[key][1]}")
    if changed:
        path.write_text("\n".join(lines) + "\n")
    return changed


def main() -> None:
    env_path = Path(".env")
    current = load_dotenv(env_path)
    example = load_dotenv(".env.example")
    added = [k for k in example if k not in current]
    if added:
        with env_path.open("a") as fh:
            fh.write("\n# ---- added by update_env.py for the final project ----\n")
            for key in added:
                fh.write(f"{key}={example[key]}\n")
    print("Added:", ", ".join(added) if added else "nothing (already up to date)")
    updated = refresh_defaults(env_path, current)
    if updated:
        print("Updated:", ", ".join(updated))
    unused = sorted(OLD_ONLY & current.keys())
    if unused:
        print("No longer used (safe to delete from .env):", ", ".join(unused))


if __name__ == "__main__":
    main()
