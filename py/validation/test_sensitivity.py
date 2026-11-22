"""No validation case survives a ±2 % error in the solver's results.

Every case of this folder runs with what the solver hands out scaled by 1.02 and by 0.98 —
displacements, reactions and the forces along the beam — and each must fail, so no tolerance can
hide an error of that size. It is the result that is scaled, not the stiffness: under loads a
stiffness 2 % off moves no reaction and no internal force.

A case joins by being in the ``CASES`` of its module; every ``test_*.py`` of the folder has one.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from ._cases import ValidationCase, scaled_results

MODULES = sorted(path.stem for path in Path(__file__).parent.glob("test_*.py") if path.stem != "test_sensitivity")
ALL_CASES: list[ValidationCase] = [
    case for module in MODULES for case in importlib.import_module(f"{__package__}.{module}").CASES
]


def test_every_case_has_its_own_name() -> None:
    names = [case.name for case in ALL_CASES]
    assert len(set(names)) == len(names) > 0


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda case: case.name)
def test_no_tolerance_reaches_one_percent(case: ValidationCase) -> None:
    assert 0.0 < case.rel < 0.01, f"{case.name}: a tolerance of {case.rel:g} can let a 2 % error through"


@pytest.mark.parametrize("factor", [1.02, 0.98], ids=["+2%", "-2%"])
@pytest.mark.parametrize("case", ALL_CASES, ids=lambda case: case.name)
def test_a_two_percent_error_fails_the_case(case: ValidationCase, factor: float) -> None:
    with scaled_results(factor):
        assert not case.passes(), f"{case.name} still passes with the solver's results scaled by {factor}"
