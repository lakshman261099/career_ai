from sqlalchemy.exc import OperationalError
from app import app
from models import db, University, User

with app.app_context():
    db.create_all()
    try:
        needs_uni = University.query.first() is None
    except OperationalError:
        # If the table shape changed in old DB, try to create_all again
        db.create_all()
        needs_uni = University.query.first() is None

    if needs_uni:
        db.session.add(University(name="VelTech University", domain="veltech.edu", tenant_slug="veltech.jobpack.ai"))
        db.session.add(University(name="Demo University", domain="demo.local", tenant_slug="demo.jobpack.ai"))

    if not User.query.filter_by(email="demo@career.ai").first():
        u = User(name="Demo User", email="demo@career.ai"); u.set_password("demo123")
        db.session.add(u)

    db.session.commit()
    print("Seed complete.")
