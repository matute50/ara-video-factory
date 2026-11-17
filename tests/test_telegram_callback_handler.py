"""Tests para telegram_callback_handler."""
from __future__ import annotations
import unittest
from unittest.mock import patch, MagicMock, PropertyMock

from src.telegram_callback_handler import (
    _parse_callback_data,
    CallbackUpdate,
    ACTION_HANDLERS,
)


class TestParseCallbackData(unittest.TestCase):
    def test_approve(self):
        action, aid = _parse_callback_data("approve_901")
        self.assertEqual(action, "approve")
        self.assertEqual(aid, 901)

    def test_discard(self):
        action, aid = _parse_callback_data("discard_42")
        self.assertEqual(action, "discard")
        self.assertEqual(aid, 42)

    def test_edit(self):
        action, aid = _parse_callback_data("edit_123")
        self.assertEqual(action, "edit")
        self.assertEqual(aid, 123)

    def test_invalid_format(self):
        action, aid = _parse_callback_data("invalid")
        self.assertEqual(action, "invalid")
        self.assertEqual(aid, 0)

    def test_missing_id(self):
        action, aid = _parse_callback_data("approve_")
        self.assertEqual(action, "approve")
        self.assertEqual(aid, 0)


class TestActionHandlersExist(unittest.TestCase):
    def test_all_actions_have_handlers(self):
        self.assertIn("approve", ACTION_HANDLERS)
        self.assertIn("discard", ACTION_HANDLERS)
        self.assertIn("edit", ACTION_HANDLERS)


if __name__ == "__main__":
    unittest.main()