#!/usr/bin/env python3
"""Start GDS Studio on demand, or open the existing instance of this clone."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

ROOT=Path(__file__).resolve().parents[1]
def health(url):
    try:
        with urllib.request.urlopen(url+'/api/health',timeout=1) as response:return json.load(response)
    except (OSError,ValueError):return None

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=8768);parser.add_argument('--no-browser',action='store_true');parser.add_argument('--background',action='store_true');args=parser.parse_args()
    if not 1024<=args.port<=65535:raise SystemExit('Choose a port between 1024 and 65535')
    url=f'http://127.0.0.1:{args.port}'
    existing=health(url)
    if existing:
        if existing.get('app')!='gds-web-studio' or existing.get('root')!=str(ROOT):raise SystemExit('This port belongs to another app or clone. Choose --port with an unused port.')
        if not args.no_browser:webbrowser.open(url)
        print(url);return
    python=ROOT/'.venv'/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
    command=[str(python) if python.is_file() else sys.executable,str(ROOT/'webapp/server.py'),'--port',str(args.port)]
    local=ROOT/'.local/webapp';local.mkdir(parents=True,exist_ok=True)
    with (local/'server.log').open('a') as log:
        child=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    for _ in range(100):
        result=health(url)
        if result and result.get('root')==str(ROOT):break
        if child.poll() is not None:raise SystemExit(f'Start failed. Run setup, then inspect {local / "server.log"}')
        time.sleep(.1)
    else:
        child.terminate();raise SystemExit('Startup timed out; see .local/webapp/server.log')
    (local/'server.pid').write_text(str(child.pid)+'\n')
    print(url,flush=True)
    if not args.no_browser:webbrowser.open(url)
    if not args.background:
        print('Press Ctrl+C to stop this studio.',flush=True)
        try:child.wait()
        except KeyboardInterrupt:child.send_signal(__import__('signal').SIGINT);child.wait(timeout=15)
if __name__=='__main__':main()
