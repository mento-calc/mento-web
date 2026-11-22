"""A continuous beam, solved exactly: reactions, shear, moment and deflection.

A straight beam of elements end to end, each with its own ``EI``, on pins and fixed ends, with
an overhang at either end or both. Linear and exact: one Euler–Bernoulli element per piece of
beam, never a mesh. The stiffness method finds the displacements of the nodes, which are exact
for any load because every load enters as its work-equivalent nodal loads (the Hermite cubics);
statics then reads the shear and the moment along each element from its end forces, and the
deflection is the elastic line integrated exactly from them. Between two breakpoints (the ends
of an element, a point load, where a distributed load starts or stops) the load is constant, so
the shear is linear, the moment a parabola and the deflection a quartic, all in closed form.

On top of that: the arrangements of a live load span by span, and the redistribution of the
moments over the supports. Both are statics on solved cases, never a new solve.

No mento, no pint, no Pyodide: floats in any consistent units (SI in the site), numpy and the
standard library, so it runs in the browser and in any interpreter alike.

Signs
-----
- x runs along the beam from its start, the first node, at 0.
- A load ``q`` or ``P`` is positive **downwards**, as gravity; a couple ``C`` is positive
  counterclockwise, drawn with x to the right.
- ``V`` is ``dM/dx``; ``M`` is positive **sagging** (the bottom fibre stretched).
- The deflection ``v`` is positive **upwards**, so a beam that sags has a negative one; the
  rotation is ``dv/dx``, positive counterclockwise.
- A reaction is the support's force on the beam: positive upwards, and its couple positive
  counterclockwise.

Example::

    beam = Beam(lengths=(5.0, 5.0), EI=(1e4, 1e4), supports=("pin", "pin", "pin"))
    solution = solve(beam, {"D": [Uniform(0, q=10.0), Uniform(1, q=10.0)]})
    solution["D"].moment(5.0)  # -31.25 = -q·L²/8 over the middle support
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

PIN, FIXED, FREE = "pin", "fixed", "free"
SUPPORTS = (PIN, FIXED, FREE)

#: Two positions closer than this fraction of the beam's length are the same point.
_SAME = 1e-9
#: An enumeration of 2ⁿ arrangements is refused past this many patterned cases.
MAX_PATTERNED = 12


class AnalysisError(ValueError):
    """An input the analysis cannot take. ``field`` names what is wrong: ``"lengths"``,
    ``"EI"``, ``"supports"``, ``"loads"`` or ``"ratio"``."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


# ---------------------------------------------------------------------------------------- loads


@dataclass(frozen=True)
class Uniform:
    """``q`` per unit length, downwards, on element ``element`` from ``start`` to ``end``
    (measured from the element's first node; ``end=None`` is its last)."""

    element: int
    q: float
    start: float = 0.0
    end: float | None = None


@dataclass(frozen=True)
class Point:
    """A force ``P``, downwards, on element ``element`` at ``at`` from its first node."""

    element: int
    P: float
    at: float


@dataclass(frozen=True)
class Couple:
    """A couple ``C``, counterclockwise, on element ``element`` at ``at`` from its first node."""

    element: int
    C: float
    at: float


Load = Uniform | Point | Couple


# ----------------------------------------------------------------------------------------- beam


@dataclass(frozen=True)
class Beam:
    """The beam: ``lengths`` and ``EI`` of each element in order, and the support at each node,
    ``len(lengths) + 1`` of them: ``"pin"``, ``"fixed"`` or ``"free"``.

    A free node at an end is the tip of an overhang; a free node inside a span joins two
    elements with no support between them (a section that changes mid-span).
    """

    lengths: tuple[float, ...]
    EI: tuple[float, ...]
    supports: tuple[str, ...]
    nodes: np.ndarray = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        lengths = tuple(float(value) for value in self.lengths)
        stiffness = tuple(float(value) for value in self.EI)
        supports = tuple(self.supports)
        if not lengths:
            raise AnalysisError("lengths", "a beam needs at least one element")
        if any(not np.isfinite(value) or value <= 0 for value in lengths):
            raise AnalysisError("lengths", "every element needs a positive length")
        if len(stiffness) != len(lengths) or any(not np.isfinite(value) or value <= 0 for value in stiffness):
            raise AnalysisError("EI", "every element needs a positive EI")
        if len(supports) != len(lengths) + 1 or any(kind not in SUPPORTS for kind in supports):
            raise AnalysisError("supports", f"one support per node, each one of {SUPPORTS}")
        held = [index for index, kind in enumerate(supports) if kind != FREE]
        if FIXED not in supports and len(held) < 2:
            raise AnalysisError("supports", "a mechanism: the beam needs two supports, or a fixed end")
        object.__setattr__(self, "lengths", lengths)
        object.__setattr__(self, "EI", stiffness)
        object.__setattr__(self, "supports", supports)
        object.__setattr__(self, "nodes", np.concatenate([[0.0], np.cumsum(lengths)]))

    @property
    def length(self) -> float:
        return float(self.nodes[-1])

    @property
    def held(self) -> tuple[int, ...]:
        """The nodes with a support."""
        return tuple(index for index, kind in enumerate(self.supports) if kind != FREE)

    @property
    def overhangs(self) -> tuple[int, ...]:
        """The elements past the first support or past the last one."""
        first, last = self.held[0], self.held[-1]
        return tuple(e for e in range(len(self.lengths)) if e < first or e >= last)

    @property
    def spans(self) -> tuple[tuple[int, ...], ...]:
        """The elements between each pair of consecutive supports, in order."""
        held = self.held
        return tuple(tuple(range(a, b)) for a, b in itertools.pairwise(held))

    @property
    def redistributable(self) -> tuple[int, ...]:
        """The supports whose moment can be redistributed: every one with no overhang beside
        it. The moment over the support of an overhang is the overhang's own statics."""
        held, last = self.held, len(self.lengths)
        return tuple(j for j in held if (j > held[0] or j == 0) and (j < held[-1] or j == last))


# ------------------------------------------------------------------------------ the element math


def _stiffness(length: float, EI: float) -> np.ndarray:
    """The element's stiffness on (v₁, θ₁, v₂, θ₂)."""
    L = length
    return (EI / L**3) * np.array(
        [
            [12.0, 6.0 * L, -12.0, 6.0 * L],
            [6.0 * L, 4.0 * L * L, -6.0 * L, 2.0 * L * L],
            [-12.0, -6.0 * L, 12.0, -6.0 * L],
            [6.0 * L, 2.0 * L * L, -6.0 * L, 4.0 * L * L],
        ]
    )


def _shapes(xi: float, L: float) -> tuple[np.ndarray, np.ndarray]:
    """The Hermite cubics at ``xi`` (0 to 1 along the element) and their slopes d/dx: what a
    force and a couple there bring to each nodal load."""
    values = np.array(
        [1 - 3 * xi**2 + 2 * xi**3, L * (xi - 2 * xi**2 + xi**3), 3 * xi**2 - 2 * xi**3, L * (xi**3 - xi**2)]
    )
    slopes = np.array([(6 * xi**2 - 6 * xi) / L, 1 - 4 * xi + 3 * xi**2, (6 * xi - 6 * xi**2) / L, 3 * xi**2 - 2 * xi])
    return values, slopes


def _shape_integrals(xi: float, L: float) -> np.ndarray:
    """∫ of each Hermite cubic from the element's first node to ``xi``, along x."""
    return np.array(
        [
            L * (xi - xi**3 + xi**4 / 2),
            L * L * (xi**2 / 2 - 2 * xi**3 / 3 + xi**4 / 4),
            L * (xi**3 - xi**4 / 2),
            L * L * (xi**4 / 4 - xi**3 / 3),
        ]
    )


# ------------------------------------------------------------------------------------- results


@dataclass(frozen=True)
class _Layout:
    """Where the pieces of the beam are: one row per stretch between two breakpoints, in order
    along the beam. Every case of a solution shares it, so a combination adds arrays."""

    beam: Beam
    start: np.ndarray  # global x where each piece starts
    end: np.ndarray  # and ends
    element: np.ndarray  # the element each piece belongs to
    EI: np.ndarray  # its EI

    def locate(self, x: np.ndarray, after: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """The piece and the distance into it of each section. At a breakpoint, ``after``
        picks the piece that starts there (the value just past a point load or a support),
        and its absence the piece that ends there."""
        tolerance = _SAME * self.beam.length
        x = np.asarray(x, dtype=float)
        after = np.broadcast_to(np.asarray(after, dtype=bool), x.shape)
        edges = np.concatenate([self.start, self.end[-1:]])
        nearest = np.abs(x[..., None] - edges).argmin(axis=-1)
        x = np.where(np.abs(edges[nearest] - x) <= tolerance, edges[nearest], x)
        if np.any(x < -tolerance) or np.any(x > self.beam.length + tolerance):
            raise AnalysisError("loads", "a section off the beam")
        last = self.start.size - 1
        past = np.clip(np.searchsorted(self.start, x, side="right") - 1, 0, last)
        before = np.clip(np.searchsorted(self.end, x, side="left"), 0, last)
        piece = np.where(after, past, before)
        return piece, np.clip(x - self.start[piece], 0.0, self.end[piece] - self.start[piece])


@dataclass(frozen=True)
class Result:
    """One load case or combination, solved: at the start of each piece of the layout, the
    load on it ``p`` (upwards, so ``-q``), and ``V``, ``M``, the rotation and the deflection;
    the reactions at each node (force, couple); the displacements of each node (v, θ).

    A redistributed result (:func:`redistribute`) is no longer elastic: its forces are in
    equilibrium but it has no deflection.
    """

    layout: _Layout = field(repr=False)
    states: np.ndarray = field(repr=False)  # (pieces, 5): p, V, M, θ, v
    reactions: np.ndarray  # (nodes, 2): force upwards, couple counterclockwise
    displacements: np.ndarray  # (nodes, 2): v, θ
    elastic: bool = True

    def _at(self, x: Iterable[float] | float, after: bool | Sequence[bool] = False) -> np.ndarray:
        """V, M, θ and v at each section, shape (sections, 4)."""
        piece, h = self.layout.locate(np.atleast_1d(np.asarray(x, dtype=float)), np.asarray(after, dtype=bool))
        return _evaluate(self.states[piece], h, self.layout.EI[piece])

    def shear(self, x: Iterable[float] | float, after: bool | Sequence[bool] = False) -> np.ndarray | float:
        """``V`` at ``x``: at a jump, just before it unless ``after``."""
        return _scalar(self._at(x, after)[:, 0], x)

    def moment(self, x: Iterable[float] | float, after: bool | Sequence[bool] = False) -> np.ndarray | float:
        """``M`` at ``x``, sagging positive."""
        return _scalar(self._at(x, after)[:, 1], x)

    def deflection(self, x: Iterable[float] | float) -> np.ndarray | float:
        """``v`` at ``x``, upwards positive."""
        self._elastic("deflection")
        return _scalar(self._at(x)[:, 3], x)

    def rotation(self, x: Iterable[float] | float) -> np.ndarray | float:
        """``dv/dx`` at ``x``, counterclockwise positive."""
        self._elastic("rotation")
        return _scalar(self._at(x)[:, 2], x)

    def _elastic(self, what: str) -> None:
        if not self.elastic:
            raise AnalysisError("ratio", f"a redistributed result has no {what}: read it off the elastic one")

    def scaled(self, factor: float) -> Result:
        return combine([(self, factor)])


def _evaluate(states: np.ndarray, h: np.ndarray, EI: np.ndarray) -> np.ndarray:
    """V, M, θ and v at ``h`` into each piece, from the piece's starting state; works on any
    leading shape of ``states`` (results × sections × 5)."""
    p, V, M, theta, v = np.moveaxis(states, -1, 0)
    return np.stack(
        [
            V + p * h,
            M + V * h + p * h**2 / 2,
            theta + (M * h + V * h**2 / 2 + p * h**3 / 6) / EI,
            v + theta * h + (M * h**2 / 2 + V * h**3 / 6 + p * h**4 / 24) / EI,
        ],
        axis=-1,
    )


def _scalar(values: np.ndarray, x: Iterable[float] | float) -> np.ndarray | float:
    return float(values[0]) if np.ndim(x) == 0 else values


def combine(terms: Iterable[tuple[Result, float]]) -> Result:
    """The factored sum of results of one solution: a combination."""
    terms = list(terms)
    if not terms:
        raise AnalysisError("loads", "a combination needs at least one term")
    layout = terms[0][0].layout
    if any(result.layout is not layout for result, _ in terms):
        raise AnalysisError("loads", "results of different solutions do not combine")
    return Result(
        layout=layout,
        states=sum(factor * result.states for result, factor in terms),
        reactions=sum(factor * result.reactions for result, factor in terms),
        displacements=sum(factor * result.displacements for result, factor in terms),
        elastic=all(result.elastic for result, _ in terms),
    )


# --------------------------------------------------------------------------------------- solving


@dataclass(frozen=True)
class Solution:
    """Every load case of a beam, solved once: ``solution["D"]`` is one, and
    ``solution.combine({"D": 1.2, "L": 1.6})`` a combination of them."""

    beam: Beam
    cases: Mapping[str, Result]

    def __getitem__(self, case: str) -> Result:
        return self.cases[case]

    def combine(self, factors: Mapping[str, float]) -> Result:
        unknown = set(factors) - set(self.cases)
        if unknown:
            raise AnalysisError("loads", f"no load case named {sorted(unknown)}")
        return combine((self.cases[case], factor) for case, factor in factors.items())


def _check_loads(beam: Beam, loads: Mapping[str, Sequence[Load]]) -> None:
    count = len(beam.lengths)
    for case, items in loads.items():
        for load in items:
            if not isinstance(load, (Uniform, Point, Couple)):
                raise AnalysisError("loads", f"{case}: not a load: {load!r}")
            if not 0 <= load.element < count:
                raise AnalysisError("loads", f"{case}: no element {load.element}")
            L = beam.lengths[load.element]
            tolerance = _SAME * beam.length
            values = [load.q, load.start, L if load.end is None else load.end] if isinstance(load, Uniform) else []
            values += [load.P, load.at] if isinstance(load, Point) else []
            values += [load.C, load.at] if isinstance(load, Couple) else []
            if not all(np.isfinite(value) for value in values):
                raise AnalysisError("loads", f"{case}: a load that is not a number")
            if isinstance(load, Uniform):
                end = L if load.end is None else load.end
                if load.start < -tolerance or end > L + tolerance or end - load.start <= tolerance:
                    raise AnalysisError("loads", f"{case}: a distributed load must lie on its element")
            elif load.at < -tolerance or load.at > L + tolerance:
                raise AnalysisError("loads", f"{case}: a load off its element")


def _breakpoints(beam: Beam, loads: Mapping[str, Sequence[Load]]) -> list[np.ndarray]:
    """Each element's breakpoints, shared by every case: its two ends and every point where a
    load of any case starts, stops or stands."""
    found: list[list[float]] = [[0.0, L] for L in beam.lengths]
    for items in loads.values():
        for load in items:
            L = beam.lengths[load.element]
            if isinstance(load, Uniform):
                found[load.element] += [load.start, L if load.end is None else load.end]
            else:
                found[load.element].append(load.at)
    breaks = []
    for e, points in enumerate(found):
        L = beam.lengths[e]
        points = np.clip(np.sort(np.asarray(points)), 0.0, L)
        keep = np.concatenate([[True], np.diff(points) > _SAME * beam.length])
        points = points[keep]
        points[-1] = L  # a load a hair from the end stands at it
        breaks.append(points)
    return breaks


def _snap(value: float, points: np.ndarray, tolerance: float) -> int:
    """The index of the breakpoint at ``value``."""
    index = int(np.abs(points - value).argmin())
    if abs(points[index] - value) > tolerance:
        raise AssertionError(f"no breakpoint at {value}")  # _breakpoints put one there
    return index


def solve(beam: Beam, loads: Mapping[str, Sequence[Load]]) -> Solution:
    """Solve every load case of ``beam``. ``loads`` maps a case's name to its loads; a case
    with none is a valid case whose every force is zero."""
    _check_loads(beam, loads)
    count = len(beam.lengths)
    dofs = 2 * (count + 1)
    tolerance = _SAME * beam.length
    names = list(loads)

    K = np.zeros((dofs, dofs))
    for e, (L, EI) in enumerate(zip(beam.lengths, beam.EI)):
        K[2 * e : 2 * e + 4, 2 * e : 2 * e + 4] += _stiffness(L, EI)

    # each element's own equivalent nodal loads, one column per case
    equivalent = np.zeros((count, 4, len(names)))
    for c, case in enumerate(names):
        for load in loads[case]:
            L = beam.lengths[load.element]
            if isinstance(load, Uniform):
                end = L if load.end is None else load.end
                share = _shape_integrals(min(end, L) / L, L) - _shape_integrals(max(load.start, 0.0) / L, L)
                equivalent[load.element, :, c] += -load.q * share
            elif isinstance(load, Point):
                values, _ = _shapes(min(max(load.at, 0.0), L) / L, L)
                equivalent[load.element, :, c] += -load.P * values
            else:
                _, slopes = _shapes(min(max(load.at, 0.0), L) / L, L)
                equivalent[load.element, :, c] += load.C * slopes
    f = np.zeros((dofs, len(names)))
    for e in range(count):
        f[2 * e : 2 * e + 4] += equivalent[e]

    held = []
    for node, kind in enumerate(beam.supports):
        held += [2 * node] if kind == PIN else [2 * node, 2 * node + 1] if kind == FIXED else []
    free = np.setdiff1d(np.arange(dofs), held)
    u = np.zeros((dofs, len(names)))
    if free.size:
        u[free] = np.linalg.solve(K[np.ix_(free, free)], f[free])
    reactions = K @ u - f
    reactions[free] = 0.0

    breaks = _breakpoints(beam, loads)
    start = np.concatenate([beam.nodes[e] + points[:-1] for e, points in enumerate(breaks)])
    end = np.concatenate([beam.nodes[e] + points[1:] for e, points in enumerate(breaks)])
    element = np.concatenate([np.full(points.size - 1, e) for e, points in enumerate(breaks)])
    layout = _Layout(beam=beam, start=start, end=end, element=element, EI=np.asarray(beam.EI)[element])

    results = {}
    for c, case in enumerate(names):
        states = []
        for e, points in enumerate(breaks):
            L, EI = beam.lengths[e], beam.EI[e]
            ends = _stiffness(L, EI) @ u[2 * e : 2 * e + 4, c] - equivalent[e, :, c]
            # what stands at each breakpoint, and the load on each piece
            forces, couples = np.zeros(points.size), np.zeros(points.size)
            intensity = np.zeros(points.size - 1)
            for load in loads[case]:
                if load.element != e:
                    continue
                if isinstance(load, Point):
                    forces[_snap(load.at, points, tolerance)] += -load.P
                elif isinstance(load, Couple):
                    couples[_snap(load.at, points, tolerance)] += load.C
                else:
                    a = _snap(load.start, points, tolerance)
                    b = _snap(L if load.end is None else load.end, points, tolerance)
                    intensity[a:b] += -load.q
            V, M = ends[0], -ends[1]
            theta, v = u[2 * e + 1, c], u[2 * e, c]
            for k in range(points.size - 1):
                V += forces[k]
                M -= couples[k]
                p = intensity[k]
                states.append((p, V, M, theta, v))
                h = points[k + 1] - points[k]
                V, M, theta, v = _evaluate(np.array([p, V, M, theta, v]), h, EI)
        results[case] = Result(
            layout=layout,
            states=np.array(states),
            reactions=reactions[:, c].reshape(-1, 2),
            displacements=u[:, c].reshape(-1, 2),
        )
    return Solution(beam=beam, cases=results)


# ------------------------------------------------------------------------- reading along the beam


def stations(results: Sequence[Result], per_element: int = 24) -> tuple[np.ndarray, np.ndarray]:
    """Where to read a set of results of one solution so that nothing is missed: every
    breakpoint from both sides, every point of zero shear of every result (where its moment
    peaks), and ``per_element`` evenly spaced points on each element to draw with. Returns
    ``(x, after)`` sorted along the beam, ``after`` true for the value just past a jump."""
    layout = results[0].layout
    beam = layout.beam
    x = [layout.start, layout.end]
    after = [np.ones(layout.start.size, bool), np.zeros(layout.end.size, bool)]
    for e, L in enumerate(beam.lengths):
        grid = beam.nodes[e] + np.linspace(0.0, L, per_element + 1)[1:-1]
        x.append(grid)
        after.append(np.zeros(grid.size, bool))
    width = layout.end - layout.start
    for result in results:
        p, V = result.states[:, 0], result.states[:, 1]
        with np.errstate(divide="ignore", invalid="ignore"):
            h = np.where(p != 0, -V / p, -1.0)
        inside = (h > _SAME * beam.length) & (h < width - _SAME * beam.length)
        x.append(layout.start[inside] + h[inside])
        after.append(np.zeros(int(inside.sum()), bool))
    x, after = np.concatenate(x), np.concatenate(after)
    x = np.round(x / (_SAME * beam.length)) * (_SAME * beam.length)  # one row per point and side
    order = np.lexsort((after, x))
    x, after = x[order], after[order]
    keep = np.concatenate([[True], (np.diff(x) > 0) | (np.diff(after.astype(int)) != 0)])
    return x[keep], after[keep]


def diagrams(results: Sequence[Result], x: np.ndarray, after: np.ndarray) -> np.ndarray:
    """V, M, θ and v of each result at each section, shape (results, sections, 4). The last two
    are NaN for a redistributed result."""
    layout = results[0].layout
    piece, h = layout.locate(x, after)
    states = np.stack([result.states for result in results])[:, piece]
    return _evaluate(states, h, layout.EI[piece])


@dataclass(frozen=True)
class Envelope:
    """The extremes of a set of results at each section, and which result gives each."""

    x: np.ndarray
    after: np.ndarray
    V_min: np.ndarray
    V_max: np.ndarray
    M_min: np.ndarray
    M_max: np.ndarray
    governs: dict[str, np.ndarray]  # "V_min", … → the index of the result


def envelope(results: Sequence[Result], x: np.ndarray | None = None, after: np.ndarray | None = None) -> Envelope:
    """The envelope of shear and moment over ``results``, at ``stations(results)`` unless
    sections are given."""
    if x is None:
        x, after = stations(results)
    elif after is None:
        after = np.zeros(np.shape(x), bool)
    values = diagrams(results, x, after)
    V, M = values[..., 0], values[..., 1]
    governs = {"V_min": V.argmin(0), "V_max": V.argmax(0), "M_min": M.argmin(0), "M_max": M.argmax(0)}
    return Envelope(x, after, V.min(0), V.max(0), M.min(0), M.max(0), governs)


# -------------------------------------------------------------------------- arrangements of loads


@dataclass(frozen=True)
class Arrangement:
    """One way of loading the beam: the patterned cases switched on, and the result."""

    on: frozenset[str]
    result: Result


def arrangements(
    solution: Solution, permanent: Mapping[str, float], patterned: Mapping[str, float]
) -> list[Arrangement]:
    """Every arrangement of a combination: the ``permanent`` cases always on, with their
    factors, and each of the ``patterned`` cases on or off — a live load span by span. 2ⁿ of
    them, all explicit, so each can be redistributed on its own."""
    if len(patterned) > MAX_PATTERNED:
        raise AnalysisError("loads", f"more than {MAX_PATTERNED} patterned cases")
    unknown = (set(permanent) | set(patterned)) - set(solution.cases)
    if unknown or not (permanent or patterned):
        raise AnalysisError("loads", f"no load case named {sorted(unknown)}" if unknown else "no load case")
    names = list(patterned)
    # every term present, a switched-off one with no weight: the empty arrangement is a zero
    found = []
    for mask in itertools.product((False, True), repeat=len(names)):
        on = frozenset(case for case, chosen in zip(names, mask) if chosen)
        terms = [(solution[case], factor) for case, factor in permanent.items()]
        terms += [(solution[case], patterned[case] if case in on else 0.0) for case in names]
        found.append(Arrangement(on, combine(terms)))
    return found


# --------------------------------------------------------------------------------- redistribution


def support_moments(result: Result) -> np.ndarray:
    """The moment over each node: from the element before it, from the one after at the
    start of the beam."""
    beam = result.layout.beam
    nodes = beam.nodes
    after = np.zeros(nodes.size, bool)
    after[0] = True
    return result.moment(nodes, after)


def redistribute(result: Result, ratio: float | Mapping[int, float]) -> Result:
    """``result`` with the hogging moment over each redistributable support reduced by
    ``ratio`` (one for all, or by node), and the moments along the spans raised to keep the
    equilibrium: a correction linear between supports, which moves the shear and the reactions
    with it. Only hogging moments are reduced; a support that sags is left as it is."""
    beam = result.layout.beam
    ratios = {j: ratio for j in beam.redistributable} if not isinstance(ratio, Mapping) else dict(ratio)
    for node, value in ratios.items():
        if node not in beam.redistributable:
            raise AnalysisError("ratio", f"node {node} is not a support whose moment can be redistributed")
        if not 0.0 <= value < 1.0:
            raise AnalysisError("ratio", "a redistribution ratio is between 0 and 1")
    elastic = support_moments(result)
    change = np.zeros(beam.nodes.size)
    for node, value in ratios.items():
        change[node] = -value * min(elastic[node], 0.0)
    # inside a span (a free node between two supports) the correction stays a straight line
    held = beam.held
    for a, b in itertools.pairwise(held):
        for node in range(a + 1, b):
            share = (beam.nodes[node] - beam.nodes[a]) / (beam.nodes[b] - beam.nodes[a])
            change[node] = change[a] + share * (change[b] - change[a])

    layout = result.layout
    e = layout.element
    lengths = np.asarray(beam.lengths)
    slope = (change[e + 1] - change[e]) / lengths[e]
    states = result.states.copy()
    states[:, 1] += slope
    states[:, 2] += change[e] + slope * (layout.start - beam.nodes[e])
    states[:, 3:] = np.nan

    per_element = (change[1:] - change[:-1]) / lengths  # ΔV of each element
    before = np.concatenate([[0.0], per_element])
    past = np.concatenate([per_element, [0.0]])
    moment_before = np.concatenate([[0.0], change[1:]])
    moment_past = np.concatenate([change[:-1], [0.0]])
    reactions = result.reactions.copy()
    for node in held:
        reactions[node, 0] += past[node] - before[node]
        if beam.supports[node] == FIXED:
            reactions[node, 1] += moment_before[node] - moment_past[node]
    return Result(layout, states, reactions, np.full_like(result.displacements, np.nan), elastic=False)
