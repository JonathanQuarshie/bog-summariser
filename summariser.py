"""Build a Bank of Ghana digest: discover briefs → render HTML → send email."""

import html
import logging
import os
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

NEWS_URL = 'https://www.bog.gov.gh/news/'
HEADERS = {'User-Agent': 'BoG-News-Digest/1.0'}
logger = logging.getLogger(__name__)


@dataclass
class Brief:
    """A discovered news brief and its source page."""

    title: str
    url: str


# --- Fetching ---
def extract_data(url: str) -> list[Brief]:
    """Discover unique news briefs in index order.

    Args:
        url: News index URL.
    Returns:
        Brief titles and absolute URLs, newest first as listed by the index.
    """
    # A descriptive User-Agent identifies the job to the site's operators.
    # A timeout prevents hangs; raise_for_status makes HTTP failures explicit.
    response = requests.get(url, timeout=15, headers=HEADERS)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, 'html.parser')
    briefs = []
    seen = set()
    # Original bug: td.tittle belonged to Hacker News; Elementor CSS is unstable.
    # Match URL paths so changing presentation classes do not break discovery.
    for anchor in soup.find_all('a', href=True):
        href = anchor.get('href')
        if not isinstance(href, str):
            continue
        if '/news_briefs/news-brief-' not in urlsplit(href).path:
            continue
        # urljoin resolves relative hrefs against the page that supplied them.
        brief_url = urljoin(url, href)
        if brief_url in seen:
            continue
        seen.add(brief_url)
        # Original bug: tag.a could be missing; select anchors directly instead.
        briefs.append(Brief(anchor.get_text(' ', strip=True) or 'News brief', brief_url))
    # Original bug: returning inside the loop discarded every subsequent item.
    return briefs


# --- Email ---
def build_html(briefs: list[Brief]) -> str:
    """Render discovered briefs as a safe HTML email.

    Args:
        briefs: Discovered brief titles and URLs.
    Returns:
        An HTML document suitable for email.
    """
    # Original bug: raw scraped text was concatenated into HTML; escape untrusted text.
    items = ''.join(
        f'<li><a href="{html.escape(brief.url, quote=True)}">'
        f'{html.escape(brief.title)}</a></li>' for brief in briefs
    )
    return f'<!doctype html><html><body><h1>Bank of Ghana News</h1><ul>{items}</ul></body></html>'


def required_env(name: str) -> str:
    """Read a required environment variable without exposing its value.

    Args:
        name: Environment variable name.
    Returns:
        Its nonempty value; raises ValueError if missing.
    """
    value = os.environ.get(name, '').strip()
    if not value:
        raise ValueError(f'Missing required environment variable: {name}')
    return value


def send_email(body: str) -> None:
    """Send one digest using Gmail credentials from the environment.

    Args:
        body: Rendered HTML digest.
    Returns:
        None; SMTP failures propagate to the caller.
    """
    sender = required_env('GMAIL_USER')
    password = required_env('GMAIL_APP_PASSWORD')
    recipient = os.environ.get('MAIL_TO') or sender
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f'Financial News updates [Automated Email] {datetime.now():%d-%m-%Y}'
    msg['From'] = sender
    msg['To'] = recipient
    msg.attach(MIMEText(body, 'html', 'utf-8'))
    # Original bug: SMTP debugging exposed authentication and email contents.
    with smtplib.SMTP('smtp.gmail.com', 587, timeout=15) as server:
        server.ehlo()
        # STARTTLS encrypts credentials and content before authentication.
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
        server.login(sender, password)
        server.sendmail(sender, [recipient], msg.as_string())
    logger.info('Digest email sent.')


def main() -> int:
    """Discover briefs and send a digest with safe error reporting.

    Args:
        None.
    Returns:
        Zero on success, one on configuration, HTTP, or SMTP failure.
    """
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    try:
        required_env('GMAIL_USER')
        required_env('GMAIL_APP_PASSWORD')
        briefs = extract_data(NEWS_URL)
        if not briefs:
            logger.info('No news briefs found.')
            return 0
        send_email(build_html(briefs))
        return 0
    except ValueError as exc:
        logger.error('%s', exc)
    except (requests.RequestException, smtplib.SMTPException, OSError) as exc:
        # Exception messages may contain remote response bodies; log types only.
        logger.error('Digest failed (%s).', type(exc).__name__)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
