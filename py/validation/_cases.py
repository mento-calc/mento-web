"""What every validation case shares: the case, the solve, and the injected error.

A case is a computation against a source: what the solver gives, in the source's units, what
the source prints, and the tolerance the source allows. It solves through :func:`solve` here,
never ``analysis.solve`` directly, so that :func:`scaled_results` can multiply everything the
solver hands out by a factor: the sensitivity test runs each case under ±2 % and requires it to
fail, so no tolerance can be loose enough to hide an error of that size. Scaling the stiffness
instead would not do: under loads it moves no reaction and no internal force.

The idea, and the cases, come from the validation net of the frame engine of mako (its
``tests/validation/fem``); the solver they run against is this site's own.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import analysis
import numpy as np

#: What the injection multiplies every result by; 1 outside :func:`scaled_results`.
_factor = 1.0


@dataclass(frozen=True)
class ValidationCase:
    """One computation against one source.

    Attributes:
        name: Unique in the folder.
        source: The document, its edition, and the example, page or table.
        run: What the solver gives, in the source's units and signs: one number per expected value.
        expected: What the source prints, in its order.
        rel: The relative tolerance the source allows: its rounding, 1e-6 for a closed form.
            Under 1 %, so that the case fails with the result off by 2 % on either side.
        abs: The absolute tolerance, for the values the source gives as zero; 0 when none.
    """

    name: str
    source: str
    run: Callable[[], Sequence[float]]
    expected: tuple[float, ...]
    rel: float
    abs: float = 0.0

    def check(self) -> None:
        np.testing.assert_allclose(
            np.asarray(self.run(), dtype=float),
            np.asarray(self.expected, dtype=float),
            rtol=self.rel,
            atol=self.abs,
            err_msg=f"{self.name} ({self.source})",
        )

    def passes(self) -> bool:
        actual = np.asarray(self.run(), dtype=float)
        return bool(np.allclose(actual, np.asarray(self.expected, dtype=float), rtol=self.rel, atol=self.abs))


def solve(beam: analysis.Beam, loads: Mapping[str, Sequence[Any]]) -> analysis.Solution:
    """``analysis.solve``, with every result times the injected factor."""
    solution = analysis.solve(beam, loads)
    if _factor == 1.0:
        return solution
    return analysis.Solution(solution.beam, {case: result.scaled(_factor) for case, result in solution.cases.items()})


@contextlib.contextmanager
def scaled_results(factor: float) -> Iterator[None]:
    """Everything :func:`solve` hands out, times ``factor``, while inside."""
    global _factor
    before = _factor
    _factor = factor
    try:
        yield
    finally:
        _factor = before
