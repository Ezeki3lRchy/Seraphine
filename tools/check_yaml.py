"""Parse every YAML file under .github/ (workflows, dependabot). Exit 1 and name the
file and position if one does not parse. A workflow with a syntax error is rejected
by GitHub before any job runs, so only another workflow's job can report it: CI's
lint job runs this. Needs PyYAML (requirements-dev.txt)."""
import glob
import os
import sys

import yaml


def main(root):
    files = sorted(glob.glob(os.path.join(root, '.github', '**', '*.yml'), recursive=True)
                   + glob.glob(os.path.join(root, '.github', '**', '*.yaml'), recursive=True))
    if not files:
        print('check_yaml: no YAML files found under .github/', file=sys.stderr)
        return 1
    failed = 0
    for path in files:
        try:
            with open(path, encoding='utf-8') as f:
                yaml.safe_load(f)
        except yaml.YAMLError as e:
            failed += 1
            print(f'check_yaml: {os.path.relpath(path, root)}: {e}', file=sys.stderr)
    print(f'check_yaml: {len(files) - failed} of {len(files)} files parse')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
