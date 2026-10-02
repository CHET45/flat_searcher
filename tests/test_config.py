import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from flat_searcher.config import DEFAULT_SS_START_URL, AppConfig


class AppConfigTests(TestCase):
    def test_from_env_loads_local_environment_file_without_overriding_process_env(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            environment_file = root / ".env"
            environment_file.write_text(
                "\n".join(
                    (
                        f"FLAT_SEARCHER_HOME={root / 'file-home'}",
                        "FLAT_SEARCHER_SS_START_URL=https://www.ss.com/lv/from-file/",
                        'FLAT_SEARCHER_LIBRARY_PATH="C:/quoted/path"',
                    )
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "FLAT_SEARCHER_ENV_FILE": str(environment_file),
                    "FLAT_SEARCHER_SS_START_URL": "https://www.ss.com/lv/from-process/",
                },
                clear=True,
            ):
                config = AppConfig.from_env()
                library_path = os.environ.get("FLAT_SEARCHER_LIBRARY_PATH")

            self.assertEqual(config.app_home, root / "file-home")
            self.assertEqual(config.cache_dir, root / "file-home" / "cache")
            self.assertEqual(config.log_file, root / "file-home" / "logs" / "flat_searcher.log")
            self.assertEqual(config.ss_start_url, "https://www.ss.com/lv/from-process/")
            self.assertEqual(library_path, "C:/quoted/path")

    def test_bom_prefixed_environment_file_still_loads_its_first_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            environment_file = Path(temp_dir) / ".env"
            environment_file.write_bytes(
                b"\xef\xbb\xbfFLAT_SEARCHER_SS_START_URL=https://www.ss.com/lv/bom/\n"
            )
            with patch.dict(
                os.environ, {"FLAT_SEARCHER_ENV_FILE": str(environment_file)}, clear=True
            ):
                config = AppConfig.from_env()

            self.assertEqual(config.ss_start_url, "https://www.ss.com/lv/bom/")

    def test_defaults_without_any_environment(self) -> None:
        with patch.dict(
            os.environ, {"FLAT_SEARCHER_ENV_FILE": str(Path(tempfile.gettempdir()) / "no.env")},
            clear=True,
        ):
            config = AppConfig.from_env()

        self.assertEqual(config.ss_start_url, DEFAULT_SS_START_URL)
        self.assertEqual(config.app_home.name, ".flat_searcher")
