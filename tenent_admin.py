from models import db, University

def add_university(name: str, domain: str = None, tenant_slug: str = None):
    uni = University(name=name, domain=domain, tenant_slug=tenant_slug)
    db.session.add(uni)
    db.session.commit()
    return uni
