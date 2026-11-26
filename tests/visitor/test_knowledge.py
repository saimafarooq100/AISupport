import json

from ai_support.visitor.ingest import extract, allowed_page
from ai_support.visitor.knowledge import answer, retrieve, verify_quotes


def test_website_extract_and_retrieve(tmp_path):
    url = 'https://www.uowdubai.ac.ae/join-uowd/fees/masters-degree'
    chunks, _ = extract('<html><main><h1>Master fees</h1><p>Master of Business Administration</p><table><tr><th>Subject fees</th></tr><tr><td>AED 1234</td></tr></table></main></html>', url, 'demo')
    path = tmp_path / 'website.json'
    path.write_text(json.dumps({'chunks': chunks}), encoding='utf-8')
    found = retrieve('Master of Business Administration subject fees', path)
    assert any('AED 1234' in p['text'] for p in found)
    assert not allowed_page('https://www.uowdubai.ac.ae.evil.test/degrees')


def test_quotes_cannot_invent_fees():
    p = [{'text': 'MBA subject fees are AED 1234.', 'title': 'Fees', 'url': 'https://www.uowdubai.ac.ae/'}]
    assert verify_quotes([{'id': 0, 'quote': 'MBA subject fees are AED 1234.'}], p)
    assert verify_quotes([{'id': 0, 'quote': 'MBA subject fees are AED 1000.'}], p) is None
    assert verify_quotes([{'id': -1, 'quote': p[0]['text']}], p) is None


def test_broad_fee_question():
    result = answer('What are the fees?')
    assert not result['can_raise_ticket'] and not result['sources']
    assert 'Which programme' in result['answer']


def test_visitor_access_and_consent(monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from ai_support.visitor import routes

    app = FastAPI()
    principal = SimpleNamespace(audience='student', user_id='S100001')
    routes.register_visitor_routes(app, lambda: principal)
    monkeypatch.setattr(routes, 'answer', lambda q: {'answer': 'Public only', 'sources': [], 'can_raise_ticket': False})
    client = TestClient(app)
    assert client.post('/v1/visitor/chat', json={'question': 'Public question'}).status_code == 200
    assert client.get('/v1/visitor/admissions/tickets').status_code == 403
    payload = {'request_id': str(uuid4()), 'name': 'Demo Visitor', 'email': 'visitor@example.test', 'question': 'Demo question', 'consent': False}
    assert client.post('/v1/visitor/tickets', json=payload).status_code == 422
    payload.update(consent=True, email='visitor@example.com')
    assert client.post('/v1/visitor/tickets', json=payload).status_code == 422
