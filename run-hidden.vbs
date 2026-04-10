Dim fso, dir, shell
Set fso   = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

dir = fso.GetParentFolderName(WScript.ScriptFullName)
Dim py
If fso.FileExists(dir & "\.venv\Scripts\pythonw.exe") Then
  py = """" & dir & "\.venv\Scripts\pythonw.exe"""
Else
  py = "pythonw"
End If

shell.Run py & " """ & dir & "\tray.py""", 0, False
