"""Collect exported TXT files of all chats into a few big files for NotebookLM.

Every chat becomes one file (split into parts if it exceeds the NotebookLM per-source limit); small chats are merged into one shared file. Uses only the standard library.

    python scripts/collect_for_notebooklm.py [--root DIR] [--out DIR] [--small-words N] [--max-words N]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_ROOT = Path.home() / "Documents" / "TelegramArchiver"


def chat_title(folder: Path) -> str:
    # folder name is "<safe title>_<id>"
    name = re.sub(r"_\d+$", "", folder.name)
    return re.sub(r"_+", " ", name).strip() or folder.name


def chat_text(folder: Path) -> str:
    files = sorted((folder / "export" / "txt").rglob("*.txt"))
    return "".join(f.read_text(encoding="utf-8") for f in files).strip()


def write_parts(out: Path, stem: str, title: str, text: str, max_words: int) -> list[Path]:
    """Write one file, or several parts split on message boundaries if the text is too long."""
    chunks: list[list[str]] = [[]]
    words = 0
    for msg in text.split("\n\n"):
        n = len(msg.split())
        if words + n > max_words and chunks[-1]:
            chunks.append([])
            words = 0
        chunks[-1].append(msg)
        words += n
    paths = []
    for i, chunk in enumerate(chunks, 1):
        suffix = f" (частина {i} з {len(chunks)})" if len(chunks) > 1 else ""
        p = out / (f"{stem}_part{i:02d}.txt" if len(chunks) > 1 else f"{stem}.txt")
        p.write_text(f"# {title}{suffix}\n\n" + "\n\n".join(chunk) + "\n", encoding="utf-8")
        paths.append(p)
    return paths


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="archive root (default: %(default)s)")
    ap.add_argument("--out", type=Path, help="output folder (default: <root>/notebooklm)")
    ap.add_argument("--small-words", type=int, default=20_000, help="chats below this go into one shared file")
    ap.add_argument("--max-words", type=int, default=400_000, help="split a file above this (NotebookLM: 500k/source)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]  # chat names in a cp1252 console
    out: Path = args.out or args.root / "notebooklm"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.txt"):
        old.unlink()

    chats = sorted(d for d in args.root.glob("*/chats/*") if d.is_dir() and not d.name.startswith("_"))
    small: list[tuple[str, str]] = []
    written: list[Path] = []
    for folder in chats:
        text = chat_text(folder)
        if not text:
            continue
        title = chat_title(folder)
        if len(text.split()) < args.small_words:
            small.append((title, text))
        else:
            written += write_parts(out, folder.name, title, text, args.max_words)
    if small:
        merged = "\n\n".join(f"========== {t} ==========\n\n{x}" for t, x in small)
        written += write_parts(out, "_small_chats", "Невеликі чати: " + ", ".join(t for t, _ in small), merged,
                               args.max_words)

    for p in written:
        print(f"{p.stat().st_size / 1024 / 1024:7.2f} MB  {p.name}")
    print(f"\n{len(written)} files -> {out}")


if __name__ == "__main__":
    main()
