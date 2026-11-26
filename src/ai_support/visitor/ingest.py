"""Refresh a bounded public UOWD website snapshot, with source metadata."""

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from ai_support.visitor.knowledge import HOST, official_url, snapshot_path

ROOT = f'https://{HOST}/'
AGENT = 'CampusSupportAcademicDemo'
SEEDS = [ROOT, ROOT+'degrees', ROOT+'join-uowd/fees/bachelors-degree',
         ROOT+'join-uowd/fees/masters-degree', ROOT+'join-uowd/admission-requirements',
         ROOT+'join-uowd/scholarships']


def allowed_page(url):
    if not official_url(url):
        return False
    p = urlsplit(url)
    return not p.query and not p.fragment and (p.path in ('', '/') or
        p.path.startswith(('/degrees', '/join-uowd', '/about/our-campus', '/about/accreditation')))


def get_page(client, url, robots=None):
    for _ in range(4):
        if not official_url(url) or (robots and not robots.can_fetch(AGENT, url)):
            raise ValueError('URL not permitted')
        with client.stream('GET', url) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers['location'])
                continue
            response.raise_for_status()
            chunks = bytearray()
            for part in response.iter_bytes():
                chunks.extend(part)
                if len(chunks) > 3_000_000:
                    raise ValueError('Page too large')
            return url, bytes(chunks)
    raise ValueError('Too many redirects')


def clean(text):
    return re.sub(r'\s+', ' ', text).strip()


def extract(html, url, retrieved):
    soup = BeautifulSoup(html, 'html.parser')
    links = set()
    for a in soup.select('a[href]'):
        target = urljoin(url, a['href']).split('#')[0]
        if allowed_page(target):
            links.add(target)
    for element in soup.select('script, style, nav, header, footer, aside, form, noscript, [role=navigation], .breadcrumb'):
        element.decompose()
    root = soup.select_one('main, [role=main], #main-content, .region-content') or soup.body or soup
    h1 = root.find('h1') or soup.find('title')
    title = clean(h1.get_text(' ', strip=True)) if h1 else url
    section = title
    previous = ''
    records = []
    for node in root.find_all(['h1', 'h2', 'h3', 'h4', 'p', 'li', 'table']):
        if node.find_parent('table') or (node.name == 'p' and node.find_parent('li')):
            continue
        if node.name.startswith('h'):
            section = clean(node.get_text(' ', strip=True)) or section
            continue
        if node.name == 'li' and node.find('li'):
            continue
        if node.name == 'table':
            rows = [' | '.join(clean(cell.get_text(' ', strip=True)) for cell in row.find_all(['th', 'td'])) for row in node.find_all('tr')]
            body = '\n'.join(rows)
            if previous and len(previous) < 180:
                body = previous + '\n' + body
        else:
            body = clean(node.get_text(' ', strip=True))
            previous = body
        if len(body) < 20:
            continue
        for offset in range(0, len(body), 1200):
            piece = body[offset:offset+1450]
            text = f'{section}\n{piece}'
            records.append({'title': title, 'url': url, 'text': text, 'retrieved_at': retrieved,
                            'content_hash': hashlib.sha256(text.encode()).hexdigest()})
    unique = {r['content_hash']: r for r in records}
    return list(unique.values()), links


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--max-pages', type=int, default=65, choices=range(1, 151), metavar='1-150')
    parser.add_argument('--output', default=str(snapshot_path()))
    args = parser.parse_args()
    from pathlib import Path
    destination = Path(args.output)
    retrieved = datetime.now(timezone.utc).isoformat()
    queue = list(SEEDS)
    visited = set()
    chunks = []
    with httpx.Client(timeout=15, headers={'User-Agent': AGENT}, follow_redirects=False) as client:
        robots = RobotFileParser()
        try:
            _, raw = get_page(client, ROOT+'robots.txt')
            robots.parse(raw.decode('utf-8', errors='replace').splitlines())
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404:
                robots.parse([])
            else:
                raise SystemExit('Could not verify robots.txt; refresh stopped.') from exc
        while queue and len(visited) < args.max_pages:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            try:
                final, html = get_page(client, url, robots)
                if not allowed_page(final):
                    continue
                records, links = extract(html, final, retrieved)
                if records:
                    chunks.extend(records)
                    print(f'{len(visited)}: {len(records)} passages — {final}', flush=True)
                # Prefer fee/admission/scholarship and programme pages, not media or forms.
                for link in sorted(links, key=lambda x: (not x.startswith(ROOT+'join-uowd'), x)):
                    if link not in visited and link not in queue and len(queue) < 400:
                        queue.append(link)
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                print(f'Skipped {url}: {type(exc).__name__}', flush=True)
            time.sleep(max(0.6, robots.crawl_delay(AGENT) or 0))
    if not chunks:
        raise SystemExit('No public website passages collected. Existing snapshot unchanged.')
    unique = {(r['url'], r['content_hash']): r for r in chunks}
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(json.dumps({'retrieved_at': retrieved, 'chunks': list(unique.values())}, ensure_ascii=False), encoding='utf-8')
    temporary.replace(destination)
    print(f'Saved {len(unique)} public passages from {len(set(r["url"] for r in chunks))} pages to {destination}')


if __name__ == '__main__':
    main()
