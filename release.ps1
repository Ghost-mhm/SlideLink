# SlideLink: One command for a release: version, build, sign, check, commit, tag, push, GitHub release, live check.
#   .\release.ps1 -Version 1.3.4 -Notes notes.md            stable (all PCs update automatically)
#   .\release.ps1 -Version 1.4.0 -Notes notes.md -Beta      pre-release (only for people who download it)
#   add -BuildApp to rebuild the Android app first (bump its version in android\AndroidManifest.xml before)
#   add -DryRun to stop after building, signing and checking (nothing is committed or uploaded)
#   Run it in a normal terminal: it needs the signing key in %USERPROFILE%\.slidelink-signing.
param(
    [Parameter(Mandatory = $true)][ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version,
    [string]$Notes = '',
    [switch]$Beta,
    [switch]$BuildApp,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location $root
$repo = 'Ghost-mhm/SlideLink'
$tag = "v$Version" + $(if ($Beta) { '-beta' } else { '' })
$utf8 = New-Object System.Text.UTF8Encoding($false)
function Step($t) { Write-Host "`n== $t" -ForegroundColor Cyan }
function Edit-File($path, $pattern, $replacement) {
    $full = Join-Path $root $path
    $text = [IO.File]::ReadAllText($full, $utf8)
    $new = [regex]::Replace($text, $pattern, $replacement)
    if ($new -eq $text -and -not ($text -match [regex]::Escape($replacement))) { throw "version not found in $path" }
    [IO.File]::WriteAllText($full, $new, $utf8)
}

Step 'Checks'
if (-not $DryRun) {
    if (git status --porcelain) { throw 'working tree is not clean: commit or stash first' }
    if ((git rev-parse --abbrev-ref HEAD) -ne 'beta') { throw 'run releases from the beta branch' }
    if (-not $Notes -or -not (Test-Path $Notes)) { throw 'give the release notes as a file: -Notes notes.md' }
    if (git tag --list $tag) { throw "tag $tag exists already" }
}
if (-not (Test-Path "$env:USERPROFILE\.slidelink-signing\update-key.xml")) { throw 'no signing key on this PC (see setup\sign.ps1)' }

Step 'Translations'
$node = (Get-Command node -ErrorAction SilentlyContinue).Source
$nodeDefault = Join-Path $env:ProgramFiles 'nodejs\node.exe'
if (-not $node -and (Test-Path $nodeDefault)) { $node = $nodeDefault }
if ($node) { & $node "$root\setup\check_i18n.js" --strict; if ($LASTEXITCODE) { throw 'texts without German translation (see above)' } }
else { Write-Host 'Node.js not found: translation check skipped' -ForegroundColor Yellow }

Step "Version $Version"
$label = $Version + $(if ($Beta) { ' beta' } else { '' })
Edit-File 'setup\Setup.cs' 'AssemblyVersion\("[\d.]+"\)' "AssemblyVersion(`"$Version.0`")"
Edit-File 'setup\Setup.cs' 'AssemblyFileVersion\("[\d.]+"\)' "AssemblyFileVersion(`"$Version.0`")"
Edit-File 'setup\Setup.cs' 'Version = "[^"]+"' "Version = `"$label`""
Edit-File 'server.py' 'server_version = "SlideLink/[^"]+"' "server_version = `"SlideLink/$Version`""

if ($BuildApp) { Step 'Android app'; & "$root\android\build.ps1" }
$app = Get-Content "$root\static\app-version.json" -Raw | ConvertFrom-Json
if (-not (Test-Path "$root\static\SlideLink.apk")) { throw 'static\SlideLink.apk missing: run with -BuildApp' }

Step 'Setup'
& "$root\setup\build_setup.ps1"

Step 'Release files'
$out = Join-Path $root "dist\$tag"
Remove-Item $out -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $out | Out-Null
Copy-Item "$root\dist\SlideLink-Setup.exe" "$out\SlideLink-Setup-$($Version)$(if ($Beta) { '-beta' }).exe"
Copy-Item "$root\static\SlideLink.apk" "$out\SlideLink-$($app.versionName).apk"
Copy-Item "$root\static\app-version.json" "$out\app-version.json"

Step 'Sign'
& "$root\setup\sign.ps1" -Dir $out

Step 'Check signatures'
python "$root\setup\check_release.py" files $out
if ($LASTEXITCODE) { throw 'signature check failed' }

if ($DryRun) { Step "Dry run done: $out (version files changed locally, nothing committed)"; return }

Step 'Commit, tag, push'
git add -A
git commit -q -m "Release $label`n`nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git tag $tag
if (-not $Beta) {
    git checkout -q main
    git merge -q --ff-only beta
    git push -q origin main
    git checkout -q beta
}
git push -q origin beta $tag
git archive --format=zip -o "$out\SlideLink-$Version-source.zip" $tag

Step 'GitHub release'
$flag = if ($Beta) { '--prerelease' } else { '--latest' }
gh release create $tag --repo $repo --title "SlideLink $label" --notes-file $Notes $flag (Get-ChildItem $out -File | ForEach-Object FullName)
if ($LASTEXITCODE) { throw 'gh release failed' }

Step 'Live check'
python "$root\setup\check_release.py" live $tag
if ($LASTEXITCODE) { throw 'live check failed' }
Step "Released $tag"
