"""Tests for prax.agent.autonomy — configurable constraint levels."""
from __future__ import annotations

from unittest.mock import patch

import pytest


class TestGetAutonomyLevel:
    def test_default_is_guided(self):
        from prax.agent.autonomy import get_autonomy_level
        # Default settings have autonomy="guided"
        with patch("prax.agent.autonomy.settings") as mock_settings:
            mock_settings.autonomy = "guided"
            assert get_autonomy_level() == "guided"

    def test_balanced(self):
        from prax.agent.autonomy import get_autonomy_level
        with patch("prax.agent.autonomy.settings") as mock_settings:
            mock_settings.autonomy = "balanced"
            assert get_autonomy_level() == "balanced"

    def test_autonomous(self):
        from prax.agent.autonomy import get_autonomy_level
        with patch("prax.agent.autonomy.settings") as mock_settings:
            mock_settings.autonomy = "autonomous"
            assert get_autonomy_level() == "autonomous"

    def test_invalid_falls_back_to_guided(self):
        from prax.agent.autonomy import get_autonomy_level
        with patch("prax.agent.autonomy.settings") as mock_settings:
            mock_settings.autonomy = "yolo"
            assert get_autonomy_level() == "guided"

    def test_case_insensitive(self):
        from prax.agent.autonomy import get_autonomy_level
        with patch("prax.agent.autonomy.settings") as mock_settings:
            mock_settings.autonomy = "BALANCED"
            assert get_autonomy_level() == "balanced"


class TestGetRecursionLimit:
    def test_guided_unchanged(self):
        from prax.agent.autonomy import get_recursion_limit
        with patch("prax.agent.autonomy.settings") as mock_settings:
            mock_settings.autonomy = "guided"
            assert get_recursion_limit(40) == 40

    def test_balanced_125x(self):
        from prax.agent.autonomy import get_recursion_limit
        with patch("prax.agent.autonomy.settings") as mock_settings:
            mock_settings.autonomy = "balanced"
            assert get_recursion_limit(40) == 50  # int(40 * 1.25)

    def test_autonomous_15x(self):
        from prax.agent.autonomy import get_recursion_limit
        with patch("prax.agent.autonomy.settings") as mock_settings:
            mock_settings.autonomy = "autonomous"
            assert get_recursion_limit(40) == 60  # int(40 * 1.5)

    def test_spoke_limit_scales(self):
        from prax.agent.autonomy import get_recursion_limit
        with patch("prax.agent.autonomy.settings") as mock_settings:
            mock_settings.autonomy = "autonomous"
            assert get_recursion_limit(80) == 120  # int(80 * 1.5)


class TestHelpers:
    def test_is_prescriptive_guided(self):
        from prax.agent.autonomy import is_prescriptive
        with patch("prax.agent.autonomy.settings") as mock_settings:
            mock_settings.autonomy = "guided"
            assert is_prescriptive() is True

    def test_is_prescriptive_balanced(self):
        from prax.agent.autonomy import is_prescriptive
        with patch("prax.agent.autonomy.settings") as mock_settings:
            mock_settings.autonomy = "balanced"
            assert is_prescriptive() is False

    def test_is_autonomous(self):
        from prax.agent.autonomy import is_autonomous
        with patch("prax.agent.autonomy.settings") as mock_settings:
            mock_settings.autonomy = "autonomous"
            assert is_autonomous() is True

    def test_is_autonomous_guided(self):
        from prax.agent.autonomy import is_autonomous
        with patch("prax.agent.autonomy.settings") as mock_settings:
            mock_settings.autonomy = "guided"
            assert is_autonomous() is False
