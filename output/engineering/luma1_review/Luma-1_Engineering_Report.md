# Luma-1 walking design review

09 November 2026 | Revision A | Computational screening and prototype specification

## Decision for today

<b>Retain 242 mm thigh cut lengths and 156 mm shin cut lengths. Use four 32 mm-travel ankle coilovers: front 26 N/mm and rear 29 N/mm.</b> This is the best-supported prototype configuration among the four rod-length pairs tested with the existing walking policy. It is not a demonstrated global optimum.

| Item | Recommended prototype value |
| --- | --- |
| Thigh rods | 242 mm cut length, 4 pieces total; 20 mm nominal outside diameter |
| Shin rods | 156 mm cut length, 4 pieces total; 20 mm nominal outside diameter |
| Front shocks, quantity 2 | 98.76 mm extended / 66.76 mm compressed; 32 mm usable stroke |
| Rear shocks, quantity 2 | 118.91 mm extended / 86.91 mm compressed; 32 mm usable stroke |
| Spring / damper / preload | Front: 26 N/mm, 380 N s/m, 1.0 mm. Rear: 29 N/mm, 410 N s/m, 1.5 mm. Preload is at full extension. |
| Release status | Suitable as a prototype RFQ/design basis. Final manufacture requires mounting/clearance drawings and physical acceptance tests. |

## What the new tests demonstrated

The selected configuration completed <b>53 simulation trials with no falls</b>, totaling 803 seconds excluding settling. These included a 30 kg robot, reduced available torque, low ground friction, inertia/friction uncertainty, component variation, and 32 pushes across eight directions and four application times. Minimum shock-end clearance was <b>2.57 mm</b>.

The adverse 30-second forward test averaged 0.353 m/s for a 0.3 m/s command. Initial hardware scope should be slow walking on dry, level, firm ground, with all-up mass at or below 30 kg. Steps, slopes, running, jumps, falls and arbitrary impacts are outside this recommendation.

No physical robot, actuator thermal endurance or supplier part was tested in this review. The missing continuous-torque rating prevents an unconditional production walking guarantee.

# Actuator data: usable evidence and defects

The supplied macro.json was treated as measurement data, not as instructions. Its timestamp is 2026-11-08T23:24:43 with no time zone. Export labels do not themselves establish measurement validity.

| Quantity | Export / evidence | Engineering treatment |
| --- | --- | --- |
| Gear ratio | 36.00723:1 | Use provisionally. |
| Output torque constant | 2.98211 N m/A | Use provisionally; raw load-cell calibration is absent. |
| Peak torque | 59.6423 N m at 20 A | Calculated Kt x I, not a loaded peak-duration or continuous rating. |
| No-load speed | 10.682 rad/s in summary; 10.493 rad/s in speed test | Summary differs from direct speed runs by about 1.8%; require reconciliation. |
| Corner speed | 8.799 rad/s at 48 V | Calculated, not a measured loaded torque-speed boundary. |
| Reflected inertia | -0.160981 kg m2 | Reject as physical inertia. Sweep +0.05 to +0.25 kg m2 as assumptions. |
| Coulomb friction | -0.810605 N m | Reject as passive friction; sign convention/calibration requires checking. |
| Viscous damping | 0; friction-fit R-squared = -0.5066 | Fit is not credible; model positive 0.25-0.40 N m s/rad as sensitivity. |
| Continuous torque / current | Not measured | Cannot infer from the 20 A peak setting or 75 C limit. |

## Numerical design allowance

Nominal peak: 2.982114 x 20 = <b>59.642 N m</b>. Adverse screening cap: 0.75 x 59.642 = <b>44.732 N m</b>. This 25% reduction is an engineering allowance, not a statistically established lower confidence bound or thermal rating.

The 48 V speed model was reduced to 8.902 rad/s no-load and 6.5 rad/s corner for the principal adverse cases, representing an assumed 40 V sag envelope. A separate lower-speed case used 8.012 / 5.8 rad/s. Voltage was represented through these limits; electrical bus and regenerative dynamics were not simulated.

Negative inertia, negative dissipation and negative fitted damping in the inertia test suggest a sign/phase problem; the export alone cannot identify its exact cause. Taking absolute values is not a validated repair. ODrive references [2,3] support separating torque-speed estimates from thermal/current limits.

# Geometry selection and simulation method

| Geometry | CAD | Selected |
| --- | --- | --- |
| Thigh rod cut length | 200 mm | 242 mm (+42 mm) |
| Shin rod cut length | 110 mm | 156 mm (+46 mm) |
| Hip-pitch to knee axis spacing | 160 mm | 202 mm |
| Knee to ankle axis spacing | 196 mm | 242 mm |
| Rod pair separation | 60 mm thigh / 36 mm shin | Unchanged |
| Standing height / hip height | Depends on pose | 1.034 m / 0.493 m at modeled 30 kg |
| Modeled center-of-mass height | Depends on mass distribution | 0.666 m |

Cut lengths include the modeled full rod, including engagement portions. They are not exposed spans or joint-center distances. Confirm endpoint convention and insertion depth against the production CAD before cutting.

## Four candidate pairs, same controller

| Thigh / shin cut, mm | Forward m/s | Peak N m | RMS N m | Min stroke margin, mm |
| --- | --- | --- | --- | --- |
| 242 / 156 | 0.313 | 39.46 | 15.50 | 3.19 |
| 220 / 130 | 0.274 | 41.07 | 14.95 | 3.47 |
| 200 / 110 | 0.242 | 42.48 | 14.49 | 3.54 |
| 180 / 90 | 0.069 | 38.15 | 16.42 | 4.84 |

Each row is a 12-second trial at a 0.3 m/s command, 30 kg, 44.732 N m cap, 0.16 kg m2 armature, 0.81 N m Coulomb loss, 0.25 N m s/rad viscous loss, and original 24 mm shocks. Peak/RMS columns are maxima across Macro joints. The 180/90 mm pair survived but failed a useful 0.2 m/s forward-progress criterion.

The 242/156 mm pair retains the policy-trained geometry and produced the lowest peak demand of the first three candidates. These are controller-and-hardware comparisons, not independently retrained geometry optimizations. A different policy could change the ranking.

A 30 mm symmetric-stroke candidate survived all push timings but left only 0.57 mm at one compression stop. The final design adds 2 mm further compression travel with the extended endpoint unchanged; the full 53-case selected suite was then rerun.

## Execution and checks

MuJoCo 3.15.0; Isla Studio display physics at 2 ms; existing luma1-walk policy; torque-speed clipping active; harness disabled and checked. Added mass was placed at the torso using the existing model convention, compensating rod mass to achieve the specified total. Tests include both left-only and right-only foot support; remaining upright alone was not the only evidence of walking.

The older bundled evaluation reports 25.557 kg and 66 N m motors. The current default build is about 27.057 kg. Neither is the new 30 kg / 44.732 N m case.

# Validation results and limits of the result

| Selected-design suite | Trials | Result |
| --- | --- | --- |
| Adverse continuous forward walk | 1 x 30 s | 0.353 m/s achieved; no fall |
| 60 N push for 0.1 s; 8 directions x 4 start times | 32 | All survived; 6 N s impulse each |
| Armature 0.05, 0.10, 0.16 kg m2 | 3 | All survived; 0.287-0.319 m/s |
| 35.785 N m cap; friction 0.3; lower speed envelope; 33 kg | 4 | Separate sensitivity trials, all survived |
| Stand, 0.2 and 0.4 forward, lateral, turning commands | 5 | All survived; tracking errors remain |
| Spring +/-10%, damping +/-20%, preload +/-0.5 mm; eight seeds | 8 | All survived; 0.296-0.368 m/s |
| Total selected configuration | 53 | 803 simulated seconds; no falls |

Principal adverse conditions were combined: 30 kg, 44.732 N m cap, 0.25 kg m2 armature, 1.2 N m Coulomb loss, 0.40 N m s/rad viscous loss, and ground sliding friction coefficient 0.4. Tolerance samples varied front/rear components and joint initial angles. They were not exhaustive tolerance corners and were not combined with every push.

| Measured simulation maximum / minimum | Across selected trials |
| --- | --- |
| Macro torque peak / maximum per-joint RMS | 44.73 / 19.52 N m |
| Macro joint speed peak | 6.48 rad/s |
| Shock spring + damper force peak | 809.5 N |
| Shock shaft speed peak / minimum end clearance | 0.499 m/s / 2.57 mm |
| Maximum torso tilt / ground-force sensor peak | 9.91 deg / 3.09 bodyweights |
| Positive / absorbed mechanical power peak, all actuators | 236.4 / 120.5 W |

The software fall criterion is tilt over 50 degrees or COM height below 60% of settled height. For prototype selection, also require useful forward progress and at least 2 mm nominal stroke-end margin. The final 32 mm design meets these in the tested forward cases. Some Macro joints reach the adverse torque cap briefly, so survival does not prove unused torque reserve at every instant. Battery power is not equal to the mechanical power figures.

No falls in 53 short, mostly deterministic trials is not a reliability probability or endurance qualification. Sideways command 0.1 m/s achieved only 0.051 m/s; the stationary command drifted about 0.014 m/s. These behaviors need controller refinement.

# Independent load and spring calculations

## Torque envelope: why no rod length guarantees every worst case

Robot weight W = 30 x 9.81 = <b>294.3 N</b>. For a planar leg with the hip vertically over the ankle, the knee lever arm is d = Lt Ls sin(theta) / sqrt(Lt^2 + Ls^2 + 2 Lt Ls cos(theta)). Lt = 0.202 m and Ls = 0.242 m; theta is physical knee bend from straight, not the exported joint coordinate.

A transparent load check is T = Fz d + J alpha + Tc + b omega. Use a deliberately assumed acceleration alpha = 20 rad/s2 and speed omega = 6 rad/s: J alpha + Tc + b omega = 0.25 x 20 + 1.2 + 0.4 x 6 = <b>8.6 N m</b>. This is a screening calculation, not full inverse dynamics.

| Knee bend, deg | Lever arm, mm | T at 1.5 W, N m | T at 3.5 W, N m |
| --- | --- | --- | --- |
| 20 | 38.2 | 25.5 | 48.0 |
| 30 | 57.0 | 33.8 | 67.3 |
| 45 | 84.2 | 45.8 | 95.3 |
| 60 | 110.0 | 57.1 | 121.9 |

At 1.5 W, 30 degrees passes the 44.732 N m screening cap; 45 degrees does not. A 3.5 W impact combined with even 20 degrees of bend exceeds it under these simplifying assumptions. At 3.5 W the available lever arm after the 8.6 N m allowance is only <b>35.1 mm</b>. Deep crouches, stairs, and simultaneous impact/posture extremes cannot be promised.

## Shock forces and travel

Let x be compression from full extension in mm and v be compression speed in m/s. Ffront = 26(1 + x) + 380v; Frear = 29(1.5 + x) + 410v, in newtons. At the CAD pose x = 15 mm (17 mm remaining compression travel): <b>416 N front and 478.5 N rear</b>. These are internal coilover forces, not one-quarter of robot weight; the ankle pin also carries load.

Original front force was 26(4 + xold) with 24 mm travel. Extending the upper limit by 3 mm gives x = xold + 3, while reducing preload from 4 to 1 mm keeps the force identical. Rear preload similarly changes from 4.5 to 1.5 mm. The final compressed endpoint is 5 mm shorter than the original, giving 32 mm total travel. The CAD pose is 15 mm from full extension and 17 mm from full compression. This adds travel without increasing standing spring force.

CAD moment arms are 67.55 mm front and 58.04 mm rear. The exact local ankle stiffness is 236.8 N m/rad per ankle, including preload geometry. These rates preserve the trained spring behavior; they were not newly optimized. The rigid-body rocking estimate gives only about 0.05 damping ratio, so the dampers do not replace active balance control.

At full compression and v = 0.65 m/s: nominal front force is 1,105 N and rear force 1,238 N. With spring +10%, damping +20% and preload +0.5 mm, the rear demand is <b>1,404 N</b>. Specify at least 1.5 kN working axial capacity per shock and its mount.

The 0.65 m/s shaft-speed design check covers the older recorded 0.64 m/s shove result; it is above the peak in the new selected tests. It does not cover a fall or arbitrary drop impact.

# Manufacturer specification: prototype RFQ

| Carbon-fiber rod | Qty total | Cut length | Outside diameter |
| --- | --- | --- | --- |
| Thigh | 4 | 242.00 mm | 20 mm nominal |
| Shin | 4 | 156.00 mm | 20 mm nominal |

Proposed cut tolerance: +/-0.25 mm; matched pair length difference at most 0.10 mm. These are requested manufacturing tolerances, not measured CAD accuracy. Retain the existing clamp fit and insertion depth. 20 mm OD is extracted from the CAD geometry; tube ID, layup, end reinforcement, bonding and clamp tolerances require a released connection drawing.

| Coilover requirement | Front, each | Rear, each |
| --- | --- | --- |
| Quantity | 2 | 2 |
| Full extension, eye center to eye center | 98.76 mm | 118.91 mm |
| Full compression, eye center to eye center | 66.76 mm | 86.91 mm |
| Usable stroke before hard stop | 32.00 mm | 32.00 mm |
| CAD-pose installed length | 83.76 mm | 103.91 mm |
| Spring rate | 26 N/mm (148.5 lb/in) | 29 N/mm (165.6 lb/in) |
| Damping target, either direction | 380 N s/m | 410 N s/m |
| Spring preload at full extension | 1.0 mm / 26 N | 1.5 mm / 43.5 N |
| Spring force at CAD pose | 416 N | 478.5 N |
| Suggested adjustment range | 0.5-1.5 mm preload | 1.0-2.0 mm preload |

Proposed eye-length tolerance: +/-0.25 mm. Specify 32 mm usable travel; a bump stop must not consume the stated working travel. Spring-rate tolerance +/-10%, damping-force tolerance +/-20% over 0.05-0.65 m/s, measured in compression and rebound. Require a force-velocity curve; oil viscosity alone is not a damping specification.

<b>Structural targets, each complete shock and mounting path:</b> working axial capacity 1.5 kN in compression and tension; proposed proof load 2.25 kN; proposed minimum ultimate 3.0 kN. These are design requirements to be substantiated by the supplier, not ratings of an identified product. No coil bind within travel including preload and tolerances.

Do not infer bearing-bore dimensions from CAD names containing M4 or M5. Confirm shaft diameters, eye widths, articulation angles, fasteners, body clearance and rod-end side-load limits from dimensioned drawings. No specific commercial shock has been fit-checked or approved.

# Structure, actuator heat and release tests

## Rod assembly sizing check

Use a preliminary structural contact envelope of 3.5 W = 1,030 N, above the largest recorded ground-force sensor peak in this test set. A conservative rod-pair axial estimate is Nrod = Fz/2 + T/s, with T = 59.642 N m. At s = 60 mm thigh / 36 mm shin this gives <b>1.51 kN / 2.17 kN per rod</b>. Request 3.0 kN axial working capacity per completed rod-and-end assembly, including reverse tension.

For illustration only, a 20 mm OD / 16 mm ID tube has A = 113.1 mm2 and I = 4,637 mm4. With assumed axial E = 70 GPa and pinned unsupported length 242 mm, Euler Pcr is 54.7 kN. At 3 kN axial load plus an assumed 30 N m local bending moment, maximum nominal stress is about 91 MPa. These material and loading assumptions are unverified; global buckling is unlikely to be the controlling issue, while clamp crushing, slip, delamination, adhesive joints and fatigue still require substantiation. This does not release a tube laminate.

## Continuous actuator requirement to verify

The largest Macro RMS torque across the selected trials was 19.52 N m, including the separate 33 kg sensitivity case. Dividing by 2.98211 N m/A gives 6.55 A equivalent. At a 20% lower effective torque constant the requirement becomes 8.18 A. Specify a bench demonstration of at least <b>8.5 A RMS-equivalent duty</b> in the final enclosure/cooling arrangement; this is a required test, not an established actuator rating.

Assuming ODrive peak-phase/Iq convention and phase-neutral R = 0.113472 ohm, copper loss is approximately 1.5 R Iq^2: 12.3 W at 8.5 A and 68.1 W at 20 A, before resistance rise, iron, gearbox and drive losses. Phase current is not battery current. Confirm the resistance/current conventions before using this as a heat budget.

## Physical evidence needed before production release

1. Recalibrate torque sign, encoder sign, load-cell scale and lever arm; rerun inertia with a known added inertia and friction in both directions. Establish a loaded torque-speed curve and permitted peak duration.

2. Run the representative walking current/speed cycle to thermal equilibrium at the intended worst ambient (proposed 35 C), in the final enclosure. Proposed acceptance: winding estimate at or below 65 C for margin to the export limit of 75 C, no drive derating, and stable output torque. A case temperature alone is insufficient without a winding correlation.

3. Bench-check the coilover force-travel and force-velocity curves; fixture-proof the complete shocks, mounts and rod ends. Confirm no interference through the full operating joint range and the proposed shock travel.

4. Conduct tethered hardware walking at 30 kg, starting with level-ground 0.2 m/s commands and increasing toward 0.3 m/s. Log actual torque/current, temperatures, bus sag, contacts, shock travel and communication delay. Release only after the hardware results reproduce the required behavior.

# Model limitations, software findings and traceability

## What this review does not establish

This is finite scenario screening, not a certified worst-case optimization. There is no measured distribution or hard bound for inertia, friction, torque calibration, temperature, backlash, gearbox stiffness, battery impedance or communication latency. Arms and Micro actuators retain their existing assumed parameters. A guarantee over all combinations is therefore unavailable.

The shock is an ideal linear spring and symmetric viscous damper, with compliant numerical travel limits. Seal stiction, gas pressure, cavitation, temperature drift, spring mass and actual shock-body packaging are not modeled. The rods and structure are rigid; structural failure, clamp slip and fatigue are not simulated. No terrain steps or slopes, falls, jumping, running or drop tests were performed.

## Software findings affecting engineering interpretation

The builder uses simplified link boxes and generally omits robot self-collision; foot-foot contact is enabled. It cannot establish actuator-housing or coilover-body clearance. Rods are represented as solid cylinders for inertial calculations even though manufactured carbon members may be tubes. Added electronics mass is lumped at the torso rather than measured component locations.

The existing evaluation keeps load extremes only from trials that survive. Those values cannot be used as crash or fall loads. Soft tendon limits can be exceeded numerically: the older report includes 5.34 mm front bottom-stop overtravel. The original analysis holding-torque calculation distributes weight equally between both feet, so it is not a single-support worst-case calculation.

The original standing-drift check does not apply the live torque-speed envelope. This report relies on the policy-driven live-session trials for walking. The new script records per-trial outcomes and extrema, including any failed trial, and verifies that the gantry is inactive. The current builder assumes stroke centered on the CAD pose. It cannot represent the proposed asymmetric 32 mm endpoints by changing the scalar stroke alone. The review script explicitly adjusts the compression stops; prototype_specification.json states the endpoints. No original robot profile, policy, or application source file was changed.

## Reproduction

From the repository root, run .venv/Scripts/python.exe output/engineering/luma1_review/run_review.py with modes screen, adverse, validate, phase and final. Raw results are in the corresponding JSON files; trial_summary.csv gives one row per trial. geometry_selected32.json contains the CAD-derived dimensions. macro_original.json preserves the supplied export; source_manifest.json records SHA-256 hashes.

Source commit: a65260eec981d52d53b2c14dbd24c579029e24d9. The recorded review date is 09 November 2026, Asia/Singapore. Simulation software: MuJoCo 3.15.0, existing Isla Studio Python runtime and bundled luma1-walk policy. No policy training was performed.

## Primary technical references

[1] MuJoCo, <link href="https://mujoco.readthedocs.io/en/stable/modeling.html" color="#087E8B">Modeling</link> and <link href="https://mujoco.readthedocs.io/en/3.3.1/XMLreference.html" color="#087E8B">XML Reference</link>: joint inertia/friction, tendon spring/damping and constraint behavior.

[2] ODrive, <link href="https://docs.odriverobotics.com/v/latest/hardware/odrive-motors.html" color="#087E8B">ODrive Motors</link>: approximate torque-speed characteristics and dependence on cooling. This does not identify the user's motor as an ODrive catalog motor.

[3] ODrive, <link href="https://docs.odriverobotics.com/v/latest/manual/hardware-config.html" color="#087E8B">Hardware Configuration</link>: thermal derating, supply and regeneration limits. Current documentation is conceptual support; firmware 0.6.x configuration names must not be copied into the user's 0.5.x controller without checking compatibility.
