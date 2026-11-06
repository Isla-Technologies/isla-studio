# 6. Troubleshooting

## Windows desktop app

**Do I need to install Python or Rust?** No. The setup `.exe` includes Python and the runtime packages. Rust runs the desktop window and manages the local server; the server, training and robot logic remain Python. Windows 10 or 11, 64-bit, is required. macOS and Linux currently use the source launcher.

**Windows shows an unknown publisher.** The current installer is unsigned. Check that the installer came from the source you intended; do not change Windows security settings to install it. Building your own installer is described in [the desktop build guide](../desktop/README.md).

**WebView2 setup needs a connection.** The installer downloads Microsoft's WebView2 setup when the runtime is missing. Connect to the internet and run setup again. Once the desktop app and its runtime are installed, simulation and CPU training work offline.

**"The installation is incomplete" or "Studio could not start."** Read the message in the startup window and copy `%LOCALAPPDATA%\technologies.isla.studio\studio.log` before reopening Studio: each launch replaces this log. Reinstall from the setup `.exe` if the message says files are missing. Python installed elsewhere on the computer is not used by the desktop app.

**Studio stays on the startup screen.** The first start copies the bundled robot and examples and reads the model. If it fails or reports that startup took too long, keep the server log above for a report. Check that your Windows Documents folder is accessible; Studio needs to create **Documents/Isla Studio** there.

**My existing robots or runs are missing.** The desktop app uses **Documents/Isla Studio**; a source checkout uses its own folders. The desktop app does not automatically move your existing work. Follow [the migration instructions](getting-started.md#windows-desktop-app), then check the copies before removing any originals. Windows may redirect Documents into OneDrive or another location.

**An example I deleted did not come back after reinstalling.** Bundled robots and examples are offered once. Reinstalling the app preserves your project folders and the record of examples you removed.

**Where are GPU packages and logs?** Optional GPU packages are in `%LOCALAPPDATA%\technologies.isla.studio\python313-packages`, and caches are in the `cache` folder beside it. The GPU installation log is `runs/gpu-install.log` under **Documents/Isla Studio**. These are separate from the installed Python runtime. Packaged CPU training has been checked; packaged GPU training has not yet been verified.

**How do I update or remove the app?** Stop any training and close Studio before running a newer setup file or uninstalling it through **Windows Settings → Apps**. The uninstaller's **Delete the application data** option removes local caches, logs and optional GPU packages; it keeps the projects in **Documents/Isla Studio**. See [Your files, updates and removal](getting-started.md#your-files-updates-and-removal).

## Starting from source

**"Isla Studio needs Python 3.10 or newer."** Install Python from [python.org](https://www.python.org/downloads/). On Windows, tick **Add python.exe to PATH**, then start Isla Studio again. If you installed a newer Python after a failed start, delete the `.venv` folder first.

**On Linux, creating the environment fails.** Install venv first: `sudo apt install python3-venv` on Ubuntu or Debian.

**On macOS, `run.command` will not open.** Right-click it and choose **Open** the first time, or run `sh run.sh` in Terminal.

**The browser says the page cannot be reached.** Isla Studio is still starting (the first start takes a few minutes), or its terminal window was closed. Look at the terminal: it prints the address once it is running.

**Port 8848 is in use.** Another source instance of Isla Studio may be running. Start this one on another port: `run.cmd --port 8849` on Windows, or `sh run.sh --port 8849` on macOS or Linux. The desktop app chooses a separate local port automatically.

**The first start fails while downloading.** It needs the internet. Start it again: it picks up where it stopped. Behind a proxy, set `HTTPS_PROXY` first.

## Training

**The GPU light says CPU.** GPU support is not installed, the driver is too old, or there is no NVIDIA GPU. Open **Train**, press **New run**, and read the line under the time estimate. Install GPU support from there, or see [NVIDIA GPU support](getting-started.md#nvidia-gpu-support). macOS always trains on the processor.

**GPU support was installed in my source checkout, but the desktop app still says CPU.** Each has its own Python packages. Use **Install NVIDIA GPU support** inside the desktop app too. Keep Studio open until it reports the installation result. If installation fails, check `runs/gpu-install.log` in that version's projects folder.

**A GPU run sits at 0 moves for minutes.** The first run on a GPU compiles first; it takes a few minutes, once.

**Training stalls, with no error, on a small GPU.** The GPU has run out of memory and is swapping. Each 4,096 practice robots of a robot like Luma-1 need about 1.3 GB. Stop the run, and start it, or other runs sharing the GPU, with fewer **Robots at once**.

**The falls curve does not drop.** Give it time: walking takes hundreds of millions of moves. If a new robot never learns to stand, check its profile: the standing pose, the feet, and the motors' torque and gains (see [Robots](robots.md#what-isla-studio-finds-and-how-to-correct-it)). A pose the robot cannot hold with its motors cannot be learned.

**Continue says the run finished all its practice.** That run is complete. Start a new run to train further.

**I closed Studio while training.** Training runs in its own process and can continue after the desktop window or source server closes. Reopen the same version and projects folder to pick up its progress. To finish a run before updating or uninstalling, select it in **Train**, press **Stop** and wait for it to save before closing Studio.

## The live view

**The robot hangs in the air.** It is in the gantry, because it cannot balance without a policy. Press **Watch policy** on a trained run, or **Gantry** to let it down.

**The robot fell over.** Press **R** to reset it.

**The keys do nothing.** Steering needs a policy running: press **Watch policy** first. Keys are ignored while you type in a field. Isla Studio says what to do first whenever a control cannot act.

**I lost the robot.** Double-click the live view to frame it again.

## The anatomy view

**Where did the red motor dots go?** Motors now appear as red actuator housings inside the robot's transparent shell. Click an actuator or a shell part to select it. The two passive ankle hinges on Luma-1 keep diamond markers.

**The actuators do not glow in the light theme.** Light mode uses red shading without bloom. In dark mode an actuator glows brighter as its motor works harder. Hovering or selecting an actuator also highlights it; colour alone is not a torque reading, so use the selected motor's values for the numbers.

**An actuator is grey or blinking.** With **Robot** selected as the data source, grey means no reply and blinking marks a fault. Select the actuator to read its state. The **Robot** source is available after connecting the robot; **Sim** shows the simulation's readings.

**An imported robot has simple cylindrical actuators.** Studio uses a housing from the model when it can match one to the joint. Otherwise it draws a motor at that joint. The drawn motor's size is an estimate; check the robot's model for its housing dimensions.

## Importing a robot

**"No motor drives a joint."** The model has no actuators Isla Studio can use. In MJCF, add position or motor actuators. A URDF gets a motor on each joint with an `effort` limit, so check that its joints have one.

**The import is refused as fixed to the world.** Isla Studio trains robots that balance on their feet. Robot arms are not supported.

**A mesh is missing.** Keep the meshes inside the robot's folder, and choose the whole folder when importing.

**The robot faces the wrong way, or the wrong parts are its feet.** Correct `frame` or `feet` in `robots/<robot>/robot.json`, then restart Isla Studio. For the desktop app, this path is under **Documents/Isla Studio**; for the source launcher, it is under the checkout.

## Hardware

**A motor does not answer.** Check its power, its CAN wiring and termination, and its bus and node on the **Robot** page. Press **Scan** on **Connect** to see which drives answer.

**A joint moves the wrong way on the model.** Set its direction to **Reversed** in the **Motor map** on the **Robot** page. If its angle is offset, set its zero.

**The robot stops by itself.** A safety limit tripped: the red banner says which. Fix the cause, then **Clear stop**.

## Still stuck?

Open an [issue](https://github.com/Isla-Technologies/isla-studio/issues) with what you did, what you expected, what happened, your operating system and whether you used the desktop app or the source launcher. Include the relevant error and the end of the desktop `studio.log` or source terminal output. Check the excerpt for private paths or hardware details before posting it publicly.
