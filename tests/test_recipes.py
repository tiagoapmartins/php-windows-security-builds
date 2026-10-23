import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import inputs
import release


class RecipeTests(unittest.TestCase):
    def test_locked_inputs(self):
        config = inputs.read_json(ROOT / "config/builds.lock.json")
        self.assertEqual(config["repository"], release.REPOSITORY)
        self.assertEqual({s["series"] for s in config["sources"]}, {"7.4", "8.0", "8.1"})
        self.assertRegex(config["sdk"]["commit"], r"^[0-9a-f]{40}$")
        for source in config["sources"]:
            self.assertRegex(source["commit"], r"^[0-9a-f]{40}$")
            self.assertTrue(source["version"].startswith(source["series"] + "."))
            for patch in source["patches"]:
                self.assertEqual(inputs.sha256(ROOT / patch["path"]), patch["sha256"])
            lock = inputs.read_json(ROOT / "config/dependencies" / (source["series"] + "-x64.json"))
            self.assertEqual(lock["series"], source["series"])
            self.assertEqual(lock["architecture"], "x64")
            self.assertGreater(len(lock["packages"]), 30)
            names = set()
            for package in lock["packages"]:
                self.assertNotIn(package["name"], names)
                names.add(package["name"])
                self.assertEqual(release.safe_name(package["name"]), package["name"])
                self.assertTrue(package["url"].startswith("https://downloads.php.net/~windows/php-sdk/deps/"))
                self.assertEqual(package["url"].split("/")[-1], package["name"])
                self.assertRegex(package["sha256"], r"^[0-9a-f]{64}$")
                self.assertGreater(package["bytes"], 0)
        for toolchain in config["toolchains"].values():
            self.assertRegex(toolchain["sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(toolchain["url"].startswith("https://aka.ms/vs/"))

    def test_unique_package_names(self):
        config = inputs.read_json(ROOT / "config/builds.lock.json")
        names = [n for s in config["sources"] for v in ("nts", "ts") for n in release.expected_packages(s, v)]
        self.assertEqual(len(names), 24)
        self.assertEqual(len(set(names)), 24)

    def test_reject_zip_path_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("../escape", "no")
            with self.assertRaisesRegex(ValueError, "Unsafe ZIP"):
                inputs.extract(archive, Path(temp) / "deps")

    def test_workflow_actions_are_pinned(self):
        import re
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            for action in re.findall(r"uses:\s+([^\s]+)", path.read_text()):
                self.assertRegex(action, r"^[\w/-]+@[0-9a-f]{40}$")


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = self.root / "payload"
        self.payload.mkdir()
        self.config = inputs.read_json(ROOT / "config/builds.lock.json")
        self.commit = "a" * 40
        self.tag = "security-2026-10-r1"
        self.digests = {}
        for source in self.config["sources"]:
            data = (ROOT / "config/dependencies" / (source["series"] + "-x64.json")).read_bytes()
            self.digests[source["series"]] = hashlib.sha256(data).hexdigest()
            for variant in ("nts", "ts"):
                build_id = f"php-{source['series']}-{variant}-x64"
                folder = self.payload / build_id
                folder.mkdir()
                names = release.expected_packages(source, variant)
                for name in names | {"logs-" + build_id + ".zip"}:
                    (folder / name).write_bytes(b"fixture package bytes")
                (folder / ("dependencies-" + build_id + ".json")).write_bytes(data)
                version_id = sum(int(n) * factor for n, factor in zip(source["version"].split("."), (10000,100,1)))
                validation = dict(status="passed", version=source["version"], versionId=version_id,
                                  zts=variant == "ts", x64=True, selectedPhptTests=4)
                (folder / ("validation-" + build_id + ".json")).write_text(json.dumps(validation))
                records = [{"name": f.name, "bytes": f.stat().st_size, "sha256": inputs.sha256(f)}
                           for f in folder.iterdir()]
                manifest = dict(
                    id=build_id, series=source["series"], version=source["version"], architecture="x64",
                    variant=variant, repository=release.REPOSITORY, buildCommit=self.commit, runId="123",
                    ref="refs/tags/" + self.tag, source=source, sdk=self.config["sdk"],
                    windowsSdk=self.config["windowsSdk"], dependencyLockSha256=self.digests[source["series"]],
                    compiler=self.config["toolchains"][source["toolchain"]]["compilerPrefix"] + "test",
                    configure=self.config["configure"] + ["--with-php-build=D:/test/deps",
                                                        "--enable-zts" if variant == "ts" else "--disable-zts"],
                    files=records,
                )
                (folder / ("build-" + build_id + ".json")).write_text(json.dumps(manifest))
                (folder / ("provenance-" + build_id + ".jsonl")).write_text("fixture: not a real attestation")

    def validate(self):
        return release.validate_payload(self.payload, self.config, self.commit, 123, self.tag, self.digests)

    def test_complete_matrix_and_checksums(self):
        payloads = self.validate()
        output = self.root / "release"
        manifest = release.assemble(payloads, output, self.tag, self.commit, 123)
        notes = release.finalize(output, manifest, self.tag)
        self.assertEqual(len(payloads), 6)
        sums = (output / "SHA256SUMS.txt").read_text().splitlines()
        self.assertEqual(len(sums), len(list(output.iterdir())) - 1)
        for line in sums:
            digest, name = line.split("  ")
            self.assertEqual(inputs.sha256(output / name), digest)
            self.assertIn(digest, notes)
            self.assertIn("/releases/download/" + self.tag + "/" + name, notes)

    def test_tampered_archive_rejected(self):
        next(self.payload.glob("*/php-7.4.33-nts-*.zip")).write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "checksum/size"):
            self.validate()

    def test_missing_matrix_member_rejected(self):
        next(self.payload.glob("*/build-php-*.json")).unlink()
        with self.assertRaisesRegex(ValueError, "six"):
            self.validate()

    def test_wrong_source_rejected(self):
        path = next(self.payload.glob("*/build-php-*.json"))
        item = inputs.read_json(path)
        item["source"]["commit"] = "b" * 40
        path.write_text(json.dumps(item))
        with self.assertRaisesRegex(ValueError, "inputs"):
            self.validate()

    def test_unexpected_file_rejected(self):
        next(self.payload.iterdir()).joinpath("surprise.exe").write_bytes(b"x")
        with self.assertRaisesRegex(ValueError, "Unexpected payload"):
            self.validate()

    def test_duplicate_inventory_rejected(self):
        path = next(self.payload.glob("*/build-php-*.json"))
        item = inputs.read_json(path)
        item["files"].append(copy.deepcopy(item["files"][0]))
        path.write_text(json.dumps(item))
        with self.assertRaisesRegex(ValueError, "inventory"):
            self.validate()

    def test_asset_path_escape_rejected(self):
        for value in ("../bad", "a/b", "a\\b", "-option", "a\nb", "a b"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                release.safe_name(value)

    def test_untrusted_run_rejected(self):
        run = dict(repository={"full_name": release.REPOSITORY},
                   head_repository={"full_name": release.REPOSITORY}, event="push", status="completed",
                   conclusion="success", path=".github/workflows/build.yml", head_sha=self.commit)
        release.trusted_run(run, release.REPOSITORY)
        for field, value in (("event", "pull_request"), ("conclusion", "failure"),
                             ("head_repository", {"full_name": "untrusted/fork"}),
                             ("path", ".github/workflows/other.yml")):
            changed = copy.deepcopy(run)
            changed[field] = value
            with self.assertRaises(ValueError):
                release.trusted_run(changed, release.REPOSITORY)



    def test_attestation_policy_binds_tag_commit_and_workflow(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        payloads = self.validate()
        output = self.root / "verification.json"
        with patch.object(release.subprocess, "run", return_value=SimpleNamespace(stdout="[]")) as command:
            release.verify_attestations(payloads, self.commit, self.tag, output)
        self.assertEqual(command.call_count, 36)
        for call in command.call_args_list:
            arguments = call.args[0]
            self.assertEqual(arguments[arguments.index("--source-digest") + 1], self.commit)
            self.assertEqual(arguments[arguments.index("--source-ref") + 1], "refs/tags/" + self.tag)
            self.assertEqual(arguments[arguments.index("--signer-workflow") + 1],
                             release.REPOSITORY + "/.github/workflows/build.yml")
            self.assertIn("--deny-self-hosted-runners", arguments)

    def test_log_storage_redirect_has_no_api_credential(self):
        import io
        import urllib.error
        from unittest.mock import Mock, patch
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("job/build.log", "completed build")
        redirect = urllib.error.HTTPError("https://api.github.com/logs", 302, "Found",
                                         {"Location": "https://storage.example.test/logs.zip"}, None)
        opener = Mock()
        opener.open.side_effect = redirect
        with patch.dict(release.os.environ, {"GH_TOKEN": "TEST-ONLY"}), \
                patch.object(release.urllib.request, "build_opener", return_value=opener), \
                patch.object(release.urllib.request, "urlopen", return_value=io.BytesIO(data.getvalue())) as download:
            release.archive_logs(release.REPOSITORY, 123, self.root / "logs.zip")
        api_call = opener.open.call_args.args[0]
        self.assertEqual(api_call.get_header("Authorization"), "Bearer TEST-ONLY")
        self.assertEqual(download.call_args.args[0], "https://storage.example.test/logs.zip")
        self.assertNotIn("headers", download.call_args.kwargs)
        self.assertEqual(zipfile.ZipFile(self.root / "logs.zip").read("job/build.log"), b"completed build")


if __name__ == "__main__":
    unittest.main()
