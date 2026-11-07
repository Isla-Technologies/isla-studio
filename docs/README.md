# Isla Studio user manual

Isla Studio teaches legged robots skills with reinforcement learning, in MuJoCo physics, on your own computer. This manual takes you from installing it to running a trained policy on a real robot.

On Windows you can use the desktop installer, which includes Python and opens Studio in its own window. The source launcher opens the same interface in a browser on Windows, macOS and Linux. The desktop window is written in Rust/Tauri; the server, training and robot-control core remain Python. See [the desktop guide](../desktop/README.md) for the current implementation and build details.

New here? Read [Install and start](getting-started.md), then follow [Your first ten minutes](getting-started.md#your-first-ten-minutes).

## Chapters

| Chapter | What it covers |
| --- | --- |
| [1. Install and start](getting-started.md) | Windows desktop or source installation, project folders, NVIDIA GPU support, the screen and X-ray anatomy view, keys and the command bar, and your first ten minutes. |
| [2. Robots](robots.md) | The hangar, importing your own robot from URDF or MJCF, and checking and correcting its profile. |
| [3. Training](training.md) | Choosing a lesson, following a run, watching and steering a policy, the practice robots, testing in full physics, and training from the command line. |
| [4. Design](hardware-design.md) | Changing the hardware and seeing what the physics says, letting training choose the design, and the parts list. |
| [5. Run it on a real robot](real-robot.md) | Connecting over CAN or the robot's computer, safety limits, arming, running a policy, and the emergency stop. |
| [6. Troubleshooting](troubleshooting.md) | What to do when something does not work. |

[Release notes](release-notes.md) record the current package, validation and limitations. For maintainers: [Architecture](architecture.md) describes the default platform and the native Rust candidate; [Rust migration](rust-migration.md) records implementation status and remaining checks. [Native development](native-development.md) explains how to build and test the candidate. [The desktop guide](../desktop/README.md) covers the current Windows installer.

## Words this manual uses

Folder names such as `runs/` and `robots/` refer to your projects: **Documents/Isla Studio** for the Windows desktop app, or the checkout folder for the source launcher unless another location is configured. See [Install and start](getting-started.md) for moving existing projects into the desktop app.

| Word | Meaning |
| --- | --- |
| **Policy** | What training produces: a small neural network that reads the robot's sensors 50 times a second and sets every motor's target. |
| **Run** | One training session: its settings, its progress, and the policy it saved. Each run is a folder in `runs/`. |
| **Skill** | What a run teaches: *Stand and recover*, *Walk*, *Walk and run*, or *Jump*. |
| **Practice robots** | The simulated copies of your robot that learn together during training, 256 to 8,192 at once. |
| **Moves** | One step of one practice robot (a fiftieth of a second). Training length is counted in millions of moves. |
| **Score** | The points a practice robot earns per try, from the skill's rewards. It rises as the robot learns. |
| **Falls** | The share of tries that end with the robot on the ground. It drops as the robot learns. |
| **Full physics** | The live view's simulation: 2 ms steps, each motor held to its torque-speed limit. Policies are tested here before hardware. |
| **Co-design** | Training that also chooses the hardware, such as rod lengths and shock absorber settings, while the robot learns to walk. |
| **Gantry** | A frame the robot hangs in, so it cannot fall. Robots that cannot balance alone wait in one until a policy runs. |
| **Anatomy** | The robot's X-ray view: its shell and internal actuators, with live readings and brighter red actuators as their load rises. |
| **Arm** | Make the real robot's motors stiff. Nothing moves on hardware until you arm. |
