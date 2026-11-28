"""Live acceptance test against real Auckland Council data. Opt-in: GSF_LIVE=1.

Expected outcomes live in tests/fixtures/acceptance.yaml and must be hand-checked.
"""
import copy
import os
from pathlib import Path

import pytest
import yaml

from gsf.config import load_config
from gsf.models import Listing

FIXTURE = yaml.safe_load((Path(__file__).parent / "fixtures" / "acceptance.yaml").read_text())
pytestmark = pytest.mark.skipif(os.environ.get("GSF_LIVE") != "1", reason="set GSF_LIVE=1 to hit live data sources")


@pytest.fixture(scope="module")
def pipe(tmp_path_factory):
    from gsf.pipeline import Pipeline
    cfg = copy.deepcopy(load_config())
    cfg["storage"]["db_path"] = str(tmp_path_factory.mktemp("live") / "live.sqlite")
    return Pipeline(cfg)


@pytest.mark.parametrize("case", FIXTURE, ids=[c["address"] for c in FIXTURE])
def test_acceptance(pipe, case):
    s = pipe.process(Listing(address=case["address"], price=900_000, bedrooms=3, existing_dwellings=1))
    got = {g["gate"]: g["status"] for g in s["gates"] if g["hard"]}
    exp = dict(case["expected"])
    if pipe.linz.enabled and exp.get("title") == "check":
        exp["title"] = "pass"     # freehold-looking sites should verify once LINZ is on
    assert {k: got[k] for k in exp} == exp, [g for g in s["gates"] if g["hard"]]
    assert all(g["reason"].strip() for g in s["gates"])
