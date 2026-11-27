# SlideLink: Installs SlideLink for the current user (files, shortcuts, Settings > Apps entry). Options: -Python -NoDesktop -Startup -NoLaunch -Version.
# SlideLink installer (per user, no admin rights needed).
# Copies the app to %LOCALAPPDATA%\Programs\SlideLink and adds Start menu + desktop shortcuts.
# Your presentations and linked devices live in %LOCALAPPDATA%\SlideLink\data and are never touched.
# Options (used by SlideLink-Setup.exe): -Python <python.exe> -NoDesktop -Startup -NoLaunch -Version <x.y>
param([switch]$NoLaunch, [string]$Python = '', [switch]$NoDesktop, [switch]$Startup, [string]$Version = '1.0')
$ErrorActionPreference = 'Stop'
$src  = $PSScriptRoot
$dest = Join-Path $env:LOCALAPPDATA 'Programs\SlideLink'

# Real Python path (not the Windows Store alias), and its windowless twin pythonw.exe
$py  = if ($Python) { $Python } else { (& python -c "import sys; print(sys.executable)").Trim() }
$pyw = Join-Path (Split-Path $py) 'pythonw.exe'
if (-not (Test-Path $pyw)) { throw "pythonw.exe not found next to $py" }

# Stop a running SlideLink tray app so its files can be replaced
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -like '*SlideLink.pyw*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

New-Item -ItemType Directory -Force -Path $dest | Out-Null
$files = 'server.py','core.py','importers.py','devices.py','network.py','decks.py','watch.py','mcp_server.py','SlideLink.pyw','import_claude_deck.py','make_icon.ps1','pdf_pages.ps1','save_doc.py','save_deck.py','updater.py','wsmini.py','e2e.py','e2e_server.py','e2e_session.py','make_cert.ps1','README.md','uninstall.ps1'
foreach ($f in $files) { if (Test-Path (Join-Path $src $f)) { Copy-Item (Join-Path $src $f) $dest -Force } }
Copy-Item (Join-Path $src 'static') $dest -Recurse -Force
Copy-Item (Join-Path $src 'chrome-extension') $dest -Recurse -Force

# Claude Code connector config pointing at the installed copy
@{ mcpServers = @{ slidelink = @{ command = $py; args = @("$dest\mcp_server.py"); env = @{ SLIDELINK_URL = 'http://localhost:8765' } } } } |
  ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 (Join-Path $dest '.mcp.json')

$shell = New-Object -ComObject WScript.Shell
$links = @((Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\SlideLink.lnk'))
$desktopLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'SlideLink.lnk'
if (-not $NoDesktop) { $links += $desktopLnk } elseif (Test-Path $desktopLnk) { Remove-Item $desktopLnk -Force }
foreach ($l in $links) {
  $s = $shell.CreateShortcut($l)
  $s.TargetPath = $pyw
  $s.Arguments = "`"$dest\SlideLink.pyw`""
  $s.WorkingDirectory = $dest
  $s.IconLocation = "$dest\static\icon.ico,0"
  $s.Description = 'SlideLink - presentations with phone remote and Claude'
  $s.Save()
}
# Keep "Start with Windows" pointing at the installed copy if it was switched on
$startupLnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\SlideLink.lnk'
if ($Startup -or (Test-Path $startupLnk)) {
  $s = $shell.CreateShortcut($startupLnk); $s.TargetPath = $pyw
  $s.Arguments = "`"$dest\SlideLink.pyw`" --background"; $s.WorkingDirectory = $dest
  $s.IconLocation = "$dest\static\icon.ico,0"; $s.Save()
}
# An update replaces icon.ico: tell Windows to drop its cached copy, otherwise shortcuts can show a blank icon
try {
  (Get-Item "$dest\static\icon.ico").LastWriteTime = Get-Date
  Start-Process ie4uinit.exe -ArgumentList '-show' -WindowStyle Hidden -Wait -ErrorAction Stop
  Add-Type -Namespace SlideLinkSetup -Name Shell -MemberDefinition '[DllImport("shell32.dll")] public static extern void SHChangeNotify(int e, uint f, IntPtr a, IntPtr b);'
  [SlideLinkSetup.Shell]::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)   # SHCNE_ASSOCCHANGED
} catch { }

# Settings > Apps entry (per user), so SlideLink can be uninstalled like any other app
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\SlideLink'
New-Item -Path $key -Force | Out-Null
$size = [int]((Get-ChildItem $dest -Recurse -File | Measure-Object Length -Sum).Sum / 1KB)
$values = @{ DisplayName = 'SlideLink'; DisplayVersion = $Version; Publisher = 'SlideLink'; InstallLocation = $dest
             DisplayIcon = "$dest\static\icon.ico"
             UninstallString = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$dest\uninstall.ps1`""
             QuietUninstallString = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$dest\uninstall.ps1`" -Quiet" }
foreach ($k in $values.Keys) { New-ItemProperty -Path $key -Name $k -Value $values[$k] -PropertyType String -Force | Out-Null }
New-ItemProperty -Path $key -Name EstimatedSize -Value $size -PropertyType DWord -Force | Out-Null
New-ItemProperty -Path $key -Name NoModify -Value 1 -PropertyType DWord -Force | Out-Null
New-ItemProperty -Path $key -Name NoRepair -Value 1 -PropertyType DWord -Force | Out-Null

Write-Host "SlideLink installed to $dest"
Write-Host ("Shortcuts: Start menu" + $(if ($NoDesktop) { " only." } else { " and desktop." }) + $(if ($Startup) { " Starts with Windows." } else { "" }))
if (-not $NoLaunch) { Start-Process -FilePath $pyw -ArgumentList "`"$dest\SlideLink.pyw`"" -WorkingDirectory $dest }


