; The Robot Times 的 Windows 安装程序（Inno Setup 6）。由 packaging/build.py 填好路径后编译。
; 装在当前用户的文件夹里，不需要管理员权限；数据在 %USERPROFILE%\.robot-times，卸载不会删。

[Setup]
AppId={{6C2B7E7A-3F1D-4D8E-9B57-2A7C51E0B0A1}
AppName=The Robot Times
AppVersion={{VERSION}}
AppPublisher=The Robot Times
AppPublisherURL=https://github.com/huxiaoyi-ovo/robot-times
DefaultDirName={localappdata}\Programs\The Robot Times
DefaultGroupName=The Robot Times
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir={{DIST}}
OutputBaseFilename={{NAME}}
SetupIconFile={{PAYLOAD}}\icon.ico
UninstallDisplayIcon={app}\icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "zh"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "在桌面放一个图标"

[Files]
Source: "{{PAYLOAD}}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\The Robot Times"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\launch.vbs"" open"; IconFilename: "{app}\icon.ico"; Comment: "打开论文早报看板"
Name: "{userdesktop}\The Robot Times"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\launch.vbs"" open"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon
Name: "{userstartup}\The Robot Times"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\launch.vbs"" serve"; IconFilename: "{app}\icon.ico"

[Run]
Filename: "{sys}\wscript.exe"; Parameters: """{app}\launch.vbs"" open"; Description: "现在打开 The Robot Times"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
