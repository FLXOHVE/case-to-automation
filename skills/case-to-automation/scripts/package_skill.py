"""Export this self-contained skill only; no project code, configs or run data."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit
import zipfile


def collect(root: Path) -> dict[str, bytes]:
    root = root.resolve()
    paths = [root / "SKILL.md", root / "LICENSE"]
    for folder, extensions in (("references", {".md"}), ("agents", {".yaml", ".yml"}), ("scripts", {".py"})):
        paths.extend(p for p in (root / folder).rglob("*") if p.is_file() and p.suffix in extensions)
    files = {}
    for path in sorted(paths):
        if not path.resolve().is_relative_to(root) or any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())
                for p in (path, *[parent for parent in path.parents if parent.is_relative_to(root)])):
            raise ValueError(f"Linked/outside skill file: {path}")
        if "__pycache__" in path.parts:
            continue
        files[path.relative_to(root).as_posix()] = path.read_bytes()
    for name, content in files.items():
        if not name.endswith(".md"):
            continue
        markdown = content.decode("utf-8")
        links = re.findall(r"\[[^\]]*\]\(([^)\n]+)\)", markdown)
        links += re.findall(r"(?m)^\s{0,3}\[[^\]]+\]:\s*(<[^>]+>|\S+)", markdown)
        for raw in links:
            reference = raw.strip().strip("<>")
            parsed = urlsplit(reference)
            if parsed.scheme == "file" or re.match(r"^[A-Za-z]:", unquote(reference)) or reference.startswith("\\\\"):
                raise ValueError(f"Machine-local, nonportable skill reference: {name} -> {raw}")
            if parsed.scheme or not parsed.path:
                continue
            target = (root / name).parent / unquote(parsed.path)
            if not target.resolve().is_relative_to(root) or target.resolve().relative_to(root).as_posix() not in files:
                raise ValueError(f"Missing/nonportable skill reference: {name} -> {raw}")
    return files


def build(root: Path, output: Path) -> dict:
    files = collect(root)
    if output.resolve().is_relative_to(root.resolve()):
        raise ValueError("Write the archive outside the skill directory")
    manifest = {"format": 1, "skill": "case-to-automation", "entry": "case-to-automation/SKILL.md",
                "requirements": "AI agent with project/file/command access; Python3.10+ for optional helper scripts; target project's own test environment",
                "files": [{"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                          for name, data in sorted(files.items())]}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr("case-to-automation/" + name, data)
        archive.writestr("case-to-automation/manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        manifest = build(Path(__file__).resolve().parents[1], args.output.resolve())
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Skill export failed: {exc}\n")
    print(json.dumps({"output": str(args.output.resolve()), "files": len(manifest["files"])+1}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
