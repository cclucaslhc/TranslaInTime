$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$exePath = Join-Path $projectRoot "dist\TranslaInTime\TranslaInTime.exe"
if (-not (Test-Path $exePath)) {
    throw "Missing exe: $exePath. Run scripts\package_qt.ps1 first."
}

$shortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "TranslaInTime.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $exePath
$shortcut.WorkingDirectory = Split-Path $exePath -Parent
$shortcut.IconLocation = "$exePath,0"
$shortcut.Description = "Launch TranslaInTime"
$shortcut.Save()

Write-Host "Created shortcut: $shortcutPath"
