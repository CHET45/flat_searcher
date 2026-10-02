"""Storage backends for the JSONL listing library."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlsplit, urlunsplit

from flat_searcher.library.records import Record

LISTINGS_FILE = "listings.jsonl"
EVENTS_FILE = "events.jsonl"
META_FILE = "meta.json"
QUEUE_DIR = "queue"
VERDICTS_DIR = "verdicts"
CRITERIA_FILE = "criteria.toml"
TRANSIT_FILE = "transit.jsonl"
TRANSIT_TARGETS_FILE = "transit-targets.json"
TRANSIT_MAP_FILE = "transit-map.json"
DIGEST_DIR = "digest"
DIGEST_PAGE = "index.html"

REDACTED = "<redacted>"


class LibraryError(RuntimeError):
    pass


class GitLibraryError(LibraryError):
    pass


class LibraryStore(Protocol):
    def load_listings(self) -> dict[str, Record]: ...

    def save_listings(self, listings: Mapping[str, Mapping[str, Any]]) -> None: ...

    def append_events(self, events: Iterable[Mapping[str, Any]]) -> None: ...

    def read_queue(self, day: str) -> list[Record]: ...

    def write_queue(self, day: str, entries: Iterable[Mapping[str, Any]]) -> None: ...

    def verdict_days(self) -> list[str]: ...

    def read_verdicts(self, day: str) -> list[Record]: ...

    def append_verdicts(self, day: str, verdicts: Iterable[Mapping[str, Any]]) -> None: ...

    def queue_days(self) -> list[str]: ...

    def load_meta(self) -> Record: ...

    def write_meta(self, meta: Mapping[str, Any]) -> None: ...

    def read_criteria(self) -> str | None: ...

    def read_events(self) -> list[Record]: ...

    def read_transit(self) -> list[Record]: ...

    def write_transit(self, entries: Iterable[Mapping[str, Any]]) -> None: ...

    def digest_days(self) -> list[str]: ...

    def write_digest(self, day: str, text: str) -> None: ...

    def write_digest_site(self, day: str, files: Mapping[str, str | bytes]) -> None: ...

    def digest_site(self, day: str) -> Path | None: ...

    def read_transit_targets(self) -> dict[str, Any]: ...

    def write_transit_targets(self, points: Mapping[str, Mapping[str, Any]]) -> None: ...

    def read_transit_map(self) -> dict[str, Any]: ...

    def write_transit_map(self, data: Mapping[str, Any]) -> None: ...


def authenticated_url(repository: str, token: str | None) -> str:
    parts = urlsplit(repository)
    if parts.scheme not in ("http", "https") or not token:
        return repository
    netloc = f"x-access-token:{quote(token, safe='')}@{parts.hostname or ''}"
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def redact(text: str, secrets: Iterable[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


class LocalLibraryStore:
    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def load_listings(self) -> dict[str, Record]:
        listings: dict[str, Record] = {}
        for number, entry in enumerate(_read_jsonl(self._root / LISTINGS_FILE), start=1):
            ss_id = entry.get("ss_id")
            if not ss_id:
                raise LibraryError(f"{LISTINGS_FILE} line {number} has no ss_id")
            listings[str(ss_id)] = entry
        return listings

    def save_listings(self, listings: Mapping[str, Mapping[str, Any]]) -> None:
        _atomic_write(
            self._root / LISTINGS_FILE,
            (_json_line(listings[ss_id]) for ss_id in sorted(listings)),
        )

    def append_events(self, events: Iterable[Mapping[str, Any]]) -> None:
        _append_jsonl(self._root / EVENTS_FILE, events)

    def read_queue(self, day: str) -> list[Record]:
        return _read_jsonl(self.queue_path(day))

    def write_queue(self, day: str, entries: Iterable[Mapping[str, Any]]) -> None:
        _atomic_write(self.queue_path(day), (_json_line(entry) for entry in entries))

    def queue_days(self) -> list[str]:
        return _days(self._root / QUEUE_DIR)

    def verdict_days(self) -> list[str]:
        return _days(self._root / VERDICTS_DIR)

    def read_verdicts(self, day: str) -> list[Record]:
        return _read_jsonl(self.verdicts_path(day))

    def append_verdicts(self, day: str, verdicts: Iterable[Mapping[str, Any]]) -> None:
        _append_jsonl(self.verdicts_path(day), verdicts)

    def load_meta(self) -> Record:
        path = self._root / META_FILE
        if not path.exists():
            return {}
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise LibraryError(f"{META_FILE} is not valid JSON: {error}") from error
        if not isinstance(meta, dict):
            raise LibraryError(f"{META_FILE} is not a JSON object")
        return meta

    def write_meta(self, meta: Mapping[str, Any]) -> None:
        payload = json.dumps(dict(meta), ensure_ascii=False, indent=2) + "\n"
        _atomic_write(self._root / META_FILE, [payload])

    def read_criteria(self) -> str | None:
        path = self._root / CRITERIA_FILE
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8-sig")

    def read_events(self) -> list[Record]:
        return _read_jsonl(self._root / EVENTS_FILE)

    def read_transit(self) -> list[Record]:
        return _read_jsonl(self._root / TRANSIT_FILE)

    def write_transit(self, entries: Iterable[Mapping[str, Any]]) -> None:
        ordered = sorted(entries, key=lambda entry: str(entry.get("ss_id")))
        _atomic_write(self._root / TRANSIT_FILE, (_json_line(entry) for entry in ordered))

    def digest_days(self) -> list[str]:
        directory = self._root / DIGEST_DIR
        if not directory.is_dir():
            return []
        return sorted(path.stem for path in directory.glob("*.md"))

    def write_digest(self, day: str, text: str) -> None:
        _atomic_write(self._root / DIGEST_DIR / f"{day}.md", [text])

    def write_digest_site(self, day: str, files: Mapping[str, str | bytes]) -> None:
        """The day's page folder, swapped in whole so a reader never sees half of it."""
        target = self._root / DIGEST_DIR / day
        staging = target.with_name(f".{day}.{os.getpid()}.tmp")
        retired = target.with_name(f".{day}.{os.getpid()}.old")
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        try:
            for relative, content in files.items():
                path = staging / relative
                if not path.resolve().is_relative_to(staging.resolve()):
                    raise LibraryError(f"digest file outside its folder: {relative}")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
            if target.exists():
                target.replace(retired)
            staging.replace(target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(retired, ignore_errors=True)

    def digest_site(self, day: str) -> Path | None:
        site = self._root / DIGEST_DIR / day
        return site if (site / DIGEST_PAGE).is_file() else None

    def read_transit_targets(self) -> dict[str, Any]:
        return self._read_json(TRANSIT_TARGETS_FILE)

    def write_transit_targets(self, points: Mapping[str, Mapping[str, Any]]) -> None:
        self._write_json(TRANSIT_TARGETS_FILE, {name: dict(point) for name, point in points.items()})

    def read_transit_map(self) -> dict[str, Any]:
        return self._read_json(TRANSIT_MAP_FILE)

    def write_transit_map(self, data: Mapping[str, Any]) -> None:
        self._write_json(TRANSIT_MAP_FILE, data)

    def _read_json(self, name: str) -> dict[str, Any]:
        path = self._root / name
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise LibraryError(f"{name} is not valid JSON: {error}") from error
        return data if isinstance(data, dict) else {}

    def _write_json(self, name: str, data: Mapping[str, Any]) -> None:
        payload = json.dumps(dict(data), ensure_ascii=False)
        _atomic_write(self._root / name, [payload + chr(10)])

    def queue_path(self, day: str) -> Path:
        return self._root / QUEUE_DIR / f"{day}.jsonl"

    def verdicts_path(self, day: str) -> Path:
        return self._root / VERDICTS_DIR / f"{day}.jsonl"


class GitLibraryStore:
    """LocalLibraryStore inside a git working copy that is pulled before and pushed after a run.

    The repository URL and the token are both secrets: the authenticated URL is built per call,
    never written to .git/config, and scrubbed from every error this class raises.
    """

    def __init__(
        self,
        repo_url: str,
        token: str,
        workdir: Path | str,
        *,
        branch: str = "main",
        subdir: str = "library",
        author_name: str = "flat-searcher",
        author_email: str = "flat-searcher@users.noreply.github.com",
        git_executable: str = "git",
    ) -> None:
        self._repo_url = repo_url
        self._token = token
        self._workdir = Path(workdir)
        self._branch = branch
        self._author_name = author_name
        self._author_email = author_email
        self._git = git_executable
        self._local = LocalLibraryStore(self._workdir / subdir)

    @property
    def root(self) -> Path:
        return self._local.root

    def load_listings(self) -> dict[str, Record]:
        return self._local.load_listings()

    def save_listings(self, listings: Mapping[str, Mapping[str, Any]]) -> None:
        self._local.save_listings(listings)

    def append_events(self, events: Iterable[Mapping[str, Any]]) -> None:
        self._local.append_events(events)

    def read_queue(self, day: str) -> list[Record]:
        return self._local.read_queue(day)

    def write_queue(self, day: str, entries: Iterable[Mapping[str, Any]]) -> None:
        self._local.write_queue(day, entries)

    def queue_days(self) -> list[str]:
        return self._local.queue_days()

    def verdict_days(self) -> list[str]:
        return self._local.verdict_days()

    def read_verdicts(self, day: str) -> list[Record]:
        return self._local.read_verdicts(day)

    def append_verdicts(self, day: str, verdicts: Iterable[Mapping[str, Any]]) -> None:
        self._local.append_verdicts(day, verdicts)

    def load_meta(self) -> Record:
        return self._local.load_meta()

    def write_meta(self, meta: Mapping[str, Any]) -> None:
        self._local.write_meta(meta)

    def read_criteria(self) -> str | None:
        return self._local.read_criteria()

    def read_events(self) -> list[Record]:
        return self._local.read_events()

    def read_transit(self) -> list[Record]:
        return self._local.read_transit()

    def write_transit(self, entries: Iterable[Mapping[str, Any]]) -> None:
        self._local.write_transit(entries)

    def digest_days(self) -> list[str]:
        return self._local.digest_days()

    def write_digest(self, day: str, text: str) -> None:
        self._local.write_digest(day, text)

    def write_digest_site(self, day: str, files: Mapping[str, str | bytes]) -> None:
        self._local.write_digest_site(day, files)

    def digest_site(self, day: str) -> Path | None:
        return self._local.digest_site(day)

    def read_transit_targets(self) -> dict[str, Any]:
        return self._local.read_transit_targets()

    def write_transit_targets(self, points: Mapping[str, Mapping[str, Any]]) -> None:
        self._local.write_transit_targets(points)

    def read_transit_map(self) -> dict[str, Any]:
        return self._local.read_transit_map()

    def write_transit_map(self, data: Mapping[str, Any]) -> None:
        self._local.write_transit_map(data)

    def pull(self) -> None:
        self._workdir.mkdir(parents=True, exist_ok=True)
        if not (self._workdir / ".git").exists():
            self._run("init", "-q", "-b", self._branch)
        url = self._authenticated_url()
        if not self._run("ls-remote", url, f"refs/heads/{self._branch}").strip():
            return
        self._run("fetch", "-q", "--depth", "1", url, self._branch)
        self._run("checkout", "-q", "-f", "-B", self._branch, "FETCH_HEAD")

    def push(self, message: str = "Update library") -> bool:
        self._run("add", "-A")
        if not self._run("status", "--porcelain").strip():
            return False
        self._run("commit", "-q", "-m", redact(message, self._secrets()))
        self._run("push", "-q", self._authenticated_url(), f"HEAD:refs/heads/{self._branch}")
        return True

    def _authenticated_url(self) -> str:
        return authenticated_url(self._repo_url, self._token)

    def _secrets(self) -> tuple[str, ...]:
        # git names the host and repository path on their own in its errors
        # ("Could not resolve host", "repository not found"), so every part of
        # the location is scrubbed, not just the URL as a whole.
        parts = urlsplit(self._repo_url)
        path = parts.path.strip("/").removesuffix(".git")
        return tuple(
            secret
            for secret in (
                self._authenticated_url(),
                self._repo_url,
                quote(self._token, safe=""),
                self._token,
                parts.hostname or "",
                path,
            )
            if secret
        )

    def _run(self, *args: str) -> str:
        command = [
            self._git,
            "-C",
            str(self._workdir),
            "-c",
            f"user.name={self._author_name}",
            "-c",
            f"user.email={self._author_email}",
            *args,
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise GitLibraryError(f"git {args[0]} failed: {redact(detail, self._secrets())}")
        return completed.stdout


_LINE_SEPARATORS = str.maketrans({"\x85": "\\u0085", " ": "\\u2028", " ": "\\u2029"})


def _json_line(entry: Mapping[str, Any]) -> str:
    # Sellers paste U+2028 into descriptions; JSON leaves it raw, and every
    # line-oriented tool then sees two lines. Escape so one record is one line.
    return json.dumps(dict(entry), ensure_ascii=False).translate(_LINE_SEPARATORS) + "\n"


def _append_jsonl(path: Path, entries: Iterable[Mapping[str, Any]]) -> None:
    payload = "".join(_json_line(entry) for entry in entries)
    if not payload:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _days(directory: Path) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("*.jsonl"))


def _read_jsonl(path: Path) -> list[Record]:
    if not path.exists():
        return []
    entries: list[Record] = []
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as error:
                raise LibraryError(f"{path.name} line {number} is not valid JSON: {error}") from error
            if not isinstance(entry, dict):
                raise LibraryError(f"{path.name} line {number} is not a JSON object")
            entries.append(entry)
    return entries


def _atomic_write(path: Path, lines: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            for line in lines:
                handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
