# SlideLink · EN: Builds every icon size (PNG 1024/512/192, icon.ico 16-256, Chrome extension) from static/icon.svg. Usage: powershell -File make_icon.ps1
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
# favicons for the app window / taskbar (Chrome picks the size that matches the display scaling: 100 % = 32, 125 % = 40, 150 % = 48 ...)
foreach ($n in 256, 128, 96, 64, 48) { Scale $full $n (Join-Path $static "icon-$n.png") }
Scale $tiny 32 (Join-Path $static 'icon-32.png')
foreach ($n in 256, 128, 64, 48) { Scale $full $n (Join-Path $out "ico-$n.png") }
foreach ($n in 32, 24, 16) { Scale $tiny $n (Join-Path $out "ico-$n.png") }
Scale $full 128 (Join-Path $Root 'chrome-extension\icon128.png')
Scale $tiny 48 (Join-Path $Root 'chrome-extension\icon48.png')
Scale $tiny 32 (Join-Path $Root 'chrome-extension\icon32.png')
Scale $tiny 16 (Join-Path $Root 'chrome-extension\icon16.png')

# Full-bleed variants: the launcher / home screen cuts them into its own shape (circle, squircle, ...).
# background = the gradient only; foreground = the motif without the tile, inside the safe zone (center ~60%)
$bgSvg = Join-Path $out 'bg.svg'; $fgSvg = Join-Path $out 'fg.svg'; $maskSvg = Join-Path $out 'maskable.svg'
$s0 = [IO.File]::ReadAllText($svg)
$defs = [Text.RegularExpressions.Regex]::Match($s0, '<defs>[\s\S]*?</defs>').Value
$art = [Text.RegularExpressions.Regex]::Replace([Text.RegularExpressions.Regex]::Match($s0, '</defs>([\s\S]*)</svg>').Groups[1].Value, '<rect x="16" y="16"[^>]*/>', '')
$head = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">'
[IO.File]::WriteAllText($bgSvg, "$head$defs<rect width=`"512`" height=`"512`" fill=`"url(#bg)`"/><rect width=`"512`" height=`"512`" fill=`"url(#shine)`"/></svg>")
[IO.File]::WriteAllText($fgSvg, "$head$defs<g transform=`"translate(256 262) scale(0.58) translate(-256 -246)`">$art</g></svg>")
[IO.File]::WriteAllText($maskSvg, "$head$defs<rect width=`"512`" height=`"512`" fill=`"url(#bg)`"/><rect width=`"512`" height=`"512`" fill=`"url(#shine)`"/><g transform=`"translate(256 262) scale(0.66) translate(-256 -246)`">$art</g></svg>")
Render $bgSvg (Join-Path $out 'bg-1024.png'); Render $fgSvg (Join-Path $out 'fg-1024.png'); Render $maskSvg (Join-Path $out 'mask-1024.png')
Scale (Join-Path $out 'mask-1024.png') 512 (Join-Path $static 'icon-maskable-512.png')

# Android: legacy icon in every density + adaptive icon (Android 8+) from the same layers
$res = Join-Path $Root 'android\icons'
$dens = @{ 'mdpi' = 1; 'hdpi' = 1.5; 'xhdpi' = 2; 'xxhdpi' = 3; 'xxxhdpi' = 4 }
foreach ($d in $dens.Keys) {
  $dir = Join-Path $res "mipmap-$d"; New-Item -ItemType Directory -Force $dir | Out-Null
  Scale $full ([int](48 * $dens[$d])) (Join-Path $dir 'ic_launcher.png')
  Scale (Join-Path $out 'fg-1024.png') ([int](108 * $dens[$d])) (Join-Path $dir 'ic_launcher_fg.png')
  Scale (Join-Path $out 'bg-1024.png') ([int](108 * $dens[$d])) (Join-Path $dir 'ic_launcher_bg.png')
}
$any = Join-Path $res 'mipmap-anydpi-v26'; New-Item -ItemType Directory -Force $any | Out-Null
[IO.File]::WriteAllText((Join-Path $any 'ic_launcher.xml'), @'
<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@mipmap/ic_launcher_bg" />
    <foreground android:drawable="@mipmap/ic_launcher_fg" />
    <monochrome android:drawable="@mipmap/ic_launcher_fg" />
</adaptive-icon>
'@)

# icon.ico with PNG entries 16..256 (Windows Vista+ reads PNG inside ICO)
$sizes = 16, 24, 32, 48, 64, 128, 256
# sizes below 256 as classic 32-bit bitmaps (the taskbar / shell icon loader shows PNG entries only at 256)
function Dib([string]$png) {
  $b = [Drawing.Bitmap]::FromFile($png); $n = $b.Width
  $m = New-Object IO.MemoryStream; $bw = New-Object IO.BinaryWriter $m
  $bw.Write([UInt32]40); $bw.Write([Int32]$n); $bw.Write([Int32]($n * 2)); $bw.Write([UInt16]1); $bw.Write([UInt16]32)
  $bw.Write([UInt32]0); $bw.Write([UInt32]0); $bw.Write([Int32]0); $bw.Write([Int32]0); $bw.Write([UInt32]0); $bw.Write([UInt32]0)
  for ($y = $n - 1; $y -ge 0; $y--) { for ($x = 0; $x -lt $n; $x++) { $c = $b.GetPixel($x, $y); $bw.Write([byte]$c.B); $bw.Write([byte]$c.G); $bw.Write([byte]$c.R); $bw.Write([byte]$c.A) } }
  $bw.Write((New-Object byte[] ([Math]::Ceiling($n / 32) * 4 * $n)))   # AND mask (unused with alpha)
  $b.Dispose(); , $m.ToArray()
}
$pngs = $sizes | ForEach-Object { $f = Join-Path $out "ico-$_.png"; if ($_ -ge 256) { , [IO.File]::ReadAllBytes($f) } else { Dib $f } }
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
