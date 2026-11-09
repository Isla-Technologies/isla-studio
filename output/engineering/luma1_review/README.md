# Luma-1 engineering review — 9 November 2026

Final deliverables are `../../pdf/Luma-1_Engineering_Report.pdf` and
`../../pdf/Luma-1_Manufacturer_Brief.pdf`. These specify a prototype/RFQ basis,
not a production walking certification.

Recommended cut lengths: thigh 242 mm, shin 156 mm, four of each. These are not
joint-center distances. Coilovers: 32 mm usable stroke, front 98.757–66.757 mm
eye-to-eye, rear 118.908–86.908 mm. The CAD pose has 15 mm available extension and
17 mm available compression. Read the PDFs for rates, damping, preload, load
requirements, assumptions and outstanding drawing/physical-test requirements.

## Reproduce

From the repository root, using the existing Python virtual environment:

```powershell
.\.venv\Scripts\python.exe output\engineering\luma1_review\run_review.py screen
.\.venv\Scripts\python.exe output\engineering\luma1_review\run_review.py adverse
.\.venv\Scripts\python.exe output\engineering\luma1_review\run_review.py validate
.\.venv\Scripts\python.exe output\engineering\luma1_review\run_review.py phase
.\.venv\Scripts\python.exe output\engineering\luma1_review\run_review.py final
```

`final` depends on the saved `validation.json` and `phase.json` scenarios. It
reruns all 53 with an extra 2 mm of compression travel relative to the 30 mm
candidate, leaving spring free length and full extension unchanged. The final
hardware stroke is therefore 32 mm. The original builder's single symmetric
stroke parameter cannot describe this geometry without the explicit end-stop
adjustment in the review script.

The report generator uses `reportlab` and saved results:

```powershell
python output\engineering\luma1_review\make_report.py
```

## Evidence

- `macro_original.json`: byte-for-byte copy of the supplied actuator export.
- `source_manifest.json`: source paths, SHA-256 hashes and repository commit.
- `screen.json`: four rod geometries and actuator-model baseline checks.
- `adverse.json`: combined adverse conditions for four rod pairs at 24/30 mm stroke.
- `validation.json`, `phase.json`: the superseded 30 mm candidate; one push had
  only 0.57 mm compression-stop clearance, motivating the 32 mm revision.
- `final.json`: all 53 selected-design trials with the actual 32 mm travel.
- `summary.json`, `trial_summary.csv`: machine-readable aggregate/per-trial results.
- `geometry_selected32.json`: selected geometry and analytical spring/motor checks.
- `prototype_specification.json`: explicit supplier dimensions; **not** an
  importable robot profile or a live hardware configuration.
- `prototype_model.xml`: local nominal MuJoCo model snapshot with explicit
  travel limits; references meshes in this checkout by absolute path. It does
  not embed a walking controller or the full sensitivity test matrix.

The simulation uses the existing walking policy without retraining. No original
robot profile, policy, application source, or physical robot setting was changed.
No supplier message, purchase or production release was made.
