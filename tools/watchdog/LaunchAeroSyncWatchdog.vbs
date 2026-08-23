Option Explicit

Dim shell, fso, scriptDir, rootDir, ps1, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
rootDir = fso.GetParentFolderName(fso.GetParentFolderName(scriptDir))
ps1 = fso.BuildPath(scriptDir, "AeroSyncWatchdog.ps1")

If Not fso.FileExists(ps1) Then
    MsgBox "AeroSync Watchdog script not found:" & vbCrLf & ps1, vbCritical, "Aero Sync - Watchdog"
    WScript.Quit 2
End If

shell.CurrentDirectory = rootDir
cmd = "powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File " & Chr(34) & ps1 & Chr(34)

' WindowStyle 0 = hidden. False = do not wait; launcher exits immediately.
shell.Run cmd, 0, False
