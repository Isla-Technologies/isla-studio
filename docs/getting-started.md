# 1. Install and start

These instructions describe the default Python backend. A Rust core candidate
is available for development, but it does not yet support every workflow.
See [native development](native-development.md) and [migration status](rust-migration.md).

## Install

### Windows desktop app

On **64-bit Windows 10 or 11**, download the setup `.exe` from [GitHub Releases](https://github.com/Isla-Technologies/isla-studio/releases/latest), run it, then open **Isla Studio** from the Start menu. It installs for your Windows account and includes Python, MuJoCo, the interface, Luma-1 and the trained examples; you do not need to install Python, Node or Rust. Studio opens in its own desktop window. The installer sets up Microsoft WebView2 if it is missing, which needs an internet connection. The current installer is unsigned. See [release notes](release-notes.md) for the checks performed and remaining validation.

Robots, training runs, policies and settings are kept in **Documents/Isla Studio**, separate from the installed app. Closing the window stops its local server cleanly. Training runs continue in their own processes, just as they do with the source launcher.

If you already use the source launcher, your existing projects stay in that checkout. To use them in the desktop app, stop any training, close Studio, and copy the robots and runs you want, plus their policies and settings, into the matching folders in **Documents/Isla Studio**. Keep the originals until you have opened and checked the copies.

The desktop app runs the simulation and training locally. Its Rust/Tauri shell manages the window, starts the bundled server and stops it when the window closes. The server, robot tools, training and hardware logic remain Python; MuJoCo supplies the physics, and the interface remains TypeScript. See [Architecture](architecture.md) for the current split and [Rust migration](rust-migration.md) for the separate native candidate's progress. [The desktop build guide](../desktop/README.md) covers building the installer. macOS and Linux desktop packages are not built yet.

The desktop app chooses its own local connection automatically. You do not need to open `localhost:8848`, keep a terminal open or use the source launch options below.

### Your files, updates and removal

| What | Windows desktop app | Source launcher |
| --- | --- | --- |
| Robots, runs, policies and settings | The matching folders in **Documents/Isla Studio** | `robots/`, `runs/`, `policies/` and `settings/` in the checkout, unless configured otherwise |
| Cache | `%LOCALAPPDATA%\technologies.isla.studio\cache` | `.cache/` in the checkout |
| Startup and server log | `%LOCALAPPDATA%\technologies.isla.studio\studio.log` | The terminal output |
| Optional GPU packages | `%LOCALAPPDATA%\technologies.isla.studio\python313-packages` | The checkout's `.venv` |

Windows can place Documents inside OneDrive or another redirected folder; use your Windows Documents location. The desktop server log is replaced on each launch, so copy it before reopening Studio when reporting a failed start.

To update the desktop app, stop any training, close Studio and run the newer setup `.exe`. There is no in-app updater. Keep a backup of **Documents/Isla Studio** before updating; projects and optional GPU packages are stored separately from the installation.

To uninstall, stop training, close Studio and remove **Isla Studio** through **Windows Settings → Apps**. The uninstaller's **Delete the application data** option removes local caches, logs and downloaded GPU packages. Your projects in **Documents/Isla Studio** are outside that application-data folder and are kept.

### From source

The source launcher runs on Windows, macOS and Linux. It needs **Python 3.10 or newer**; the first start installs everything else.

1. Install Python from [python.org](https://www.python.org/downloads/). On Windows, tick **Add python.exe to PATH** in the installer. On Ubuntu or Debian, also run `sudo apt install python3-venv`.
2. Download Isla Studio: on the [repository page](https://github.com/Isla-Technologies/isla-studio), choose **Code**, then **Download ZIP**, and unzip it anywhere. Or clone it:

   ```sh
   git clone https://github.com/Isla-Technologies/isla-studio.git
   ```

## Start from source

| Computer | Start Isla Studio |
| --- | --- |
| Windows | Double-click `run.cmd`. |
| macOS | Double-click `run.command`. The first time, right-click it and choose **Open**. Or run `sh run.sh` in Terminal. |
| Linux | Run `sh run.sh` in a terminal. |

The first start creates a private Python environment in the `.venv` folder and downloads what Isla Studio needs. This takes a few minutes and needs the internet. Later starts take seconds.

Your browser then opens **<http://localhost:8848>**. The screen is a page in your browser, but everything runs on your computer: nothing is uploaded.

To stop Isla Studio, close its terminal window or press Ctrl+C in it. Training runs keep going on their own, and Isla Studio picks them up when it starts again.

### Start options

Add options after the script's name, for example `run.cmd --port 8849`:

| Option | What it does |
| --- | --- |
| `--host 0.0.0.0` | Lets other devices on your network open Isla Studio, such as a tablet beside the robot, at `http://<this computer's address>:8848`. |
| `--port 8849` | Uses another port, if 8848 is taken. |
| `--robot luma_1` | Loads this robot instead of the last one used. |
| `--no-browser` | Starts without opening a browser. |

## NVIDIA GPU support

An NVIDIA GPU trains about five times faster than a processor. Isla Studio can use one on Windows and Linux, with a current NVIDIA driver (CUDA 12). macOS trains on the processor.

To install GPU support in either the desktop app or the source version, open **Train**, press **New run**, and press **Install NVIDIA GPU support**. It is a large download and takes a few minutes; Isla Studio tells you when it is done and whether the GPU is ready. The desktop app keeps these optional packages in the location above, separate from its bundled Python runtime. The Windows package has been checked with CPU training; GPU training in that package has not yet been verified.

For the source version, you can also install GPU support from a terminal in the checkout:

```sh
.venv\Scripts\python -m pip install -r requirements-gpu.txt     # Windows
.venv/bin/python -m pip install -r requirements-gpu.txt         # Linux
```

The first training run on a GPU spends a few minutes compiling before it starts.

## The screen

![Isla Studio's Train page: the panel on the left, the live robot in the middle, the anatomy view and learning curves on the right](images/train.webp)

- **Top bar.** The six pages: **Hangar**, **Train**, **Design**, **Connect**, **Deploy** and **Robot** (keys 1 to 6). On the right, three lights show the simulation (SIM), the real robot (off, connected or armed) and whether training can use a GPU (GPU) or only the processor (CPU). Then the light and dark switch and the **E-STOP** button.
- **Left panel.** The page's controls. The `–` button folds it away.
- **Live view.** The robot in the live simulation, in full physics. Drag to orbit, scroll to zoom, double-click to frame the robot, and Shift-drag on the robot to push it. The toolbar above it pauses, resets and hangs the robot in a gantry, and switches between the live robot and the practice robots of a run.
- **Anatomy.** An X-ray of the robot: its shell drawn as glass and edges, with red actuators inside. In the dark theme, an actuator glows brighter as its motor works harder; the light theme uses red shading without glow. A motor that does not reply turns grey, and a faulted one blinks. The passive ankle hinges keep their diamond markers. Click an actuator or a part to select it and read its details. The buttons show every motor's label, switch between the simulation's and the real robot's readings, enlarge it or make it full screen.

![The anatomy view, full screen, with every motor's angle labelled](images/anatomy.webp)

### Keys

| Key | What it does |
| --- | --- |
| W, A, S, D | While a policy runs: walk forward, left, back, right. |
| Q, E | Turn left, turn right. |
| Shift with W | Run, for policies that learned to run. |
| J | Jump, for policies that learned to jump. |
| Space | Pause or run the simulation. |
| R | Reset the simulation. |
| 1 to 6 | Go to a page. |
| Ctrl K | Open the command bar. |
| Esc | Emergency stop for a connected robot. |

### The command bar

Press **Ctrl K**, or click the bar at the bottom, and type what you want in plain words: "teach it to walk", "jump", "push it", "arm", "light mode". Choose a match with the arrow keys and Enter.

![The command bar, listing commands such as teaching the robot to walk and run, pushing it, and walking forward](images/command-bar.webp)

### Light and dark

The sun or moon button beside the E-stop switches between the dark and light themes (or type "light mode" in the command bar). Isla Studio remembers your choice; until you choose, it follows your computer's setting.

![The Train page in the light theme](images/light.webp)

## Your first ten minutes

Isla Studio comes with **Luma-1** and two of its trained runs, so you can see a trained robot before you train your own.

1. **Watch a trained robot walk.** Open **Train** and choose the run **luma1-walk**. Press **Watch policy**. Luma-1 leaves the gantry and balances on its own; hold **W** to walk forward, **A** and **D** to step sideways, **Q** and **E** to turn. Shift-drag on the robot to push it.

   ![Luma-1 walking forward under the luma1-walk policy, a red arrow showing the command](images/watch.webp)

2. **See how it learned.** On the right, the curves show its score rising and its falls dropping over 217 million moves of practice. Press **Practice robots** to watch sixteen of the robots it practised with.
3. **See how it tested.** Open **Deploy** and choose **luma1-walk**. **Tried in full physics** shows its top speed, how far it can be shoved, and the loads on every motor and shock absorber.
4. **Train your own.** Open **Train**, press **New run**, choose **Stand and recover**, and press **Start training**. On a laptop GPU it takes about ten minutes; on a processor, about twenty. Watch the falls curve drop, then press **Watch policy** to try it. See [Training](training.md).

The other example, **luma1-leg-design**, is the run that chose Luma-1's leg rods and shock absorbers while it learned to walk. See [Design](hardware-design.md).
