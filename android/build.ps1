# SlideLink · EN: Builds and signs SlideLink.apk without Gradle and publishes it for in-app updates.
# SlideLink · DE: Baut und signiert SlideLink.apk ohne Gradle und stellt sie für Updates in der App bereit.
# Builds SlideLink.apk (Android remote) without Gradle: aapt2 -> javac -> d8 -> zipalign -> apksigner.
param([string]$Sdk = (Join-Path (Split-Path $PSScriptRoot) 'android-sdk'))
$ErrorActionPreference = 'Continue'   # native tools print notes on stderr; exit codes are checked instead
$env:JAVA_HOME = 'C:\Program Files\Eclipse Adoptium\jdk-21.0.13.9-hotspot'
$jbin = Join-Path $env:JAVA_HOME 'bin'
$bt = Join-Path $Sdk 'build-tools\35.0.0'
$jar = Join-Path $Sdk 'platforms\android-35\android.jar'
$here = $PSScriptRoot
$out = Join-Path $here 'build'
Remove-Item $out -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$out\res\mipmap-xxxhdpi", "$out\gen", "$out\classes", "$out\dex" | Out-Null

# 1. (no PC addresses are built in: the app learns them from SlideLink at runtime, so the APK is the same for everyone)

# 2. resources + manifest
Copy-Item (Join-Path (Split-Path $here) 'static\icon-192.png') "$out\res\mipmap-xxxhdpi\ic_launcher.png"
& "$bt\aapt2.exe" compile --dir "$out\res" -o "$out\res.zip"; if ($LASTEXITCODE) { throw 'aapt2 compile failed' }
& "$bt\aapt2.exe" link -I $jar --manifest "$here\AndroidManifest.xml" -o "$out\base.apk" --java "$out\gen" "$out\res.zip" --min-sdk-version 26 --target-sdk-version 35
if ($LASTEXITCODE) { throw 'aapt2 link failed' }

# 3. compile + dex
$src = Get-ChildItem "$here\src" -Recurse -Filter *.java | ForEach-Object FullName
& "$jbin\javac.exe" -nowarn -Xlint:none --release 11 -classpath $jar -d "$out\classes" @src 2>&1 | Where-Object { $_ -notmatch 'warning|^Note:' }
if ($LASTEXITCODE) { throw 'javac failed' }
$classes = Get-ChildItem "$out\classes" -Recurse -Filter *.class | ForEach-Object FullName
& "$bt\d8.bat" --release --min-api 26 --lib $jar --output "$out\dex" @classes; if ($LASTEXITCODE) { throw 'd8 failed' }

# 4. package, align, sign (the key is kept so updates install over the old app)
& "$env:LOCALAPPDATA\Python\pythoncore-3.15-64\python.exe" -I -c "import zipfile,sys; z=zipfile.ZipFile(sys.argv[1],'a'); z.write(sys.argv[2],'classes.dex'); z.close()" "$out\base.apk" "$out\dex\classes.dex"
if ($LASTEXITCODE) { throw 'adding classes.dex failed' }
& "$bt\zipalign.exe" -f -p 4 "$out\base.apk" "$out\aligned.apk"; if ($LASTEXITCODE) { throw 'zipalign failed' }
$ks = Join-Path $here 'slidelink.keystore'
if (-not (Test-Path $ks)) {
    & "$jbin\keytool.exe" -genkeypair -keystore $ks -storepass slidelink -keypass slidelink -alias slidelink -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=SlideLink" 2>&1 | Out-Null
}
& "$bt\apksigner.bat" sign --ks $ks --ks-pass pass:slidelink --key-pass pass:slidelink --out "$here\SlideLink.apk" "$out\aligned.apk"
if ($LASTEXITCODE) { throw 'apksigner failed' }
& "$bt\apksigner.bat" verify "$here\SlideLink.apk"; if ($LASTEXITCODE) { throw 'verify failed' }
"built: $here\SlideLink.apk ($([math]::Round((Get-Item "$here\SlideLink.apk").Length/1KB)) KB)"


Copy-Item "$here\SlideLink.apk" (Join-Path (Split-Path $here) 'static\SlideLink.apk') -Force
# the app checks this file to offer updates
$mf = [xml](Get-Content "$here\AndroidManifest.xml" -Raw)
$ns = 'http://schemas.android.com/apk/res/android'
$ver = [ordered]@{ versionCode = [int]$mf.manifest.GetAttribute('versionCode', $ns); versionName = $mf.manifest.GetAttribute('versionName', $ns);
                  url = '/SlideLink.apk'; notes = $(if (Test-Path "$here\release-notes.txt") { (Get-Content "$here\release-notes.txt" -Raw).Trim() } else { '' }) }
[IO.File]::WriteAllText((Join-Path (Split-Path $here) 'static\app-version.json'), ($ver | ConvertTo-Json -Compress))
"version file: $($ver.versionName) ($($ver.versionCode))"   # served by SlideLink at /SlideLink.apk
