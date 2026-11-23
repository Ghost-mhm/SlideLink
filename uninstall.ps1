# SlideLink · EN: Removes SlideLink (app, shortcuts, Apps entry); presentations stay unless -RemoveData.
# Removes the SlideLink app, its shortcuts and its Settings > Apps entry. Your presentations and linked
# devices stay in %LOCALAPPDATA%\SlideLink unless you pass -RemoveData. -Quiet: no message at the end.
param([switch]$RemoveData, [switch]$Quiet)
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -like '*SlideLink.pyw*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
$paths = @(
  (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\SlideLink.lnk'),
  (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\SlideLink.lnk'),
  (Join-Path ([Environment]::GetFolderPath('Desktop')) 'SlideLink.lnk'),
  (Join-Path $env:LOCALAPPDATA 'Programs\SlideLink')
)
if ($RemoveData) { $paths += (Join-Path $env:LOCALAPPDATA 'SlideLink') }
foreach ($p in $paths) { if (Test-Path $p) { Remove-Item $p -Recurse -Force -ErrorAction SilentlyContinue } }
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\SlideLink' -Recurse -Force -ErrorAction SilentlyContinue
$msg = 'SlideLink was removed.' + $(if (-not $RemoveData) { "`nYour presentations and linked devices were kept, so reinstalling brings everything back." } else { '' })
if ($Quiet) { Write-Host $msg }
else {
  Add-Type -AssemblyName System.Windows.Forms
  [void][System.Windows.Forms.MessageBox]::Show($msg, 'SlideLink', 'OK', 'Information')
}
