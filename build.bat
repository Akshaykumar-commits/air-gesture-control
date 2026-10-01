@echo off
REM Run from this folder. Needs Python 3.10 or 3.11 installed.
python -m venv buildenv
call buildenv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pyinstaller --noconfirm --windowed --name GestureControl ^
  --collect-all mediapipe ^
  --collect-all screen_brightness_control ^
  --hidden-import pystray._win32 ^
  main.py
echo.
echo Done. Test: dist\GestureControl\GestureControl.exe
pause
