"""Feaso maths: three hand-calculated cases (arithmetic shown) plus edge cases."""
import pytest

from gsf.feaso import FeasoInputError, FeasoInputs, StressSpec, run, run_with_stress

approx = lambda v: pytest.approx(v, rel=1e-5)  # noqa: E731


def test_case1_defaults_gross_fails_ltv():
    # price 900,000; existing rent 700/wk; unit rent 550/wk; all spec defaults.
    # cost_base = 900,000 + 2*150,000 + 40,000              = 1,240,000
    # denom     = 1 - 0.07 * 6/12 * 1.0                      = 0.965
    # loan      = 1,240,000 / 0.965                          = 1,284,974.09
    # cap int   = 1,284,974.09 - 1,240,000                   = 44,974.09
    # rent      = 52 * (700 + 2*550) = 52 * 1,800            = 93,600 (gross = noi)
    # end value = 93,600 / 0.06                              = 1,560,000
    # ltv       = 1,284,974.09 / 1,560,000                   = 0.823701  -> FAIL (> 0.65)
    # equity    = 1,560,000 - 1,284,974.09                   = 275,025.91
    # headroom  = 0.65*1,560,000 - 1,284,974.09 = 1,014,000 - ... = -270,974.09
    # io_cover  = 93,600 / (1,284,974.09 * 0.07 = 89,948.19) = 1.040599  -> PASS
    r = run(FeasoInputs(price=900_000, existing_rent_pw=700, unit_rent_pw=550))
    assert r.cost_base == 1_240_000
    assert r.loan == approx(1_284_974.09)
    assert r.capitalised_interest == approx(44_974.09)
    assert r.annual_rent == 93_600
    assert r.noi == 93_600
    assert r.end_value == approx(1_560_000)
    assert r.ltv == approx(0.823701)
    assert not r.ltv_pass
    assert r.equity == approx(275_025.91)
    assert r.refi_headroom == approx(-270_974.09)
    assert r.io_cover == approx(1.040599)
    assert r.io_pass


def test_case2_net_basis_draw_06():
    # price 600,000; rents 650 + 2*600; net basis (opex 25%); draw factor 0.6.
    # cost_base = 600,000 + 300,000 + 40,000                 = 940,000
    # denom     = 1 - 0.07 * 0.5 * 0.6 = 1 - 0.021           = 0.979
    # loan      = 940,000 / 0.979                            = 960,163.43
    # cap int                                                = 20,163.43
    # rent      = 52 * 1,850                                 = 96,200
    # noi       = 96,200 * 0.75                              = 72,150
    # end value = 72,150 / 0.06                              = 1,202,500
    # ltv       = 960,163.43 / 1,202,500                     = 0.798473 -> FAIL
    # equity    = 1,202,500 - 960,163.43                     = 242,336.57
    # headroom  = 781,625 - 960,163.43                       = -178,538.43
    # io_cover  = 72,150 / (960,163.43*0.07 = 67,211.44)     = 1.073481 -> PASS
    r = run(FeasoInputs(price=600_000, existing_rent_pw=650, unit_rent_pw=600,
                        yield_basis="net", draw_factor=0.6))
    assert r.cost_base == 940_000
    assert r.loan == approx(960_163.43)
    assert r.capitalised_interest == approx(20_163.43)
    assert r.noi == approx(72_150)
    assert r.end_value == approx(1_202_500)
    assert r.ltv == approx(0.798473)
    assert r.equity == approx(242_336.57)
    assert r.refi_headroom == approx(-178_538.43)
    assert r.io_cover == approx(1.073481)


def test_case3_passing_site():
    # price 500,000; unit cost 100,000; misc 0; rate 6%; 12 months cap; yield 5% gross.
    # cost_base = 500,000 + 200,000 + 0                      = 700,000
    # denom     = 1 - 0.06 * 12/12 * 1                       = 0.94
    # loan      = 700,000 / 0.94                             = 744,680.85
    # rent      = 52 * (600 + 2*500) = 52 * 1,600            = 83,200
    # end value = 83,200 / 0.05                              = 1,664,000
    # ltv       = 744,680.85 / 1,664,000                     = 0.447525 -> PASS
    # equity                                                 = 919,319.15
    # headroom  = 1,081,600 - 744,680.85                     = 336,919.15
    # io_cover  = 83,200 / 44,680.85                         = 1.862096 -> PASS
    r = run(FeasoInputs(price=500_000, existing_rent_pw=600, unit_rent_pw=500,
                        unit_cost_installed=100_000, misc=0, interest_rate=0.06,
                        cap_months=12, target_yield=0.05))
    assert r.loan == approx(744_680.85)
    assert r.end_value == approx(1_664_000)
    assert r.ltv == approx(0.447525)
    assert r.ltv_pass
    assert r.equity == approx(919_319.15)
    assert r.refi_headroom == approx(336_919.15)
    assert r.io_cover == approx(1.862096)


def test_case1_combined_stress():
    # rents x0.85: 595 + 2*467.5 = 1,530/wk -> 79,560/yr
    # dev cost x1.1: units 2*165,000 + misc 44,000 -> cost_base 1,274,000
    # rate 8%: denom = 1 - 0.08*0.5 = 0.96 -> loan 1,327,083.33
    # yield 6.5%: end value = 79,560 / 0.065 = 1,224,000
    # ltv = 1.084218 ; io_cover = 79,560 / 106,166.67 = 0.749388
    rep = run_with_stress(FeasoInputs(price=900_000, existing_rent_pw=700, unit_rent_pw=550), StressSpec())
    c = rep.scenarios["combined (all four)"]
    assert c.cost_base == approx(1_274_000)
    assert c.loan == approx(1_327_083.33)
    assert c.end_value == approx(1_224_000)
    assert c.ltv == approx(1.084218)
    assert c.io_cover == approx(0.749388)
    assert rep.worst_ltv == approx(1.084218)
    assert rep.worst_io_cover == approx(0.749388)
    assert len(rep.scenarios) == 5


def test_zero_interest_io_cover_is_none_and_passes():
    r = run(FeasoInputs(price=500_000, existing_rent_pw=600, unit_rent_pw=500, interest_rate=0))
    assert r.capitalised_interest == 0
    assert r.io_cover is None and r.io_pass


@pytest.mark.parametrize("kwargs", [
    dict(price=None, existing_rent_pw=600, unit_rent_pw=500),
    dict(price=0, existing_rent_pw=600, unit_rent_pw=500),
    dict(price=500_000, existing_rent_pw=None, unit_rent_pw=500),
    dict(price=500_000, existing_rent_pw=600, unit_rent_pw=500, yield_basis="NET!"),
    dict(price=500_000, existing_rent_pw=600, unit_rent_pw=500, target_yield=0),
    dict(price=500_000, existing_rent_pw=600, unit_rent_pw=500, interest_rate=0.5, cap_months=24),
])
def test_bad_inputs_raise_clear_error(kwargs):
    with pytest.raises(FeasoInputError):
        run(FeasoInputs(**kwargs))
