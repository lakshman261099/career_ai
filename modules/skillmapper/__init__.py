# modules/skillmapper/__init__.py
from flask import Blueprint

bp = Blueprint(
    "skillmapper",
    __name__,
    url_prefix="/skillmapper"
)

from . import routes  # noqa: E402,F401
