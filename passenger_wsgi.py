import os
import sys

# Set up paths for cPanel Passenger
# Add the project root directory to python path
sys.path.insert(0, os.path.dirname(__file__))

# Point to virtualenv python if it exists (standard cPanel path fallback)
# Often cPanel activates the venv itself, but this handles manual executions or specific path fallbacks
venv_path = os.path.join(os.path.dirname(__file__), 'venv/bin/activate_this.py')
if os.path.exists(venv_path):
    with open(venv_path) as f:
        exec(f.read(), {'__file__': venv_path})

# Set the Django settings module
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fieldtrack.settings")

# Import the WSGI application
from fieldtrack.wsgi import application
