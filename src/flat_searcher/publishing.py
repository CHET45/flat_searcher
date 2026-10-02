"""Publish a digest site as a GitHub Pages site: one fresh commit, force-pushed."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from flat_searcher.library.store import authenticated_url, redact

BRANCH = "main"
IDENTITY = ("-c", "user.name=flat-searcher", "-c", "user.email=flat-searcher@users.noreply.github.com")


class PublishError(RuntimeError):
    pass


def publish_site(folder: Path, day: str, repository: str, token: str | None) -> tuple[int, int]:
    """Returns the number of files and their total bytes."""
    url = authenticated_url(repository, token)
    secrets = tuple(secret for secret in (token, url, repository) if secret)
    files = [path for path in folder.rglob("*") if path.is_file()]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        site = Path(temp) / "site"
        shutil.copytree(folder, site)
        (site / ".nojekyll").write_text("", encoding="utf-8")
        _git(site, secrets, "init", "-q", "-b", BRANCH)
        _git(site, secrets, "-c", "core.autocrlf=false", "add", "-A")
        _git(site, secrets, *IDENTITY, "commit", "-q", "-m", f"digest {day}")
        _git(site, secrets, "push", "-q", "--force", url, f"{BRANCH}:{BRANCH}")
    return len(files), sum(path.stat().st_size for path in files)


def _git(site: Path, secrets: tuple[str, ...], *args: str) -> None:
    result = subprocess.run(
        ["git", *args], cwd=site, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if result.returncode != 0:
        raise PublishError(redact(result.stderr.strip() or f"git {args[0]} failed", secrets))
