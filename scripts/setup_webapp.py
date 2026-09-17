#!/usr/bin/env python3
"""Create this clone's local Python environment and pinned browser dependencies."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import venv

ROOT=Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--python',help='Use an existing Python environment instead of creating .venv');args=parser.parse_args()
    if args.python:
        python=str(Path(args.python).expanduser().resolve())
    else:
        venv.EnvBuilder(with_pip=True).create(ROOT/'.venv')
        python=str(ROOT/'.venv'/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python'))
    npm=shutil.which('npm')
    if not npm:raise SystemExit('Install Node.js with npm, then run this script again.')
    subprocess.run([python,'-m','pip','install','-r',str(ROOT/'webapp/requirements.txt')],check=True)
    subprocess.run([npm,'ci','--ignore-scripts','--no-audit','--no-fund'],cwd=ROOT/'webapp',check=True)
    print('Setup complete. Start with: python3 scripts/start_webapp.py')
    print('If using --python, start with that same interpreter or configure it in Local setup.')
if __name__=='__main__':main()
