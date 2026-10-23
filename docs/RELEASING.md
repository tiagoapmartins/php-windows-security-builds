# Release procedure

## Initial repository setup

1. Keep main as the default branch and enable GitHub Actions.
2. In Settings → General → Releases, enable release immutability if available for the repository. This protects published assets and the associated tag; drafts remain editable.
3. Protect main and the security-* tag namespace with suitable repository rules. Restrict who can create/delete release tags.
4. Leave workflow permissions at read-only by default; each workflow declares only its required permissions. Public hosted runners, GITHUB_TOKEN and GitHub OIDC are used; no personal access token or signing secret is required for builds.
5. Run **Check build recipes**, then manually dispatch **Build Windows PHP** on main. Inspect all six jobs and their artifacts. A manual build does not create a release or publish attestations.

The first hosted run is essential: static/local validation does not prove that Microsoft's historical installer feeds and the current hosted image still work together.

## Prepare a release

Review config/builds.lock.json, all dependency locks, patches and workflows. Update a dependency or source pin only deliberately, recording why in the commit. Do not silently advance upstream branches or dependency lists.

Keep PHP runtime versions separate from the distribution revision. PHP 7.4 must continue to report 7.4.33, with matching configure.ac and php_version.h. Use a new distribution tag for every changed recipe or rebuild intended for distribution.

From a reviewed, committed main checkout:

    git tag -a security-2026-10-r1 -m "PHP Windows security builds, October 2026, revision 1"
    git push origin security-2026-10-r1

Do not move or reuse a release tag. All six versions/variants build together from that recipe commit.

## Review the draft

After **Build Windows PHP** succeeds, **Collect release evidence**:

- accepts only a successful tag-push build from this repository and the expected workflow;
- resolves the tag to the producing recipe commit;
- checks the complete matrix, upstream pins, dependency inventories, runtime identity and every recorded file digest;
- verifies GitHub attestations for the packages, native log archive and build manifests against the tag, commit and workflow identity;
- downloads the completed build workflow's full log ZIP for every run attempt while those logs still exist;
- creates a draft release containing all packages and evidence, with a full SHA-256 download table.

Review the draft's six runtime downloads, bundled extension inventories, validation scope, upstream changes and third-party dependency versions. Download representative packages and verify their hashes and attestations. Test the applications you intend to support.

**Publish the draft manually only after review.** No workflow publishes a release automatically. With immutable releases enabled, publishing locks the assets and tag.

## Failures and recovery

A failed build creates diagnostic workflow artifacts but no release. Fix the recipe on main, validate it and create a new tag; do not retarget an existing release tag.

If a completed successful tag build's collector fails before creating a release, manually dispatch **Collect release evidence** with the original build run ID while its artifacts/logs are still retained. The collector rejects pull-request/manual/foreign build runs and mismatched payloads.

The collector refuses to overwrite either a draft or a published release. If an upload partially created a draft, inspect and delete that unpublished draft manually before retrying collection; retain the original tag. Never delete or modify a published immutable release to repair it; issue a new revision.

Actions retention does not become permanent just because this repository exists. The release must successfully collect and retain its evidence before the original logs/artifacts expire.

## External tools and dependency updates

The compiler bootstrapper checksums are pinned. If Microsoft replaces a bootstrapper, a build will fail rather than accept new bytes automatically. Verify the new file's Microsoft Authenticode signature, review the installer/toolset change and commit a new checksum intentionally.

The installer still resolves Microsoft-maintained component feeds. Record the full compiler/image versions from each build; additional archived installer layouts would be required to preserve those components independently.

Dependency ZIP hashes were obtained from the official PHP Windows dependency service. They pin the selected bytes; they are not independent vendor signatures or a security audit. Preserve package licenses and review historical dependencies before recommending these builds for an application.
