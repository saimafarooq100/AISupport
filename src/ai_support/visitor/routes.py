"""Independent visitor endpoints; authenticated demo Admissions queue."""

import os
import re
from uuid import UUID

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from ai_support.visitor.knowledge import answer


class VisitorQuestion(BaseModel):
    question: str = Field(min_length=3, max_length=1500)


class TicketRequest(BaseModel):
    request_id: UUID
    name: str = Field(min_length=2, max_length=100)
    email: str = Field(min_length=5, max_length=150)
    question: str = Field(min_length=3, max_length=1500)
    consent: bool


class TicketUpdate(BaseModel):
    status: str
    resolution: str = Field(default='', max_length=2000)


def connection():
    import psycopg
    url = os.getenv('AI_SUPPORT_DATABASE_URL')
    if not url:
        raise HTTPException(503, 'Ticket database is not configured')
    return psycopg.connect(url, connect_timeout=5)


def prepare(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS campus_visitor_tickets (
        request_id uuid PRIMARY KEY, name text NOT NULL, email text NOT NULL,
        question text NOT NULL, department text NOT NULL DEFAULT 'Admissions',
        status text NOT NULL DEFAULT 'Open', resolution text NOT NULL DEFAULT '',
        created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now())''')


def register_visitor_routes(app, current_principal):
    def admissions_only(principal=Depends(current_principal)):
        # Explicit synthetic role for this demonstration, not a real staff entitlement.
        allowed = os.getenv('AI_SUPPORT_DEMO_ADMISSIONS_ID', 'E100001')
        if principal.audience != 'staff' or principal.user_id != allowed:
            raise HTTPException(403, 'Admissions queue access denied')
        return principal

    @app.post('/v1/visitor/chat', tags=['visitor'])
    def visitor_chat(payload: VisitorQuestion):
        return answer(payload.question)

    @app.post('/v1/visitor/tickets', tags=['visitor'])
    def create_ticket(payload: TicketRequest):
        if not payload.consent or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', payload.email):
            raise HTTPException(422, 'Consent and a valid demo email are required')
        if not payload.email.lower().endswith('@example.test'):
            raise HTTPException(422, 'Use a synthetic @example.test email in this local demo')
        with connection() as conn:
            prepare(conn)
            conn.execute('''INSERT INTO campus_visitor_tickets(request_id,name,email,question)
                VALUES (%s,%s,%s,%s) ON CONFLICT(request_id) DO NOTHING''',
                (payload.request_id, payload.name, payload.email, payload.question))
            row = conn.execute('SELECT name,email,question FROM campus_visitor_tickets WHERE request_id=%s', (payload.request_id,)).fetchone()
            if row != (payload.name, payload.email, payload.question):
                raise HTTPException(409, 'Request reference already used')
        return {'reference': 'UOWD-ADM-'+str(payload.request_id).upper(), 'department': 'Admissions', 'status': 'Submitted to local demo queue'}

    @app.get('/v1/visitor/admissions/tickets', tags=['visitor'])
    def admissions_tickets(principal=Depends(admissions_only)):
        del principal
        from psycopg.rows import dict_row
        with connection() as conn:
            prepare(conn)
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute('''SELECT request_id,name,email,question,status,resolution,created_at
                    FROM campus_visitor_tickets ORDER BY created_at DESC LIMIT 100''')
                return {'tickets': cur.fetchall()}

    @app.patch('/v1/visitor/admissions/tickets/{request_id}', tags=['visitor'])
    def update_ticket(request_id: UUID, payload: TicketUpdate, principal=Depends(admissions_only)):
        del principal
        if payload.status not in ('Open', 'In Progress', 'Resolved'):
            raise HTTPException(422, 'Invalid ticket status')
        with connection() as conn:
            prepare(conn)
            result = conn.execute('''UPDATE campus_visitor_tickets SET status=%s,resolution=%s,
                updated_at=now() WHERE request_id=%s''', (payload.status,payload.resolution,request_id))
            if result.rowcount != 1:
                raise HTTPException(404, 'Ticket not found')
        return {'status': payload.status}
