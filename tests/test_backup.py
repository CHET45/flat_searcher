import sqlite3
import tempfile
from pathlib import Path
from unittest import TestCase

from flat_searcher.db import ListingRepository, open_database
from flat_searcher.db.bootstrap import init_database
from flat_searcher.models import ListingPayload
from flat_searcher.services.backup import backup_database


class BackupDatabaseTests(TestCase):
    def test_backup_creates_consistent_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "flat_searcher.sqlite3"
            init_database(database_path)
            with open_database(database_path) as connection:
                repository = ListingRepository(connection)
                run_id = repository.create_app_run("test", "2026-06-17T12:00:00+00:00")
                repository.upsert_listing(
                    ListingPayload(
                        ss_id="backup-1",
                        ss_url="https://www.ss.com/msg/backup-1.html",
                        price_eur=90_000,
                        area_m2=50.0,
                        declared_rooms_ss=2,
                        description_text="Text",
                    ),
                    app_run_id=run_id,
                    checked_at="2026-06-17T12:00:01+00:00",
                )

            result = backup_database(database_path, Path(temp_dir) / "backup.sqlite3")

            self.assertTrue(result.backup_path.exists())
            self.assertGreater(result.size_bytes, 0)
            copy = sqlite3.connect(result.backup_path)
            try:
                count = copy.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
            finally:
                copy.close()
            self.assertEqual(count, 1)

    def test_backup_into_directory_generates_timestamped_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "flat_searcher.sqlite3"
            init_database(database_path)
            output_dir = Path(temp_dir) / "backups"
            output_dir.mkdir()

            result = backup_database(database_path, output_dir)

            self.assertEqual(result.backup_path.parent, output_dir)
            self.assertTrue(result.backup_path.name.startswith("flat_searcher-backup-"))

    def test_missing_database_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(FileNotFoundError):
                backup_database(Path(temp_dir) / "missing.sqlite3")
