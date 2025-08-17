# python tenant_admin.py add andhrauniversity student@example.edu
# python tenant_admin.py remove andhrauniversity student@example.edu
import sys
from app import app
from models import db, University, UniversityAllowlist

if __name__ == "__main__":
    action = sys.argv[1]
    subdomain = sys.argv[2]
    email = sys.argv[3].lower().strip()
    with app.app_context():
        uni = University.query.filter_by(subdomain=subdomain).first()
        if not uni: 
            print("No such university"); exit(1)
        row = UniversityAllowlist.query.filter_by(university_id=uni.id, email=email).first()
        if action=="add":
            if row: print("Already in list"); exit(0)
            db.session.add(UniversityAllowlist(university_id=uni.id, email=email)); db.session.commit()
            print("Added.")
        elif action=="remove":
            if not row: print("Not in list"); exit(0)
            db.session.delete(row); db.session.commit()
            print("Removed.")
        else:
            print("Unknown action")
