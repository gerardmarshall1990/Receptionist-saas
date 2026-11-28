"""The sheet formulas must reproduce gsf.feaso exactly. Evaluated here with a tiny formula interpreter
for the specific operators used (no spreadsheet needed)."""
import re

import pytest

from gsf.config import load_config
from gsf.feaso import FeasoInputs, StressSpec, run_with_stress
from gsf.sheet_feaso import feaso_formulas, settings_rows


def evaluate(cfg, price, ex, un, units=2):
    settings = {f"Settings!$B${i + 1}": row[1] for i, row in enumerate(settings_rows(cfg)) if i > 0}
    cells = {"E2": price, "G2": ex, "H2": un, "I2": units}
    cols = "JKLMNOPQRSTUVW"
    for col, f in zip(cols, feaso_formulas(2)):
        expr = f[1:]
        for k, v in settings.items():
            expr = expr.replace(k, repr(v))
        expr = re.sub(r"\$?([A-Z])\$?2\b", lambda m: f"C['{m.group(1)}2']", expr)
        expr = (expr.replace("<>", "!=").replace("<=", " <= ").replace(">=", " >= ")
                .replace('=""', '==""').replace('="net"', '=="net"').replace("=0,", "==0,"))
        C = cells

        def IF(c, a, b):
            return a if c else b

        def OR(*a):
            return any(a)

        def AND(*a):
            return all(a)

        def MAX(*a):
            return max(a)
        cells[f"{col}2"] = eval(expr, {"IF": IF, "OR": OR, "AND": AND, "MAX": MAX, "C": C})
    return cells


@pytest.mark.parametrize("basis,draw", [("gross", 1.0), ("net", 0.6)])
def test_sheet_formulas_match_python(basis, draw):
    cfg = load_config()
    cfg["feaso"]["yield_basis"], cfg["feaso"]["draw_factor"] = basis, draw
    c = evaluate(cfg, 900_000, 700, 550)
    f = cfg["feaso"]
    rep = run_with_stress(FeasoInputs(price=900_000, existing_rent_pw=700, unit_rent_pw=550,
                                      **{k: f[k] for k in ("interest_rate", "cap_months", "draw_factor", "target_yield",
                                                           "yield_basis", "opex_ratio", "ltv_cap", "units",
                                                           "unit_cost_installed", "misc")}), StressSpec(**f["stress"]))
    b, s = rep.base, rep.scenarios["combined (all four)"]
    assert c["J2"] == pytest.approx(b.cost_base) and c["K2"] == pytest.approx(b.loan)
    assert c["P2"] == pytest.approx(b.equity) and c["R2"] == pytest.approx(b.ltv)
    assert c["S2"] == pytest.approx(b.io_cover) and c["T2"] == pytest.approx(max(0, -b.refi_headroom))
    assert c["U2"] == pytest.approx(s.ltv) and c["V2"] == pytest.approx(s.io_cover)
    assert c["W2"] == ("PASS" if b.ltv_pass and b.io_pass else "FAIL")
