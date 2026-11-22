import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from desk import ai


class CodexCliTests(unittest.TestCase):
    @patch.dict(os.environ, {"DESK_AI_PROVIDER": "codex"})
    @patch("desk.ai.shutil.which", return_value="/test/codex")
    def test_extracts_from_cli_output_with_original_text_check(self, _which):
        def respond(args, **kwargs):
            self.assertIn("WVA 19032", kwargs["input"])
            self.assertNotIn("WVA 19032", args)
            self.assertEqual(args[args.index("--sandbox") + 1], "read-only")
            output = Path(args[args.index("--output-last-message") + 1])
            output.write_text(json.dumps({"code": "19032", "namespace": "WVA", "axle": "invented"}))

        with patch("desk.ai.subprocess.run", side_effect=respond), patch("desk.ai.urlopen") as http:
            self.assertEqual(ai.assist("query", "WVA 19032"), {"code": "19032", "namespace": "WVA"})
            http.assert_not_called()

    @patch.dict(os.environ, {"DESK_AI_PROVIDER": "codex"})
    @patch("desk.ai.shutil.which", return_value="/test/codex")
    def test_timeout_leaves_buyer_for_review(self, _which):
        with patch("desk.ai.subprocess.run", side_effect=subprocess.TimeoutExpired("codex", 100)):
            self.assertFalse(ai.assist("buyer", "Our fleet")["supported"])
