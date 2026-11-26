"""MY | UOWD Campus Support, using only backend API calls."""

import os
from uuid import uuid4

import streamlit as st

from ai_support.ui.client import APIClientError, SupportAPIClient

st.set_page_config(page_title='MY | UOWD Campus Support', page_icon='🎓', layout='wide')
st.markdown('''
<style>
.block-container {max-width: 1080px; padding-top: 2rem; padding-bottom: 3rem;}
.campus-banner {background:#002b49;color:white;border-radius:16px;padding:1.4rem 1.8rem;margin-bottom:1.6rem;}
.campus-brand {font-size:1.8rem;font-weight:750;letter-spacing:.04em;}
.campus-sub {font-size:.92rem;color:#e3edf5;margin-top:.3rem;}
.campus-card {border:1px solid rgba(125,125,125,.30);border-radius:12px;padding:1rem;min-height:86px;margin-bottom:.6rem;}
.campus-card strong {display:block;margin-bottom:.3rem;}
.campus-card small {opacity:.85;}
[data-testid="stChatMessage"] {border:1px solid rgba(125,125,125,.18);border-radius:12px;padding:.8rem;}
[data-testid="stButton"] button[kind="primary"], [data-testid="stFormSubmitButton"] button[kind="primary"] {background:#002b49;border-color:#002b49;color:white;}
@media(max-width:640px){.block-container{padding-top:1rem}.campus-banner{padding:1rem}.campus-brand{font-size:1.4rem}}
</style>
<div class="campus-banner"><div class="campus-brand">MY | UOWD</div>
<div class="campus-sub">University of Wollongong in Dubai · Campus Support</div></div>
''', unsafe_allow_html=True)


class CampusClient(SupportAPIClient):
    def visitor_chat(self, question):
        return self._request('POST', '/v1/visitor/chat', json={'question': question})

    def visitor_ticket(self, payload):
        return self._request('POST', '/v1/visitor/tickets', json=payload)

    def admissions_tickets(self, token):
        return self._request('GET', '/v1/visitor/admissions/tickets', token)

    def update_ticket(self, token, ticket_id, status, resolution):
        return self._request('PATCH', f'/v1/visitor/admissions/tickets/{ticket_id}', token,
                             {'status': status, 'resolution': resolution})


client = CampusClient(os.getenv('AI_SUPPORT_API_URL', 'http://127.0.0.1:8000'), timeout=120)
for key, default in [('access_token', None), ('audience', None), ('messages', []), ('signed_in_id', None)]:
    st.session_state.setdefault(key, default)

VISITOR_CARDS = [('🎓 Programmes', 'Explore degrees'), ('📋 Admissions', 'Entry requirements'),
                 ('💳 Tuition fees', 'Fees and payment plans'), ('🏅 Scholarships', 'Financial support'),
                 ('🏫 Campus life', 'Facilities and student life'), ('📄 Student visa', 'Visa information')]
INTERNAL_CARDS = [('💻 IT Support', 'Accounts and technical support'), ('📚 Registry', 'Academic services'),
                  ('💳 Finance', 'Fees and payments')]


def cards(items):
    for start in range(0, len(items), 3):
        for col, (title, desc) in zip(st.columns(3), items[start:start+3], strict=False):
            with col:
                # All markup content here is developer-authored, not user input.
                st.markdown(f'<div class="campus-card"><strong>{title}</strong><small>{desc}</small></div>',
                            unsafe_allow_html=True)


def sign_out():
    for key in ('access_token', 'audience', 'signed_in_id'):
        st.session_state[key] = None
    st.session_state.messages = []
    st.rerun()


if not st.session_state.access_token and st.session_state.audience != 'visitor':
    st.header('Welcome to Campus Support')
    st.write('Your university support, all in one place.')
    st.subheader('How would you like to continue?')
    selected = st.radio('Choose your role', ['Student', 'Staff', 'Visitor'], horizontal=True,
                        label_visibility='collapsed')
    if selected == 'Visitor':
        st.subheader('Explore UOWD')
        st.write('Ask about programmes, admissions, tuition fees, scholarships and campus life.')
        if st.button('Continue as visitor', type='primary', use_container_width=True):
            st.session_state.audience = 'visitor'
            st.session_state.messages = []
            st.rerun()
    else:
        with st.form(f'login-{selected}'):
            default_id = 'S100001' if selected == 'Student' else 'E100001'
            user_id = st.text_input(f'{selected} ID', value=default_id, key=f'id-{selected}')
            login_code = st.text_input('Access code', type='password')
            if st.form_submit_button('Sign in', type='primary', use_container_width=True):
                try:
                    login = client.login(user_id, login_code)
                    st.session_state.access_token = login['access_token']
                    st.session_state.audience = login['audience']
                    st.session_state.signed_in_id = login['user_id']
                    st.session_state.messages = []
                    st.rerun()
                except APIClientError as error:
                    st.error(str(error))
        st.caption('Demo accounts only. Do not enter actual university credentials.')
    st.divider()
    st.subheader('Explore our support services')
    cards(INTERNAL_CARDS + [('🎓 Admissions', 'Programme and application enquiries')])
    st.stop()

visitor = st.session_state.audience == 'visitor'
with st.sidebar:
    st.header('Campus Support')
    st.write(f'**{str(st.session_state.audience).title()}**')
    if st.session_state.signed_in_id:
        st.caption(f'Demo ID: {st.session_state.signed_in_id}')
    if st.button('New conversation', use_container_width=True):
        st.session_state.messages = []
        st.rerun()
    if st.button('Return to welcome page' if visitor else 'Sign out', use_container_width=True):
        sign_out()
    st.link_button('Official UOWD website', 'https://www.uowdubai.ac.ae/')
    view = 'Chat'
    if st.session_state.audience == 'staff' and st.session_state.signed_in_id == 'E100001':
        view = st.radio('Workspace', ['Chat', 'Admissions enquiries'])

if view == 'Admissions enquiries':
    st.header('Admissions enquiries')
    st.caption('Local demonstration queue — no live university integration.')
    try:
        tickets = client.admissions_tickets(st.session_state.access_token)['tickets']
        if not tickets:
            st.info('No enquiries yet.')
        for ticket in tickets:
            tid = str(ticket['request_id'])
            with st.expander(f"{ticket['status']} | UOWD-ADM-{tid.upper()}"):
                st.text(ticket['question'])
                st.text(f"{ticket['name']} · {ticket['email']}")
                with st.form(f'update-{tid}'):
                    statuses = ['Open', 'In Progress', 'Resolved']
                    status = st.selectbox('Status', statuses, index=statuses.index(ticket['status']))
                    resolution = st.text_area('Resolution', value=ticket['resolution'])
                    if st.form_submit_button('Save update'):
                        try:
                            client.update_ticket(st.session_state.access_token, tid, status, resolution)
                            st.rerun()
                        except APIClientError as error:
                            st.error(str(error))
    except APIClientError as error:
        st.error(str(error))
    st.stop()


def submit_prompt(prompt):
    prompt = prompt.strip()
    if not prompt:
        return
    st.session_state.messages.append({'role': 'user', 'content': prompt})
    try:
        with st.spinner('Looking for relevant university information…'):
            if visitor:
                # The public workflow has no authenticated data or internal tools.
                reply = client.visitor_chat(prompt)
                message = {'role': 'assistant', 'content': reply['answer'], 'sources': reply['sources'],
                           'can_raise_ticket': reply['can_raise_ticket'], 'question': prompt,
                           'request_id': str(uuid4())}
            else:
                reply = client.chat(st.session_state.access_token, prompt)
                message = {'role': 'assistant', 'content': reply['answer'], 'message_id': reply['message_id'],
                           'citations': reply['citations'], 'action': reply.get('action'),
                           'handoff_url': reply.get('handoff_url')}
        st.session_state.messages.append(message)
    except APIClientError as error:
        st.session_state.messages.append({'role': 'assistant', 'content': str(error)})
    st.rerun()


st.header('Welcome to UOWD' if visitor else 'How can we help you today?')
st.caption('Ask a question or explore our support services.')
if visitor:
    st.caption('Public website information only. Use demo contact details when submitting an enquiry.')
if not st.session_state.messages:
    cards(VISITOR_CARDS if visitor else INTERNAL_CARDS)
    st.subheader('Frequently asked questions' if visitor else 'Suggested questions')
    suggestions = (('What programmes does UOWD offer?', 'What are the admission requirements for a master’s degree?',
                    'What is the MBA tuition fee per subject?', 'What scholarships are available?') if visitor else
                   ('I cannot see my class timetable.', 'How can I reset my password?', 'How can I pay my tuition fees?',
                    'Show my course progress.') if st.session_state.audience == 'student' else
                   ('How can I reset my password?', 'I need IT support.', 'Show my staff profile.', 'I need help with finance.'))
    for i, question in enumerate(suggestions):
        if st.button(question, key=f'suggestion-{i}', use_container_width=True):
            submit_prompt(question)

for index, message in enumerate(st.session_state.messages):
    with st.chat_message(message['role']):
        if visitor:
            st.text(message['content'])
            for j, source in enumerate(message.get('sources', [])):
                st.link_button(f"Source {j+1}: {source['title']}", source['url'])
                if source.get('retrieved_at'):
                    st.caption(f"Website retrieved: {source['retrieved_at'][:10]}")
            if message.get('ticket'):
                st.success(f"Enquiry saved to local Admissions queue: {message['ticket']['reference']}")
            elif message.get('can_raise_ticket'):
                with st.expander('Raise an enquiry with Admissions'):
                    with st.form(f'ticket-{index}'):
                        st.caption('Local demo only. Use a fictional name and @example.test email address.')
                        name = st.text_input('Full name', value='Demo Visitor')
                        email = st.text_input('Email address', value='visitor@example.test')
                        enquiry = st.text_area('Enquiry', value=message['question'], max_chars=1500)
                        consent = st.checkbox('I agree to submit these demo details to the local Admissions queue.')
                        if st.form_submit_button('Submit request', type='primary'):
                            if not consent:
                                st.warning('Please confirm consent first.')
                            else:
                                try:
                                    message['ticket'] = client.visitor_ticket({'request_id': message['request_id'],
                                        'name': name, 'email': email, 'question': enquiry, 'consent': consent})
                                    st.rerun()
                                except APIClientError as error:
                                    st.error(str(error))
            continue
        st.write(message['content'])
        if message.get('citations'):
            with st.expander('Sources'):
                for citation in message['citations']:
                    st.code(citation['citation'])
        if message.get('handoff_url'):
            st.link_button('Open official support page', message['handoff_url'])
        action = message.get('action')
        if action and not message.get('action_sent'):
            st.info(f"Draft to {action['department']} ({action['recipient']})\n\n**{action['subject']}**\n\n{action['body']}")
            if st.button('Confirm and send via Mailpit', key=f"confirm-{action['action_id']}"):
                try:
                    client.confirm(st.session_state.access_token, action['action_id'])
                    message['action_sent'] = True
                    st.rerun()
                except APIClientError as error:
                    st.error(str(error))
        elif action and message.get('action_sent'):
            st.success('The synthetic email was delivered to Mailpit.')
        if message['role'] == 'assistant' and message.get('message_id'):
            if message.get('feedback_recorded'):
                st.caption('Thank you — feedback recorded.')
            else:
                left, right = st.columns(2)
                helpful = left.button('Helpful', key=f'helpful-{index}')
                not_helpful = right.button('Not helpful', key=f'not-helpful-{index}')
                if helpful or not_helpful:
                    try:
                        client.feedback(st.session_state.access_token, message['message_id'], helpful)
                        message['feedback_recorded'] = True
                        st.rerun()
                    except APIClientError as error:
                        st.error(str(error))

if prompt := st.chat_input('Ask a UOWD question…', max_chars=1500 if visitor else 3000):
    submit_prompt(prompt)
