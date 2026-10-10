"""Render every .md in this repo to a plain-text .txt twin beside it.

The twins exist for terminals, pagers, screen readers and anywhere markdown
does not render. They are GENERATED - never edit a .txt by hand:

    python scripts/md_txt.py           # rewrite every twin
    python scripts/md_txt.py --check   # exit 1 if any is out of sync

The suite runs --check, so editing a .md and forgetting to regenerate fails
the tests rather than shipping a stale mirror. README.txt was hand-made until
evo.ai#242 changed the README; a hand-kept twin drifts silently, and a stale
plain-text copy is worse than none because it reads as current.

Ported from evo-ai's scripts/md_txt.py (itself from the house reference,
evo.locate scripts/readme_txt.py), keeping this repo's hand-made conventions
where they differed: bold markers are stripped rather than capitalised, and a
dash becomes " - ".

Line breaks come from the source, which is already wrapped; only a line that
a link expansion pushes past the limit is re-wrapped, so a table or an ASCII
diagram is never reflowed.
"""
from __future__ import annotations

import re
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache",
             "dist", "build", "site-packages", "state"}
WRAP_AT = 100


def _link_target(target: str) -> str:
    """A relative link to a .md points at its .txt twin; anything else keeps
    its own address."""
    if target.startswith(("http://", "https://", "#", "mailto:")):
        return target
    base, _, anchor = target.partition("#")
    if base.endswith(".md"):
        base = base[:-3] + ".txt"
    return base + (("#" + anchor) if anchor else "")


def _link(m: "re.Match") -> str:
    """A link as "text (target)", or just the target when the text IS the
    target - otherwise the same path reads twice."""
    label, target = m.group(1), _link_target(m.group(2))
    if label == m.group(2) or label == target:
        return target
    return f"{label} ({target})"


def _inline(text: str) -> str:
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"[image: \1]", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _link, text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = text.replace("—", "-").replace("–", "-").replace("…", "...")
    text = text.replace("‘", "'").replace("’", "'")
    text = text.replace("“", '"').replace("”", '"')
    return text


def _wrap(line: str) -> list[str]:
    """Re-wrap only a line a substitution made too long. A table row, an
    indented code line and anything short is left exactly as written."""
    if len(line) <= WRAP_AT or line.startswith("    ") or line.lstrip().startswith("|"):
        return [line]
    indent = line[:len(line) - len(line.lstrip())]
    marker = re.match(r"([-*+]\s+|\d+\.\s+)", line.lstrip())
    hanging = indent + (" " * len(marker.group(1)) if marker else "")
    return textwrap.wrap(line, width=WRAP_AT, initial_indent="",
                         subsequent_indent=hanging, break_long_words=False,
                         break_on_hyphens=False) or [line]


def render(md: str) -> str:
    out: list[str] = []
    in_fence = False
    for line in md.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            out.append(("    " + line) if line else "")
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            text = _inline(heading.group(2))
            out.append(text)
            out.append(("=" if len(heading.group(1)) == 1 else "-") * len(text))
            continue
        out.extend(_wrap(_inline(line)))
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
    # Strip blank LINES, not whitespace: .strip() would eat the indent of a
    # code block that opens the document.
    return text.strip("\n") + "\n"


def sources() -> list[Path]:
    out = []
    for p in ROOT.rglob("*.md"):
        rel = p.relative_to(ROOT)
        if set(rel.parts[:-1]) & SKIP_DIRS:
            continue
        out.append(p)
    return sorted(out)


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale: list[str] = []
    for md in sources():
        twin = md.with_suffix(".txt")
        want = render(md.read_text(encoding="utf-8"))
        have = twin.read_text(encoding="utf-8") if twin.exists() else None
        if have != want:
            if check:
                stale.append(str(md.relative_to(ROOT)))
            else:
                twin.write_text(want, encoding="utf-8", newline="\n")
    if check:
        if stale:
            print("stale plain-text twin(s):\n  " + "\n  ".join(stale))
            print("run: python scripts/md_txt.py")
            return 1
        print(f"all {len(sources())} plain-text twins are in sync")
        return 0
    print(f"wrote {len(sources())} plain-text twins")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
