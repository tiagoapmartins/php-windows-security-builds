param(
    [Parameter(Mandatory)][ValidateSet('7.4','8.0','8.1')][string]$Series,
    [Parameter(Mandatory)][string]$Work
)
$ErrorActionPreference = 'Stop'
$config = Get-Content "$PSScriptRoot/../config/builds.lock.json" -Raw | ConvertFrom-Json
$source = $config.sources | Where-Object series -EQ $Series
$toolchain = $config.toolchains.($source.toolchain)
$installer = Join-Path $Work 'downloads/vs_buildtools.exe'
if ((Get-FileHash $installer -Algorithm SHA256).Hash.ToLowerInvariant() -ne $toolchain.sha256) {
    throw 'Installer checksum mismatch'
}
$signature = Get-AuthenticodeSignature -LiteralPath $installer
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation,') {
    throw 'Installer must have a valid Microsoft Authenticode signature'
}
$vsPath = Join-Path $Work ('vs' + $toolchain.year)
$arguments = @('--quiet','--wait','--norestart','--nocache','--installPath',('"' + $vsPath + '"'),
    '--add','Microsoft.VisualStudio.Workload.VCTools','--includeRecommended')
$process = Start-Process -FilePath $installer -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode -notin @(0,3010)) { throw "VS installer failed: $($process.ExitCode)" }
if (-not (Test-Path "$vsPath/VC/Auxiliary/Build/vcvarsall.bat")) { throw 'Compiler setup script missing' }
$programFiles = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
if (-not (Test-Path "$programFiles/Windows Kits/10/Include/$($config.windowsSdk)/um/Windows.h")) {
    throw "Required Windows SDK $($config.windowsSdk) is missing"
}

$compilers = @(Get-ChildItem "$vsPath/VC/Tools/MSVC" -Directory -ErrorAction SilentlyContinue |
    Where-Object Name -Like ($toolchain.toolset + '*') |
    ForEach-Object { Join-Path $_.FullName 'bin/Hostx64/x64/cl.exe' } |
    Where-Object { Test-Path -LiteralPath $_ })
if (-not $compilers.Count) { throw "The installer did not supply the required $($toolchain.toolset) x64 compiler" }
foreach ($compiler in $compilers) { Write-Host "Installed compiler: $compiler ($((Get-Item $compiler).VersionInfo.FileVersion))" }
