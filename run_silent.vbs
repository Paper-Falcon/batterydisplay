' BatteryDisplay silent launcher - starts without a console window.
' Double-click to run. Copy into shell:startup for autostart.
'
' Why the interpreter is resolved explicitly:
' this machine has two Python installs and only one has pystray/Pillow/hidapi.
' A bare "pythonw" can resolve to the wrong one, and pythonw has no console,
' so an import error stays invisible and the app simply never appears.
'
' Note: VBScript requires CRLF line endings. Keep this file saved that way.

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

projectDir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = projectDir

localAppData = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%")

interp = ""
If fso.FileExists(localAppData & "\Programs\Python\Python313\pythonw.exe") Then
    interp = localAppData & "\Programs\Python\Python313\pythonw.exe"
ElseIf fso.FileExists(localAppData & "\Programs\Python\Python312\pythonw.exe") Then
    interp = localAppData & "\Programs\Python\Python312\pythonw.exe"
ElseIf fso.FileExists("C:\Python313\pythonw.exe") Then
    interp = "C:\Python313\pythonw.exe"
Else
    interp = "pythonw.exe"
End If

script = projectDir & "\src\prototype\app.py"

If Not fso.FileExists(script) Then
    MsgBox "app.py not found:" & vbCrLf & script, 16, "BatteryDisplay"
    WScript.Quit 1
End If

cmd = """" & interp & """ """ & script & """"
sh.Run cmd, 0, False
