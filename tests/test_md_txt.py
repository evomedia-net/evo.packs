"""Every .md has a generated .txt twin, and the suite says so.

README.txt was hand-made: nothing produced it and nothing checked it. It was
accurate, but only because nobody had changed the README since - a hand-kept
twin drifts silently, and a stale plain-text copy is worse than none because
it reads as current.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("md_txt", ROOT / "scripts" / "md_txt.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["md_txt"] = mod
    spec.loader.exec_module(mod)
    return mod


md_txt = _load()


def test_every_twin_is_in_sync():
    """Edit a .md, forget to regenerate, fail here rather than shipping a
    mirror that no longer mirrors."""
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "md_txt.py"), "--check"],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_check_notices_a_stale_twin(tmp_path, monkeypatch):
    """Without this the test above could pass by checking nothing."""
    (tmp_path / "page.md").write_text("# Title\n\nBody.\n", encoding="utf-8")
    (tmp_path / "page.txt").write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(md_txt, "ROOT", tmp_path)
    assert md_txt.main(["--check"]) == 1


def test_headings_links_and_markers():
    out = md_txt.render(
        "# Title\n\n## Part\n\nSee [the docs](docs/a.md) and [x](https://x.test) - "
        "**bold**, *em*, `code` — dash.\n")
    assert out.splitlines()[:2] == ["Title", "====="]
    assert "Part\n----" in out
    assert "the docs (docs/a.txt)" in out and "x (https://x.test)" in out
    assert "bold, em, code - dash." in out


def test_code_blocks_are_indented_not_fenced():
    out = md_txt.render("```bash\nevopacks sign\n```\n")
    assert out == "    evopacks sign\n"
