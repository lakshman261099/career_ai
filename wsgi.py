# wsgi.py
from app import create_app

# this will be imported by Gunicorn on Render
app = create_app()
