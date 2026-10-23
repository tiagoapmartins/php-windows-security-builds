# PHP Windows security builds

Unofficial Windows x64 builds of the PHP project's branches for CVE-2026-109618 and CVE-2026-109619.

| PHP runtime version | Toolchain | Packages |
| --- | --- | --- |
| 7.4.33 | Visual Studio 2017 / VC15 | NTS and TS |
| 8.0.30 | Visual Studio 2019 / VS16 | NTS and TS |
| 8.1.34 | Visual Studio 2019 / VS16 | NTS and TS |

This repository contains build recipes, rather than a fork of php-src. One main branch manages all three PHP versions. A tag such as security-2026-10-r1 identifies the complete recipe used for a release.

These are community builds of end-of-life PHP versions, not official PHP releases or a promise of continued support. They include the selected upstream PHP fixes. The historical third-party dependencies are inventoried and are **not comprehensively updated or audited** by this project.

## Downloads and verification

Published packages will appear in [GitHub Releases](https://github.com/tiagoapmartins/php-windows-security-builds/releases). Each release shows the full SHA-256 alongside each download and includes SHA256SUMS.txt.

On Windows, compare the downloaded file's hash with the release:

    Get-FileHash .\php-7.4.33-nts-Win32-vc15-x64.zip -Algorithm SHA256

Release builds also include GitHub artifact attestation bundles. With a recent GitHub CLI:

    gh attestation verify .\php-7.4.33-nts-Win32-vc15-x64.zip --repo tiagoapmartins/php-windows-security-builds --signer-workflow tiagoapmartins/php-windows-security-builds/.github/workflows/build.yml --source-ref refs/tags/security-2026-10-r1 --source-digest RECIPE_COMMIT

Replace the tag and RECIPE_COMMIT with those recorded in the release manifest. An attestation identifies the producing workflow and bytes; it does not establish that the source or resulting software is free of vulnerabilities. PHP binaries are not newly Authenticode-signed by this project.

Use NTS for typical IIS/FastCGI deployments and TS when an Apache module is required. Install the matching Microsoft Visual C++ runtime. PDO Firebird also needs a separately supplied Firebird client. Debug, development and test packages are provided separately from runtime ZIPs.

## Traceability

[config/builds.lock.json](config/builds.lock.json) pins upstream PHP and PHP SDK commits, compiler installer checksums, configure flags, and the Windows SDK version. Each dependency archive is locked by URL, byte count and SHA-256 in config/dependencies. Acquisition checks these values before extraction and never calls the SDK's moving dependency-update command.

PHP 7.4 receives one explicit version metadata patch. Its configure.ac and generated main/php_version.h both report 7.4.33 / PHP_VERSION_ID 70433. The patch adds no security code; the security changes come from the pinned upstream branch. Before building, the header is synchronized from configure.ac for each series.

The SDK is **php-sdk-binary-tools**, tag php-sdk-2.8.4. That is the SDK's version, unrelated to PHP's runtime version.

Release tags carry the distribution revision. The runtime version remains 7.4.33, 8.0.30 or 8.1.34 to preserve ordinary version comparisons. Software checking PHP_VERSION alone cannot distinguish these builds from the corresponding official versions; use the release tag and SHA-256 for that.

The Windows runner image and Visual Studio installer component feeds are maintained externally. The installer binary is hash pinned; the exact installed compiler version and image version are recorded in each manifest and native logs. This is a traceable build recipe, **not a guarantee of byte-for-byte reproducibility**.

## Build and test

The process follows [PHP's Windows SDK build guide](https://wiki.php.net/internals/windows/stepbystepbuild_sdk_2), using the native VS2017/VS2019 compilers, the official SDK, buildconf, configure, nmake, and nmake snap. Each isolated GitHub Actions matrix job builds one variant on windows-2022.

From Actions, run **Build Windows PHP** manually to validate all six builds without creating a release. Pushing a correctly named security tag builds all six and then creates a **draft** release. See [the release procedure](docs/RELEASING.md).

Local builds require Windows, Python 3.11+, Git, PowerShell 7, sufficient free disk space, and Windows SDK 10.0.19041.0. Installer provisioning requires an elevated session. Choose an empty, short work directory:

    pwsh ./scripts/build.ps1 -Series 7.4 -Variant nts -Work D:\php-build

To use an existing matching compiler installation:

    pwsh ./scripts/build.ps1 -Series 8.1 -Variant ts -Work D:\php-build -SkipInstall -VisualStudioPath "C:\Program Files (x86)\Microsoft Visual Studio\2019\BuildTools"

A work directory must not contain a previous source/SDK checkout. Outputs are written to out/php-SERIES-VARIANT-x64. Build logs live under WORK/logs. Installers and dependencies require network access; missing historical downloads or hash mismatches stop the build.

Validation checks CLI/CGI startup, runtime version and ID, x64/ZTS, loadable packaged extensions, JSON, compression, Unicode/ICU, OpenSSL, random bytes, SQLite, GD and XML, plus four selected Zend PHPTs. ZIP integrity is checked. This is a smoke test, not the full PHP test suite, a CVE exploit regression suite, or Apache/IIS integration testing.

Recipe/release-tool tests use the Python standard library:

    python -m unittest discover -s tests -v

## Evidence retention

Actions logs and temporary workflow artifacts expire under repository retention settings (this project requests 90 days for artifacts). A separate collector runs **after the build workflow finishes**, verifies package and manifest attestations, and attaches the complete original build-workflow log ZIP to the draft release.

Release assets also include per-build native logs, dependency inventories, validation results, manifests, provenance bundles and verification results. These remain with the release independently of Actions retention, unless the release is deleted. The collector's own ongoing job log is not included in the completed build-workflow log archive.

Enable immutable releases before publishing for stronger protection against later asset/tag replacement. GitHub Pages or a blog can display release metadata and link to these release assets; binary downloads do not need to be stored in a Pages repository.

## Licenses

The recipes and tooling in this repository are MIT licensed. PHP and its bundled dependencies retain their own licenses. Runtime/debug/development/test packages are generated by upstream packaging rules; preserve their included license notices when redistributing.
