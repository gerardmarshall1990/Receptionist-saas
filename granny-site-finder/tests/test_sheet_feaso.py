"""The sheet formulas must reproduce gsf.feaso exactly. Evaluated here with a tiny formula interpreter
for the specific operators used (no spreadsheet needed)."""
import re

import pytest

from gsf.config import load_config
from gsf.feaso import FeasoInputs, StressSpec, run_with_stress
from gsf.sheet_feaso import feaso_formulas, settings_rows


class _Blank:
    """An empty cell: equal to "", absorbs arithmetic (the harness evaluates both IF branches)."""
    def __eq__(self, o): return o == "" or isinstance(o, _Blank)  # noqa: E704
    def __hash__(self): return 0  # noqa: E704
    def _same(self, *a): return self  # noqa: E704
    __add__ = __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = __truediv__ = __rtruediv__ = _same
    def __lt__(self, o): return False  # noqa: E704
    __gt__ = __le__ = __ge__ = __lt__


B = _Blank()


def evaluate(cfg, price, ex, un, units=2, cv=""):
    price, ex, un, cv = [B if v == "" else v for v in (price, ex, un, cv)]
    settings = {f"Settings!$B${i + 1}": row[1] for i, row in enumerate(settings_rows(cfg)) if i > 0}
    cells = {"E2": price, "G2": ex, "H2": un, "I2": units, "AA2": cv}
    from gsf.sheet_feaso import gv_formulas
    order = list(zip("JKLMNOPQRST", feaso_formulas(2)[:11])) + list(zip(["AB", "AC", "AD"], gv_formulas(2))) \
        + list(zip("UVW", feaso_formulas(2)[11:]))
    for col, f in order:
        expr = f[1:]
        for k, v in settings.items():
            expr = expr.replace(k, repr(v))
        expr = re.sub(r"\$?([A-Z]{1,2})\$?2\b", lambda m: f"C['{m.group(1)}2']", expr)
        expr = (expr.replace("<>", "!=").replace("<=", " <= ").replace(">=", " >= ")
                .replace('=""', '==""').replace('="net"', '=="net"').replace('="gv"', '=="gv"').replace('="income"', '=="income"').replace("=0,", "==0,"))
        C = cells

        def IF(c, a, b):
            return a if c else b

        def OR(*a):
            return any(a)

        def AND(*a):
            return all(a)

        def MAX(*a):
            nums = [x for x in a if not isinstance(x, _Blank)]
            return max(nums) if nums else B
        expr = expr.replace('""', "B")
        val = eval(expr, {"IF": IF, "OR": OR, "AND": AND, "MAX": MAX, "C": C, "B": B})
        cells[f"{col}2"] = val
    return cells


@pytest.mark.parametrize("basis,draw,cv,vb", [("gross", 1.0, "", "lower"), ("net", 0.6, "", "lower"),
                                              ("gross", 1.0, 1_000_000, "lower"), ("gross", 1.0, 1_000_000, "income"),
                                              ("net", 1.0, 1_400_000, "gv")])
def test_sheet_formulas_match_python(basis, draw, cv, vb):
    cfg = load_config()
    cfg["feaso"]["yield_basis"], cfg["feaso"]["draw_factor"], cfg["feaso"]["value_basis"] = basis, draw, vb
    c = evaluate(cfg, 900_000, 700, 550, cv=cv)
    f = cfg["feaso"]
    rep = run_with_stress(FeasoInputs(price=900_000, existing_rent_pw=700, unit_rent_pw=550,
                                      **{k: f[k] for k in ("interest_rate", "cap_months", "draw_factor", "target_yield",
                                                           "yield_basis", "opex_ratio", "ltv_cap", "units",
                                                           "unit_cost_installed", "misc", "unit_value_added", "value_basis")},
                                      cv=cv or None), StressSpec(**f["stress"]))
    b, s = rep.base, rep.scenarios["combined (all four)"]
    assert c["J2"] == pytest.approx(b.cost_base) and c["K2"] == pytest.approx(b.loan)
    assert c["P2"] == pytest.approx(b.equity) and c["R2"] == pytest.approx(b.ltv)
    assert c["S2"] == pytest.approx(b.io_cover) and c["T2"] == pytest.approx(max(0, -b.refi_headroom))
    assert c["U2"] == pytest.approx(s.ltv_used) and c["V2"] == pytest.approx(s.io_cover)
    assert c["AD2"] == pytest.approx(b.ltv_used)
    if cv:
        assert c["AB2"] == pytest.approx(b.gv_value) and c["AC2"] == pytest.approx(b.gv_ltv)
    assert c["W2"] == ("PASS" if b.ltv_pass and b.io_pass else "FAIL")
