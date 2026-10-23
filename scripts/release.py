"""Validate a completed, trusted tag build and assemble a draft GitHub Release."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from inputs import read_json, sha256

TAG = re.compile(r"security-\d{4}-(?:0[1-9]|1[0-2])-r[1-9]\d*")
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
REPOSITORY = "tiagoapmartins/php-windows-security-builds"


def json_text(value):
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def safe_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise ValueError("Unsafe asset name")
    return value


def expected_packages(source, variant):
    version, compiler = source["version"], source["toolchain"]
    suffix = ("-nts" if variant == "nts" else "") + "-Win32-" + compiler + "-x64.zip"
    return {
        "php-" + version + suffix,
        "php-debug-pack-" + version + suffix,
        "php-devel-pack-" + version + suffix,
        "php-test-pack-" + version + "-" + variant + "-Win32-" + compiler + "-x64.zip",
    }


def validate_payload(directory, config, commit, run_id, tag, dependency_digests):
    directory = Path(directory)
    manifests = list(directory.glob("*/build-php-*.json"))
    if len(manifests) != 6:
        raise ValueError("Exactly six build manifests are required")
    seen = set()
    result = []
    for path in sorted(manifests):
        item = read_json(path)
        series, variant = item["series"], item["variant"]
        if variant not in ("nts", "ts"):
            raise ValueError("Unexpected variant")
        source = next((s for s in config["sources"] if s["series"] == series), None)
        if source is None:
            raise ValueError("Unexpected PHP series")
        build_id = "php-" + series + "-" + variant + "-x64"
        if build_id in seen or item["id"] != build_id or path.name != "build-" + build_id + ".json":
            raise ValueError("Duplicate/incorrect build identity")
        seen.add(build_id)
        if (item["repository"] != REPOSITORY or item["buildCommit"] != commit
                or str(item["runId"]) != str(run_id) or item["ref"] != "refs/tags/" + tag
                or item["source"] != source or item["sdk"] != config["sdk"]
                or item["version"] != source["version"] or item["architecture"] != "x64"
                or item["windowsSdk"] != config["windowsSdk"]
                or item["dependencyLockSha256"] != dependency_digests[series]):
            raise ValueError("Build inputs do not match the tagged recipe")
        compiler = config["toolchains"][source["toolchain"]]
        if not item["compiler"].startswith(compiler["compilerPrefix"]):
            raise ValueError("Unexpected compiler")
        flags = item["configure"]
        if (flags[:len(config["configure"])] != config["configure"]
                or len(flags) != len(config["configure"]) + 2
                or not flags[-2].startswith("--with-php-build=")
                or flags[-1] != ("--enable-zts" if variant == "ts" else "--disable-zts")):
            raise ValueError("Unexpected configure flags")
        records = item["files"]
        names = [safe_name(record["name"]) for record in records]
        packages = expected_packages(source, variant)
        required = packages | {"logs-" + build_id + ".zip", "validation-" + build_id + ".json",
                               "dependencies-" + build_id + ".json"}
        if len(names) != len(set(names)) or set(names) != required:
            raise ValueError("Incomplete or unexpected package inventory")
        bundle = "provenance-" + build_id + ".jsonl"
        allowed = required | {path.name, bundle}
        files = list(path.parent.iterdir())
        if any(p.is_symlink() or not p.is_file() for p in files) or {p.name for p in files} != allowed:
            raise ValueError("Unexpected payload files")
        for record in records:
            artifact = path.parent / record["name"]
            if (not HEX64.fullmatch(record["sha256"]) or sha256(artifact) != record["sha256"]
                    or artifact.stat().st_size != record["bytes"]):
                raise ValueError("Artifact checksum/size mismatch: " + artifact.name)
        if sha256(path.parent / ("dependencies-" + build_id + ".json")) != dependency_digests[series]:
            raise ValueError("Dependency inventory mismatch")
        validation = read_json(path.parent / ("validation-" + build_id + ".json"))
        version_id = sum(int(n) * factor for n, factor in zip(source["version"].split("."), (10000, 100, 1)))
        if (validation["status"] != "passed" or validation["version"] != source["version"]
                or validation["versionId"] != version_id or validation["zts"] != (variant == "ts")
                or not validation["x64"] or validation["selectedPhptTests"] != 4):
            raise ValueError("Runtime validation failed")
        result.append((path, item, packages))
    expected = {"php-" + s["series"] + "-" + v + "-x64" for s in config["sources"] for v in ("nts", "ts")}
    if seen != expected or {p.name for p in directory.iterdir()} != expected:
        raise ValueError("Incomplete matrix or unexpected artifact directories")
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api_request(path):
    return urllib.request.Request("https://api.github.com" + path, headers={
        "Authorization": "Bearer " + os.environ["GH_TOKEN"],
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "php-windows-security-builds",
    })


def api_json(path):
    with urllib.request.urlopen(api_request(path), timeout=60) as response:
        return json.load(response)


def archive_logs(repository, run_id, destination):
    # Never forward the API credential to GitHub's signed storage redirect.
    opener = urllib.request.build_opener(NoRedirect)
    for attempt in range(5):
        try:
            try:
                response = opener.open(api_request(f"/repos/{repository}/actions/runs/{run_id}/logs"), timeout=60)
            except urllib.error.HTTPError as error:
                if error.code != 302:
                    raise
                location = error.headers["Location"]
                if urllib.parse.urlsplit(location).scheme != "https":
                    raise ValueError("Insecure log download URL")
                response = urllib.request.urlopen(location, timeout=120)
            with response, open(destination, "wb") as output:
                shutil.copyfileobj(response, output)
            import zipfile
            with zipfile.ZipFile(destination) as logs:
                if not logs.namelist() or logs.testzip() is not None:
                    raise ValueError("Invalid workflow logs ZIP")
            return
        except (urllib.error.HTTPError, urllib.error.URLError):
            if attempt == 4:
                raise
            time.sleep(5)


def verify_attestations(payloads, commit, tag, output):
    records = []
    for manifest_path, item, packages in payloads:
        # The manifest binds validation, dependency inventories, and native logs to the build.
        for name in sorted(packages | {manifest_path.name, "logs-" + item["id"] + ".zip"}):
            command = [
                "gh", "attestation", "verify", str(manifest_path.parent / name),
                "--repo", REPOSITORY, "--bundle", str(manifest_path.parent / ("provenance-" + item["id"] + ".jsonl")),
                "--signer-workflow", REPOSITORY + "/.github/workflows/build.yml",
                "--source-digest", commit, "--source-ref", "refs/tags/" + tag,
                "--deny-self-hosted-runners", "--format", "json",
            ]
            verified = subprocess.run(command, check=True, capture_output=True, text=True)
            records.append({"asset": name, "verification": json.loads(verified.stdout)})
    output.write_text(json_text(records), encoding="utf-8")


def assemble(payloads, destination, tag, commit, run_id):
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Release staging directory must not already exist")
    destination.mkdir(parents=True)
    seen = set()
    for path, _, _ in payloads:
        for artifact in sorted(path.parent.iterdir()):
            safe_name(artifact.name)
            if artifact.name in seen:
                raise ValueError("Duplicate release asset name")
            seen.add(artifact.name)
            shutil.copyfile(artifact, destination / artifact.name)
    return {
        "schemaVersion": 1, "repository": REPOSITORY, "tag": tag, "buildCommit": commit,
        "buildRunId": run_id, "buildRunUrl": f"https://github.com/{REPOSITORY}/actions/runs/{run_id}",
        "collectorCommit": os.environ.get("GITHUB_SHA"),
        "builds": [item for _, item, _ in payloads],
        "limitations": "Unofficial builds of EOL PHP branches. Selected PHP security fixes only; bundled third-party dependencies are not comprehensively updated. Not a claim of bit-for-bit reproducibility.",
    }


def finalize(destination, manifest, tag):
    assets = []
    # release-manifest inventories all other assets; SHA256SUMS also hashes release-manifest.
    for file in sorted(destination.iterdir()):
        assets.append({"name": file.name, "sha256": sha256(file), "bytes": file.stat().st_size})
    manifest["assets"] = assets
    (destination / "release-manifest.json").write_text(json_text(manifest), encoding="utf-8")
    assets.append({"name": "release-manifest.json", "sha256": sha256(destination / "release-manifest.json"),
                   "bytes": (destination / "release-manifest.json").stat().st_size})
    assets.sort(key=lambda item: item["name"])
    (destination / "SHA256SUMS.txt").write_text("".join(f"{item['sha256']}  {item['name']}\n" for item in assets), encoding="utf-8")
    base = f"https://github.com/{REPOSITORY}/releases/download/{tag}/"
    lines = [
        "# Unofficial PHP Windows security builds", "",
        "x64 NTS and TS builds of PHP 7.4.33, 8.0.30 and 8.1.34, using the pinned upstream "
        "CVE-2026-109618/109619 branches. These are community builds, not releases from the PHP project.", "",
        "The PHP runtime versions remain unchanged for compatibility. Identify this distribution by "
        "the release tag, recipe commit and SHA-256. Third-party dependencies are listed in the inventories; "
        "these builds do not provide comprehensive maintenance for these EOL branches.", "",
        f"Recipe commit: \x60{manifest['buildCommit']}\x60. [Build workflow]({manifest['buildRunUrl']}).", "",
        "## Runtime downloads", "", "| Version | Variant | Download | SHA-256 |", "| --- | --- | --- | --- |",
    ]
    by_name = {item["name"]: item for item in assets}
    for build in manifest["builds"]:
        runtime = next(n for n in expected_packages(build["source"], build["variant"])
                       if n.startswith("php-" + build["version"] + "-"))
        lines.append(f"| {build['version']} | {build['variant'].upper()} x64 | "
                     f"[ZIP]({base}{runtime}) | \x60{by_name[runtime]['sha256']}\x60 |")
    lines += ["", "## All downloads and evidence", "",
              "Includes debug/development/test packages, signed provenance bundles, validation results, "
              "dependency inventories, native logs, full build-workflow logs, and attestation verification results.",
              "Checksums cover every asset except SHA256SUMS.txt itself. The manifest excludes its own hash.", "",
              f"[Download SHA256SUMS.txt]({base}SHA256SUMS.txt)", "",
              "| Download | Bytes | SHA-256 |", "| --- | ---: | --- |"]
    for item in assets:
        lines.append(f"| [{item['name']}]({base}{item['name']}) | {item['bytes']} | \x60{item['sha256']}\x60 |")
    lines += ["", "Verification and test scope: see README.md and docs/RELEASING.md in the tagged recipe.",
              "Native PHP binaries have no new Authenticode signature; GitHub attestations identify the build workflow.", ""]
    return "\n".join(lines)


def trusted_run(run, repository):
    if (repository != REPOSITORY or run["repository"]["full_name"] != repository
            or run["head_repository"]["full_name"] != repository or run["event"] != "push"
            or run["status"] != "completed" or run["conclusion"] != "success"
            or run["path"] != ".github/workflows/build.yml" or not HEX40.fullmatch(run["head_sha"])):
        raise ValueError("Only a completed successful same-repository tag build may create a release")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    repository = os.environ["GITHUB_REPOSITORY"]
    run = api_json(f"/repos/{repository}/actions/runs/{args.run_id}")
    trusted_run(run, repository)
    commit = run["head_sha"]
    subprocess.run(["git", "fetch", "--depth=1", "origin", commit], check=True)
    config = json.loads(subprocess.check_output(["git", "show", commit + ":config/builds.lock.json"], text=True))
    first = sorted(args.payload.glob("*/build-php-*.json"))
    if not first:
        raise ValueError("No build manifests")
    ref = read_json(first[0])["ref"]
    tag = ref.removeprefix("refs/tags/")
    if ref != "refs/tags/" + tag or not TAG.fullmatch(tag):
        raise ValueError("Invalid release tag")
    tag_ref = api_json(f"/repos/{repository}/git/ref/tags/{tag}")["object"]
    while tag_ref["type"] == "tag":
        tag_ref = api_json(f"/repos/{repository}/git/tags/{tag_ref['sha']}")["object"]
    if tag_ref["type"] != "commit" or tag_ref["sha"] != commit:
        raise ValueError("Tag does not identify the build commit")
    # Fail closed for both drafts and published releases, including manual reruns.
    try:
        api_json(f"/repos/{repository}/releases/tags/{tag}")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
    else:
        raise ValueError("Release already exists; this collector never overwrites releases")
    dependency_digests = {}
    import hashlib
    for source in config["sources"]:
        content = subprocess.check_output(["git", "show", commit + ":config/dependencies/" + source["series"] + "-x64.json"])
        dependency_digests[source["series"]] = hashlib.sha256(content).hexdigest()
    payloads = validate_payload(args.payload, config, commit, args.run_id, tag, dependency_digests)
    manifest = assemble(payloads, args.output, tag, commit, args.run_id)
    manifest["collectorCommit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    verify_attestations(payloads, commit, tag, args.output / "attestation-verification.json")
    archive_logs(repository, args.run_id, args.output / f"workflow-logs-{args.run_id}.zip")
    body = finalize(args.output, manifest, tag)
    notes = args.output.parent / "release-notes.md"
    notes.write_text(body, encoding="utf-8")
    subprocess.run(["gh", "release", "create", tag, "--repo", repository, "--verify-tag", "--draft",
                    "--title", "PHP Windows security builds " + tag, "--notes-file", str(notes),
                    *[str(p) for p in sorted(args.output.iterdir())]], check=True)


if __name__ == "__main__":
    main()
