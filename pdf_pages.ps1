# SlideLink · EN: Renders each PDF page to a PNG with Windows' built-in PDF engine (Windows.Data.Pdf).
# SlideLink · DE: Rendert jede PDF-Seite als PNG mit der in Windows eingebauten PDF-Engine (Windows.Data.Pdf).
# Renders every page of a PDF to page-001.png, page-002.png, ... with Windows' built-in PDF engine (Windows.Data.Pdf).
# Usage: powershell -File pdf_pages.ps1 <file.pdf> <output folder> [width in px]
param([Parameter(Mandatory)][string]$Pdf, [Parameter(Mandatory)][string]$Out, [int]$Width = 1600)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]

$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
$asAction = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' } | Select-Object -First 1
function Wait-Op($op, [Type]$type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(); $t.Result }
function Wait-Action($op) { $asAction.Invoke($null, @($op)).Wait() }

New-Item -ItemType Directory -Force $Out | Out-Null
$file = Wait-Op ([Windows.Storage.StorageFile]::GetFileFromPathAsync((Resolve-Path $Pdf).Path)) ([Windows.Storage.StorageFile])
$doc = Wait-Op ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($file)) ([Windows.Data.Pdf.PdfDocument])
for ($i = 0; $i -lt $doc.PageCount; $i++) {
    $page = $doc.GetPage($i)
    $opts = New-Object Windows.Data.Pdf.PdfPageRenderOptions
    $opts.DestinationWidth = $Width
    $opts.DestinationHeight = [uint32]([math]::Round($Width * $page.Size.Height / $page.Size.Width))
    $mem = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
    Wait-Action ($page.RenderToStreamAsync($mem, $opts))
    $net = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($mem.GetInputStreamAt(0))
    $fs = [IO.File]::Create((Join-Path $Out ('page-{0:000}.png' -f ($i + 1))))
    $net.CopyTo($fs); $fs.Close(); $net.Dispose(); $mem.Dispose(); $page.Dispose()
}
"pages: $($doc.PageCount)"
