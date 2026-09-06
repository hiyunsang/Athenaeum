' Athenaeum server autostart (no window). Copy this file to the Startup folder:  Win+R -> shell:startup
' It finds server.py next to itself, so if you copy it elsewhere edit the path below or keep a copy of this .vbs in paper-search
' and put a *shortcut* to it in the Startup folder instead.
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
server = here & "\server.py"
If Not fso.FileExists(server) Then
  ' copied to Startup folder: try the usual place next to this repo (edit if your folder differs)
  server = fso.BuildPath(CreateObject("WScript.Shell").ExpandEnvironmentStrings("%USERPROFILE%"), "Documents\Athenaeum\paper-search\server.py")
End If
If fso.FileExists(server) Then
  CreateObject("WScript.Shell").Run "pythonw """ & server & """", 0, False
End If
