# SlideLink: Creates this PC's own HTTPS certificate for the home Wi-Fi (cert.pem + key.pem). The phone app trusts exactly this one.
# Usage: powershell -File make_cert.ps1 -Out <folder>
param([Parameter(Mandatory = $true)][string]$Out)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force $Out | Out-Null

function Len([int]$n) {
  if ($n -lt 128) { return , [byte[]]@($n) }
  $b = [BitConverter]::GetBytes($n); [Array]::Reverse($b)
  $i = 0; while ($b[$i] -eq 0) { $i++ }
  $b = [byte[]]$b[$i..($b.Count - 1)]
  return , [byte[]](@([byte](0x80 -bor $b.Count)) + $b)
}
function Int([byte[]]$v) {
  $i = 0; while ($i -lt $v.Count - 1 -and $v[$i] -eq 0) { $i++ }
  $v = $v[$i..($v.Count - 1)]
  if ($v[0] -ge 0x80) { $v = @([byte]0) + $v }
  return , [byte[]](@([byte]0x02) + (Len $v.Count) + $v)
}
function Seq([byte[]]$content) { return , [byte[]](@([byte]0x30) + (Len $content.Count) + $content) }
function Pem([string]$label, [byte[]]$der) {
  $b64 = [Convert]::ToBase64String($der) -replace '(.{64})', "`$1`n"
  return "-----BEGIN $label-----`n$($b64.TrimEnd())`n-----END $label-----`n"
}

$rsa = New-Object System.Security.Cryptography.RSACryptoServiceProvider 2048
$req = New-Object System.Security.Cryptography.X509Certificates.CertificateRequest('CN=SlideLink', $rsa,
  [System.Security.Cryptography.HashAlgorithmName]::SHA256, [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)
$san = New-Object System.Security.Cryptography.X509Certificates.SubjectAlternativeNameBuilder
$san.AddDnsName('localhost'); $san.AddDnsName("$($env:COMPUTERNAME.ToLower()).local")
$req.CertificateExtensions.Add($san.Build())
$cert = $req.CreateSelfSigned([DateTimeOffset]::Now.AddDays(-1), [DateTimeOffset]::Now.AddYears(10))

$p = $rsa.ExportParameters($true)
$body = (Int @([byte]0)) + (Int $p.Modulus) + (Int $p.Exponent) + (Int $p.D) + (Int $p.P) + (Int $p.Q) + (Int $p.DP) + (Int $p.DQ) + (Int $p.InverseQ)
$key = Seq ([byte[]]$body)
[IO.File]::WriteAllText((Join-Path $Out 'key.pem'), (Pem 'RSA PRIVATE KEY' $key))
[IO.File]::WriteAllText((Join-Path $Out 'cert.pem'), (Pem 'CERTIFICATE' $cert.RawData))
'ok'
