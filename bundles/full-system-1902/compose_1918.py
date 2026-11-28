"""Install a rebuilt screen into its pinned full-system base."""
import argparse
import json
from pathlib import Path
import shutil

from compose_1916 import repin, require, sha
from package_screen_1913 import verify_screen

RELEASES = {
    '1918': ('2.1.2-233-1902.17', '3b50fbbcc42d7cd60be4cab64cc907d6779b7b32',
             '2.1.2-233-1902.18', '1.2.0-233.20261128.1918',
             'Restore the loaded bay background in the U1 material screen.'),
    '1919': ('2.1.2-233-1902.18', '1c4dd6cbc859691f64d474b76434f62be12e50f1',
             '2.1.2-233-1902.19', '1.2.0-233.20261128.1919',
             'Use the confirmed source for the loaded bay and screen head unload.'),
}
SCREEN_PACKAGE = 'usr/local/share/extended-pkg/helixscreen'


def compose(root, previous, output, commit, screen_archive, screen_provenance, profile='1918'):
    base_version, base_commit, version, screen_version, scope = RELEASES[profile]
    report = json.loads(previous.read_text())
    require(report['firmware_version'] == base_version
            and report['integration_commit'] == base_commit
            and (root / 'etc/FULLVERSION').read_text().strip() == base_version,
            'expected the verified ' + base_version + ' image')
    for path, digest in report['runtime_file_sha256'].items():
        require(sha(root / path) == digest, 'base runtime mismatch: ' + path)
    for key in ('screen', 'provider'):
        require(sha(root / report[key + '_archive']) == report[key + '_sha256'],
                'base archive mismatch: ' + key)
    screen = json.loads(screen_provenance.read_text())
    require(screen['integration_commit'] == commit, 'screen was built from a different commit')
    verify_screen(screen_archive, screen, screen_version)

    old = root / report['screen_archive']
    new = old.with_name('helixscreen-233-' + screen_version + '.tar.gz')
    require(not new.exists(), 'new screen already exists')
    shutil.copyfile(screen_archive, new)
    name = str(new.relative_to(root))
    repin(root, 'helixscreen', report['screen_package_version'], screen_version,
          report['screen_archive'], name, report['screen_sha256'], screen['sha256'])
    old.unlink()
    (root / 'etc/FULLVERSION').write_text(version + '\n')
    for path in ('etc/FULLVERSION', SCREEN_PACKAGE):
        report['runtime_file_sha256'][path] = sha(root / path)
    report.update(firmware_version=version, integration_commit=commit,
                  screen_archive=name, screen_sha256=screen['sha256'],
                  screen_build_package_version=screen_version,
                  screen_package_version=screen_version, hardware_verified=False, scope=scope)
    output.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'previous', 'output'):
        parser.add_argument(name, type=Path)
    parser.add_argument('commit')
    parser.add_argument('screen_archive', type=Path)
    parser.add_argument('screen_provenance', type=Path)
    parser.add_argument('--profile', choices=RELEASES, default='1918')
    args = parser.parse_args()
    compose(args.root, args.previous, args.output, args.commit,
            args.screen_archive, args.screen_provenance, args.profile)
