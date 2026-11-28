"""Instant feasibility (feaso). Pure functions, no I/O.

Implements spec section 3 exactly. All outputs are screening estimates, not valuations.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any


class FeasoInputError(ValueError):
    pass


@dataclass(frozen=True)
class FeasoInputs:
    price: float
    existing_rent_pw: float        # weekly rent for the existing house
    unit_rent_pw: float            # weekly rent per unit
    interest_rate: float = 0.07
    cap_months: float = 6
    draw_factor: float = 1.0
    target_yield: float = 0.06
    yield_basis: str = "gross"     # "gross" | "net"
    opex_ratio: float = 0.25
    ltv_cap: float = 0.65
    units: int = 2
    unit_cost_installed: float = 150000
    misc: float = 40000
    min_io_cover: float = 1.0
    # Second valuation (summation): current council CV + value the units add. None = not used.
    cv: float | None = None
    unit_value_added: float | None = None   # per unit; None = installed cost
    value_basis: str = "lower"               # lower | income | gv : which value the LTV verdict uses

    def validate(self) -> None:
        if self.price is None or self.price <= 0:
            raise FeasoInputError("price must be a positive number")
        if self.existing_rent_pw is None or self.unit_rent_pw is None:
            raise FeasoInputError("rents are required")
        if self.existing_rent_pw < 0 or self.unit_rent_pw < 0:
            raise FeasoInputError("rents cannot be negative")
        if self.value_basis not in ("lower", "income", "gv"):
            raise FeasoInputError("value_basis must be lower, income or gv")
        if self.cv is not None and self.cv <= 0:
            raise FeasoInputError("cv must be positive")
        if self.yield_basis not in ("gross", "net"):
            raise FeasoInputError("yield_basis must be 'gross' or 'net'")
        if self.target_yield <= 0:
            raise FeasoInputError("target_yield must be > 0")
        if not 0 <= self.interest_rate < 1:
            raise FeasoInputError("interest_rate must be in [0, 1)")
        if self.cap_months < 0 or self.draw_factor < 0 or self.units < 0:
            raise FeasoInputError("cap_months, draw_factor and units must be >= 0")
        if not 0 <= self.opex_ratio < 1:
            raise FeasoInputError("opex_ratio must be in [0, 1)")
        denom = 1 - self.interest_rate * self.cap_months / 12 * self.draw_factor
        if denom <= 0:
            raise FeasoInputError("interest x cap_months x draw_factor too large: loan is unbounded")


@dataclass(frozen=True)
class FeasoResult:
    cost_base: float
    loan: float
    capitalised_interest: float
    annual_rent: float
    noi: float
    end_value: float
    ltv: float
    ltv_pass: bool
    equity: float
    refi_headroom: float
    io_cover: float | None         # None when interest is zero (cover is unbounded)
    io_pass: bool
    gv_value: float | None = None      # CV + units' added value
    gv_ltv: float | None = None
    ltv_used: float = 0.0              # the LTV the verdict uses (see value_basis)
    value_used: str = "income"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run(i: FeasoInputs) -> FeasoResult:
    i.validate()
    cost_base = i.price + i.units * i.unit_cost_installed + i.misc
    loan = cost_base / (1 - i.interest_rate * i.cap_months / 12 * i.draw_factor)
    capitalised_interest = loan - cost_base
    annual_rent = 52 * (i.existing_rent_pw + i.units * i.unit_rent_pw)
    noi = annual_rent if i.yield_basis == "gross" else annual_rent * (1 - i.opex_ratio)
    end_value = noi / i.target_yield
    ltv = loan / end_value if end_value > 0 else float("inf")
    equity = end_value - loan
    refi_headroom = i.ltv_cap * end_value - loan
    annual_interest = loan * i.interest_rate
    io_cover = noi / annual_interest if annual_interest > 0 else None
    gv_value = gv_ltv = None
    ltv_used, value_used = ltv, "income"
    if i.cv:
        per_unit = i.unit_value_added if i.unit_value_added is not None else i.unit_cost_installed
        gv_value = i.cv + i.units * per_unit
        gv_ltv = loan / gv_value
        if i.value_basis == "gv" or (i.value_basis == "lower" and gv_ltv > ltv):
            ltv_used, value_used = gv_ltv, "gv"
    return FeasoResult(
        cost_base=cost_base,
        loan=loan,
        capitalised_interest=capitalised_interest,
        annual_rent=annual_rent,
        noi=noi,
        end_value=end_value,
        ltv=ltv,
        ltv_pass=ltv_used <= i.ltv_cap,
        equity=equity,
        refi_headroom=refi_headroom,
        io_cover=io_cover,
        io_pass=io_cover is None or io_cover >= i.min_io_cover,
        gv_value=gv_value,
        gv_ltv=gv_ltv,
        ltv_used=ltv_used,
        value_used=value_used,
    )


@dataclass(frozen=True)
class StressSpec:
    rent_change: float = -0.15
    cost_change: float = 0.10
    target_yield: float = 0.065
    interest_rate: float = 0.08


def _apply(i: FeasoInputs, s: StressSpec, rent=False, cost=False, yld=False, rate=False) -> FeasoInputs:
    out = i
    if rent:
        out = replace(out, existing_rent_pw=out.existing_rent_pw * (1 + s.rent_change),
                      unit_rent_pw=out.unit_rent_pw * (1 + s.rent_change))
    if cost:
        # "cost +10%" applies to the development costs (units + misc), not the purchase price.
        out = replace(out, unit_cost_installed=out.unit_cost_installed * (1 + s.cost_change),
                      misc=out.misc * (1 + s.cost_change))
    if yld:
        out = replace(out, target_yield=s.target_yield)
    if rate:
        out = replace(out, interest_rate=s.interest_rate)
    return out


@dataclass
class FeasoReport:
    inputs: FeasoInputs
    base: FeasoResult
    scenarios: dict[str, FeasoResult] = field(default_factory=dict)
    worst_ltv: float = 0.0
    worst_io_cover: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "inputs": asdict(self.inputs),
            "base": self.base.to_dict(),
            "scenarios": {k: v.to_dict() for k, v in self.scenarios.items()},
            "worst_ltv": self.worst_ltv,
            "worst_io_cover": self.worst_io_cover,
        }


def run_with_stress(i: FeasoInputs, s: StressSpec | None = None) -> FeasoReport:
    s = s or StressSpec()
    base = run(i)
    scen = {
        f"rents {s.rent_change:+.0%}": run(_apply(i, s, rent=True)),
        f"dev cost {s.cost_change:+.0%}": run(_apply(i, s, cost=True)),
        f"yield {s.target_yield:.1%}": run(_apply(i, s, yld=True)),
        f"rate {s.interest_rate:.1%}": run(_apply(i, s, rate=True)),
        "combined (all four)": run(_apply(i, s, rent=True, cost=True, yld=True, rate=True)),
    }
    all_results = [base, *scen.values()]
    covers = [r.io_cover for r in all_results if r.io_cover is not None]
    return FeasoReport(
        inputs=i,
        base=base,
        scenarios=scen,
        worst_ltv=max(r.ltv_used for r in all_results),
        worst_io_cover=min(covers) if covers else None,
    )
