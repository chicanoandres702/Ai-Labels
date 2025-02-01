import os
import json
import logging
from flask import Flask, redirect, request, session, url_for
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.cloud import firestore
from google.cloud import pubsub_v1
from google.cloud import secretmanager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', os.urandom(24))

# Configuration
PROJECT_ID = "gmail-labels-421404"
SCOPES = [
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.labels'
]

# Initialize clients
db = firestore.Client()
secrets_client = secretmanager.SecretManagerServiceClient()

def get_oauth_config():
    """Get OAuth configuration from Secret Manager"""
    try:
        name = f"projects/{PROJECT_ID}/secrets/oauth-client-config/versions/latest"
        response = secrets_client.access_secret_version(request={"name": name})
        return json.loads(response.payload.data.decode("UTF-8"))
    except Exception as e:
        logger.error(f"Error getting OAuth config: {e}")
        return None

def create_oauth_flow(redirect_uri):
    """Create OAuth flow using config from Secret Manager"""
    client_config = get_oauth_config()
    if not client_config:
        raise ValueError("OAuth configuration not found")

    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )
    return flow

@app.route('/')
def index():
    if 'email' in session:
        return f'Welcome {session["email"]}! <a href="/logout">Logout</a>'
    return 'Welcome! <a href="/authorize">Login with Gmail</a>'

@app.route('/authorize')
def authorize():
    try:
        redirect_uri = url_for('oauth2callback', _external=True)
        flow = create_oauth_flow(redirect_uri)
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true'
        )
        session['state'] = state
        return redirect(authorization_url)
    except Exception as e:
        logger.error(f"Authorization error: {e}")
        return f"Authorization failed: {str(e)}", 500

@app.route('/oauth2callback')
def oauth2callback():
    try:
        if 'state' not in session:
            return 'State missing from session', 400
        
        redirect_uri = url_for('oauth2callback', _external=True)
        flow = create_oauth_flow(redirect_uri)
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
        
        return redirect(url_for('index'))
            
    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        return f'Error: {str(e)}', 500

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=True)
