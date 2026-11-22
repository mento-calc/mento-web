"""The continuous beam solver against PyNite (MIT), on random beams.

PyNite is a development dependency only (``requirements-dev.txt``): it brings scipy, and the site
never loads it. Each random beam (``test_analysis._random_case``: one to six spans, fixed or
pinned ends, overhangs, a stiffness per element, full and partial uniform loads, point forces and
couples) is built in PyNite as a plane beam along X, loaded downwards along −Y, and the two must
agree on the reactions, the nodal displacements and, along every element, the shear, the moment
and the deflection.

PyNite's own sign for a member's moment and shear is read once, off a simply supported beam,
and asserted to be a plain ±1: what is compared is everything else.
"""

from __future__ import annotations

import numpy as np
import pytest
from analysis import Beam, Couple, Point, Uniform, solve
from test_analysis import SEEDS, _random_case

pynite = pytest.importorskip("Pynite")

COMBO = "C"


def _model(beam: Beam, loads: list) -> object:
    """``beam`` in PyNite: nodes along X, every node held out of the plane XY."""
    model = pynite.FEModel3D()
    model.add_material("m", E=1.0, G=1.0, nu=0.3, rho=0.0)
    for index, x in enumerate(beam.nodes):
        model.add_node(f"n{index}", float(x), 0.0, 0.0)
    for e, EI in enumerate(beam.EI):
        model.add_section(f"s{e}", A=1e6 * EI, Iy=EI, Iz=EI, J=EI)
        model.add_member(f"e{e}", f"n{e}", f"n{e + 1}", "m", f"s{e}")
    first_held = beam.held[0]
    for index, kind in enumerate(beam.supports):
        model.def_support(
            f"n{index}",
            support_DX=index == first_held,
            support_DY=kind != "free",
            support_DZ=True,
            support_RX=True,
            support_RY=True,
            support_RZ=kind == "fixed",
        )
    for load in loads:
        name = f"e{load.element}"
        if isinstance(load, Uniform):
            end = beam.lengths[load.element] if load.end is None else load.end
            model.add_member_dist_load(name, "FY", -load.q, -load.q, load.start, end)
        elif isinstance(load, Point):
            model.add_member_pt_load(name, "FY", -load.P, load.at)
        elif isinstance(load, Couple):
            model.add_member_pt_load(name, "MZ", load.C, load.at)
    model.add_load_combo(COMBO, {"Case 1": 1.0})
    model.analyze_linear(check_statics=False)
    return model


def _pynite_signs() -> tuple[float, float]:
    """PyNite's sign for a member's moment ``Mz`` and shear ``Fy`` against the solver's, read on
    a simply supported beam: sagging and ``V = dM/dx`` here."""
    beam = Beam((4.0,), (1.0,), ("pin", "pin"))
    loads = [Uniform(0, 1.0)]
    member = _model(beam, loads).members["e0"]
    ours = solve(beam, {"q": loads})["q"]
    moment = member.moment("Mz", 2.0, COMBO) / ours.moment(2.0)
    shear = member.shear("Fy", 1.0, COMBO) / ours.shear(1.0)
    assert abs(abs(moment) - 1.0) < 1e-9 and abs(abs(shear) - 1.0) < 1e-9
    return float(np.sign(moment)), float(np.sign(shear))


@pytest.fixture(scope="module")
def signs() -> tuple[float, float]:
    return _pynite_signs()


@pytest.mark.parametrize("seed", SEEDS)
def test_the_same_beam_in_pynite(seed: int, signs: tuple[float, float]) -> None:
    beam, loads = _random_case(seed)
    model = _model(beam, loads)
    result = solve(beam, {"all": loads})["all"]
    moment_sign, shear_sign = signs

    reactions = np.array(
        [[model.nodes[f"n{j}"].RxnFY[COMBO], model.nodes[f"n{j}"].RxnMZ[COMBO]] for j in range(beam.nodes.size)]
    )
    scale = np.abs(result.reactions).max()
    np.testing.assert_allclose(result.reactions, reactions, atol=1e-7 * scale)

    displacements = np.array(
        [[model.nodes[f"n{j}"].DY[COMBO], model.nodes[f"n{j}"].RZ[COMBO]] for j in range(beam.nodes.size)]
    )
    np.testing.assert_allclose(result.displacements, displacements, atol=1e-7 * np.abs(displacements).max())

    for e, L in enumerate(beam.lengths):
        member = model.members[f"e{e}"]
        # off the ends and off the loads' own points, where a jump makes "the value at x" ambiguous
        local = np.linspace(0.0, L, 13)[1:-1] + 1e-3 * L
        x = beam.nodes[e] + local
        theirs = np.array(
            [
                [shear_sign * member.shear("Fy", s, COMBO) for s in local],
                [moment_sign * member.moment("Mz", s, COMBO) for s in local],
                [member.deflection("dy", s, COMBO) for s in local],
            ]
        )
        ours = np.array([result.shear(x), result.moment(x), result.deflection(x)])
        jumps = {round(load.at, 9) for load in loads if load.element == e and isinstance(load, (Point, Couple))} | {
            round(value, 9)
            for load in loads
            if load.element == e and isinstance(load, Uniform)
            for value in (load.start, load.end or L)
        }
        clear = np.array([all(abs(s - jump) > 1e-6 * L for jump in jumps) for s in local])
        for row, name in enumerate(("V", "M", "v")):
            size = np.abs(ours[row]).max() + 1e-12
            np.testing.assert_allclose(
                ours[row, clear], theirs[row, clear], atol=1e-6 * size, err_msg=f"seed {seed}, element {e}, {name}"
            )
