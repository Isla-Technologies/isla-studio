# Isla Studio

Isla Technologies' robot training studio. It teaches robots skills with reinforcement learning in MuJoCo, chooses their hardware while they learn (co-design), and runs the result on the real robot through a safety layer. It runs on Windows, macOS and Linux, in the browser, from one Python program.

## Start it

You need **Python 3.10 or newer** ([python.org](https://www.python.org/downloads/); on Windows tick *Add python.exe to PATH*). The first start sets everything up: it needs the internet and takes a few minutes.

| Computer | Start Isla Studio |
|---|---|
| Windows | Double-click `run.cmd` |
| macOS | Double-click `run.command` (the first time: right-click it, *Open*). Or in Terminal: `sh run.sh` |
| Linux | `sh run.sh` (on Ubuntu or Debian, first `sudo apt install python3-venv`) |

Your browser opens **http://localhost:8848**. Closing the terminal window stops Isla Studio; training runs keep going and Isla Studio picks them up when it starts again.

Options go after the script's name, for example `run.cmd --host 0.0.0.0`:

- `--host 0.0.0.0` lets other devices on your network open it (a tablet beside the robot): `http://<this computer's address>:8848`.
- `--port 8849` if 8848 is taken.
- `--robot luma_1` loads a named robot instead of the last one used.
- `--no-browser` starts without opening a browser.

### Training on an NVIDIA GPU

On Windows and Linux with an NVIDIA GPU and a current driver (CUDA 12), press **Install NVIDIA GPU support** on the Train page, or run `.venv/Scripts/python -m pip install -r requirements-gpu.txt` (Windows) or `.venv/bin/python -m pip install -r requirements-gpu.txt` (Linux). A GPU trains about five times faster; the first GPU run spends a few minutes compiling. macOS trains on the processor.

## What is where

| Page | What it does |
|---|---|
| **Hangar** | Every robot Isla Studio knows, in its bay. Look one over on the turntable, load it for training, or import a new one. |
| **Train** | Start a run from a preset (walk, stand and recover, find the best leg design) or with every option; watch the practice robots and the learning curves; watch a trained policy drive the live robot. |
| **Design** | Change the hardware (rod lengths, shock absorbers) and see what the physics says before building it. |
| **Connect** | Bring up the real robot: CAN adapters, motor map, arming, live motor table. A practice robot rehearses all of it without hardware. |
| **Deploy** | Run a trained policy on the robot, with the checklist. |
| **Robot** | The robot's profile: mass, actuators, the motor map. |

The anatomy view at the top right is the robot from its own model, with a point on every motor: click one to select it, drag to turn the robot. `Ctrl K` opens the command bar (type what you want: "teach it to walk", "push it", "arm"). `Esc` is the emergency stop for a connected robot. The sun or moon beside the E-stop switches between the dark and light themes (or type "light mode" in the command bar); Studio remembers the choice, and until you make one it follows your system.

## The hangar

Each robot is a folder in `robots/`: its MuJoCo model in `source/` and its profile, `robot.json`. **Load for training** switches the whole studio to that robot; Isla Studio remembers it.

**Import a robot** takes any legged robot as MJCF (`.xml`) or URDF (`.urdf`) with its meshes (STL, OBJ or MSH). Choose the robot's folder, or give the path of its model file. Isla Studio finds:

- the base (the body with the free joint; a URDF's root link),
- the standing pose (the model's keyframe, or its zero pose),
- the two feet (the parts that reach the floor) and which is left,
- which way the robot faces,
- the motors: torque limits and servo gains from the model, a limb for each from the joint's name, and a default CAN map (one bus per limb).

A URDF has no actuators (MuJoCo does not read URDF transmissions), so each of its joints gets a motor as strong as its `effort` limit. Gains estimated rather than read from the model are marked as assumed. Check what was found in `robots/<id>/robot.json`, especially the CAN map. Robots fixed to the world (arms) are refused: Isla Studio trains robots that stand on their own feet.

A robot that cannot stand by holding its pose (most need a policy to balance) hangs in a **gantry** in the live view until a policy runs, as real robots do during bring-up. The Gantry button switches it.

## Luma-1

| | |
|---|---|
| Mass | 27.0 kg: 24.58 kg from the CAD and 2.42 kg of virtual mass at the torso |
| Motors | 19: 11 macro actuators (36:1, 3 N·m/A, 66 N·m at 22 A continuous) and 8 micro actuators (two 7.385:1 stages, 54.54:1; 1.36 N·m/A; 23.39 N·m at 17.2 A continuous) |
| Legs | Carbon-fibre thigh and shin rods whose length co-design chooses: +42 mm thigh and +46 mm shin over the CAD, adopted from the co-design run `luma1-codesign-66nm`. That is four thigh rods cut to 242 mm and four shin rods cut to 156 mm (the CAD's are 200 and 110 mm), for a standing height of 1.03 m. |
| Ankles | Passive, on four shock absorbers (front and back of each leg) whose spring rate, damping and preload co-design chooses: 26 / 29 N/mm, 380 / 410 N·s/m, 4 / 4.5 mm preload (front / back), adopted from the same run. Eye to eye, extended to compressed: 95.8 to 71.8 mm front, 115.9 to 91.9 mm back. |

The Design page lists these as **Parts for this design** for whatever design is on its sliders. Deploy's **Test in full physics** measures the peak loads on each shock absorber and motor for a trained policy.

Assumed until measured, and labelled so in the app:

- the actuators' no-load speeds (5.8 rad/s macro, 14 rad/s micro);
- their reflected rotor inertia (armature): 0.1 kg·m² macro and 0.03 kg·m² micro, an 80 mm-class and a 50 mm-class rotor through their gearboxes;
- the shock absorbers' 24 mm stroke;
- continuous torque as the torque limit (there are no peak ratings yet).

Motors follow their torque-speed line: full torque up to a corner speed, less above it, and past the no-load speed they brake, as a motor whose back-EMF beats its battery does.

## Connecting the robot

Motor drives sit on CAN buses, reached through USB-CAN adapters through [python-can](https://python-can.readthedocs.io/): CANable and other serial adapters (`slcan`, any computer), candleLight (`gs_usb`: `pip install gs-usb`), PEAK (`pcan`, with PEAK's driver), Kvaser, and `socketcan` on Linux. Drives speak the MIT protocol or ODrive CANSimple.

The robot's own computer (a Jetson or a Raspberry Pi) can serve its motors and IMU instead. Copy this folder there, set it up once with `sh run.sh --no-browser` (stop it with Ctrl+C when it says it is running), then start the robot's side with

```
.venv/bin/python -m isla_rl.runtime --robot luma_1
```

and choose **Robot computer** on the Connect page. If the laptop falls silent while the motors are enabled, the robot relaxes them.

Nothing moves until you arm. Arming starts from the measured pose and ramps stiffness up over two seconds. Limits on speed, torque and stiffness are conservative until you change them. Test in a gantry first.

## Training from the command line

The web app creates runs in `runs/`. To continue one in a terminal, or on another computer with Isla Studio installed:

```
python -m isla_rl.learn train runs/luma1-walk-codesign --device gpu
```

Progress prints as one JSON line per update. Create `runs/<name>/stop` (or press Ctrl+C) to stop with a checkpoint; the same command continues.

To try a run's policy in the live view's physics (2 ms steps, the motors' torque-speed limits, the shock absorbers as built) and measure what building the robot needs:

```
python -m isla_rl.learn evaluate runs/luma1-walk-run
```

What it does depends on the policy:

- **Walking policies** are asked for a ladder of speeds, then backward, sideways, turning and a curve. They are shoved from eight sides at growing strength, then roam on random commands for a minute.
- **Jumping policies** stand, are shoved, then jump ten times.

Every trial records each shock absorber's stroke, shaft speed and force, and each motor's peak and RMS torque and top speed. The report prints and is saved as `runs/<name>/evaluation.json`.

## Folders

| Folder | Holds |
|---|---|
| `isla_rl/robot/` | Robot profiles, URDF/MJCF import, the simulation model, co-design physics |
| `isla_rl/sim/` | The live simulation the 3D view shows |
| `isla_rl/hardware/` | CAN drivers, motor protocols, the safety layer, the practice robot |
| `isla_rl/runtime/` | What runs on the robot's own computer |
| `isla_rl/learn/` | Practice robots (CPU and GPU), PPO, skills and rewards, co-design, testing in full physics |
| `isla_rl/server/` | The local server behind the web app |
| `ui/` | Studio's interface (Preact, TypeScript, Three.js); `ui/dist` is the built copy the server serves |
| `robots/` | Each robot's profile and its CAD source (meshes, URDF) |
| `tests/` | The test suite (`python -m pytest`) |

Made on this computer and kept out of the repository: `runs/` (training runs: settings, progress, checkpoints, policies), `policies/` (policies exported for robots), `settings/` (this computer's hardware settings and last robot) and `.cache/`.

## For developers

```
python -m pip install -r requirements-dev.txt
python -m pytest
python -m ruff check isla_rl tests
```

The interface needs Node 22 only to change it: `cd ui`, `npm ci`, then `npm run dev` (Vite on port 5173, talking to the Python server on 8848) or `npm run build` to update `ui/dist`. `.github/workflows/ci.yml` runs the tests on Windows, macOS and Linux and builds the web app.
