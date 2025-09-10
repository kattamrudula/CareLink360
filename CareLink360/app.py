from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import os
import requests
import json
from functools import wraps
from descope import DescopeClient
from agent import StorageAgent  # Import the agent

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'your-secret-key-here')

# Descope configuration
DESCOPE_PROJECT_ID = os.environ.get('DESCOPE_PROJECT_ID')
DESCOPE_MANAGEMENT_KEY = os.environ.get('DESCOPE_MANAGEMENT_KEY')

# Initialize Descope client
descope_client = DescopeClient(project_id=DESCOPE_PROJECT_ID, management_key=DESCOPE_MANAGEMENT_KEY)

# Decorator to require login
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def home():
    if 'user_id' in session:
        return redirect(url_for('index'))
    return redirect(url_for('login'))

@app.route('/login')
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/index')
@login_required
def index():
    user_email = session.get('user_email', 'Unknown User')
    user_name = session.get('user_name', 'User')
    return render_template('index.html', user_email=user_email, user_name=user_name)

@app.route('/connections')
@login_required
def connections():
    user_email = session.get('user_email', 'Unknown User')
    user_name = session.get('user_name', 'User')
    return render_template('connections.html', user_email=user_email, user_name=user_name)

def connect_google_drive(redirect_url, refresh_token):
    """Initiate connection for Google Drive."""
    app_id = "datatune-google-drive"
    scopes = [
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/drive.metadata.readonly"
    ]
    connect_data = {
        "appId": app_id,
        "options": {
            "redirectUrl": redirect_url,
            "scopes": scopes
        }
    }
    return connect_data

def connect_one_drive(redirect_url, refresh_token):
    """Initiate connection for OneDrive."""
    app_id = "datatune-one-drive"
    scopes = [
        "Files.ReadWrite",
        "User.Read"
    ]
    connect_data = {
        "appId": app_id,
        "options": {
            "redirectUrl": redirect_url,
            "scopes": scopes
        }
    }
    return connect_data

def connect_custom_oauth_storage(redirect_url, refresh_token):
    """Initiate connection for custom OAuth cloud storages (Azure, AWS S3, GCP)."""
    app_id = "datatune-custom-oauth"
    # Scopes are service-specific and may not be standard OAuth,
    # so we'll pass an empty list for this example.
    scopes = []
    connect_data = {
        "appId": app_id,
        "options": {
            "redirectUrl": redirect_url,
            "scopes": scopes
        }
    }
    return connect_data

@app.route('/api/connect-service', methods=['POST'])
@login_required
def connect_service():
    """Initiate connection to various services via Descope outbound app"""
    try:
        data = request.get_json()
        service = data.get('service')
        refresh_token = session.get('refresh_token')

        if not refresh_token:
            return jsonify({'success': False, 'error': 'User not authenticated'}), 401
        
        redirect_url = request.url_root.rstrip('/') + url_for('oauth_callback')
        
        connect_data = {}
        if service == 'google-drive':
            connect_data = connect_google_drive(redirect_url, refresh_token)
        elif service == 'one-drive':
            connect_data = connect_one_drive(redirect_url, refresh_token)
        elif service in ['azure-blob-storage', 'aws-s3', 'gcp-cloud-storage']:
            connect_data = connect_custom_oauth_storage(redirect_url, refresh_token)
        else:
            return jsonify({'success': False, 'error': 'Unsupported service'}), 400

        headers = {
            'Authorization': f'Bearer {DESCOPE_PROJECT_ID}:{refresh_token}',
            'Content-Type': 'application/json'
        }

        response = requests.post(
            'https://api.descope.com/v1/outbound/oauth/connect',
            headers=headers,
            json=connect_data
        )

        if response.status_code == 200:
            result = response.json()
            return jsonify({
                'success': True,
                'message': f"{service} connection initiated",
                'auth_url': result.get('url'),
                'redirect_required': True
            })
        else:
            print(f"Descope connect error: {response.text}")
            return jsonify({'success': False, 'error': 'Failed to initiate connection'}), 400

    except Exception as e:
        print(f"Connect service error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/auth/oauth-callback')
@login_required
def oauth_callback():
    """Handle OAuth callback from any service"""
    try:
        code = request.args.get('code')
        state = request.args.get('state')
        error = request.args.get('error')

        if error:
            print(f"OAuth error: {error}")
            return redirect(url_for('connections', error=error))

        if code:
            print(f"OAuth callback successful with code: {code[:20]}...")
            return redirect(url_for('connections', connected='service'))
        else:
            return redirect(url_for('connections', error='missing_code'))

    except Exception as e:
        print(f"OAuth callback error: {e}")
        return redirect(url_for('connections', error='callback_failed'))

@app.route('/api/search-files', methods=['POST'])
@login_required
def search_files():
    """
    Initiates a search on all connected services using the StorageAgent.
    """
    try:
        data = request.get_json()
        query = data.get('query', '')
        user_id = session.get('user_id')

        if not query.strip():
            return jsonify({'success': False, 'error': 'Search query is required'}), 400

        # Create an instance of the agent with the user's information
        agent = StorageAgent(descope_client, user_id)
        
        # Call the agent's orchestration method
        results = agent.search_all_storages(query)

        # Aggregate the results from the agent's response
        all_files = []
        results_by_service = {}
        for service_name, result in results.items():
            if result.get('success'):
                all_files.extend(result.get('files', []))
                results_by_service[service_name] = {
                    'count': len(result.get('files', [])),
                    'files': result.get('files', [])
                }
            else:
                results_by_service[service_name] = {
                    'count': 0,
                    'files': [],
                    'error': result.get('error')
                }

        connected_services = [s for s, r in results.items() if r.get('success')]

        return jsonify({
            'success': True,
            'files': all_files,
            'total': len(all_files),
            'results_by_service': results_by_service,
            'connected_services': connected_services
        })
    except Exception as e:
        print(f"Search files error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 400

def get_connected_services(user_id):
    """Get list of connected services for the user"""
    connected_services = []
    try:
        # Check if user has Google Drive connected
        google_drive_token_resp = descope_client.mgmt.outbound_application.fetch_token("datatune-google-drive", user_id)
        if google_drive_token_resp and google_drive_token_resp.get('access_token'):
            connected_services.append('google-drive')

        # Check if user has OneDrive connected
        onedrive_token_resp = descope_client.mgmt.outbound_application.fetch_token("datatune-one-drive", user_id)
        if onedrive_token_resp and onedrive_token_resp.get('access_token'):
            connected_services.append('one-drive')

        # Check if user has a custom connection for other cloud storages
        custom_oauth_token_resp = descope_client.mgmt.outbound_application.fetch_token("datatune-custom-oauth", user_id)
        if custom_oauth_token_resp and custom_oauth_token_resp.get('access_token'):
            connected_services.append('custom-oauth-storage')

        return connected_services

    except Exception as e:
        print(f"Error getting connected services: {e}")
        return connected_services

def get_file_extension(filename):
    """Extract file extension from filename"""
    if '.' in filename:
        return filename.rsplit('.', 1)[1].lower()
    return 'unknown'

def format_file_size(size_bytes):
    """Format file size in human readable format"""
    if not size_bytes or size_bytes == 0:
        return '0 B'

    try:
        size_bytes = int(size_bytes)
        size_names = ['B', 'KB', 'MB', 'GB', 'TB']
        i = 0
        while size_bytes >= 1024 and i < len(size_names) - 1:
            size_bytes /= 1024.0
            i += 1
        return f"{size_bytes:.1f} {size_names[i]}"
    except:
        return 'Unknown'

def format_date(date_string):
    """Format date string for display"""
    if not date_string:
        return 'Unknown'

    try:
        from datetime import datetime
        if 'T' in date_string:
            dt = datetime.fromisoformat(date_string.replace('Z', '+00:00'))
        else:
            dt = datetime.strptime(date_string, '%Y-%m-%d')
        return dt.strftime('%Y-%m-%d')
    except:
        return date_string

@app.route('/auth/callback', methods=['POST'])
def auth_callback():
    """Handle authentication callback from Descope"""
    try:
        data = request.get_json()

        session['user_id'] = data.get('userId')
        session['user_email'] = data.get('email')
        session['user_name'] = data.get('name', data.get('email', 'User'))
        session['session_token'] = data.get('sessionToken')  # Store session JWT
        session['refresh_token'] = data.get('refreshToken')
        print(f"User authenticated: {session['user_email']}")

        return jsonify({'success': True, 'redirect': url_for('index')})

    except Exception as e:
        print(f"Auth callback error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'service': 'DataTune API'})

@app.route('/api/user')
@login_required
def get_user():
    return jsonify({
        'user_id': session.get('user_id'),
        'user_email': session.get('user_email'),
        'user_name': session.get('user_name')
    })

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8000)
