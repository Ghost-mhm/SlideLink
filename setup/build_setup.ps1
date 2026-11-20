# SlideLink · EN: Builds dist/SlideLink-Setup.exe from the current app files with the C# compiler built into Windows.
# SlideLink · DE: Baut dist/SlideLink-Setup.exe aus den aktuellen App-Dateien mit dem in Windows enthaltenen C#-Compiler.
# Builds dist\SlideLink-Setup.exe from the current app files (uses the C# compiler that comes with Windows).
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot
$out = Join-Path $PSScriptRoot 'build'
Remove-Item $out -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$out\payload", "$root\dist" | Out-Null
$files = 'server.py','mcp_server.py','SlideLink.pyw','import_claude_deck.py','make_icon.py','pdf_pages.ps1',
         'save_doc.py','save_deck.py','README.md','install.ps1','uninstall.ps1'
foreach ($f in $files) { Copy-Item (Join-Path $root $f) "$out\payload\" }
Copy-Item (Join-Path $root 'static'), (Join-Path $root 'chrome-extension') "$out\payload\" -Recurse
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::CreateFromDirectory("$out\payload", "$out\payload.zip")
$fw = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319"
& "$fw\csc.exe" /nologo /target:winexe /optimize "/out:$root\dist\SlideLink-Setup.exe" "/win32icon:$root\static\icon.ico" `
  "/resource:$out\payload.zip,payload.zip" /r:System.Windows.Forms.dll /r:System.Drawing.dll `
  "/r:$fw\System.IO.Compression.dll" "/r:$fw\System.IO.Compression.FileSystem.dll" "$PSScriptRoot\Setup.cs"
if ($LASTEXITCODE) { throw "compile failed" }
"built: $root\dist\SlideLink-Setup.exe ($([math]::Round((Get-Item "$root\dist\SlideLink-Setup.exe").Length / 1KB)) KB)"
