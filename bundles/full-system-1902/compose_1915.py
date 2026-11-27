#!/usr/bin/env python3
"""Install the U1 web UI into an extracted, verified 1902.14 image."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil

HERE = Path(__file__).resolve().parent
WEB = HERE.parent / "unified-web"
BASE_COMMIT = "138b98ba17583ffa59d257982ff993f5e7c1fdb2"
BASE_VERSION = "2.1.2-233-1902.14"
VERSION = "2.1.2-233-1902.15"
INSTALL_DIR = "usr/local/share/u1-web"
NGINX_PATH = "etc/nginx/fluidd.d/u1-web.conf"
COMPANION_INIT = "etc/init.d/S58u1-device-service"
COMPANION_GATE = '    [ "$selected" = helixscreen ] || return 0'
LINKS_PATH = "usr/local/share/firmware-config/functions/10_links_u1_web.yaml"


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


class ShellAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("src"):
            self.assets.append(attrs["src"])
        elif tag == "link" and attrs.get("rel") in {"stylesheet", "icon", "modulepreload"}:
            self.assets.append(attrs["href"])


def asset_files(web):
    """Copy only browser assets, and check the HTML and module entry points."""
    paths = {p.name: p for p in web.iterdir()
             if p.is_file() and p.suffix in {".html", ".css", ".js", ".svg", ".png", ".woff2"}}
    for name in ("index.html", "control.js", "material.js"):
        if name not in paths:
            raise ValueError("missing unified web asset: " + name)
    shell = ShellAssets()
    shell.feed(paths["index.html"].read_text())
    references = list(shell.assets)
    for path in paths.values():
        if path.suffix == ".js":
            references.extend(re.findall(
                r"""(?:\bfrom\s*|\bimport\s*\(?\s*)["'](\.[^"']+)["']""",
                path.read_text()))
    for ref in references:
        relative = ref.split("?", 1)[0].removeprefix("./")
        if relative not in paths:
            raise ValueError("missing or nonlocal unified web dependency: " + ref)
    return paths


def compose(root, previous, output, commit, web=WEB):
    report = json.loads(previous.read_text())
    version = root / "etc/FULLVERSION"
    if (version.read_text().strip() != BASE_VERSION or report["firmware_version"] != BASE_VERSION
            or report["integration_commit"] != BASE_COMMIT):
        raise ValueError("expected the successful 1902.14 system")
    # These files are the active web and companion configuration in the pinned base.
    for rel in ("etc/nginx/fluidd.d/auth-check.conf",
                "etc/nginx/fluidd.d/multiace-web.conf",
                "etc/nginx/fluidd.d/firmware-config.conf",
                "usr/local/lib/u1-device-service/device_service.py"):
        if not (root / rel).is_file():
            raise ValueError("missing base service dependency: " + rel)
    companion = (root / "usr/local/lib/u1-device-service/device_service.py").read_text()
    if 'route == "/firmware/settings"' not in companion or 'update_firmware_setting(data)' not in companion:
        raise ValueError("missing screen settings handler in base")
    init = root / COMPANION_INIT
    service_script = init.read_text()
    if service_script.count(COMPANION_GATE) != 1:
        raise ValueError("unknown screen companion startup script")
    for key in ("screen", "provider"):
        if digest(root / report[key + "_archive"]) != report[key + "_sha256"]:
            raise ValueError("base archive mismatch: " + key)
    files = {INSTALL_DIR + "/" + name: source for name, source in asset_files(web).items()}
    files[NGINX_PATH] = web / "install/unified-web.conf"
    files[LINKS_PATH] = web / "install/u1-web-links.yaml"
    for rel, source in files.items():
        destination = root / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(0o644)
    # The existing companion serves both clients. Keep it available when the
    # operator switches the device back to the stock touchscreen.
    init.write_text(service_script.replace(COMPANION_GATE,
                    '    [ "$selected" = helixscreen ] || [ -f /usr/local/share/u1-web/index.html ] || return 0'))
    version.write_text(VERSION + "\n")
    changed = sorted([*files, COMPANION_INIT, "etc/FULLVERSION"])
    report["runtime_files"] = sorted(set(report["runtime_files"]).union(changed))
    report["runtime_file_sha256"].update({rel: digest(root / rel) for rel in changed})
    report.update(firmware_version=VERSION, integration_commit=commit, base_system_commit=BASE_COMMIT,
                  screen_integration_commit=BASE_COMMIT, base_firmware_version=BASE_VERSION,
                  unified_web_files=changed, unified_web_url="/u1/", hardware_verified=False,
                  scope="Unified printer web console; screen and provider archives retained from 1902.14.")
    output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("root", "previous", "output"):
        parser.add_argument(key, type=Path)
    parser.add_argument("commit")
    args = parser.parse_args()
    compose(args.root, args.previous, args.output, args.commit)
