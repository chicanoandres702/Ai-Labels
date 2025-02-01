import os
import json
import logging
from flask import Flask, redirect, request, session, url_for
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.cloud import firestore
from google.cloud import pubsub_v1

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', os.urandom(24))

# Configuration
PROJECT_ID = "gmail-labels-421404"  # Your project ID
SCOPES = [
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.labels'
]

# Initialize Firestore
db = firestore.Client()

def setup_gmail_watch(service, user_email):
    """Setup Gmail push notifications"""
    try:
        request = {
            'labelIds': ['INBOX'],
            'topicName': f'projects/{PROJECT_ID}/topics/gmail-notifications'
        }
        
        response = service.users().watch(userId='me', body=request).execute()
        logger.info(f"Watch response: {response}")
        
        # Store watch details in Firestore
        user_ref = db.collection('users').document(user_email)
        user_ref.update({
            'watch_details': response,
            'watch_status': 'active'
        })
        
        return True
    except Exception as e:
        logger.error(f"Watch setup error: {e}")
        return False

@app.route('/')
def index():
    if 'email' in session:
        return f'Welcome {session["email"]}! <a href="/logout">Logout</a>'
    return 'Welcome! <a href="/authorize">Login with Gmail</a>'

@app.route('/authorize')
def authorize():
    flow = Flow.from_client_secrets_file(
        'client_secret.json',
        scopes=SCOPES,
        redirect_uri=url_for('oauth2callback', _external=True)
    )
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true'
    )
    session['state'] = state
    return redirect(authorization_url)

@app.route('/oauth2callback')
def oauth2callback():
    try:
        flow = Flow.from_client_secrets_file(
            'client_secret.json',
            scopes=SCOPES,
            state=session['state'],
            redirect_uri=url_for('oauth2callback', _external=True)
        )
        
        flow.fetch_token(authorization_response=request.url)
        credentials = flow.credentials
        
        # Build Gmail service
        service = build('gmail', 'v1', credentials=credentials)
        
        # Get user email
        user_info = service.users().getProfile(userId='me').execute()
        email = user_info['emailAddress']
        session['email'] = email
        
        # Store in Firestore
        user_ref = db.collection('users').document(email)
        user_ref.set({
            'email': email,
            'credentials': {
                'token': credentials.token,
                'refresh_token': credentials.refresh_token,
                'token_uri': credentials.token_uri,
                'client_id': credentials.client_id,
                'client_secret': credentials.client_secret,
                'scopes': credentials.scopes
            }
        })
        
        # Setup watch
        if setup_gmail_watch(service, email):
            return redirect(url_for('index'))
        else:
            return 'Watch setup failed. Check logs for details.'
            
    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        return f'Error: {str(e)}'

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=True)
