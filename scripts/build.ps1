param(
    [Parameter(Mandatory)][ValidateSet('7.4','8.0','8.1')][string]$Series,
    [Parameter(Mandatory)][ValidateSet('nts','ts')][string]$Variant,
    [Parameter(Mandatory)][string]$Work,
    [switch]$SkipInstall,
    [string]$VisualStudioPath
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = Split-Path $PSScriptRoot -Parent
$Work = [IO.Path]::GetFullPath($Work)
if ($Work -match '[%!"&|<>^]') { throw 'Choose a simple work path without cmd metacharacters' }
$id = "php-$Series-$Variant-x64"
$logs = Join-Path $Work 'logs'
$payload = Join-Path $repo "out/$id"
New-Item -ItemType Directory -Force $logs,$payload | Out-Null
Start-Transcript -Path "$logs/driver.log" | Out-Null
try {
    $config = Get-Content "$repo/config/builds.lock.json" -Raw | ConvertFrom-Json
    $source = $config.sources | Where-Object series -EQ $Series
    $toolchain = $config.toolchains.($source.toolchain)
    & python "$PSScriptRoot/inputs.py" --series $Series --work $Work
    if ($LASTEXITCODE -ne 0) { throw 'Input preparation failed' }
    if (-not $SkipInstall) { & "$PSScriptRoot/install-toolchain.ps1" -Series $Series -Work $Work }
    if (-not $VisualStudioPath) { $VisualStudioPath = Join-Path $Work ('vs' + $toolchain.year) }
    if ($VisualStudioPath -match '[%!"&|<>^]') { throw 'Unsafe compiler path' }
    $version = & "$PSScriptRoot/sync-version-header.ps1" -SourceRoot "$Work/php-src"
    if ($version -ne $source.version) { throw "Unexpected configured version: $version" }
    $zts = if ($Variant -eq 'ts') { '--enable-zts' } else { '--disable-zts' }
    $flags = @($config.configure) + @("--with-php-build=$Work\deps", $zts)
    $flagText = ($flags | ForEach-Object { '"' + $_ + '"' }) -join ' '
    $batch = @(
        '@echo off', 'setlocal EnableExtensions',
        ('call "' + $VisualStudioPath + '\VC\Auxiliary\Build\vcvarsall.bat" x64 ' + $config.windowsSdk + ' -vcvars_ver=' + $toolchain.toolset),
        'if errorlevel 1 exit /b 1',
        ('set "PHP_SDK_VS=' + $source.toolchain + '"'), 'set "PHP_SDK_ARCH=x64"', 'set "PHP_SDK_OS_ARCH=x64"',
        ('call "' + $Work + '\sdk\bin\phpsdk_setvars.bat"'), 'if errorlevel 1 exit /b 1',
        ('where cl > "' + $logs + '\compiler.log" 2>&1'),
        ('cl >> "' + $logs + '\compiler.log" 2>&1'),
        ('set VCToolsVersion > "' + $logs + '\toolchain-env.log"'),
        ('set WindowsSDKVersion >> "' + $logs + '\toolchain-env.log"'),
        ('powershell -NoProfile -Command "(Get-Item (Get-Command cl.exe).Source).VersionInfo.FileVersion" > "' + $logs + '\compiler-version.txt"'),
        ('cd /d "' + $Work + '\php-src"'),
        ('call buildconf --force > "' + $logs + '\buildconf.log" 2>&1'), 'if errorlevel 1 exit /b 1',
        ('call configure ' + $flagText + ' > "' + $logs + '\configure.log" 2>&1'), 'if errorlevel 1 exit /b 1',
        ('nmake /NOLOGO > "' + $logs + '\build.log" 2>&1'), 'if errorlevel 1 exit /b 1',
        ('nmake /NOLOGO snap > "' + $logs + '\package.log" 2>&1'), 'exit /b %errorlevel%'
    )
    [IO.File]::WriteAllText("$Work/build.cmd", ($batch -join ([char]13 + [string][char]10)), [Text.ASCIIEncoding]::new())
    Write-Host "Building $id. Native build output is saved under $logs."
    & $env:ComSpec /d /c "$Work/build.cmd"
    if ($LASTEXITCODE -ne 0) {
        Get-ChildItem $logs -Filter '*.log' | ForEach-Object { Write-Host $_.Name; Get-Content $_.FullName -Tail 30 }
        throw 'Native build failed'
    }
    $compiler = (Get-Content "$logs/compiler-version.txt" -Raw).Trim()
    if (-not $compiler.StartsWith($toolchain.compilerPrefix)) { throw "Unexpected compiler: $compiler" }
    $release = if ($Variant -eq 'ts') { 'Release_TS' } else { 'Release' }
    $releaseRoot = "$Work/php-src/x64/$release"
    & "$PSScriptRoot/validate-build.ps1" -Runtime "$releaseRoot/php-$version" -Source "$Work/php-src" -Version $version -Variant $Variant -Output "$logs/validation"
    $archives = @(Get-ChildItem $releaseRoot -Filter 'php-*.zip')
    if ($archives.Count -ne 4) { throw "Expected four packages; got $($archives.Count)" }
    foreach ($archive in $archives) {
        $name = $archive.Name
        if ($name -like 'php-test-pack-*') { $name = "php-test-pack-$version-$Variant-Win32-$($source.toolchain)-x64.zip" }
        Copy-Item -LiteralPath $archive.FullName -Destination (Join-Path $payload $name)
    }
    Copy-Item "$logs/validation/validation.json" "$payload/validation-$id.json"
    Stop-Transcript | Out-Null
    Compress-Archive -Path "$logs/*" -DestinationPath "$payload/logs-$id.zip"
    Copy-Item "$repo/config/dependencies/$Series-x64.json" "$payload/dependencies-$id.json"
    $fileRecords = @(Get-ChildItem $payload -File | Sort-Object Name | ForEach-Object {
        [ordered]@{name=$_.Name;bytes=$_.Length;sha256=(Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
    })
    $manifest = [ordered]@{
        schemaVersion=1; id=$id; series=$Series; version=$version; variant=$Variant; architecture='x64'
        repository=$env:GITHUB_REPOSITORY; buildCommit=$env:GITHUB_SHA; ref=$env:GITHUB_REF
        runId=$env:GITHUB_RUN_ID; runAttempt=$env:GITHUB_RUN_ATTEMPT
        source=$source; sdk=$config.sdk; configure=$flags; compiler=$compiler; windowsSdk=$config.windowsSdk
        runnerImage=$env:ImageOS; runnerImageVersion=$env:ImageVersion
        dependencyLockSha256=(Get-FileHash "$repo/config/dependencies/$Series-x64.json" -Algorithm SHA256).Hash.ToLowerInvariant()
        files=$fileRecords
        testScope='CLI, CGI, extension smoke tests, four selected Zend PHPTs; no full PHP suite or Apache/IIS integration'
    }
    [IO.File]::WriteAllText("$payload/build-$id.json", ($manifest | ConvertTo-Json -Depth 12) + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    & python -c "import sys,zipfile; from pathlib import Path; files=list(Path(sys.argv[1]).glob('*.zip')); assert all(zipfile.ZipFile(p).testzip() is None for p in files)" $payload
    if ($LASTEXITCODE -ne 0) { throw 'Package ZIP integrity check failed' }
    Write-Host "Prepared $payload"
} finally {
    try { Stop-Transcript -ErrorAction SilentlyContinue | Out-Null } catch {}
}
