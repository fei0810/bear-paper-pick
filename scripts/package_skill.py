#!/usr/bin/env python3
"""Build a bear-paper-pick archive from an explicit allowlist.

Only the listed paths are shipped. Every file is scanned for credential-like
lines before anything is written, symlinks are refused, and tar metadata is
normalised so the archive carries no builder username and is byte-reproducible.

Profiles:
  runtime  what an agent needs to run the skill (default)
  review   runtime + tests, evaluation and design documents for an independent reviewer

Usage:
  python3 scripts/package_skill.py --output ../bear-paper-pick-dev.tar.gz
  python3 scripts/package_skill.py --profile review --output /tmp/bear-paper-pick-review.tar.gz
"""
import argparse
import gzip
import re
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCNAME = "bear-paper-pick"
RUNTIME_FILES = [
    "SKILL.md", "README.md", "agents/openai.yaml", "VERSION", "LICENSE", "NOTICE.md", ".env.example", ".gitignore",
    "docs/PRIVACY.md", "docs/ACCEPTANCE.md", "docs/DEVELOPMENT.md", "AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md", "CHANGELOG.md",
    "references/api-recipes.md", "references/deep-read.md", "references/deep-read-example.md",
    "references/jev-policy.md", "references/self-test.md", "references/workflow.md", "references/protocol.md",
    "scripts/config.py", "scripts/fetch.py", "scripts/jev_rank.py", "scripts/profile_snapshot.py",
    "scripts/sift.py", "scripts/screening.py", "scripts/package_skill.py",
]
REVIEW_FILES = ["scripts/check_repository.py", "tests/test_fetch.py", "tests/test_jev_rank.py", "tests/test_package.py", "tests/test_release.py", "tests/test_screening.py",
                "tests/fixtures/jev_candidates.json", "tests/fixtures/jev_profile.json", "tests/fixtures/jev_rounded_responses.json", ".github/workflows/ci.yml", ".github/ISSUE_TEMPLATE/bug_report.md"]
PROFILES = {"runtime": RUNTIME_FILES, "review": RUNTIME_FILES + REVIEW_FILES}
SKIP_PARTS = {"__pycache__"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".tmp", ".lock"}
# .env 及其变体、macOS 目录元数据、旧归档一律拒绝发布；.env.example 是唯一例外。
DENY_NAME = re.compile(r"^(\.env(\..+)?|\.DS_Store|.*\.tar\.gz|.*\.tgz)$")
# 键名含 API_KEY、值为 16 位以上 token 字符的行视为疑似凭据；.env.example 的空值不会命中。
SECRET = re.compile(rb"(?m)^(?:export[ \t]+)?[A-Z0-9_]*API_KEY[ \t]*=[ \t]*['\"]?[A-Za-z0-9_.\-]{16,}")


def runtime_files(profile="runtime", root=ROOT):
    root = Path(root)
    # Inspect source directories for misplaced secrets/symlinks, but only ship
    # individual manifest paths. A notes.md or runtime.json cannot enter by accident.
    for folder in ("scripts", "references", "tests", "docs", ".github", "agents"):
        path = root / folder
        if path.is_symlink(): raise RuntimeError(f"拒绝符号链接：{folder}")
        if not path.exists(): continue
        for child in path.rglob("*"):
            if child.is_symlink(): raise RuntimeError(f"拒绝符号链接：{child.relative_to(root)}")
            if child.is_file() and "__pycache__" not in child.parts:
                check(child, root)
    for entry in PROFILES[profile]:
        path = root / entry
        if path.is_symlink(): raise RuntimeError(f"拒绝符号链接：{entry}")
        if not path.is_file(): raise RuntimeError(f"白名单中的路径不存在：{entry}")
        yield path


def check(path, root=ROOT):
    rel = Path(path).relative_to(root)
    if path.name != ".env.example" and DENY_NAME.match(path.name):
        raise RuntimeError(f"禁止发布：{rel}")
    if SECRET.search(path.read_bytes()):
        raise RuntimeError(f"疑似包含凭据：{rel}")


def _normalize(info):
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = 0
    info.mode = 0o755 if info.mode & 0o111 else 0o644
    return info


def build_archive(output, profile="runtime", root=ROOT):
    if profile not in PROFILES:
        raise ValueError(f"未知 profile：{profile}")
    root = Path(root).resolve()
    output = Path(output).resolve()
    if output == root or root in output.parents:
        raise RuntimeError("发布包应写到 Skill 目录之外")
    files = list(runtime_files(profile, root))
    for path in files:  # 全部检查通过后才开始写，失败时不留半成品
        check(path, root)
    expected = {f"{ARCNAME}/{path.relative_to(root).as_posix()}" for path in files}
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz, \
            tarfile.open(fileobj=gz, mode="w") as archive:
        for path in files:
            archive.add(path, arcname=f"{ARCNAME}/{path.relative_to(root).as_posix()}", recursive=False, filter=_normalize)
    with tarfile.open(output, "r:gz") as archive:
        names = set(archive.getnames())
    if names != expected:
        raise RuntimeError("归档内容与白名单不一致：" + ", ".join(sorted(names ^ expected)))
    return output, len(files)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", required=True)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="runtime")
    args = parser.parse_args(argv)
    try:
        output, count = build_archive(args.output, args.profile)
    except RuntimeError as error:
        print(f"ERROR: {error}")
        return 65
    print(f"已生成 {args.profile} 包：{output}（{count} 个文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
