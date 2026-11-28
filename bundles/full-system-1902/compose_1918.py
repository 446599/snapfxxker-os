"""Install the rebuilt screen into the verified 1902.17 full-system base."""
import argparse
import json
from pathlib import Path
import shutil

from compose_1916 import repin, require, sha
from package_screen_1913 import verify_screen

BASE_VERSION = '2.1.2-233-1902.17'
BASE_COMMIT = '3b50fbbcc42d7cd60be4cab64cc907d6779b7b32'
VERSION = '2.1.2-233-1902.18'
SCREEN_VERSION = '1.2.0-233.20261128.1918'
SCREEN_PACKAGE = 'usr/local/share/extended-pkg/helixscreen'


def compose(root, previous, output, commit, screen_archive, screen_provenance):
    report = json.loads(previous.read_text())
    require(report['firmware_version'] == BASE_VERSION
            and report['integration_commit'] == BASE_COMMIT
            and (root / 'etc/FULLVERSION').read_text().strip() == BASE_VERSION,
            'expected the verified 1902.17 image')
    for path, digest in report['runtime_file_sha256'].items():
        require(sha(root / path) == digest, 'base runtime mismatch: ' + path)
    for key in ('screen', 'provider'):
        require(sha(root / report[key + '_archive']) == report[key + '_sha256'],
                'base archive mismatch: ' + key)
    screen = json.loads(screen_provenance.read_text())
    require(screen['integration_commit'] == commit, 'screen was built from a different commit')
    verify_screen(screen_archive, screen, SCREEN_VERSION)

    old = root / report['screen_archive']
    new = old.with_name('helixscreen-233-' + SCREEN_VERSION + '.tar.gz')
    require(not new.exists(), 'new screen already exists')
    shutil.copyfile(screen_archive, new)
    name = str(new.relative_to(root))
    repin(root, 'helixscreen', report['screen_package_version'], SCREEN_VERSION,
          report['screen_archive'], name, report['screen_sha256'], screen['sha256'])
    old.unlink()
    (root / 'etc/FULLVERSION').write_text(VERSION + '\n')
    for path in ('etc/FULLVERSION', SCREEN_PACKAGE):
        report['runtime_file_sha256'][path] = sha(root / path)
    report.update(firmware_version=VERSION, integration_commit=commit,
                  screen_archive=name, screen_sha256=screen['sha256'],
                  screen_build_package_version=SCREEN_VERSION,
                  screen_package_version=SCREEN_VERSION, hardware_verified=False,
                  scope='Restore the loaded bay background in the U1 material screen.')
    output.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'previous', 'output'):
        parser.add_argument(name, type=Path)
    parser.add_argument('commit')
    parser.add_argument('screen_archive', type=Path)
    parser.add_argument('screen_provenance', type=Path)
    args = parser.parse_args()
    compose(args.root, args.previous, args.output, args.commit,
            args.screen_archive, args.screen_provenance)
