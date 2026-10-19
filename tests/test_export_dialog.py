"""The export window's Ratings default (server/static/export.js), run in Node."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parent.parent / "server" / "static" / "export.js"


def default_ratings(photos, scope=""):
    src = JS.read_text()
    fn = re.search(r"function defaultRatings\(photos, scope\) \{.*?\n\}", src, re.S).group(0)
    code = f"{fn}\nprocess.stdout.write(JSON.stringify(defaultRatings({json.dumps(photos)}, {json.dumps(scope)})));"
    return json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)


@pytest.mark.skipif(not shutil.which("node"), reason="needs Node")
def test_keep_only_when_anything_is_rated():
    unrated = [{"folder": 1, "pick": None, "cull": None}, {"folder": 2, "pick": None, "cull": None}]
    assert default_ratings(unrated) == ""                                       # Every photo
    culled = [{"folder": 1, "pick": None, "cull": {"r": "maybe"}}, {"folder": 2, "pick": None, "cull": None}]
    assert default_ratings(culled) == "keep"                                    # Keep only
    assert default_ratings(culled, "2") == ""                                   # that folder has no ratings
    by_hand = [{"folder": 3, "pick": "reject", "cull": None}]
    assert default_ratings(by_hand, "3") == "keep"


def test_export_and_cull_ask_before_spending():
    static = JS.parent
    assert "askSpend('export'" in JS.read_text() and "askSpend('cull'" in (static / "cull.js").read_text()
    html = (static / "index.html").read_text()
    assert 'id="export-spend"' in html and 'id="cull-spend"' in html
    account = (static / "account.js").read_text()
    assert "if (Acc.unlimited) return go();" in account                         # the studio is never asked
