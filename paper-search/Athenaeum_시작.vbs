' Athenaeum server autostart (no window). Put a *shortcut* to this file in the Startup folder:  Win+R -> shell:startup
' Uses the bundled python\ (portable release) next to paper-search if present, otherwise pythonw on PATH.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
server = here & "\server.py"
bundled = fso.GetParentFolderName(here) & "\python\pythonw.exe"
If fso.FileExists(server) Then
  If fso.FileExists(bundled) Then
    sh.Run """" & bundled & """ """ & server & """", 0, False
  Else
    sh.Run "pythonw """ & server & """", 0, False
  End If
End If
