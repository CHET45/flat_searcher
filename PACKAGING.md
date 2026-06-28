# Packaging Flat Searcher For Windows

This document describes the recommended strategy for distributing the desktop app
on Windows once the MVP has stabilized. Packaging is intentionally deferred until
the core analysis loop is stable, so this is a strategy guide rather than a build
script.

## Goals

1. Produce a self-contained Windows build that a non-developer can run.
2. Bundle the optional desktop UI (PySide6 + Qt WebEngine) and the embedded
   Leaflet map.
3. Keep the user's local database, image cache and temporary files outside the
   packaged application directory so app updates never destroy analyzed data.

## Recommended Tool

Use **PyInstaller**. It supports PySide6 and Qt WebEngine through official hooks
and produces a standard Windows distributable.

```powershell
python -m pip install -e ".[ai,ui]"
python -m pip install pyinstaller
```

## Runtime Data Lives Outside The Bundle

The application already resolves all runtime paths through `AppConfig` (app home,
database, cache, temporary images, floor plans). Packaging must NOT place the
database inside the PyInstaller bundle. Keep using the per-user app home
(`FLAT_SEARCHER_*` environment variables or the default user directory) so that:

- existing analyzed listings, scores and user workflow state survive updates;
- a reinstall does not wipe the SQLite database;
- the backup command (`flat-searcher backup-db`) remains the supported way to
  export user data.

## Qt WebEngine Considerations

The embedded map uses `QWebEngineView`. When packaging:

1. Build with the `--collect-all PySide6` style data collection so the Qt WebEngine
   process, resources and translations are bundled.
2. Verify the map renders from the packaged build, not only from source, because
   WebEngine ships extra runtime files (`QtWebEngineProcess.exe`, ICU data,
   locales) that are easy to miss.
3. The Leaflet assets are generated as inline HTML, so no separate web bundle is
   required, but an internet connection is still needed for map tiles.

## Optional Integrations

The packaged build should include the UI extra. The AI (`google-genai`) extra is
optional at runtime:

- AI analysis requires `GEMINI_API_KEY`; without it the app still runs using
  previously stored analyses and the deterministic local pipeline.
- Geocoding uses the standard library and makes external network requests as an
  explicit user action, so it needs no extra dependency.

Decide per release whether to bundle the AI extra or document it as an optional
install step.

## Suggested Build Outline

```powershell
pyinstaller `
  --name FlatSearcher `
  --windowed `
  --collect-all PySide6 `
  --collect-submodules flat_searcher `
  src/flat_searcher/__main__.py
```

After building:

1. Launch the packaged executable on a clean Windows profile.
2. Run `init-db`, `sync-listings --limit 2`, `process-listings --mock` and
   `run-ui` to confirm the full path works from the bundle.
3. Confirm the map tab renders and that `backup-db` writes a usable copy.

## Reliability Checklist Before Release

- [ ] Existing analyzed listings survive an app update (database is external).
- [ ] An invalid AI response does not break a full run (failures are isolated).
- [ ] Parser fixture tests pass, so SS.com HTML changes are detected.
- [ ] `backup-db` exports a consistent copy of the database.
- [ ] Runtime data (database, image cache, temporary files) is git-ignored and
      never committed.
