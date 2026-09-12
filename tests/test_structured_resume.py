from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.structured_automation import StructuredExecutor, validate_plan
from core.structured_sessions import resume_session


class StructuredResumeTests(unittest.TestCase):
    def test_resume_executes_the_exact_selected_checkpoint(self) -> None:
        plan = validate_plan({
            "summary": "resume exact plan",
            "steps": [{"action": "wait", "args": {"seconds": 0}}],
        })
        session_id = "a" * 20
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            checkpoint = directory / f"{session_id}.json"
            StructuredExecutor(execution_id="saved-execution")._save_checkpoint(
                checkpoint,
                plan,
                [],
                0,
                goal="wait exactly once",
            )

            result = resume_session(
                session_id,
                directory=directory,
                approve_all=True,
            )
            saved = json.loads(checkpoint.read_text())

        self.assertTrue(result.success)
        self.assertEqual(result.completed_steps, 1)
        self.assertTrue(saved["complete"])
        self.assertEqual(saved["execution_id"], "saved-execution")

    def test_uncertain_checkpoint_is_never_resumed(self) -> None:
        plan = validate_plan({
            "summary": "uncertain click",
            "steps": [{"action": "click", "args": {"text": "Submit"}}],
        })
        session_id = "b" * 20
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            checkpoint = directory / f"{session_id}.json"
            StructuredExecutor(approve_all=True)._save_checkpoint(
                checkpoint,
                plan,
                [],
                0,
                in_progress=1,
                phase="dispatched",
                uncertain=True,
                goal="submit once",
            )

            result = resume_session(
                session_id,
                directory=directory,
                approve_all=True,
            )

        self.assertFalse(result.success)
        self.assertIn("uncertain outcome", result.message)
        self.assertEqual(result.completed_steps, 0)


if __name__ == "__main__":
    unittest.main()
