"""The continuous beam solver against closed forms, its own statics and its contract.

The closed forms are the classic ones: the tables of any beam handbook, and the three-moment
equation for spans of different stiffness. The published examples, each with its source, are in
``py/validation``; the comparison with PyNite in ``test_analysis_pynite.py``.
"""

from __future__ import annotations

import ast
import itertools
import sys

import analysis
import numpy as np
import pytest
from analysis import (
    AnalysisError,
    Beam,
    Couple,
    Point,
    Uniform,
    arrangements,
    diagrams,
    envelope,
    redistribute,
    solve,
    stations,
    support_moments,
)

L = 5.0  # each span
q = 10.0
P = 40.0
EI = 1.0e4


def _equal_spans(count: int, ends: tuple[str, str] = ("pin", "pin")) -> Beam:
    return Beam((L,) * count, (EI,) * count, (ends[0],) + ("pin",) * (count - 1) + (ends[1],))


def _uniform(beam: Beam, intensity: float = q) -> list[Uniform]:
    return [Uniform(e, intensity) for e in range(len(beam.lengths))]


# ----------------------------------------------------------------------------- closed forms


class TestEqualSpansUnderAUniformLoad:
    def test_two_spans(self) -> None:
        """−qL²/8 over the middle support, 3qL/8 · 10qL/8 · 3qL/8 at the supports and the
        largest sagging moment 9qL²/128, at 3L/8 from each end."""
        beam = _equal_spans(2)
        result = solve(beam, {"D": _uniform(beam)})["D"]
        assert result.moment(L) == pytest.approx(-q * L**2 / 8)
        assert result.reactions[:, 0] == pytest.approx([3 * q * L / 8, 10 * q * L / 8, 3 * q * L / 8])
        assert result.reactions[:, 1] == pytest.approx([0.0] * 3)
        assert result.moment(3 * L / 8) == pytest.approx(9 * q * L**2 / 128)
        assert result.moment(2 * L - 3 * L / 8) == pytest.approx(9 * q * L**2 / 128)

    def test_the_shear_jumps_by_the_reaction_over_the_middle_support(self) -> None:
        beam = _equal_spans(2)
        result = solve(beam, {"D": _uniform(beam)})["D"]
        assert result.shear(L) == pytest.approx(-5 * q * L / 8)
        assert result.shear(L, after=True) == pytest.approx(5 * q * L / 8)
        assert result.moment(L) == pytest.approx(result.moment(L, after=True))

    def test_three_spans(self) -> None:
        """−qL²/10 over each interior support; 0.4qL, 1.1qL, 1.1qL, 0.4qL at the supports."""
        beam = _equal_spans(3)
        result = solve(beam, {"D": _uniform(beam)})["D"]
        assert result.moment([L, 2 * L]) == pytest.approx([-q * L**2 / 10] * 2)
        assert result.reactions[:, 0] == pytest.approx([0.4 * q * L, 1.1 * q * L, 1.1 * q * L, 0.4 * q * L])

    def test_the_deflection_of_two_spans(self) -> None:
        """v(x) = −q·x·(L³ − 3L·x² + 2x³)/(48·EI) in each span, mirrored in the second."""
        beam = _equal_spans(2)
        result = solve(beam, {"D": _uniform(beam)})["D"]
        x = np.linspace(0.0, 2 * L, 41)
        s = np.minimum(x, 2 * L - x)
        expected = -q * s * (L**3 - 3 * L * s**2 + 2 * s**3) / (48 * EI)
        np.testing.assert_allclose(result.deflection(x), expected, rtol=1e-12, atol=1e-15)
        assert result.deflection(L / 2) == pytest.approx(-q * L**4 / (192 * EI))


class TestOneSpan:
    def test_simply_supported(self) -> None:
        beam = Beam((L,), (EI,), ("pin", "pin"))
        result = solve(beam, {"D": [Uniform(0, q)]})["D"]
        assert result.moment(L / 2) == pytest.approx(q * L**2 / 8)
        assert result.deflection(L / 2) == pytest.approx(-5 * q * L**4 / (384 * EI))
        assert result.rotation(0.0) == pytest.approx(-q * L**3 / (24 * EI))

    def test_fixed_at_both_ends(self) -> None:
        """−qL²/12 at each end, qL²/24 at mid-span, qL⁴/(384·EI); each clamp holds qL²/12, the
        left one counterclockwise."""
        beam = Beam((L,), (EI,), ("fixed", "fixed"))
        result = solve(beam, {"D": [Uniform(0, q)]})["D"]
        assert result.moment([0.0, L]) == pytest.approx([-q * L**2 / 12] * 2)
        assert result.moment(L / 2) == pytest.approx(q * L**2 / 24)
        assert result.deflection(L / 2) == pytest.approx(-q * L**4 / (384 * EI))
        assert result.reactions == pytest.approx(np.array([[q * L / 2, q * L**2 / 12], [q * L / 2, -q * L**2 / 12]]))

    def test_fixed_and_pinned(self) -> None:
        """Fixed at the start: −qL²/8 there, 5qL/8 and 3qL/8, 9qL²/128 at 5L/8."""
        beam = Beam((L,), (EI,), ("fixed", "pin"))
        result = solve(beam, {"D": [Uniform(0, q)]})["D"]
        assert result.moment(0.0) == pytest.approx(-q * L**2 / 8)
        assert result.reactions[:, 0] == pytest.approx([5 * q * L / 8, 3 * q * L / 8])
        assert result.moment(5 * L / 8) == pytest.approx(9 * q * L**2 / 128)

    def test_a_cantilever(self) -> None:
        """The tip drops qL⁴/(8·EI) under q and PL³/(3·EI) under P at it."""
        beam = Beam((L,), (EI,), ("fixed", "free"))
        solution = solve(beam, {"q": [Uniform(0, q)], "P": [Point(0, P, L)]})
        assert solution["q"].deflection(L) == pytest.approx(-q * L**4 / (8 * EI))
        assert solution["P"].deflection(L) == pytest.approx(-P * L**3 / (3 * EI))
        assert solution["P"].moment(0.0) == pytest.approx(-P * L)
        assert solution["P"].reactions[0] == pytest.approx([P, P * L])

    def test_a_partial_load(self) -> None:
        """q on 2–6 m of a 10 m simple span: R_A = 2.4q, R_B = 1.6q; the peak, where the shear
        is zero, at 4.4 m: 2.4q·4.4 − q·2.4²/2 = 7.68q."""
        beam = Beam((10.0,), (EI,), ("pin", "pin"))
        result = solve(beam, {"D": [Uniform(0, q, 2.0, 6.0)]})["D"]
        assert result.reactions[:, 0] == pytest.approx([2.4 * q, 1.6 * q])
        assert result.shear(4.4) == pytest.approx(0.0, abs=1e-9)
        assert result.moment(4.4) == pytest.approx(7.68 * q)
        assert result.moment(8.0) == pytest.approx(1.6 * q * 2.0)

    def test_a_point_load_and_its_jump(self) -> None:
        """P at a = 2 of a 5 m span: P·b/L before it, −P·a/L past it, P·a·b/L under it."""
        a, b = 2.0, 3.0
        beam = Beam((a + b,), (EI,), ("pin", "pin"))
        result = solve(beam, {"P": [Point(0, P, a)]})["P"]
        assert result.shear(a) == pytest.approx(P * b / (a + b))
        assert result.shear(a, after=True) == pytest.approx(-P * a / (a + b))
        assert result.moment(a) == pytest.approx(P * a * b / (a + b))

    def test_a_couple_and_its_jump(self) -> None:
        """A counterclockwise couple C at a: ΣM about B gives R_A = C/L and R_B = −C/L; the
        moment is R_A·a = C·a/L just before it and drops by C across it."""
        a, C = 2.0, 30.0
        beam = Beam((L,), (EI,), ("pin", "pin"))
        result = solve(beam, {"C": [Couple(0, C, a)]})["C"]
        assert result.reactions[:, 0] == pytest.approx([C / L, -C / L])
        assert result.moment(a) == pytest.approx(C * a / L)
        assert result.moment(a, after=True) == pytest.approx(C * a / L - C)


class TestOneLoadAtATime:
    def test_a_point_load_at_mid_span_of_one_span_of_two(self) -> None:
        """−3PL/32 over B; 13P/32, 22P/32 and −3P/32 (C holds the beam down); 13PL/64 under P."""
        beam = _equal_spans(2)
        result = solve(beam, {"P": [Point(0, P, L / 2)]})["P"]
        assert result.moment(L) == pytest.approx(-3 * P * L / 32)
        assert result.moment(L / 2) == pytest.approx(13 * P * L / 64)
        assert result.reactions[:, 0] == pytest.approx([13 * P / 32, 22 * P / 32, -3 * P / 32])
        assert result.moment(1.5 * L) == pytest.approx(-3 * P * L / 64)

    def test_a_couple_at_the_middle_support_splits_by_the_stiffness_of_each_span(self) -> None:
        """Spans of 4 and 6 m, each pinned at its far end (3EI/L): 0.6 and 0.4 of a clockwise
        couple C, which raises the sagging moment by C across B: −0.6C before, +0.4C past."""
        C, L1, L2 = 30.0, 4.0, 6.0
        beam = Beam((L1, L2), (EI, EI), ("pin", "pin", "pin"))
        result = solve(beam, {"C": [Couple(1, -C, 0.0)]})["C"]
        assert result.moment(L1) == pytest.approx(-0.6 * C)
        assert result.moment(L1, after=True) == pytest.approx(0.4 * C)
        assert result.reactions[:, 0] == pytest.approx([-0.6 * C / L1, 0.6 * C / L1 - 0.4 * C / L2, 0.4 * C / L2])

    def test_spans_of_different_stiffness(self) -> None:
        """The three-moment equation: M_B = −q·(L₁³/EI₁ + L₂³/EI₂) / (8·(L₁/EI₁ + L₂/EI₂))."""
        L1, L2, EI1, EI2 = 4.0, 7.0, 2.0e4, 5.0e4
        beam = Beam((L1, L2), (EI1, EI2), ("pin", "pin", "pin"))
        result = solve(beam, {"D": [Uniform(0, q), Uniform(1, q)]})["D"]
        expected = -q * (L1**3 / EI1 + L2**3 / EI2) / (8 * (L1 / EI1 + L2 / EI2))
        assert result.moment(L1) == pytest.approx(expected)

    def test_a_section_that_changes_inside_a_span(self) -> None:
        """A free node in the middle of a span changes nothing when the two halves are alike."""
        whole = solve(Beam((L,), (EI,), ("pin", "pin")), {"D": [Uniform(0, q)]})["D"]
        halves = solve(Beam((L / 2, L / 2), (EI, EI), ("pin", "free", "pin")), {"D": [Uniform(0, q), Uniform(1, q)]})
        x = np.linspace(0.0, L, 11)
        np.testing.assert_allclose(halves["D"].moment(x), whole.moment(x), rtol=1e-12, atol=1e-12)
        np.testing.assert_allclose(halves["D"].deflection(x), whole.deflection(x), rtol=1e-12, atol=1e-15)


class TestOverhangs:
    a, span = 1.5, 6.0

    def _solution(self) -> analysis.Solution:
        """P on each tip, the supports a from each end."""
        beam = Beam((self.a, self.span, self.a), (EI,) * 3, ("free", "pin", "pin", "free"))
        return solve(beam, {"P": [Point(0, P, 0.0), Point(2, P, self.a)]})

    def test_a_point_load_on_each_overhang(self) -> None:
        """−P·a over each support and all the way between, where the shear is zero."""
        result = self._solution()["P"]
        a, s = self.a, self.span
        assert result.moment([a, a + 1.0, a + s / 2, a + s]) == pytest.approx([-P * a] * 4)
        assert result.shear(a + 2.0) == pytest.approx(0.0, abs=1e-9)
        assert result.shear(a / 2) == pytest.approx(-P)
        assert result.reactions[:, 0] == pytest.approx([0.0, P, P, 0.0])

    def test_the_span_lifts_and_the_tips_drop(self) -> None:
        """The span rises P·a·s²/(8·EI) at its middle; each tip drops P·a²·s/(2·EI) + P·a³/(3·EI)."""
        result = self._solution()["P"]
        a, s = self.a, self.span
        assert result.deflection(a + s / 2) == pytest.approx(P * a * s**2 / (8 * EI))
        tip = -(P * a**2 * s / (2 * EI) + P * a**3 / (3 * EI))
        assert result.deflection([0.0, 2 * a + s]) == pytest.approx([tip, tip])

    def test_which_elements_are_overhangs(self) -> None:
        beam = self._solution().beam
        assert beam.overhangs == (0, 2)
        assert beam.spans == ((1,),)
        assert beam.redistributable == ()


# ------------------------------------------------------------------------------ redistribution


class TestRedistribution:
    def test_two_spans(self) -> None:
        """M_B = −(1 − r)·qL²/8; R_A = qL/2 − (1 − r)·qL/8; the span peaks at R_A²/(2q)."""
        beam = _equal_spans(2)
        elastic = solve(beam, {"D": _uniform(beam)})["D"]
        for r in (0.0, 0.1, 0.2, 0.3):
            result = redistribute(elastic, r)
            reaction = q * L / 2 - (1 - r) * q * L / 8
            assert result.moment(L) == pytest.approx(-(1 - r) * q * L**2 / 8)
            assert result.reactions[:, 0] == pytest.approx([reaction, q * L - 2 * reaction + q * L, reaction])
            peak = reaction / q
            assert result.shear(peak) == pytest.approx(0.0, abs=1e-9)
            assert result.moment(peak) == pytest.approx(reaction**2 / (2 * q))

    def test_nothing_to_redistribute_is_the_elastic_result(self) -> None:
        beam = _equal_spans(3, ("fixed", "pin"))
        elastic = solve(beam, {"D": _uniform(beam)})["D"]
        result = redistribute(elastic, 0.0)
        x, after = stations([elastic])
        np.testing.assert_allclose(diagrams([result], x, after)[0, :, :2], diagrams([elastic], x, after)[0, :, :2])
        np.testing.assert_allclose(result.reactions, elastic.reactions)

    def test_a_fixed_end_is_redistributed_and_its_clamp_follows(self) -> None:
        beam = Beam((L,), (EI,), ("fixed", "fixed"))
        result = redistribute(solve(beam, {"D": [Uniform(0, q)]})["D"], 0.2)
        assert result.moment([0.0, L]) == pytest.approx([-0.8 * q * L**2 / 12] * 2)
        assert result.moment(L / 2) == pytest.approx(q * L**2 / 8 - 0.8 * q * L**2 / 12)
        assert result.reactions[:, 1] == pytest.approx([0.8 * q * L**2 / 12, -0.8 * q * L**2 / 12])

    def test_the_support_of_an_overhang_is_not_moved(self) -> None:
        """Over the support of an overhang the moment is the overhang's statics."""
        beam = Beam((1.5, L, L), (EI,) * 3, ("free", "pin", "pin", "pin"))
        elastic = solve(beam, {"D": [Uniform(e, q) for e in range(3)]})["D"]
        assert beam.redistributable == (2, 3)
        result = redistribute(elastic, 0.25)
        assert result.moment(1.5) == pytest.approx(elastic.moment(1.5))
        assert result.moment(1.5 + L) == pytest.approx(0.75 * elastic.moment(1.5 + L))
        with pytest.raises(AnalysisError, match="node 1"):
            redistribute(elastic, {1: 0.1})

    def test_a_support_that_sags_is_left_as_it_is(self) -> None:
        beam = _equal_spans(3)
        elastic = solve(beam, {"P": [Point(0, P, L / 2)]})["P"]
        moments = support_moments(elastic)
        assert moments[2] > 0  # the far support sags under a load in the first span
        result = redistribute(elastic, 0.2)
        assert support_moments(result)[2] == pytest.approx(moments[2])
        assert support_moments(result)[1] == pytest.approx(0.8 * moments[1])

    def test_a_redistributed_result_has_no_deflection(self) -> None:
        beam = _equal_spans(2)
        result = redistribute(solve(beam, {"D": _uniform(beam)})["D"], 0.1)
        with pytest.raises(AnalysisError, match="deflection"):
            result.deflection(1.0)

    @pytest.mark.parametrize("ratio", [-0.1, 1.0])
    def test_a_ratio_out_of_range_is_refused(self, ratio: float) -> None:
        beam = _equal_spans(2)
        with pytest.raises(AnalysisError, match="between 0 and 1"):
            redistribute(solve(beam, {"D": _uniform(beam)})["D"], ratio)


# ------------------------------------------------------------------- arrangements and envelope


class TestArrangements:
    def _solution(self) -> analysis.Solution:
        beam = Beam((1.2, 5.0, 6.5, 4.0), (EI, 1.5 * EI, EI, 0.8 * EI), ("free", "pin", "pin", "pin", "fixed"))
        loads = {"D": [Uniform(e, 12.0) for e in range(4)]}
        loads |= {f"L{e}": [Uniform(e, 8.0)] for e in range(4)}
        return solve(beam, loads)

    def test_every_subset_once(self) -> None:
        found = arrangements(self._solution(), {"D": 1.2}, {f"L{e}": 1.6 for e in range(4)})
        assert len(found) == 16
        assert len({arrangement.on for arrangement in found}) == 16

    def test_the_envelope_is_the_permanent_part_plus_every_positive_part(self) -> None:
        """Two roads to one envelope: the extremes over the 2ⁿ arrangements, and the permanent
        part plus the positive (or negative) part of each patterned case alone."""
        solution = self._solution()
        found = arrangements(solution, {"D": 1.2}, {f"L{e}": 1.6 for e in range(4)})
        results = [arrangement.result for arrangement in found]
        x, after = stations(results)
        bounds = envelope(results, x, after)
        dead = diagrams([solution["D"]], x, after)[0]
        live = diagrams([solution[f"L{e}"] for e in range(4)], x, after)
        for column, (low, high) in ((0, (bounds.V_min, bounds.V_max)), (1, (bounds.M_min, bounds.M_max))):
            parts = 1.6 * live[..., column]
            np.testing.assert_allclose(high, 1.2 * dead[:, column] + np.clip(parts, 0, None).sum(0), atol=1e-9)
            np.testing.assert_allclose(low, 1.2 * dead[:, column] + np.clip(parts, None, 0).sum(0), atol=1e-9)

    def test_the_envelope_peak_is_read_where_it_is(self) -> None:
        """The stations hold every zero of every arrangement's shear: no fine grid finds a
        larger sagging moment."""
        solution = self._solution()
        results = [a.result for a in arrangements(solution, {"D": 1.2}, {f"L{e}": 1.6 for e in range(4)})]
        peak = envelope(results).M_max.max()
        fine = np.linspace(0.0, solution.beam.length, 20001)
        assert diagrams(results, fine, np.zeros(fine.size, bool))[..., 1].max() <= peak + 1e-9

    def test_unknown_or_too_many_cases_are_refused(self) -> None:
        solution = self._solution()
        with pytest.raises(AnalysisError, match="no load case"):
            arrangements(solution, {"X": 1.0}, {})
        too_many = {f"L{i}": 1.0 for i in range(analysis.MAX_PATTERNED + 1)}
        with pytest.raises(AnalysisError, match="patterned"):
            arrangements(solution, {}, too_many)


# -------------------------------------------------------------------------- its own statics


def _random_case(seed: int) -> tuple[Beam, list[analysis.Load]]:
    """A beam of 1 to 6 spans, any end, maybe an overhang, a stiffness per element and a mix
    of full, partial, point and couple loads."""
    rng = np.random.default_rng(seed)
    count = int(rng.integers(1, 7))
    lengths = list(rng.uniform(2.0, 9.0, count))
    supports = [str(rng.choice(["pin", "fixed"]))] + ["pin"] * (count - 1) + [str(rng.choice(["pin", "fixed"]))]
    if rng.random() < 0.5:
        lengths.insert(0, float(rng.uniform(0.5, 2.5)))
        supports[0] = "pin"
        supports.insert(0, "free")
    if rng.random() < 0.5:
        lengths.append(float(rng.uniform(0.5, 2.5)))
        supports[-1] = "pin"
        supports.append("free")
    if count == 1 and "fixed" not in supports and supports.count("pin") < 2:
        supports = ["pin" if kind == "free" else kind for kind in supports]
    beam = Beam(tuple(lengths), tuple(rng.uniform(0.5e4, 3e4, len(lengths))), tuple(supports))
    loads: list[analysis.Load] = []
    for e, length in enumerate(beam.lengths):
        loads.append(Uniform(e, float(rng.uniform(1, 20))))
        a, b = sorted(rng.uniform(0.0, length, 2))
        loads.append(Uniform(e, float(rng.uniform(-5, 15)), float(a), float(b)))
        loads.append(Point(e, float(rng.uniform(-10, 50)), float(rng.uniform(0.0, length))))
        loads.append(Couple(e, float(rng.uniform(-20, 20)), float(rng.uniform(0.0, length))))
    return beam, loads


def _load_resultants(loads: list[analysis.Load], beam: Beam) -> tuple[float, float]:
    """The downward force of the loads and their counterclockwise moment about x = 0."""
    force = moment = 0.0
    for load in loads:
        x0 = beam.nodes[load.element]
        if isinstance(load, Uniform):
            end = beam.lengths[load.element] if load.end is None else load.end
            weight = load.q * (end - load.start)
            force += weight
            moment -= weight * (x0 + (load.start + end) / 2)
        elif isinstance(load, Point):
            force += load.P
            moment -= load.P * (x0 + load.at)
        else:
            moment += load.C
    return force, moment


SEEDS = range(40)


@pytest.mark.parametrize("seed", SEEDS)
def test_the_reactions_balance_the_loads(seed: int) -> None:
    beam, loads = _random_case(seed)
    result = solve(beam, {"all": loads})["all"]
    force, moment = _load_resultants(loads, beam)
    assert result.reactions[:, 0].sum() == pytest.approx(force, rel=1e-9, abs=1e-8)
    reacted = (result.reactions[:, 0] * beam.nodes).sum() + result.reactions[:, 1].sum()
    assert reacted + moment == pytest.approx(0.0, abs=1e-7 * (1 + abs(moment)))


@pytest.mark.parametrize("seed", SEEDS)
def test_the_elastic_line_closes_on_every_node(seed: int) -> None:
    """Integrated from each element's start, the deflection and the rotation arrive at the next
    node as the stiffness solve found them; zero deflection over every support."""
    beam, loads = _random_case(seed)
    result = solve(beam, {"all": loads})["all"]
    scale = np.abs(result.displacements).max() + 1e-12
    left = result._at(beam.nodes[1:], False)[:, [3, 2]]
    np.testing.assert_allclose(left, result.displacements[1:], atol=1e-9 * scale)
    held = list(beam.held)
    np.testing.assert_allclose(result.displacements[held, 0], 0.0, atol=1e-12 * scale)


@pytest.mark.parametrize("seed", SEEDS)
def test_the_moment_is_the_integral_of_the_shear(seed: int) -> None:
    """M′ = V and V′ = −q, by central differences off the breakpoints; and every piece is
    continuous in V and M where nothing stands at its start."""
    beam, loads = _random_case(seed)
    result = solve(beam, {"all": loads})["all"]
    layout = result.layout
    middle = (layout.start + layout.end) / 2
    width = layout.end - layout.start
    h = 1e-4 * width
    V, M = result.shear(middle), result.moment(middle)
    dM = (result.moment(middle + h) - result.moment(middle - h)) / (2 * h)
    dV = (result.shear(middle + h) - result.shear(middle - h)) / (2 * h)
    scale = np.abs(V).max() + np.abs(M).max()
    np.testing.assert_allclose(dM, V, atol=1e-6 * scale)
    np.testing.assert_allclose(dV, result.states[:, 0], atol=1e-6 * scale)


@pytest.mark.parametrize("seed", SEEDS[:15])
def test_redistributed_arrangements_stay_in_equilibrium(seed: int) -> None:
    beam, loads = _random_case(seed)
    if not beam.redistributable:
        pytest.skip("nothing to redistribute")
    result = redistribute(solve(beam, {"all": loads})["all"], 0.2)
    force, moment = _load_resultants(loads, beam)
    assert result.reactions[:, 0].sum() == pytest.approx(force, rel=1e-9, abs=1e-8)
    reacted = (result.reactions[:, 0] * beam.nodes).sum() + result.reactions[:, 1].sum()
    assert reacted + moment == pytest.approx(0.0, abs=1e-7 * (1 + abs(moment)))
    layout = result.layout
    middle = (layout.start + layout.end) / 2
    h = 1e-4 * (layout.end - layout.start)
    dM = (result.moment(middle + h) - result.moment(middle - h)) / (2 * h)
    np.testing.assert_allclose(dM, result.shear(middle), atol=1e-6 * (np.abs(result.states[:, 1:3]).max()))


def test_superposition() -> None:
    beam, loads = _random_case(7)
    half = len(loads) // 2
    solution = solve(beam, {"a": loads[:half], "b": loads[half:], "all": loads})
    combined = solution.combine({"a": 1.0, "b": 1.0})
    x, after = stations([combined])
    np.testing.assert_allclose(diagrams([combined], x, after), diagrams([solution["all"]], x, after), atol=1e-9)
    np.testing.assert_allclose(combined.reactions, solution["all"].reactions, atol=1e-9)


def test_a_mirrored_beam_gives_the_mirrored_answer() -> None:
    lengths, stiffness = (4.0, 6.0, 5.0), (1e4, 2e4, 1.5e4)
    beam = Beam(lengths, stiffness, ("fixed", "pin", "pin", "pin"))
    mirror = Beam(lengths[::-1], stiffness[::-1], ("pin", "pin", "pin", "fixed"))
    result = solve(beam, {"P": [Point(1, P, 1.5), Uniform(2, q, 1.0, 4.0)]})["P"]
    mirrored = solve(mirror, {"P": [Point(1, P, 4.5), Uniform(0, q, 1.0, 4.0)]})["P"]
    x = np.linspace(0.3, 14.7, 17)
    np.testing.assert_allclose(result.moment(x), mirrored.moment(15.0 - x), atol=1e-9)
    np.testing.assert_allclose(result.deflection(x), mirrored.deflection(15.0 - x), atol=1e-12)


# --------------------------------------------------------------------------------- the contract


def test_inputs_are_left_untouched_and_the_answer_repeats() -> None:
    beam, loads = _random_case(3)
    copy = list(loads)
    first = solve(beam, {"all": loads})["all"]
    second = solve(beam, {"all": loads})["all"]
    assert loads == copy
    assert np.array_equal(first.states, second.states) and np.array_equal(first.reactions, second.reactions)


def test_the_module_imports_numpy_and_the_standard_library_only() -> None:
    """No mento, pint, pandas or Pyodide: the solver runs anywhere numpy does."""
    with open(analysis.__file__, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    imported = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    }
    imported |= {
        node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imported - {"numpy"} <= set(sys.stdlib_module_names) | {"__future__"}


@pytest.mark.parametrize(
    ("arguments", "field", "match"),
    [
        (((), (), ("pin",)), "lengths", "at least one"),
        (((0.0,), (EI,), ("pin", "pin")), "lengths", "positive length"),
        (((L,), (-1.0,), ("pin", "pin")), "EI", "positive EI"),
        (((L,), (EI,), ("pin",)), "supports", "one support per node"),
        (((L,), (EI,), ("pin", "roller")), "supports", "one support per node"),
        (((L,), (EI,), ("pin", "free")), "supports", "mechanism"),
        (((L, L), (EI, EI), ("free", "pin", "free")), "supports", "mechanism"),
    ],
)
def test_what_a_beam_refuses(arguments: tuple, field: str, match: str) -> None:
    with pytest.raises(AnalysisError, match=match) as raised:
        Beam(*arguments)
    assert raised.value.field == field


@pytest.mark.parametrize(
    ("load", "match"),
    [
        (Uniform(2, q), "no element"),
        (Uniform(0, q, 3.0, 2.0), "lie on its element"),
        (Uniform(0, q, 0.0, L + 1.0), "lie on its element"),
        (Point(0, P, -0.5), "off its element"),
        (Point(0, float("nan"), 1.0), "not a number"),
        ("10 kN", "not a load"),
    ],
)
def test_what_a_load_refuses(load: object, match: str) -> None:
    with pytest.raises(AnalysisError, match=match) as raised:
        solve(_equal_spans(2), {"D": [load]})
    assert raised.value.field == "loads"


def test_a_load_a_hair_off_a_node_stands_at_it() -> None:
    beam = _equal_spans(2)
    hair = solve(beam, {"P": [Point(0, P, L + 1e-12)]})["P"]
    exact = solve(beam, {"P": [Point(0, P, L)]})["P"]
    np.testing.assert_allclose(hair.reactions, exact.reactions, atol=1e-9)


def test_every_combination_of_ends_solves() -> None:
    for start, end in itertools.product(("pin", "fixed"), repeat=2):
        beam = _equal_spans(2, (start, end))
        result = solve(beam, {"D": _uniform(beam)})["D"]
        assert result.reactions[:, 0].sum() == pytest.approx(2 * q * L)
