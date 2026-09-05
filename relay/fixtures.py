import json
from .config import ROOT

_data = json.loads((ROOT / "config/fixtures.json").read_text())
users = _data["users"]
catalog = _data["catalog"]
articles = _data["articles"]
