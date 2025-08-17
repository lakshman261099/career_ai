# python seed.py andhrauniversity "Andhra University" allowlist.csv
import sys, csv, os
from app import app
from models import db, University, UniversityAllowlist

if __name__ == "__main__":
    subdomain = sys.argv[1]
    name = sys.argv[2]
    csv_path = sys.argv[3] if len(sys.argv) > 3 else None
    with app.app_context():
        uni = University.query.filter_by(subdomain=subdomain).first()
        if not uni:
            uni = University(subdomain=subdomain, name=name, status="active")
            db.session.add(uni); db.session.commit()
            print("Created university:", subdomain)
        else:
            print("University exists:", subdomain)
        if csv_path and os.path.exists(csv_path):
            added=0
            with open(csv_path) as f:
                for row in csv.reader(f):
                    email=(row[0] or "").strip().lower()
                    if not email: continue
                    if not UniversityAllowlist.query.filter_by(university_id=uni.id, email=email).first():
                        db.session.add(UniversityAllowlist(university_id=uni.id, email=email))
                        added+=1
            db.session.commit()
            print("Added allowlist rows:", added)
