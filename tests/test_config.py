import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from flat_searcher.config import AppConfig


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
                        "GEMINI_API_KEY=file-key",
                        "GEMINI_MODEL=file-model",
                        "OVERPASS_ENDPOINT=https://file-overpass.test/api",
                    )
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "FLAT_SEARCHER_ENV_FILE": str(environment_file),
                    "GEMINI_MODEL": "process-model",
                },
                clear=True,
            ):
                config = AppConfig.from_env()

            self.assertEqual(config.app_home, root / "file-home")
            self.assertEqual(config.gemini_api_key, "file-key")
            self.assertEqual(config.gemini_model, "process-model")
            self.assertEqual(
                config.overpass_endpoint,
                "https://file-overpass.test/api",
            )
