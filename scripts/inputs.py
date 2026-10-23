"""Locked input acquisition; Python standard library only."""
import argparse
import hashlib
import http.client
import urllib.error
import json
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def download(url, destination, expected, size=None):
    if not url.startswith("https://"):
        raise ValueError("HTTPS is required")
    destination = Path(destination)
    if (destination.exists() and sha256(destination) == expected
            and (size is None or destination.stat().st_size == size)):
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as output:
                if not response.url.startswith("https://"):
                    raise ValueError("Insecure download redirect")
                shutil.copyfileobj(response, output)
            if sha256(temporary) != expected or (size is not None and temporary.stat().st_size != size):
                raise ValueError("Download digest/size mismatch: " + destination.name)
            temporary.replace(destination)
            return
        except Exception as error:
            temporary.unlink(missing_ok=True)
            transient = (urllib.error.URLError, http.client.IncompleteRead, TimeoutError, ConnectionError)
            if not isinstance(error, transient) or attempt == 4:
                raise
            print(f"Retrying {destination.name} after a network error ({attempt + 1}/5)", flush=True)
            time.sleep(5 * 2 ** attempt)


def extract(archive, destination):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as package:
        for entry in package.infolist():
            target = (destination / entry.filename).resolve()
            if not target.is_relative_to(destination) or "\\" in entry.filename:
                raise ValueError("Unsafe ZIP member")
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("ZIP symlinks are forbidden")
        if package.testzip() is not None:
            raise ValueError("ZIP integrity check failed")
        package.extractall(destination)


def checkout(repository, commit, destination):
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Use an empty work directory; refusing to reuse a checkout")
    subprocess.run(["git", "init", str(destination)], check=True)
    subprocess.run(["git", "-C", str(destination), "config", "core.autocrlf", "false"], check=True)
    subprocess.run(["git", "-C", str(destination), "remote", "add", "origin", repository], check=True)
    subprocess.run(["git", "-C", str(destination), "fetch", "--depth=1", "origin", commit], check=True)
    subprocess.run(["git", "-C", str(destination), "checkout", "--detach", "FETCH_HEAD"], check=True)
    actual = subprocess.check_output(["git", "-C", str(destination), "rev-parse", "HEAD"], text=True).strip()
    if actual != commit:
        raise ValueError("Unexpected checkout commit")


def prepare(series, work):
    config = read_json(ROOT / "config/builds.lock.json")
    source = next(item for item in config["sources"] if item["series"] == series)
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    checkout(config["sdk"]["repository"], config["sdk"]["commit"], work / "sdk")
    checkout(source["repository"], source["commit"], work / "php-src")
    for patch in source["patches"]:
        path = ROOT / patch["path"]
        if sha256(path) != patch["sha256"]:
            raise ValueError("Patch checksum mismatch")
        subprocess.run(["git", "-C", str(work / "php-src"), "apply", "--check", str(path)], check=True)
        subprocess.run(["git", "-C", str(work / "php-src"), "apply", str(path)], check=True)
    dependencies = read_json(ROOT / "config/dependencies" / (series + "-x64.json"))
    for package in dependencies["packages"]:
        archive = work / "downloads" / package["name"]
        print("Dependency: " + package["name"], flush=True)
        download(package["url"], archive, package["sha256"], package["bytes"])
        extract(archive, work / "deps")
        archive.unlink()
    toolchain = config["toolchains"][source["toolchain"]]
    download(toolchain["url"], work / "downloads/vs_buildtools.exe", toolchain["sha256"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--series", required=True, choices=["7.4", "8.0", "8.1"])
    parser.add_argument("--work", required=True)
    args = parser.parse_args()
    prepare(args.series, args.work)
