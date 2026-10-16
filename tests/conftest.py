"""Keep tests isolated: fake credentials → block network → use temporary state."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import summariser as s


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Prevent real HTTP, Gemini, SMTP, and persistent state in every test.

    Args:
        monkeypatch: Pytest patch manager.
        tmp_path: Temporary working directory.
    Returns:
        None.
    """
    monkeypatch.chdir(tmp_path)
    for name in ('GEMINI_API_KEY', 'GMAIL_USER', 'GMAIL_APP_PASSWORD', 'MAIL_TO', 'GEMINI_MODEL'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('GEMINI_API_KEY', '********')
    monkeypatch.setenv('GMAIL_USER', 'learner@example.com')
    monkeypatch.setenv('GMAIL_APP_PASSWORD', '********')
    monkeypatch.setattr(s.requests, 'get', MagicMock(side_effect=AssertionError('HTTP not mocked')))
    monkeypatch.setattr(s.genai, 'Client', MagicMock(side_effect=AssertionError('Gemini not mocked')))
    monkeypatch.setattr(s.smtplib, 'SMTP', MagicMock(side_effect=AssertionError('SMTP not mocked')))
