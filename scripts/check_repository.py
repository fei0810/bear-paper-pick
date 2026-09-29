"""Offline source/review archive checks; no credentials or external calls."""
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote

import package_skill

ROOT = Path(__file__).resolve().parents[1]


def main():
    errors = []
    # Includes ignored local config only if someone explicitly listed it to ship.
    public = set(package_skill.PROFILES["review"])
    for name in public:
        path = ROOT / name
        if not path.is_file():
            errors.append(f"Missing public file: {name}")
            continue
        try: package_skill.check(path, ROOT)
        except RuntimeError as e: errors.append(str(e))
        content = path.read_text(encoding="utf-8")
        if ("/" + "Users/") in content or ("/" + "private/tmp/") in content:
            errors.append(f"Local absolute path in public file: {name}")
        if path.suffix == ".md":
            for target in re.findall(r"\]\(([^)]+)\)", content):
                target = target.strip("<>").split("#", 1)[0]
                if not target or "://" in target or target.startswith("mailto:"): continue
                if not (path.parent / unquote(target)).exists():
                    errors.append(f"Broken link in {name}: {target}")
    # Source-only git checks do not apply to clean review tarballs.
    if (ROOT / ".git").is_dir():
        tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
        for name in filter(None, tracked):
            if name not in public:
                errors.append(f"Tracked file outside public manifest: {name}")
    entry = (ROOT / "SKILL.md").read_text()
    if not entry.startswith("---\nname: bear-paper-pick\ndescription:"):
        errors.append("Skill frontmatter name/description mismatch")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:\.dev\d+)?\n", (ROOT / "VERSION").read_text()):
        errors.append("Invalid VERSION")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"Source checks passed: {len(public)} public files, local links, frontmatter, version and credential patterns.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
