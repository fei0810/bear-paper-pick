#!/usr/bin/env python3
"""Packaging must ship only allowlisted files and refuse anything credential-like."""
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import package_skill  # noqa: E402


def make_tree(root):
    root = Path(root)
    root.mkdir()
    for name in package_skill.RUNTIME_FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n", encoding="utf-8")
    (root / ".env.example").write_text("TYPESAFE_API_KEY=\nJEV_MODE=shadow\n", encoding="utf-8")
    return root


class PackageTests(unittest.TestCase):
    def build(self, root, profile="runtime"):
        output = Path(root).parent / f"out-{profile}.tar.gz"
        return package_skill.build_archive(output, profile, root)

    def test_clean_tree_ships_only_allowlisted_files(self):
        with tempfile.TemporaryDirectory() as parent:
            root = make_tree(Path(parent) / "src")
            (root / ".env").write_text("TYPESAFE_API_KEY=sk-live-abcdefghijklmnop\n", encoding="utf-8")
            (root / "paper-radar-data" / "line" / "runs").mkdir(parents=True)
            (root / "paper-radar-data" / "line" / "runs" / "rank-run.json").write_text("{}", encoding="utf-8")
            (root / "scripts" / "__pycache__").mkdir()
            (root / "scripts" / "__pycache__" / "tool.cpython-311.pyc").write_bytes(b"\0")
            (root / "scripts" / ".paper-radar-x.tmp").write_text("", encoding="utf-8")
            (root / "scripts/runtime.json").write_text("{\"private\":true}")
            output, count = self.build(root)
            with tarfile.open(output, "r:gz") as archive:
                names = sorted(archive.getnames())
                members = archive.getmembers()
        self.assertEqual(count, len(package_skill.RUNTIME_FILES))
        self.assertEqual(names, sorted("bear-paper-pick/" + path for path in package_skill.RUNTIME_FILES))
        self.assertTrue(all(m.uname == "" and m.uid == 0 and m.mtime == 0 for m in members))

    def test_env_variants_inside_allowlisted_dirs_are_refused(self):
        for name in (".env", ".env.local", ".env.production"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as parent:
                root = make_tree(Path(parent) / "src")
                (root / "references" / name).write_text("x\n", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "禁止发布"):
                    self.build(root)

    def test_credential_like_lines_are_refused(self):
        with tempfile.TemporaryDirectory() as parent:
            root = make_tree(Path(parent) / "src")
            (root / "scripts" / "notes.txt").write_text('TYPESAFE_API_KEY="sk-live-1234567890abcdef"\n', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "疑似包含凭据"):
                self.build(root)
            self.assertFalse((Path(parent) / "out-runtime.tar.gz").exists())

    @unittest.skipIf(not hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlinks_are_refused(self):
        with tempfile.TemporaryDirectory() as parent:
            root = make_tree(Path(parent) / "src")
            outside = Path(parent) / "outside.txt"
            outside.write_text("secret\n", encoding="utf-8")
            try:
                os.symlink(outside, root / "references" / "link.md")
            except OSError:
                self.skipTest("cannot create symlink")
            with self.assertRaisesRegex(RuntimeError, "符号链接"):
                self.build(root)

    def test_archive_bytes_are_reproducible(self):
        with tempfile.TemporaryDirectory() as parent:
            root = make_tree(Path(parent) / "src")
            first, _ = package_skill.build_archive(Path(parent) / "a.tar.gz", "runtime", root)
            second, _ = package_skill.build_archive(Path(parent) / "b.tar.gz", "runtime", root)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_real_repository_packages_cleanly(self):
        with tempfile.TemporaryDirectory() as parent:
            for profile in ("runtime", "review"):
                output, count = package_skill.build_archive(Path(parent) / f"{profile}.tar.gz", profile)
                self.assertGreater(count, 0)


if __name__ == "__main__":
    unittest.main()
