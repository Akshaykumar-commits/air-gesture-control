# Air Gesture Control

Control your Windows PC with your hand. Move the cursor, click, scroll, change volume and brightness, flip slides and open apps, all from the air with just a webcam.

![demo](demo.gif)
<!-- Replace demo.gif with your own screen recording, then upload it to this repo. -->

**Free · Works offline · Your camera video never leaves your PC**

## Download

1. Go to the [latest release](https://github.com/Akshaykumar-commits/air-gesture-control/releases/latest).
2. Download `GestureControlSetup.exe` and run it.
3. Open **Air Gesture Control** from the Start Menu, press **Start**, and hold your hand up in good light.

> **Windows warning?** The app is not code-signed yet, so SmartScreen may show "Windows protected your PC". Click **More info**, then **Run anyway**.

**Needs:** Windows 10 or 11 (64-bit) and a webcam. No Python required.

## Gestures

| Gesture | Action |
|---|---|
| ☝️ **Point** (index finger only) | Move the cursor |
| 🤏 **Pinch** (thumb + index) | Click. Hold to drag |
| 👌 **Thumb + middle finger** | Right click |
| ✌️ **Peace** (index + middle), move up/down | Scroll |
| 🖐️ **Open palm swipe** | Left/right = arrow keys, up = Task View, down = Show desktop |
| 🤙 **Shaka** (thumb + pinky), move up/down | Volume |
| 🤟 **Love** (thumb + index + pinky), move up/down | Screen brightness |
| 3️⃣ **Three fingers, hold** | Open app 1 (Chrome by default) |
| 4️⃣ **Four fingers, hold** | Open app 2 (File Explorer by default) |
| 🤘 **Rock, hold** | Close window (Alt+F4). Can be turned off |
| ✊ **Fist, hold** | Lock / unlock all gestures |

For "hold" gestures, keep the pose steady. A progress ring around your palm shows when it will trigger.

## Settings

The app window lets you change the camera, cursor speed, smoothness, and which apps the 3-finger and 4-finger gestures open. Settings are saved automatically. Closing the window keeps the app running in the system tray.

## Safety

- Move your **real mouse to the top-left corner** of the screen to stop the app immediately.
- Make a **fist** to lock gestures so nothing triggers by accident.

## Privacy

The camera is used only to find your hand, on your own PC. Video is not saved, recorded or sent anywhere, and the app needs no internet connection.

## Tips

- Good, even lighting and a plain background improve tracking a lot.
- If the cursor feels slow, increase **Cursor speed**. If it shakes, lower **Responsiveness**.
- If the FPS shown in the preview is below about 20, try brighter light or close heavy apps.

## Run from source

```
pip install -r requirements.txt
python main.py
```
Use Python 3.10 or 3.11. To build the Windows app, run `build.bat`, then compile `installer.iss` with [Inno Setup](https://jrsoftware.org/isinfo.php).

## Built with

Python, [OpenCV](https://opencv.org/), [MediaPipe](https://developers.google.com/mediapipe) hand tracking, PyAutoGUI, NumPy, Tkinter, PyInstaller and Inno Setup.
