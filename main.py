"""Convenience entry point for running Flat Searcher from an IDE (e.g. PyCharm).

Right-click this file and choose "Run 'main'" to launch the desktop UI against
the bundled ``data/flat_searcher.sqlite3`` database.

To run any other subcommand, either edit the run configuration's parameters
(Run > Edit Configurations > Parameters) or pass arguments on the command line,
for example::

    python main.py show-ranking --database data/flat_searcher.sqlite3
    python main.py process-listings --database data/flat_searcher.sqlite3 --mock

This is identical to invoking ``python -m flat_searcher <args>``; it only adds a
sensible default (launch the UI) when no arguments are given.
"""

from __future__ import annotations

import sys

from flat_searcher.cli import main

DEFAULT_ARGS = ["run-ui", "--database", "data/flat_searcher.sqlite3"]


if __name__ == "__main__":
    argv = sys.argv[1:] or DEFAULT_ARGS
    raise SystemExit(main(argv))
