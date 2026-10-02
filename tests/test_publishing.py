import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase

from flat_searcher.publishing import PublishError, publish_site


def _git(bare: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "--git-dir", str(bare), *args], capture_output=True, text=True, check=True
    ).stdout


def _site(root: Path, name: str, files: dict[str, bytes]) -> Path:
    site = root / name
    for relative, content in files.items():
        (site / relative).parent.mkdir(parents=True, exist_ok=True)
        (site / relative).write_bytes(content)
    return site


class PublishSiteTests(TestCase):
    def test_each_publish_replaces_the_site_with_a_single_commit(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
            root = Path(temp)
            bare = root / "site.git"
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
            first = _site(root, "one", {"index.html": b"<p>one</p>", "photos/a.jpg": b"abc"})
            self.assertEqual(publish_site(first, "2026-09-24", str(bare), None), (2, 13))
            second = _site(root, "two", {"index.html": b"<p>two</p>", "map.js": b"window.x = 1;",
                                         "photos/b.jpg": b"b"})
            publish_site(second, "2026-09-25", str(bare), None)
            self.assertEqual(_git(bare, "log", "--format=%s", "main").splitlines(), ["digest 2026-09-25"])
            self.assertEqual(_git(bare, "show", "main:index.html"), "<p>two</p>")
            self.assertEqual(
                _git(bare, "ls-tree", "-r", "--name-only", "main").split(),
                [".nojekyll", "index.html", "map.js", "photos/b.jpg"],
            )

    def test_a_failed_push_raises_without_the_token(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
            site = _site(Path(temp), "site", {"index.html": b"<p>"})
            with self.assertRaises(PublishError) as caught:
                publish_site(site, "2026-09-24", str(Path(temp) / "nowhere"), "tok-secret")
            self.assertNotIn("tok-secret", str(caught.exception))
