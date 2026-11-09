"""No document in this repository names a path on somebody's workstation.

A drive-letter path is true on exactly one machine. In a document it is worse
than useless: it tells a reader nothing they can act on, it leaks the shape of
a private disk, and it stops being true the moment the workspace is cloned or
moved. The portable form says the same thing and keeps saying it: a
repo-relative path, or the name of the repository the file is in.

The fleet sweep behind this check found nothing to remove here, so this is the
check rather than a clean-up. It earns its keep because a pack is built on one
machine and installed on another: the build and install notes are exactly the
kind of document a pasted command lands in, and a path from the build machine
means nothing on the server that reads it.

The .txt twins are checked too: a twin is generated from its markdown, so a
path pasted into a document propagates into a second file that then looks like
an independent source.

A drive letter is matched as a SINGLE character with a non-word character
before it, or "http://host" would read as drive "p" and every document with a
link in it would fail - which, for the changelog, is every line.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

#: Patterns that mean "a path on one particular computer".
LOCAL_PATH = re.compile(
    r"""(?<![A-Za-z0-9])(?:
          [A-Za-z]:[\\/]{1,2}[A-Za-z0-9_.]   # C:\x, F:/x, F:\\x
        | /mnt/[a-z]/                         # WSL's view of a Windows drive
        | /[a-z]/evomedia                     # Git Bash's view of one
        )""",
    re.VERBOSE,
)

#: Spellings that are already placeholders and say so, so they are not paths
#: to anywhere. Matched as substrings of the line.
PLACEHOLDERS = (
    r"C:\path\to",
    r"<drive>\<parent>",
)

#: Documents whose SUBJECT is a literal path, where there is nothing portable
#: to put instead. Add to this only for that reason - "it is only in a code
#: block" is not one, because that is the case this test exists for.
EXEMPT: set[str] = set()


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists():
            return parent
    raise AssertionError("no repository root above this test")


ROOT = _repo_root()


def _tracked_documents() -> list[str]:
    """Every tracked .md and .txt, asked of git rather than of the disk.

    A filesystem walk finds the virtualenv, built pack folders and whatever
    scratch file is lying around, and would fail the suite over a file that
    is not ours; git already knows which files are.
    """
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z", "--", "*.md", "*.txt"],
        capture_output=True, encoding="utf-8", errors="replace", check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def test_the_check_found_documents_to_read() -> None:
    """A positive control.

    If discovery silently returned nothing - git not on PATH, a changed flag,
    a renamed file - the real test below would pass while reading no files at
    all, and the check would be gone without a word.
    """
    documents = _tracked_documents()
    assert len(documents) >= 4, f"only {len(documents)} documents found; discovery is wrong"
    assert any(d.endswith(".txt") for d in documents), "no twins found"


def test_the_pattern_catches_a_real_path_and_spares_a_url() -> None:
    """The check can fail, not just pass.

    The made-up drive letters keep this file from spelling out the real
    layout in order to guard it. The URL case is the one that would otherwise
    fire on every document here, because the changelog is all links.
    """
    assert LOCAL_PATH.search(r"built at X:\evomedia.net\evo.packs\dist")
    assert LOCAL_PATH.search("see /mnt/x/evomedia.net/evo.packs")
    assert LOCAL_PATH.search(r"at C:\Users\Someone Else\Desktop")
    assert not LOCAL_PATH.search("https://github.com/evomedia-net/evo.packs/pull/1")
    assert not LOCAL_PATH.search("http://localhost:6333/collections")
    assert not LOCAL_PATH.search("run `python -m packs.build` from the repository root")


def test_no_document_names_a_workstation_path() -> None:
    offenders: list[str] = []
    for rel in _tracked_documents():
        if rel in EXEMPT or Path(rel).with_suffix(".md").as_posix() in EXEMPT:
            continue
        text = (ROOT / rel).read_text(encoding="utf-8-sig", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            if any(holder in line for holder in PLACEHOLDERS):
                continue
            if LOCAL_PATH.search(line):
                offenders.append(f"{rel}:{number}: {line.strip()[:120]}")

    assert not offenders, (
        "a path that is only true on one machine: write it relative to the "
        "repository instead, or name the repository the file is in.\n  "
        + "\n  ".join(offenders)
    )
