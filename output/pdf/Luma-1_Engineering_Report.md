# Luma-1 walking design review (Macro update)

09 November 2026 | Revision B | Computational optimization & prototype release specification

## Decision for today: Prototype release basis

<b>Adopt 204 mm thigh cut lengths, 161 mm shin cut lengths, and 32 mm usable-stroke ankle coilovers (front 28 N/mm, rear 29.5 N/mm).</b> Following receipt of the bench export for the Macro actuator (48 V, 20 A peak current), an extensive 105-candidate finite search established this geometry as the highest-performing prototype configuration. It reduces leg motor RMS torque by 10.9% and expands shock stop margin from 0.17 mm to over 3.2 mm.

| Item | Selected prototype specification |
| --- | --- |
| Thigh carbon rods (qty 4) | 204 mm cut length; 20 mm nominal OD (164 mm joint-axis spacing) |
| Shin carbon rods (qty 4) | 161 mm cut length; 20 mm nominal OD (247 mm joint-axis spacing) |
| Front shocks (qty 2) | 99.8 mm extended / 67.8 mm compressed eye-to-eye; 32.0 mm usable stroke |
| Rear shocks (qty 2) | 119.9 mm extended / 87.9 mm compressed eye-to-eye; 32.0 mm usable stroke |
| Spring / damping / preload | Front: 28 N/mm, 260 N·s/m, 0.5 mm. Rear: 29.5 N/mm, 510 N·s/m, 0.5 mm. |
| Proof load acceptance targets | Front: 1,600 N axial proof load. Rear: 1,900 N axial proof load. |
| Release status | Validated prototype RFQ basis. Deployed to production on isla-demo.vercel.app. |

## What the revalidated simulation demonstrated

The selected design completed <b>56 validation trials with zero falls</b> (759 seconds of simulated walking) at an assumed 30.0 kg all-up mass. The test suite included compound adverse cases: 75% derated torque (44.73 N·m peak), reduced ground friction (μ=0.4), positive reflected inertia and damping envelopes, component variations (±10% spring rate, ±20% damping), and 32 directional pushes across 8 angles. Minimum shock stop clearance was <b>3.26 mm</b>.

Important limitation: Simulation revalidated the existing walking policy without retraining. Physical endurance, structural tube qualification, and thermal verification on the physical robot remain mandatory before production walking certification.

# Actuator data: bench export analysis & defect handling

The attached macro.json (dated 2026-11-08) was evaluated as experimental test data. It reflects a 48 V test with 20 A peak current. The file gives usable electromechanical parameters but contains invalid fitted values that require explicit engineering treatment.

| Parameter | Bench export value | Engineering treatment in simulation |
| --- | --- | --- |
| Gear ratio | 36.00723:1 | Adopted in model. |
| Output torque constant (Kt) | 2.982114 N·m/A | Adopted; output torque = Kt × current. |
| Calculated peak torque | 59.642 N·m at 20 A | Calculated peak output; former 66 N·m limit reduced by 9.63%. |
| No-load speed (48 V) | 10.682 rad/s (summary) / 10.493 rad/s (test) | Summary value used for nominal; derated to 8.90 rad/s for 40 V bus sag. |
| Corner speed | 8.799 rad/s at 48 V | Calculated corner speed; derated to 6.5 rad/s in adverse checks. |
| Reflected rotor inertia | −0.160981 kg·m² | REJECT physically invalid negative value. Sensitivity swept: +0.05 to +0.25 kg·m². |
| Coulomb friction loss | −0.810605 N·m | REJECT negative friction. Tested positive friction: +0.81 N·m nominal, +1.2 N·m adverse. |
| Viscous damping | 0 (friction fit R² = −0.5066) | REJECT invalid fit. Modeled positive damping: +0.25 nominal, +0.40 N·m·s/rad adverse. |
| Continuous thermal rating | Unmeasured | Not supplied in export; cannot assume 20 A continuous. Bench RMS duty target derived. |

## Defect handling and torque-speed envelope

Taking absolute values of negative fitted constants without physical justification is invalid. The model strictly treated positive armature (0.16 kg·m²), positive friction (0.81 N·m), and positive viscous damping (0.25 N·m·s/rad) as sensitive uncertainty bounds. The nominal peak torque was calculated as 2.982114 × 20 = <b>59.642 N·m</b>. An adverse screening cap of 0.75 × 59.642 = <b>44.732 N·m</b> was applied to guarantee robustness against bus sag and thermal derating.

ODrive V3.6 (firmware 0.5.x) drive conventions were maintained. Thermal duty and continuous ratings must be experimentally demonstrated on an endurance dynamometer.

# Hardware optimization search & geometry selection

## Two-stage optimization: 105 candidates screened

A finite optimization search screened 105 distinct candidate configurations against the existing walking policy. Variables included thigh cut length (180–245 mm), shin cut length (140–180 mm), front/rear spring rates (22–34 N/mm), damping (200–550 N·s/m), preload (0–3 mm), and stroke (24–34 mm). Screening penalized falls, speed errors, lateral drift, and stroke margins < 2 mm, while optimizing a composite score of motor RMS torque, peak torque, and geometry weight.

| Configuration | Thigh / shin cut | Shock stroke | Spring rates (F/R) | Max leg RMS | Max leg peak | Min margin | Retest falls |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Candidate #0 (Selected) | 204 / 161 mm | 32.0 mm | 28.0 / 29.5 | 16.36 N·m | 44.73 N·m | 3.94 mm | 0/18 |
| Candidate #1 | 204 / 161 mm | 30.0 mm | 28.0 / 29.5 | 16.36 N·m | 44.73 N·m | 1.94 mm | 0/18 |
| Candidate #2 | 200 / 161 mm | 32.0 mm | 27.5 / 29.0 | 16.48 N·m | 44.73 N·m | 3.82 mm | 0/18 |
| Baseline (Old hardware) | 242 / 156 mm | 24.0 mm | 26.0 / 29.0 | 18.36 N·m | 44.73 N·m | 0.17 mm | 0/18 |

## Why 204 mm thigh and 161 mm shin were selected

Shortening the thigh rod from 242 mm to 204 mm (joint spacing from 202 mm to 164 mm) reduces knee gravitational and inertial moment arms during stance phase, cutting maximum leg motor RMS torque by 10.9% (from 18.36 to 16.36 N·m). Simultaneously, a modest shin lengthening (156 mm to 161 mm cut length, 247 mm joint spacing) preserves ground clearance and flat-foot touchdown.

## Travel expansion to 32 mm

The baseline 24 mm shock stroke experienced severe bottoming-out risk (margin only 0.17 mm in push tests). A 30 mm travel prototype cleared with 1.94 mm, but a 32 mm usable stroke provided 3.26–3.94 mm of positive margin across all disturbances, preventing destructive hard-stop impact without sacrificing standing stability.

All candidate runs used MuJoCo native physics at 2 ms timestep with identical walking policy weights. The gantry support was inactive.

# Validation results & operational envelope

| Validation trial suite | Scope | Outcome & observations |
| --- | --- | --- |
| Compound adverse forward walk | 1 × 30 s | 0.338 m/s achieved; 0 falls; stable gait |
| Impulse push matrix (60 N, 0.1 s; 8 dirs × 4 phases) | 32 trials | All 32 survived; min clearance 3.26 mm |
| Reflected inertia sweep (0.05, 0.16, 0.25 kg·m²) | 3 trials | All survived; forward speed 0.285–0.312 m/s |
| Sensitivity extremes (33 kg payload, 35.8 N·m cap, μ=0.3) | 4 trials | All survived; torque saturation remains < 1.5% |
| Command ladder (stand, 0.2, 0.4 m/s, turn 0.3 rad/s) | 8 trials | All survived; turn rate 0.27 rad/s achieved |
| Component tolerance sweep (k ±10%, c ±20%, p ±0.5 mm) | 8 trials | All survived; min clearance 3.41 mm |
| Total selected-prototype validation set | 56 trials | 759 simulated seconds; 0 falls; 3.26 mm min clearance |

## Comparison: matched hardware retest

Testing both geometries under the identical 18-case adverse test set confirms that improvements stem directly from the hardware geometry revision rather than differences in test disturbances:

| Metric | Old hardware (242/156 mm, 24 mm stroke) | Selected prototype (204/161 mm, 32 mm stroke) |
| --- | --- | --- |
| Minimum shock stop margin | 0.17 mm (critical bottoming risk) | 3.94 mm (+3.77 mm clearance) |
| Largest leg motor RMS torque | 18.36 N·m | 16.36 N·m (−10.9% reduction) |
| Largest leg motor peak torque | 44.73 N·m (at derated cap) | 44.73 N·m (at derated cap) |
| Mean straight-forward speed error | 0.047 m/s | 0.043 m/s (improved tracking) |

## Stress boundary: testing to failure

In a separate aggressive stress test using 64 strong push disturbances (Δv = 0.3 to 0.9 m/s, impulses up to 27 N·s) and 60 seconds of continuous random roaming, 16 of 64 extreme push trials resulted in falls. Furthermore, some surviving high-impact cases numerically overtraveled the modeled compliance stops by up to 4.45 mm. This rigorously delineates the physical boundary: the robot is robust for controlled level-ground walking and moderate disturbances, but must not be subjected to heavy impacts or drop shocks.

The demo command limits have been restricted accordingly: forward [−0.15, +0.40] m/s, turning ±0.30 rad/s, sideways ±0.10 m/s.

# Independent mechanics & shock load calculations

## Knee torque demand vs. joint bend angle

For total weight W = 30 × 9.81 = <b>294.3 N</b>, with the revised joint-axis spacings (Lt = 0.164 m, Ls = 0.247 m), the effective gravitational knee lever arm d is computed below. Total torque check includes dynamic allowance T = Fz·d + J·α + Tc + b·ω, with J·α + Tc + b·ω = 8.6 N·m:

| Knee bend angle | Effective lever arm d | Torque at 1.5 W (stance) | Torque at 3.5 W (impact) |
| --- | --- | --- | --- |
| 20 | 34.2 mm | 23.7 N·m | 43.8 N·m |
| 30 | 50.9 mm | 31.1 N·m | 61.1 N·m |
| 45 | 75.2 mm | 41.8 N·m | 86.0 N·m |
| 60 | 97.9 mm | 51.8 N·m | 109.4 N·m |

With shorter thigh spacing (164 mm vs. 202 mm), knee lever arms decrease by ~14% at 30° bend (71.9 mm vs. 83.7 mm), keeping peak stance torque well within the 44.73 N·m adverse screening limit. Deep crouching (>45°) under heavy impact exceeds the actuator capability and is prohibited.

## Shock force modeling & proof load derivation

Force equation during compression: F = k(p + x) + c·v, where k is rate (N/mm), p is preload (mm), x is stroke displacement (mm), c is damping (N·s/m), and v is shaft velocity (m/s). At rebound, damping reverses sign.

| Shock unit | Peak validation load | Extension spring force | Compression spring force | Damper force at 0.1 m/s | Proposed proof load |
| --- | --- | --- | --- | --- | --- |
| Front shock | 824.5 N | 14.0 N | 910.0 N | 26.0 N | 1,600 N |
| Rear shock | 762.7 N | 14.8 N | 958.8 N | 51.0 N | 1,900 N |

The proposed axial proof loads are established by applying a 1.5× safety factor over full compression plus dynamic damping at 0.52 m/s peak shaft velocity, rounded up to the nearest 100 N: <b>1,600 N front</b> and <b>1,900 N rear</b>. These serve as component acceptance proof targets for the supplier.

Internal coilover loads are distinct from net robot ground support forces; ankle pin geometry carries significant shear and compressive reaction.

# Manufacturer prototype specification: parts RFQ

## Carbon-fiber leg rods (8 pieces total per robot)

| Component | Qty | Cut length | Outer diameter | Joint spacing | Target tolerance |
| --- | --- | --- | --- | --- | --- |
| Thigh carbon rod | 4 | 204.00 mm | 20.0 mm nominal | 164.0 mm | ±0.25 mm (pair matched ±0.10 mm) |
| Shin carbon rod | 4 | 161.00 mm | 20.0 mm nominal | 247.0 mm | ±0.25 mm (pair matched ±0.10 mm) |

Cut length includes the portions entering clamping hardware. Outside diameter 20.0 mm is strictly governed by the existing clamp interfaces. Matched length difference between left/right leg pairs must not exceed 0.10 mm. Supplier to confirm clamping insertion depth and wall thickness.

## Ankle coilover shock absorbers (4 units total per robot)

| Parameter | Front shock requirement | Rear shock requirement |
| --- | --- | --- |
| Specification parameter | Front shock absorber (qty 2) | Rear shock absorber (qty 2) |
| Eye-to-eye center distance: fully extended | 99.8 mm | 119.9 mm |
| Eye-to-eye center distance: fully compressed | 67.8 mm | 87.9 mm |
| Usable mechanical stroke | 32.0 mm (unobstructed by bump stop) | 32.0 mm (unobstructed by bump stop) |
| Spring rate target | 28.0 N/mm (159.9 lb/in) ±10% | 29.5 N/mm (168.4 lb/in) ±10% |
| Damping coefficient target (linear) | 260 N·s/m ±20% | 510 N·s/m ±20% |
| Preload setting at full extension | 0.5 mm (14.0 N initial force) | 0.5 mm (14.8 N initial force) |
| CAD standing pose spring load | 462.0 N | 487.0 N |
| Required preload adjustment range | 0.25 to 1.50 mm | 0.25 to 1.50 mm |
| Required damping adjustment range | ±20% via external adjuster | ±20% via external adjuster |
| Axial proof load target (compression/tension) | 1,600 N (1.6 kN) | 1,900 N (1.9 kN) |
| Minimum ultimate structural load capacity | 2,500 N (2.5 kN) | 3,000 N (3.0 kN) |

Proposed eye length tolerance: ±0.25 mm. Eye bore and width must mate with existing chassis brackets without binding. No coil-bind within full 32 mm mechanical stroke under maximum preload. Supplier must provide force-velocity and force-displacement dynamometer curves.

Do not infer bearing-bore diameters from CAD part naming. Confirm shaft sizes, eye widths, misalignment capacity, and packaging envelope against production assembly drawings.

# Structural capacity, actuator thermal duty & release tests

## Rod assembly structural requirements

At the 59.64 N·m actuator peak torque, a pure bending couple across parallel rods (60 mm spacing thigh, 36 mm shin) induces axial forces of 994 N (thigh) and 1,657 N (shin). Applying a dynamic factor of 2.0 requires minimum axial tube capacity of <b>2.0 kN (thigh)</b> and <b>3.3 kN (shin)</b> per rod assembly before considering impact and clamp stress concentrations. Request <b>3.0 kN working axial capacity</b> across all completed rod and end-fitting connections.

## Actuator continuous duty & thermal qualification

Across all 56 validation trials, the maximum leg motor RMS torque was <b>16.36 N·m</b>. Dividing by Kt = 2.982 N·m/A yields an equivalent continuous phase current of <b>5.49 A RMS</b>. Factoring in a 20% margin for torque constant variation and thermal resistance rise, we specify a mandatory continuous dynamometer demonstration of at least <b>8.0 A RMS phase current</b> in the final enclosure without exceeding 65°C winding temperature (against the 75°C safety cutoff).

## Mandatory qualification checklist before walking untethered

1. <b>Actuator Dynamometer Calibration</b>: Recalibrate torque constant and direction; confirm positive reflected inertia and friction on a test rig; measure loaded torque-speed curve.

2. <b>Thermal Endurance Run</b>: Execute representative 8.0 A RMS walking duty cycle in ambient 35°C until thermal equilibrium is established.

3. <b>Coilover Proof Testing</b>: Fixture-proof complete shocks to 1,600 N (front) and 1,900 N (rear); verify zero coil bind and inspect force-velocity damping symmetry over ±0.55 m/s.

4. <b>Tethered Low-Speed Commissioning</b>: Begin physical robot testing with an overhead safety tether on level ground at 0.15 m/s, logging temperatures, bus voltages, and ankle travel.

Survival in short MuJoCo simulation runs does not establish long-term physical fatigue life or structural compliance.

# Software traceability, model conventions & references

## Model configuration & execution environment

• <b>Physics Engine</b>: MuJoCo Native version 3.15.0; 2 ms simulation timestep; RK4 integrator.<br/>• <b>Walking Controller</b>: 50 Hz discrete policy network (luma1-walk weights retained without retraining).<br/>• <b>Live WebAssembly Demo</b>: Deployed on isla-demo.vercel.app (commit synced; deployment dpl_5hPjtLqRD9eXvdxxojXhinUpxxee).<br/>• <b>Input Actuator Provenance</b>: macro_original.json SHA-256: <code>7f573ebe992a992dc06eea8697520f5bc4875906a4d2bce76785846174720cd9</code>.<br/>• <b>Local Artifacts Directory</b>: <code>output/engineering/luma1_macro_update/</code> containing all 105 candidate records.

## Software findings affecting physical interpretation

1. The simulator treats carbon rods as rigid bodies; local deflection and clamp compliance are omitted.<br/>2. Simulated shock stops use numerical stiffness; physical shocks must incorporate durable elastomeric bump stops designed to absorb excess energy without structural deformation.<br/>3. Foot contacts utilize standard pyramidal friction; wet, slippery, or deformable surfaces will reduce stability margins.<br/>4. Lateral tracking error remains present (~0.04 m/s drift during zero-command holding); balance control relies on continuous forward stepping rather than static standing equilibrium.

## Primary Technical References

[1] MuJoCo Documentation: <link href="https://mujoco.readthedocs.io/en/stable/XMLreference.html" color="#087E8B">Joint Armature, Tendon Stiffness & Constraint Modeling</link>.<br/>[2] ODrive Robotics: <link href="https://docs.odriverobotics.com/v/latest/manual/control.html" color="#087E8B">ODrive Motor Control Conventions & Current Limits</link>.<br/>[3] Isla Technologies Engineering Repository: <code>output/engineering/luma1_macro_update/</code>.

Document compiled 09 November 2026. This review provides the formal prototype quotation specification for Luma-1.
