# 6. Troubleshooting

## Starting

**"Isla Studio needs Python 3.10 or newer."** Install Python from [python.org](https://www.python.org/downloads/). On Windows, tick **Add python.exe to PATH**, then start Isla Studio again. If you installed a newer Python after a failed start, delete the `.venv` folder first.

**On Linux, creating the environment fails.** Install venv first: `sudo apt install python3-venv` on Ubuntu or Debian.

**On macOS, `run.command` will not open.** Right-click it and choose **Open** the first time, or run `sh run.sh` in Terminal.

**The browser says the page cannot be reached.** Isla Studio is still starting (the first start takes a few minutes), or its terminal window was closed. Look at the terminal: it prints the address once it is running.

**Port 8848 is in use.** Another Isla Studio may be running. Start this one on another port: `run.cmd --port 8849`.

**The first start fails while downloading.** It needs the internet. Start it again: it picks up where it stopped. Behind a proxy, set `HTTPS_PROXY` first.

## Training

**The GPU light says CPU.** GPU support is not installed, the driver is too old, or there is no NVIDIA GPU. Open **Train**, press **New run**, and read the line under the time estimate. Install GPU support from there, or see [NVIDIA GPU support](getting-started.md#nvidia-gpu-support). macOS always trains on the processor.

**A GPU run sits at 0 moves for minutes.** The first run on a GPU compiles first; it takes a few minutes, once.

**Training stalls, with no error, on a small GPU.** The GPU has run out of memory and is swapping. Each 4,096 practice robots of a robot like Luma-1 need about 1.3 GB. Stop the run, and start it, or other runs sharing the GPU, with fewer **Robots at once**.

**The falls curve does not drop.** Give it time: walking takes hundreds of millions of moves. If a new robot never learns to stand, check its profile: the standing pose, the feet, and the motors' torque and gains (see [Robots](robots.md#what-isla-studio-finds-and-how-to-correct-it)). A pose the robot cannot hold with its motors cannot be learned.

**Continue says the run finished all its practice.** That run is complete. Start a new run to train further.

## The live view

**The robot hangs in the air.** It is in the gantry, because it cannot balance without a policy. Press **Watch policy** on a trained run, or **Gantry** to let it down.

**The robot fell over.** Press **R** to reset it.

**The keys do nothing.** Steering needs a policy running: press **Watch policy** first. Keys are ignored while you type in a field. Isla Studio says what to do first whenever a control cannot act.

**I lost the robot.** Double-click the live view to frame it again.

## Importing a robot

**"No motor drives a joint."** The model has no actuators Isla Studio can use. In MJCF, add position or motor actuators. A URDF gets a motor on each joint with an `effort` limit, so check that its joints have one.

**The import is refused as fixed to the world.** Isla Studio trains robots that balance on their feet. Robot arms are not supported.

**A mesh is missing.** Keep the meshes inside the robot's folder, and choose the whole folder when importing.

**The robot faces the wrong way, or the wrong parts are its feet.** Correct `frame` or `feet` in `robots/<robot>/robot.json`, then restart Isla Studio.

## Hardware

**A motor does not answer.** Check its power, its CAN wiring and termination, and its bus and node on the **Robot** page. Press **Scan** on **Connect** to see which drives answer.

**A joint moves the wrong way on the model.** Set its direction to **Reversed** in the **Motor map** on the **Robot** page. If its angle is offset, set its zero.

**The robot stops by itself.** A safety limit tripped: the red banner says which. Fix the cause, then **Clear stop**.

## Still stuck?

Open an [issue](https://github.com/Isla-Technologies/isla-studio/issues) with what you did, what you expected, what happened, and the end of Isla Studio's terminal output.
