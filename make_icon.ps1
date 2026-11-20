# SlideLink · EN: Builds every icon size (PNG 1024/512/192, icon.ico 16-256, Chrome extension) from static/icon.svg. Usage: powershell -File make_icon.ps1
# SlideLink · DE: Erzeugt alle Symbolgrößen (PNG 1024/512/192, icon.ico 16-256, Chrome-Erweiterung) aus static/icon.svg. Aufruf: powershell -File make_icon.ps1
# Renders the SlideLink logo (static/icon.svg) at 1024 px with headless Chrome and scales it down with high quality.
# Tiny sizes (<= 32 px) use a simplified version (no thin lines), so they stay sharp in the tray and browser tabs.
param([string]$Root = $PSScriptRoot)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$out = Join-Path $env:TEMP 'sl-logo'; New-Item -ItemType Directory -Force $out | Out-Null
$chrome = "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
if (-not (Test-Path $chrome)) { $chrome = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe" }

function Render([string]$svgPath, [string]$png) {
  $html = Join-Path $out 'r.html'
  [IO.File]::WriteAllText($html, "<!doctype html><html><body style='margin:0;background:transparent'><img src='file:///$($svgPath.Replace('\','/'))' style='width:100vw;height:100vh;display:block'></body></html>")
  & $chrome --headless=new --disable-gpu --hide-scrollbars --default-background-color=00000000 "--screenshot=$png" --window-size=1024,1024 "file:///$($html.Replace('\','/'))" 2>$null
  Start-Sleep -Milliseconds 800
}
function Scale([string]$src, [int]$size, [string]$dst) {
  $img = [Drawing.Image]::FromFile($src)
  $bmp = New-Object Drawing.Bitmap $size, $size, ([Drawing.Imaging.PixelFormat]::Format32bppArgb)
  $g = [Drawing.Graphics]::FromImage($bmp)
  $g.InterpolationMode = 'HighQualityBicubic'; $g.SmoothingMode = 'HighQuality'; $g.PixelOffsetMode = 'HighQuality'; $g.CompositingQuality = 'HighQuality'
  $g.Clear([Drawing.Color]::Transparent)
  $g.DrawImage($img, 0, 0, $size, $size)
  $bmp.Save($dst, [Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose(); $bmp.Dispose(); $img.Dispose()
}

$svg = (Resolve-Path (Join-Path $Root 'static\icon.svg')).Path
$small = Join-Path $out 'icon-small.svg'
# simplified tile for tiny sizes: no text lines, no signal arcs, thicker phone outline
$s = [IO.File]::ReadAllText($svg)
$s = [Text.RegularExpressions.Regex]::Replace($s, '<rect x="104"[^>]*/>', '')
$s = [Text.RegularExpressions.Regex]::Replace($s, '<path d="M 396[^>]*/>', '')
$s = $s.Replace('<rect x="352" y="226" width="36" height="8" rx="4" fill="#3a3d52"/>', '').Replace('<rect x="340" y="396" width="60" height="10" rx="5" fill="#3a3d52"/>', '')
$s = $s.Replace('stroke-width="10"/>', 'stroke-width="18"/>')
[IO.File]::WriteAllText($small, $s)

Render $svg (Join-Path $out 'full-1024.png')
Render $small (Join-Path $out 'small-1024.png')
$full = Join-Path $out 'full-1024.png'; $tiny = Join-Path $out 'small-1024.png'
$static = Join-Path $Root 'static'
Copy-Item $full (Join-Path $static 'icon-1024.png') -Force
Scale $full 512 (Join-Path $static 'icon-512.png')
Scale $full 192 (Join-Path $static 'icon-192.png')
foreach ($n in 256, 128, 64, 48) { Scale $full $n (Join-Path $out "ico-$n.png") }
foreach ($n in 32, 24, 16) { Scale $tiny $n (Join-Path $out "ico-$n.png") }
Scale $full 128 (Join-Path $Root 'chrome-extension\icon128.png')
Scale $tiny 48 (Join-Path $Root 'chrome-extension\icon48.png')

# icon.ico with PNG entries 16..256 (Windows Vista+ reads PNG inside ICO)
$sizes = 16, 24, 32, 48, 64, 128, 256
$pngs = $sizes | ForEach-Object { [IO.File]::ReadAllBytes((Join-Path $out "ico-$_.png")) }
$ms = New-Object IO.MemoryStream; $w = New-Object IO.BinaryWriter $ms
$w.Write([UInt16]0); $w.Write([UInt16]1); $w.Write([UInt16]$sizes.Count)
$offset = 6 + 16 * $sizes.Count
for ($i = 0; $i -lt $sizes.Count; $i++) {
  $sz = $sizes[$i]; $len = $pngs[$i].Length
  $w.Write([byte]($(if ($sz -ge 256) { 0 } else { $sz }))); $w.Write([byte]($(if ($sz -ge 256) { 0 } else { $sz })))
  $w.Write([byte]0); $w.Write([byte]0); $w.Write([UInt16]1); $w.Write([UInt16]32); $w.Write([UInt32]$len); $w.Write([UInt32]$offset)
  $offset += $len
}
foreach ($p in $pngs) { $w.Write($p) }
[IO.File]::WriteAllBytes((Join-Path $static 'icon.ico'), $ms.ToArray())
"icons written: icon-1024/512/192.png, icon.ico (16-256), chrome-extension icons"
