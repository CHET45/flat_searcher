# Running in PyCharm

The project is already configured for PyCharm:

- **Interpreter:** `Python 3.12 (flat_searcher)`, bound to the in-project
  virtual environment at `.venv`.
- **Source root:** `src` (so `import flat_searcher` resolves in the editor).
- **Gemini key:** read from the `GEMINI_API_KEY` user environment variable, so
  PyCharm picks it up automatically when launched from the Start menu.

## First time

1. Open the `flat_searcher` folder in PyCharm (File > Open).
2. If prompted for an interpreter, choose **Existing environment** and point it
   at `.venv\Scripts\python.exe`. (It is usually detected automatically.)
3. Wait for indexing to finish.

## Ready-made run configurations

Pick one from the run-configuration dropdown (top-right toolbar) and press
**Run** (or **Debug**):

| Configuration | What it does |
| --- | --- |
| **Flat Searcher UI** | Launches the desktop app against `data/flat_searcher.sqlite3`. |
| **Process Listings (Mock)** | Full pipeline with deterministic mock AI (no network/AI key). |
| **Process Listings (Gemini)** | Full pipeline using the real Gemini API key. |
| **Show Ranking** | Prints the top ranked apartments to the console. |
| **All Tests (pytest)** | Runs the test suite. |

## Quick alternative

Right-click **`main.py`** > **Run 'main'**. With no arguments it launches the
desktop UI. To run another subcommand, edit the configuration's *Parameters*
field, e.g. `show-ranking --database data/flat_searcher.sqlite3 --limit 10`.

## Notes

- The bundled `data/flat_searcher.sqlite3` already contains synced and
  AI-analyzed listings, so the UI and ranking show data immediately.
- To re-run Gemini analysis on already-analyzed listings, the Gemini
  configuration passes `--force-analysis`.
- If you only need the UI without the AI extras, everything required is already
  installed in `.venv`.
