"""The issuer's call: the language, the record of an uncalled run, the policy fitted on a pilot and applied out of
sample, against a PDE (within 4 standard errors plus the policy's bias, and never below the PDE: a policy fitted on
other paths is one the issuer could follow, never better than the best)."""

import dataclasses

import numpy as np
import pytest
from _callable_refs import callable_note
from _paths import SPX_CALIBRATION
from _structured_refs import FLAT, VOL, Q, R

from optlab.core import BlackScholesModel
from optlab.core.models import LocalVolModel
from optlab.structured.cashflows import Note
from optlab.structured.context import PricingContext
from optlab.structured.exercise import survival_and_flows
from optlab.structured.pricer import price, price_many
from optlab.structured.profile import profile
from optlab.structured.script.evaluator import Evaluator
from optlab.structured.script.lexer import ScriptError
from optlab.structured.script.parser import parse
from optlab.structured.script.printer import to_text
from optlab.structured.script.timeline import build_timeline
from optlab.structured.script.validate import validate

N_PATHS = 100_000
BIAS = 0.0010  # the policy's bias allowed on top of 4 SE, in units of the notional (10 bp)


def callable_rc(c=0.06, calls=(0.5, 1.0, 1.5), T=2.0, step=0.5, state="", strike=1.0):
    """Fixed coupons every `step`, the issuer's call at par after the coupon on `calls`, a reverse convertible at T."""
    lines = ["underlying SPX", f"param c = {c}"] + ([f"state {state}"] if state else [])
    t = step
    while t < T - 1e-9:
        lines += [f"event t={t:g} pay t={t:g}", f"  pay c * {step:g} as coupon"]
        if any(abs(t - u) < 1e-9 for u in calls):
            lines.append("  call pay 1")
        lines.append("end")
        t += step
    lines += [
        f"event t={T:g} pay t={T:g}",
        f"  pay c * {step:g} as coupon",
        f"  pay min(SPX / {100 * strike:g}, 1) as redemption",
        "end",
    ]
    return "\n".join(lines) + "\n"


def bs():
    return BlackScholesModel(VOL)


def check(mc, ref, what):
    """The Monte Carlo value of a note under a fitted policy: never below the PDE (4 SE), at most 4 SE plus the bias
    above it."""
    v, se = mc.value.price, mc.value.stderr
    assert v > ref - 4 * se, f"{what}: {v:.6f} below the PDE {ref:.6f} by more than 4 SE ({se:.2e})"
    assert v < ref + 4 * se + BIAS, f"{what}: {v:.6f} above the PDE {ref:.6f} by more than 4 SE plus the bias"


# --- the language ---


def test_call_and_state_print_and_parse_back():
    text = callable_rc(state="SPX")
    program = validate(parse(text))
    assert program.states == ("SPX",)
    assert validate(parse(to_text(program))) == program
    assert "call pay 1" in to_text(program)


def test_call_and_state_stay_ordinary_names_elsewhere():
    """`call` and `state` are words of the language only where they start a call or a declaration: a variable may
    still be called so."""
    text = "underlying SPX\nevent t=1 pay t=1\n  call = SPX / 100\n  state = call * 2\n  pay state as redemption\nend\n"
    program = validate(parse(text))
    assert program.states == ()
    assert validate(parse(to_text(program))) == program


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("underlying SPX\nevent t=1\n  call pay 1\nend\n", "payment date"),
        ("underlying SPX\nevent t=1 pay t=1\n  call pay 1\n  call pay 1\n  pay 1 as redemption\nend\n", "one call"),
        ("underlying SPX\nstate memory\nevent t=1 pay t=1\n  call pay 1\n  pay 1 as redemption\nend\n", "memory"),
        ("underlying SPX\nstate SPX, SPX\nevent t=1 pay t=1\n  pay 1 as redemption\nend\n", "twice"),
    ],
)
def test_a_call_is_validated(text, message):
    with pytest.raises(ScriptError, match=message):
        validate(parse(text))


def test_an_issuer_call_is_its_own_class():
    program = validate(parse(callable_rc()))
    prof = profile(program, build_timeline(program, None))
    assert prof.product_class == "issuer_callable"
    assert "issuer" in prof.description


# --- the record of an uncalled run ---


def test_no_call_rebuilds_the_uncalled_flows_and_a_call_everywhere_redeems_at_once():
    program = validate(parse(callable_rc()))
    timeline = build_timeline(program, None)
    from optlab.structured.engine import _scenario

    scenario = _scenario(bs(), FLAT, "SPX", timeline.sim_times, 2_000, 7, 100.0, {}, {})
    out = Evaluator(program, timeline).run(scenario)
    uncalled = {k: np.array(v, copy=True) for k, v in out.flows.items()}
    calls = sorted(out.exercise.calls)
    rebuilt = survival_and_flows(dataclasses.replace(out), {k: np.zeros(2_000) for k in calls})
    assert set(rebuilt.flows) == set(uncalled)
    for k, v in uncalled.items():
        np.testing.assert_array_equal(rebuilt.flows[k], v)
    first = survival_and_flows(dataclasses.replace(out), {k: np.full(2_000, float(k == calls[0])) for k in calls})
    t = first.exercise.events[calls[0]].t_pay
    np.testing.assert_allclose(first.flows[(t, "redemption")], 1.0)
    np.testing.assert_allclose(first.flows[(t, "coupon")], 0.03)
    assert all(np.all(f == 0) for (u, _), f in first.flows.items() if u > t)
    np.testing.assert_allclose(sum(first.redemption_weights.values()), 1.0)
    np.testing.assert_allclose(first.terminated[calls[0]], 1.0)


# --- against the PDE ---


def test_callable_reverse_convertible_against_the_pde():
    ref = callable_note(
        lambda p: np.minimum(p, 1.0),
        2.0,
        [(t, 0.03) for t in (0.5, 1.0, 1.5, 2.0)],
        [(0.5, 1.0), (1.0, 1.0), (1.5, 1.0)],
        r=R,
        q=Q,
        vol=VOL,
    )
    plain = callable_note(
        lambda p: np.minimum(p, 1.0), 2.0, [(t, 0.03) for t in (0.5, 1.0, 1.5, 2.0)], r=R, q=Q, vol=VOL
    )
    res = price(callable_rc(), bs(), FLAT, n_paths=N_PATHS, seed=11)
    check(res, ref, "callable RC")
    assert plain - ref > 0.002  # the call is worth something to the issuer
    assert "issuer_call" in res.timings and res.timings["issuer_call"]["pilot_paths"] > 0


def test_callable_reverse_convertible_under_local_vol_against_the_pde():
    """The SPX calibration's surface, its Dupire local vol in the PDE (in the log-forward moneyness the model reads)."""
    surface = PricingContext.from_calibration(SPX_CALIBRATION).surface

    def local_vol(t, S):
        return np.sqrt(surface.local_var(np.log(S / (100.0 * np.exp((R - Q) * t))), max(t, 1 / 365)))

    ref = callable_note(
        lambda p: np.minimum(p, 1.0),
        2.0,
        [(t, 0.03) for t in (0.5, 1.0, 1.5, 2.0)],
        [(0.5, 1.0), (1.0, 1.0), (1.5, 1.0)],
        r=R,
        q=Q,
        vol=0.25,
        local_vol=local_vol,
    )
    res = price(callable_rc(), LocalVolModel(surface), FLAT, n_paths=N_PATHS, seed=31)
    check(res, ref, "callable RC under local vol")


def test_the_issuer_discounts_on_its_own_curve():
    """With a funding spread the note is worth less and the issuer calls earlier: the policy uses the note's
    discounting."""
    spread = 0.02
    ref = callable_note(
        lambda p: np.minimum(p, 1.0),
        2.0,
        [(t, 0.03) for t in (0.5, 1.0, 1.5, 2.0)],
        [(0.5, 1.0), (1.0, 1.0), (1.5, 1.0)],
        r=R,
        q=Q,
        vol=VOL,
        spread=spread,
    )
    res = price(callable_rc(), bs(), FLAT, fmt=Note(spread=spread), n_paths=N_PATHS, seed=12)
    check(res, ref, "callable RC on the issuer's curve")


def test_callable_phoenix_with_a_state_against_the_pde():
    """Quarterly conditional coupons (8 % p.a. above 70 %), the issuer's call at par from one year, a European
    knock-in at 60 % at three years: the state is the spot."""
    T, step, barrier, ki = 3.0, 0.25, 0.7, 0.6
    dates = [round(step * i, 10) for i in range(1, int(T / step) + 1)]
    lines = ["underlying SPX", "param c = 0.08", "state SPX"]
    for t in dates[:-1]:
        lines += [
            f"event t={t:g} pay t={t:g}",
            f"  if SPX >= {100 * barrier:g} then",
            "    pay c / 4 as coupon",
            "  end",
        ]
        if t >= 1.0 - 1e-9:
            lines.append("  call pay 1")
        lines.append("end")
    lines += [
        f"event t={T:g} pay t={T:g}",
        f"  if SPX >= {100 * barrier:g} then",
        "    pay c / 4 as coupon",
        "  end",
        f"  if SPX < {100 * ki:g} then",
        "    pay SPX / 100 as redemption",
        "  else",
        "    pay 1 as redemption",
        "  end",
        "end",
    ]
    text = "\n".join(lines) + "\n"
    coupon = lambda p: np.where(p >= barrier, 0.02, 0.0)  # noqa: E731 (a coupon of the performance)
    ref = callable_note(
        lambda p: np.where(p < ki, p, 1.0),
        T,
        [(t, coupon) for t in dates],
        [(t, 1.0) for t in dates[:-1] if t >= 1.0 - 1e-9],
        r=R,
        q=Q,
        vol=VOL,
    )
    res = price(text, bs(), FLAT, n_paths=N_PATHS, seed=13)
    check(res, ref, "callable phoenix")


def test_the_policy_is_kept_for_the_spot_bumps_so_delta_is_smooth():
    """price_many fits one policy on the base and applies it to every spot factor: the delta matches the PDE's."""
    h = 0.01
    res = price_many(
        callable_rc(), bs(), FLAT, [{}, {}, {}], n_paths=N_PATHS, seed=14, spot_factors=[1 - h, 1.0, 1 + h]
    )
    down, base, up = (r.value.price for r in res)
    ref = [
        callable_note(
            lambda p: np.minimum(p, 1.0),
            2.0,
            [(t, 0.03) for t in (0.5, 1.0, 1.5, 2.0)],
            [(0.5, 1.0), (1.0, 1.0), (1.5, 1.0)],
            r=R,
            q=Q,
            vol=VOL,
            at=a,
        )
        for a in (1 - h, 1 + h)
    ]
    delta, ref_delta = (up - down) / (2 * h), (ref[1] - ref[0]) / (2 * h)
    assert res[0].pairs is not None and res[2].pairs is not None  # noqa: S101 (narrows the type: Monte Carlo keeps its pairs)
    pair = (res[2].pairs - res[0].pairs) / (2 * h)
    se = float(pair.std(ddof=1) / np.sqrt(len(pair)))
    assert abs(delta - ref_delta) < 4 * se + 0.01, (delta, ref_delta, se)
    assert res[0].policy is res[1].policy is res[2].policy


def test_the_same_seed_gives_the_same_price():
    a = price(callable_rc(), bs(), FLAT, n_paths=20_000, seed=21)
    b = price(callable_rc(), bs(), FLAT, n_paths=20_000, seed=21)
    assert a.value.price == b.value.price


def test_the_dual_bound_and_the_policy_bracket_the_pde():
    """Andersen-Broadie from continuations branched at each call date: a lower bound under the PDE, the policy's
    value above it, and a gap of under 10 bp between them."""
    ref = callable_note(
        lambda p: np.minimum(p, 1.0),
        2.0,
        [(t, 0.03) for t in (0.5, 1.0, 1.5, 2.0)],
        [(0.5, 1.0), (1.0, 1.0), (1.5, 1.0)],
        r=R,
        q=Q,
        vol=VOL,
    )
    res = price(callable_rc(), bs(), FLAT, n_paths=N_PATHS, seed=11, dual={"n_outer": 1_000, "n_inner": 200})
    bound = res.timings["issuer_call"]["dual"]
    assert bound["lower"] < ref + 4 * bound["lower_se"]
    assert -4 * bound["gap_se"] < bound["gap"] < 0.001
    check(res, ref, "callable RC with its dual bound")


def test_the_dual_bound_says_when_it_does_not_apply():
    spx = PricingContext.from_calibration(SPX_CALIBRATION)
    res = price(callable_rc(), spx.model("heston"), FLAT, n_paths=20_000, seed=3, dual=True)
    assert "dual" not in res.timings["issuer_call"]
    assert any("no dual bound for a model with a variance process" in w for w in res.warnings)
