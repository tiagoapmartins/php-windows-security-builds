param(
    [Parameter(Mandatory)][string]$Runtime,
    [Parameter(Mandatory)][string]$Source,
    [Parameter(Mandatory)][string]$Version,
    [Parameter(Mandatory)][ValidateSet('nts','ts')][string]$Variant,
    [Parameter(Mandatory)][string]$Output
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force "$Output/snmp/mib_indexes","$Output/empty-ini" | Out-Null
$env:SNMP_PERSISTENT_DIR = "$Output/snmp"
$env:SNMPCONFPATH = "$Output/snmp"
$env:MIBS = ';'
$env:PHP_INI_SCAN_DIR = "$Output/empty-ini"
if ($Variant -eq 'ts' -and -not (Test-Path "$Runtime/php$($Version.Split('.')[0])apache2_4.dll")) {
    throw 'Missing Apache TS module'
}
$phpArguments = @('-n','-d',"extension_dir=$Runtime/ext",'-d','date.timezone=UTC')
$extensions = Get-ChildItem "$Runtime/ext" -Filter 'php_*.dll' |
    Where-Object Name -NotIn @('php_opcache.dll','php_dl_test.dll','php_zend_test.dll','php_pdo_firebird.dll') |
    Sort-Object @{Expression={if ($_.Name -eq 'php_mbstring.dll') {0} elseif ($_.Name -eq 'php_exif.dll') {2} else {1}}},Name
foreach ($extension in $extensions) { $phpArguments += @('-d',"extension=$($extension.Name)") }
$phpArguments += @('-d',"zend_extension=$Runtime/ext/php_opcache.dll","$PSScriptRoot/smoke.php")
$smoke = & "$Runtime/php.exe" @phpArguments 2>&1
$exitCode = $LASTEXITCODE
$smoke | Set-Content "$Output/smoke.log" -Encoding utf8
if ($exitCode -ne 0) { throw 'CLI smoke test failed' }
$result = ($smoke -join [Environment]::NewLine) | ConvertFrom-Json
$numeric = $Version.Split('.')
$versionId = [int]$numeric[0]*10000 + [int]$numeric[1]*100 + [int]$numeric[2]
if ($result.version -ne $Version -or $result.versionId -ne $versionId -or
    $result.zts -ne ($Variant -eq 'ts') -or -not $result.x64) { throw 'Runtime identity mismatch' }
& "$Runtime/php-cgi.exe" -n -v > "$Output/cgi.log" 2>&1
if ($LASTEXITCODE -ne 0) { throw 'CGI startup failed' }
$tests = @('bug36568.phpt','bug40261.phpt','bug40770.phpt','bug43128.phpt') | ForEach-Object { "$Source/Zend/tests/$_" }
foreach ($test in $tests) { if (-not (Test-Path $test)) { throw "Missing regression test $test" } }
$env:TEST_PHP_EXECUTABLE = "$Runtime/php.exe"
$env:NO_INTERACTION = '1'
$env:REPORT_EXIT_STATUS = '1'
& "$Runtime/php.exe" -n "$Source/run-tests.php" -q -p "$Runtime/php.exe" @tests > "$Output/phpt.log" 2>&1
if ($LASTEXITCODE -ne 0) { throw 'Selected Zend regression tests failed' }
$result | Add-Member NoteProperty phpExeSha256 (Get-FileHash "$Runtime/php.exe" -Algorithm SHA256).Hash.ToLowerInvariant()
$result | Add-Member NoteProperty nativeFileVersion (Get-Item "$Runtime/php.exe").VersionInfo.FileVersion
$result | Add-Member NoteProperty selectedPhptTests $tests.Count
$result | Add-Member NoteProperty excludedExtensions @('php_dl_test.dll','php_zend_test.dll','php_pdo_firebird.dll (requires an external Firebird client)')
[IO.File]::WriteAllText("$Output/validation.json", ($result | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
Write-Host "Validated PHP $Version $Variant x64"
