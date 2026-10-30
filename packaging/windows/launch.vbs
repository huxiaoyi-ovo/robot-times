' The Robot Times 启动器（Windows）：不弹黑窗口，用安装包自带的 uv 运行 bootstrap.py。
'   wscript launch.vbs open    打开看板（开始菜单、桌面图标）
'   wscript launch.vbs serve   后台运行看板（登录后自动启动）
Set fs = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fs.GetParentFolderName(WScript.ScriptFullName)
cmd = "open"
If WScript.Arguments.Count > 0 Then cmd = WScript.Arguments(0)
sh.Environment("Process")("UV_PYTHON_PREFERENCE") = "only-managed"
sh.Run """" & dir & "\uv.exe"" run --no-project --python 3.12 --quiet """ & dir & "\bootstrap.py"" " & cmd, 0, False
