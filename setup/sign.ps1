# SlideLink · EN: Signs release files for the auto-updater (RSA-3072, SHA-256, PKCS#1 v1.5). Writes <file>.sig next to each file.
#             The private key stays on this PC (%USERPROFILE%\.slidelink-signing), never in the repo or on GitHub.
#             -NewKey creates the key once and writes the public key to setup\update-key.pub (put into updater.py).
# SlideLink · DE: Signiert Release-Dateien für den Auto-Updater (RSA-3072, SHA-256, PKCS#1 v1.5). Schreibt <Datei>.sig daneben.
#             Der private Schlüssel bleibt auf diesem PC (%USERPROFILE%\.slidelink-signing), nie im Repo oder auf GitHub.
param([string[]]$Files = @(), [string]$Dir = '', [switch]$NewKey)
if ($Dir) { $Files += Get-ChildItem $Dir -File | Where-Object Extension -ne '.sig' | ForEach-Object FullName }
$Files = @($Files | ForEach-Object { $_ -split ',' } | Where-Object { $_ })
$ErrorActionPreference = 'Stop'
$keyDir = Join-Path $env:USERPROFILE '.slidelink-signing'
$keyFile = Join-Path $keyDir 'update-key.xml'

if ($NewKey) {
    if (Test-Path $keyFile) { throw "key exists already: $keyFile (delete it yourself only if you really want a new one)" }
    New-Item -ItemType Directory -Force $keyDir | Out-Null
    $rsa = New-Object System.Security.Cryptography.RSACryptoServiceProvider 3072
    [IO.File]::WriteAllText($keyFile, $rsa.ToXmlString($true))
    $p = $rsa.ExportParameters($false)
    $hex = ($p.Modulus | ForEach-Object { $_.ToString('x2') }) -join ''
    $e = [Convert]::ToInt32((($p.Exponent | ForEach-Object { $_.ToString('x2') }) -join ''), 16)
    Set-Content -Encoding ascii (Join-Path $PSScriptRoot 'update-key.pub') "$e`n$hex"
    "new key: $keyFile"
    "public key: $(Join-Path $PSScriptRoot 'update-key.pub')"
}

if ($Files.Count) {
    if (-not (Test-Path $keyFile)) { throw "no signing key at $keyFile - run with -NewKey once" }
    $rsa = New-Object System.Security.Cryptography.RSACryptoServiceProvider
    $rsa.FromXmlString([IO.File]::ReadAllText($keyFile))
    foreach ($f in $Files) {
        $f = (Resolve-Path $f).Path
        $sig = $rsa.SignData([IO.File]::ReadAllBytes($f), 'SHA256')
        [IO.File]::WriteAllText("$f.sig", [Convert]::ToBase64String($sig))
        "signed: $f"
    }
}
