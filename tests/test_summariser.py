"""Verify the digest pipeline: discover → extract → summarise → render → deliver."""

import json
import smtplib
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests
from google.genai import errors
from pypdf import PdfWriter

import summariser as s

FIXTURES = Path(__file__).parent / 'fixtures'


def test_extract_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discover multiple unique path matches without CSS dependencies.

    Args:
        monkeypatch: Pytest patch manager.
    Returns:
        None.
    """
    response = MagicMock(content=(FIXTURES / 'news.html').read_bytes())
    get = MagicMock(return_value=response)
    monkeypatch.setattr(s.requests, 'get', get)
    briefs = s.extract_data(s.NEWS_URL)
    assert [(b.title, b.url) for b in briefs] == [
        ('News Brief & Markets', 'https://www.bog.gov.gh/news_briefs/news-brief-14-10-26/'),
        ('Previous brief', 'https://www.bog.gov.gh/news_briefs/news-brief-13-10-26/'),
    ]
    response.raise_for_status.assert_called_once()
    assert get.call_args.kwargs == {'timeout': 15, 'headers': s.HEADERS}


@pytest.mark.parametrize('function', [s.extract_data, s.find_pdf_url, s.fetch_pdf_text])
def test_http_failure(monkeypatch: pytest.MonkeyPatch, function: Callable[[str], object]) -> None:
    """Propagate HTTP failures from each fetching function.

    Args:
        monkeypatch: Pytest patch manager.
        function: Fetching function under test.
    Returns:
        None.
    """
    response = MagicMock()
    response.raise_for_status.side_effect = requests.HTTPError('failure')
    monkeypatch.setattr(s.requests, 'get', MagicMock(return_value=response))
    with pytest.raises(requests.HTTPError):
        function('https://example.com')


@pytest.mark.parametrize('content, expected', [
    ((FIXTURES / 'brief.html').read_bytes(), 'https://www.bog.gov.gh/wp-content/uploads/2026/10/news.PDF?download=1'),
    (b'<a>No href</a><a href="/file.txt">Text</a>', None),
])
def test_find_pdf_url(monkeypatch: pytest.MonkeyPatch, content: bytes, expected: str | None) -> None:
    """Resolve the first PDF, including relative URLs and query strings.

    Args:
        monkeypatch: Pytest patch manager.
        content: Wrapper-page fixture.
        expected: Expected PDF URL or absence.
    Returns:
        None.
    """
    monkeypatch.setattr(s.requests, 'get', MagicMock(return_value=MagicMock(content=content)))
    assert s.find_pdf_url('https://www.bog.gov.gh/brief/') == expected


def test_pdf_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Normalise whitespace and bound PDF text passed to Gemini.

    Args:
        monkeypatch: Pytest patch manager.
    Returns:
        None.
    """
    monkeypatch.setattr(s.requests, 'get', MagicMock(return_value=MagicMock(content=b'pdf')))
    monkeypatch.setattr(s, 'PdfReader', MagicMock(return_value=MagicMock(pages=[
        MagicMock(extract_text=lambda: '  Banking\n\tnews  '),
        MagicMock(extract_text=lambda: 'Fintech ' * 100),
    ])))
    assert s.fetch_pdf_text('https://example.com/a.pdf', max_chars=20) == 'Banking news Fintech'


def test_scanned_pdf(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Skip an actual blank PDF without calling Gemini.

    Args:
        monkeypatch: Pytest patch manager.
        caplog: Captured log records.
    Returns:
        None.
    """
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = BytesIO()
    writer.write(buffer)
    monkeypatch.setattr(s.requests, 'get', MagicMock(return_value=MagicMock(content=buffer.getvalue())))
    assert s.fetch_pdf_text('https://example.com/a.pdf') is None
    assert 'no extractable text' in caplog.text


def test_build_html() -> None:
    """Escape untrusted text and attributes and render missing summaries.

    Args:
        None.
    Returns:
        None.
    """
    body = s.build_html([
        s.Brief('<script>title</script>', 'https://example.com', 'https://example.com/a.pdf?x="&y=1', '- <img src=x>\n- Banking & fintech'),
        s.Brief('Missing', 'https://example.com/missing'),
    ])
    assert '<script>' not in body and '<img src=x>' not in body
    assert '&lt;script&gt;title&lt;/script&gt;' in body
    assert '<li>&lt;img src=x&gt;</li>' in body
    assert '&quot;&amp;y=1' in body
    assert 'No summary available' in body and 'Source PDF' in body


@pytest.mark.parametrize('output', ['- Banking story.', None, ''])
def test_summarise(monkeypatch: pytest.MonkeyPatch, output: str | None) -> None:
    """Use Gemini configuration and tolerate empty model responses.

    Args:
        monkeypatch: Pytest patch manager.
        output: Simulated model response text.
    Returns:
        None.
    """
    factory = MagicMock()
    generate = factory.return_value.__enter__.return_value.models.generate_content
    generate.return_value = MagicMock(text=output)
    monkeypatch.setattr(s.genai, 'Client', factory)
    monkeypatch.setenv('GEMINI_MODEL', 'configured-model')
    assert s.summarise('Brief', 'x' * 200) == (output or None)
    kwargs = generate.call_args.kwargs
    assert kwargs['model'] == 'configured-model'
    assert kwargs['config'].temperature == 0.2
    assert kwargs['config'].max_output_tokens == 1500


def test_summarise_short_and_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid short-input calls and degrade gracefully on API errors.

    Args:
        monkeypatch: Pytest patch manager.
    Returns:
        None.
    """
    assert s.summarise('Brief', 'short') is None
    factory = MagicMock()
    factory.return_value.__enter__.return_value.models.generate_content.side_effect = errors.APIError(429, {'message': 'limit'})
    monkeypatch.setattr(s.genai, 'Client', factory)
    assert s.summarise('Brief', 'x' * 200) is None


def test_seen_round_trip() -> None:
    """Persist unique URLs and handle a first run without state.

    Args:
        None.
    Returns:
        None.
    """
    assert s.load_seen() == set()
    s.save_seen({'https://example.com/b', 'https://example.com/a'})
    assert s.load_seen() == {'https://example.com/a', 'https://example.com/b'}
    assert json.loads(Path('seen.json').read_text()) == sorted(s.load_seen())


@pytest.mark.parametrize('data', ['{}', '[1]', 'invalid'])
def test_corrupt_seen(data: str) -> None:
    """Reject corrupt state rather than accidentally resending old briefs.

    Args:
        data: Invalid state contents.
    Returns:
        None.
    """
    Path('seen.json').write_text(data)
    with pytest.raises(ValueError):
        s.load_seen()


@pytest.mark.parametrize('dry_run, fails', [(True, False), (False, True), (False, False)])
def test_delivery_state(monkeypatch: pytest.MonkeyPatch, dry_run: bool, fails: bool) -> None:
    """Update seen URLs only after successful delivery, never on preview.

    Args:
        monkeypatch: Pytest patch manager.
        dry_run: Whether to save a preview.
        fails: Whether SMTP fails.
    Returns:
        None.
    """
    old = 'https://example.com/old'
    new = s.Brief('New', 'https://example.com/new')
    s.save_seen({old})
    monkeypatch.setattr(s, 'extract_data', MagicMock(return_value=[s.Brief('Old', old), new]))
    process = MagicMock(side_effect=lambda brief: brief)
    monkeypatch.setattr(s, 'process_brief', process)
    send = MagicMock(side_effect=smtplib.SMTPException('failure') if fails else None)
    monkeypatch.setattr(s, 'send_email', send)
    assert s.main(['--dry-run'] if dry_run else []) == (1 if fails else 0)
    process.assert_called_once_with(new)
    assert s.load_seen() == ({old, new.url} if not dry_run and not fails else {old})
    if dry_run:
        send.assert_not_called()
        assert Path('preview.html').exists()


def test_no_new_briefs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit successfully without summarising or emailing old briefs.

    Args:
        monkeypatch: Pytest patch manager.
    Returns:
        None.
    """
    brief = s.Brief('Old', 'https://example.com/old')
    s.save_seen({brief.url})
    monkeypatch.setattr(s, 'extract_data', MagicMock(return_value=[brief]))
    process = MagicMock()
    monkeypatch.setattr(s, 'process_brief', process)
    send = MagicMock()
    monkeypatch.setattr(s, 'send_email', send)
    assert s.main([]) == 0
    process.assert_not_called()
    send.assert_not_called()


def test_max_briefs_and_failure_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Limit processing and keep working after one brief request fails.

    Args:
        monkeypatch: Pytest patch manager.
    Returns:
        None.
    """
    monkeypatch.setattr(s, 'extract_data', MagicMock(return_value=[s.Brief(str(i), f'https://example.com/{i}') for i in range(5)]))
    find = MagicMock(side_effect=[requests.Timeout(), 'https://example.com/b.pdf', None])
    monkeypatch.setattr(s, 'find_pdf_url', find)
    monkeypatch.setattr(s, 'fetch_pdf_text', MagicMock(return_value='x' * 200))
    monkeypatch.setattr(s, 'summarise', MagicMock(return_value='- Banking story.'))
    assert s.main(['--dry-run']) == 0
    assert find.call_count == s.MAX_BRIEFS
    assert Path('preview.html').read_text().count('<section>') == 3


def test_missing_credentials(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Fail clearly before any network request when a required key is missing.

    Args:
        monkeypatch: Pytest patch manager.
        caplog: Captured logs.
    Returns:
        None.
    """
    monkeypatch.delenv('GEMINI_API_KEY')
    assert s.main(['--dry-run']) == 1
    assert 'Missing required environment variable: GEMINI_API_KEY' in caplog.text


def test_send_email(monkeypatch: pytest.MonkeyPatch) -> None:
    """Encrypt SMTP before login, default the recipient, and avoid debugging.

    Args:
        monkeypatch: Pytest patch manager.
    Returns:
        None.
    """
    factory = MagicMock()
    monkeypatch.setattr(s.smtplib, 'SMTP', factory)
    s.send_email('<p>Digest</p>')
    server = factory.return_value.__enter__.return_value
    names = [call[0] for call in server.method_calls]
    assert names == ['ehlo', 'starttls', 'ehlo', 'login', 'sendmail']
    assert server.sendmail.call_args.args[1] == ['learner@example.com']
    assert 'Bank of Ghana News Digest [Automated]' in server.sendmail.call_args.args[2]
    server.set_debuglevel.assert_not_called()
