# Validation of the continuous beam solver

The cases `py/analysis.py` is checked against. Each file has one source and a list `CASES`;
each case says in its docstring where its numbers are (edition, section, example and page), how
they were transcribed to the solver's signs, and the arithmetic. The expected value comes from
the source, never from a program. `test_sensitivity.py` runs every case with the solver's
results scaled by 1.02 and by 0.98, and both must fail: no tolerance hides an error that size.

The cases were transcribed from the validation net of mako's frame engine
(`tests/validation/fem`), which read each value off the book and noted the slips it found. The
solver they run against is this site's own.

## Sources

| File | Source | What it covers | Cases |
|---|---|---|---|
| `test_karnovsky.py` | Karnovsky and Lebed, *Advanced Methods of Structural Analysis*, 2nd ed. (2021): §13.6.1, Example 13.5, §9.3.1, Examples 9.2 and 9.3 | Two to four spans: uniform and point loads, influence lines, a joint rotation | 5 |
| `test_mcguire.py` | McGuire, Gallagher and Ziemian, *Matrix Structural Analysis*, 2nd ed. (2000): Examples 4.9, 4.10 and 5.6, Table 5.1 | A fixed end, an overhang, members of different stiffness, a couple at a support, a deflection; fixed-end forces of a point, a couple and a partial load | 4 |

The closed forms of the handbooks (one span on every pair of ends, two and three equal spans,
the three-moment equation for spans of different stiffness, overhangs) and the solver's own
statics (equilibrium, `M′ = V`, the elastic line closing on every node, superposition, the
envelope by two roads) are in `py/test_analysis.py`; the comparison with PyNite, on random
beams, in `py/test_analysis_pynite.py`.

## What no published example covers yet

- **Moment redistribution.** It is checked against its closed form for two equal spans and
  against the solver's own equilibrium after each redistribution (`py/test_analysis.py`). A
  worked example with numbers, one per family of codes (ACI 318 / CIRSOC 201 and EN 1992), is
  still to be transcribed with the book in hand; candidates are the redistribution chapter of
  the PCA *Notes on ACI 318* and Mosley, Bungey and Hulse, *Reinforced Concrete Design to
  Eurocode 2*. Until then no number here comes from one.

Left out on purpose, because the solver does not take what they load: settlements and imposed
rotations, internal hinges, temperature, trapezoidal loads, shear deformation, elastic supports.
