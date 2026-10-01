; Inno Setup script - open in Inno Setup and press Compile (after build.bat)
[Setup]
AppName=Air Gesture Control
AppVersion=1.0
DefaultDirName={autopf}\GestureControl
DefaultGroupName=Air Gesture Control
OutputBaseFilename=GestureControlSetup
OutputDir=installer_out
Compression=lzma
SolidCompression=yes
[Files]
Source: "dist\GestureControl\*"; DestDir: "{app}"; Flags: recursesubdirs
[Icons]
Name: "{group}\Air Gesture Control"; Filename: "{app}\GestureControl.exe"
Name: "{autodesktop}\Air Gesture Control"; Filename: "{app}\GestureControl.exe"
[Run]
Filename: "{app}\GestureControl.exe"; Description: "Launch now"; Flags: postinstall nowait skipifsilent
