#!/usr/bin/env python3
"""One entry point for the four independently versioned components."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    components = json.loads((ROOT / 'components.json').read_text())['components']
    if len(sys.argv) == 1 or sys.argv[1] in ('--help', '-h', '--plan'):
        print('python build.py {stock,screen,multiace,ace} [component arguments]')
        for name, item in components.items():
            print(f"{name:9} {item['label']}  {item['path']}")
        print('Each component builds separately. This command never flashes hardware.')
        return 0
    name = sys.argv[1]
    if name not in components:
        print('Unknown component: ' + name, file=sys.stderr)
        return 2
    return subprocess.call([sys.executable, str(ROOT / components[name]['entry']),
                            *sys.argv[2:]], cwd=ROOT)


if __name__ == '__main__':
    raise SystemExit(main())
