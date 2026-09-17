"""Standalone loopback-only studio. No FacultyOS service or LLM is used."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
import yaml
from storage import Library, read_json, write_json
from pipeline import ROOT, FILES, OPTIONS, options, preset, resolve_file, file_hash, prepare_commands, build_commands, preview_mesh


class Studio:
    def __init__(self,root=ROOT):
        self.root=Path(root).resolve();self.local=self.root/'.local/webapp';self.local.mkdir(parents=True,exist_ok=True)
        self.config_path=self.local/'config.json';self.lock=threading.RLock();self.jobs={};self.active=None;self.process=None;self.token=secrets.token_urlsafe(32)
        self.preset_dir=self.root/'configs/blender/render_presets'
        self.preview_path=None
        self.library=Library(self.root)
        self.job_directories={}
        self.restore_jobs()

    def config(self):
        cfg={key:default for key,(_,default) in FILES.items()}
        cfg.update(python=sys.executable,blender=shutil.which('blender') or ('/Applications/Blender.app/Contents/MacOS/Blender' if Path('/Applications/Blender.app/Contents/MacOS/Blender').is_file() else ''))
        if self.config_path.exists():cfg.update(json.loads(self.config_path.read_text()))
        return cfg

    def status(self):
        cfg=self.config();checks=[]
        for key,(label,_) in FILES.items():
            ready=bool(cfg.get(key)) and resolve_file(self.root,cfg[key]).is_file()
            checks.append(dict(key=key,label=label,state='ready' if ready else 'missing',action='' if ready else f'Provide the local path to {label}.'))
        for key in ('python','blender'):
            found=shutil.which(cfg.get(key,''))
            checks.append(dict(key=key,label=key.title(),state='ready' if found and os.access(found,os.X_OK) else 'missing',action=f'Set an executable {key} path.'))
        # Probe configured interpreter, not the server's environment. Never import the private tech.py.
        python_ok=False
        if next(c for c in checks if c['key']=='python')['state']=='ready':
            try:
                p=subprocess.run([cfg['python'],'-c','import yaml,gdstk,shapely,importlib.util;from shapely import constrained_delaunay_triangles;assert importlib.util.find_spec("gdsfactory") is not None'],capture_output=True,timeout=25)
                python_ok=p.returncode==0
            except (OSError,subprocess.TimeoutExpired):pass
        checks.append(dict(key='dependencies',label='Python geometry packages',state='ready' if python_ok else 'missing',action='Run python3 scripts/setup_webapp.py, or choose the configured gds-blender-pipeline Python.'))
        vendor=(self.root/'webapp/node_modules/three/build/three.module.js').is_file()
        checks.append(dict(key='three',label='Offline 3D viewer',state='ready' if vendor else 'missing',action='Run python3 scripts/setup_webapp.py.'))
        preview_ready=all(c['state']=='ready' for c in checks if c['key']!='blender')
        selected=self.selected_layout()
        return dict(layout=selected,layouts=self.library.catalog(),config=cfg,checks=checks,preview_ready=preview_ready,build_ready=preview_ready and next(c for c in checks if c['key']=='blender')['state']=='ready',options=OPTIONS,token=self.token,active_job=self.active)

    def selected_layout(self):
        path=resolve_file(self.root,self.config().get('gds',''))
        try:
            relative=path.relative_to(self.library.base)
            layout=self.library.layout(relative.parts[0])
            if path!=self.library.raw(layout):return None
            return layout
        except (ValueError,OSError,KeyError,IndexError):return None

    def import_layout(self,source,original_name=None,origin=None):
        with self.lock:
            if self.active:raise ValueError('Wait for the active job before changing layout')
            layout=self.library.import_raw(source,original_name,origin)
            cfg=self.config();cfg['gds']=self.library.raw(layout).relative_to(self.root).as_posix()
            write_json(self.config_path,cfg)
        return self.status()

    def save_config(self,data):
        if not isinstance(data,dict) or set(data)-set(self.config()):raise ValueError('Unknown configuration field')
        cfg=self.config()|data
        if any(not isinstance(v,str) or '\x00' in v or '\n' in v for v in cfg.values()):raise ValueError('Paths must be plain strings')
        with self.lock:
            if self.active:raise ValueError('Wait for the active job before changing configuration')
            if cfg.get('gds') and resolve_file(self.root,cfg['gds']).is_file():
                layout=self.library.import_raw(cfg['gds'])
                cfg['gds']=self.library.raw(layout).relative_to(self.root).as_posix()
            write_json(self.config_path,cfg);self.preview_path=None
        return self.status()

    def select_layout(self,key):
        layout=self.library.layout(key)
        return self.save_config({'gds':str(self.library.raw(layout))})

    def presets(self):
        entries=self.library.preset_entries(self.selected_layout())
        entries.extend(dict(id='legacy/'+p.name,label='Shared / legacy · '+p.name,path=p.relative_to(self.root).as_posix()) for p in sorted(self.preset_dir.glob('*.yaml')) if p.is_file() and not p.is_symlink())
        return entries

    def get_preset(self,name):
        entry=next((entry for entry in self.presets() if entry['id']==name),None)
        if not entry:raise ValueError('Unknown preset for this layout')
        path=self.root/entry['path']
        return dict(preset=yaml.safe_load(path.read_text()),etag=file_hash(path),file=entry['id'],path=entry['path'])

    def context(self,data,require_visual=False):
        cfg=self.resolved_config()
        layout=self.selected_layout()
        if not layout:
            layout=self.library.import_raw(cfg['gds'])
            cfg['gds']=str(self.library.raw(layout))
            saved=self.config();saved['gds']=self.library.raw(layout).relative_to(self.root).as_posix();write_json(self.config_path,saved)
        if data.get('layout_id') and data['layout_id']!=layout['id']:raise ValueError('Layout changed; reload the preview')
        opts=options(data.get('options',{}));current=self.library.recipe(layout,cfg,opts)
        visual=None
        if require_visual:
            visual=self.library.visual(layout,data.get('visual_id'))
            if visual['fingerprint']!=current['fingerprint']:raise ValueError('Raw GDS, preprocessing code, configuration or options changed. Reload the visual preview before building or saving.')
        return cfg,layout,opts,current,visual

    def check_visual(self,data):
        with self.lock:
            if self.active:raise ValueError('Wait for the active job before checking visual GDS')
            cfg,layout,opts,current,_=self.context(data)
            return dict(layout=layout,**self.library.check_visual(layout,current))

    def lineage(self,layout,visual):
        directory=self.library.visual_dir(layout,visual['id'])
        return dict(layout_id=layout['id'],raw_sha256=layout['raw_sha256'],raw_path=self.library.raw(layout).relative_to(self.root).as_posix(),visual_id=visual['id'],visual_fingerprint=visual['fingerprint'],visual_sha256=visual['outputs'][layout['name']+'_visual.gds'],visual_path=(directory/(layout['name']+'_visual.gds')).relative_to(self.root).as_posix(),stack_path=(directory/'stack.yaml').relative_to(self.root).as_posix())

    def save_preset(self,data):
        with self.lock:
            if self.active:raise ValueError('Wait for the active job before saving a preset')
            cfg,layout,opts,current,visual=self.context(data,require_visual=True)
            value=preset(data.get('name',''),data.get('camera'),opts,data.get('hidden_layers',[]),data.get('base',{}))
            value['webapp']['lineage']=self.lineage(layout,visual)
            return self.library.save_preset(layout,value,data.get('etag'))

    def restore_jobs(self):
        paths=[*self.local.glob('layouts/*/runs/*/result.json'),*self.local.glob('jobs/*/result.json')]
        for path in paths:
            try:
                job=read_json(path)
                if not re.fullmatch(r'[a-f0-9]{16}',job['id']):continue
                if job['status']=='running':job.update(status='interrupted',stage='Interrupted by server restart')
                self.jobs[job['id']]=job;self.job_directories[job['id']]=path.parent
            except (OSError,ValueError,KeyError):continue
        # Interrupted runs may not yet have a result; retain their request and log.
        for path in self.local.glob('layouts/*/runs/*/request.json'):
            if (path.parent/'result.json').exists():continue
            try:
                snap=read_json(path);job=snap['job'];job.update(status='interrupted',stage='Interrupted by server restart',log=[],outputs=[])
                self.jobs[job['id']]=job;self.job_directories[job['id']]=path.parent
            except (OSError,ValueError,KeyError):continue

    def history(self,key):
        layout=self.library.layout(key);runs=[]
        for job in self.jobs.values():
            directory=self.job_directories[job['id']]
            try:
                snapshot=read_json(directory/'request.json')
                if snapshot.get('input_hashes',{}).get('gds')!=layout['raw_sha256']:continue
                item=copy.deepcopy(job);item['directory']=directory.relative_to(self.root).as_posix()
                item['legacy']=not snapshot.get('lineage');item['lineage']=snapshot.get('lineage',{})
                item['preset_name']=(snapshot.get('preset') or {}).get('name','preview')
                runs.append(item)
            except (ValueError,OSError):continue
        return dict(layout=layout,directory=self.library.layout_dir(key).relative_to(self.root).as_posix(),visuals=self.library.visuals(layout),presets=self.library.preset_entries(layout),runs=sorted(runs,key=lambda x:x['started'],reverse=True))

    def resolved_config(self):
        cfg=self.config()
        for key in FILES:
            if not cfg[key]:raise ValueError(f"Provide {FILES[key][0]} in Local setup")
            cfg[key]=str(resolve_file(self.root,cfg[key]))
            if not Path(cfg[key]).is_file():raise ValueError(f"Missing file: {FILES[key][0]}")
        cfg['python']=shutil.which(cfg['python']) or cfg['python']
        cfg['blender']=shutil.which(cfg['blender']) or cfg['blender']
        return cfg

    def start(self,data):
        action=data.get('action')
        if action not in ('preview','build','build_render'):raise ValueError('Unknown action')
        force=data.get('force_visual',False)
        if not isinstance(force,bool):raise ValueError('Force regenerate must be boolean')
        if action!='preview' and force:raise ValueError('Regenerate and preview visual GDS before building')
        with self.lock:
            if self.active:raise ValueError('A job is already running')
            cfg,layout,opts,current,visual=self.context(data,require_visual=action!='preview')
            if action!='preview' and (not cfg['blender'] or not Path(cfg['blender']).is_file()):raise ValueError('Configure Blender before building')
            value=None;saved=None
            if action!='preview':
                value=preset('automatic',data.get('camera'),opts,data.get('hidden_layers',[]),data.get('base',{}))
                value['webapp']['lineage']=self.lineage(layout,visual)
                saved=self.library.save_automatic_preset(layout,value)
                value=saved['preset']
            directory=self.library.allocate_run(layout,action,value['name'] if value else 'preview')
            job_id=secrets.token_hex(8)
            job=dict(options=opts,id=job_id,action=action,status='running',stage='Checking visual GDS',started=time.time(),log=[],outputs=[],cancel=False,layout_id=layout['id'],run_name=directory.name,directory=directory.relative_to(self.root).as_posix())
            if saved:job['saved_preset']={key:saved[key] for key in ('file','path','etag','reused')}
            self.jobs[job_id]=job;self.job_directories[job_id]=directory;self.active=job_id
            snapshot=dict(source_preset=data.get('source_preset'),config=cfg,options=opts,preset=value,input_hashes={k:file_hash(cfg[k]) for k in FILES},job=copy.deepcopy(job),recipe=current,force_visual=force)
            if saved:snapshot['saved_preset']=copy.deepcopy(job['saved_preset'])
            if visual:snapshot['lineage']=self.lineage(layout,visual)
            write_json(directory/'request.json',snapshot)
            if saved:shutil.copyfile(self.root/saved['path'],directory/'preset.yaml')
            threading.Thread(target=self.work,args=(job_id,cfg,opts,directory,snapshot,layout,visual),daemon=True).start()
            return copy.deepcopy(job)

    def run(self,job,command):
        if job['cancel']:raise InterruptedError('Cancelled')
        with self.lock:
            p=subprocess.Popen(command,cwd=self.root,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1,start_new_session=True,env=os.environ|{'PYTHONUNBUFFERED':'1'})
            self.process=p
            if job['cancel']:os.killpg(p.pid,signal.SIGTERM)
        with (self.job_directories[job['id']]/'job.log').open('a') as log:
            for line in p.stdout:
                log.write(line);log.flush()
                with self.lock:job['log']=(job['log']+[line.rstrip()])[-600:]
        code=p.wait()
        with self.lock:self.process=None
        if job['cancel']:raise InterruptedError('Cancelled')
        if code:
            detail=next((line for line in reversed(job['log']) if re.match(r'^(ValueError|RuntimeError|ModuleNotFoundError|ImportError|FileNotFoundError):',line)), '')
            raise RuntimeError(detail or f'{Path(command[0]).name} exited with code {code}. See job log.')

    def work(self,job_id,cfg,opts,directory,snapshot,layout,visual):
        job=self.jobs[job_id];commands=[]
        try:
            if not visual:
                check=self.library.check_visual(layout,snapshot['recipe'])
                visual=check['visual'] if not snapshot['force_visual'] else None
                if visual:
                    job['visual_reused']=True;job['stage']='Reusing '+visual['id']
                else:
                    visual,visual_dir=self.library.new_visual(layout,snapshot['recipe'],snapshot['force_visual'])
                    commands=prepare_commands(cfg,opts,visual_dir,root=self.root,visual_name=layout['name']+'_visual.gds')
                    write_json(visual_dir/'commands.json',commands)
                    write_json(directory/'commands.json',commands)
                    for index,command in enumerate(commands):
                        job['stage']=f"Generating {visual['id']} · step {index+1} / {len(commands)}"
                        self.run(job,command)
                    if self.library.recipe(layout,cfg,opts)!=snapshot['recipe']:raise ValueError('An input or preprocessing code changed during generation; reload and retry')
                    if job['cancel']:raise InterruptedError('Cancelled')
                    visual=self.library.finish_visual(layout,visual);job['visual_reused']=False
            else:job['visual_reused']=True
            lineage=self.lineage(layout,visual);snapshot['lineage']=lineage;job['lineage']=lineage
            write_json(directory/'request.json',snapshot)
            if job['action']=='preview':
                job['stage']='Triangulating '+visual['id']
                command=[cfg['python'],str(self.root/'webapp/server.py'),'--mesh-job',str(directory)]
                commands.append(command);write_json(directory/'commands.json',commands);self.run(job,command)
                self.preview_path=directory/'preview.json';job['preview_url']=f'/api/jobs/{job_id}/preview'
            else:
                output=directory/'build';output.mkdir()
                visual_dir=self.library.visual_dir(layout,visual['id'])
                tail=build_commands(cfg,opts,directory,root=self.root,visual_directory=visual_dir,visual_name=layout['name']+'_visual.gds',build_directory=output)[5:]
                commands+=tail;write_json(directory/'commands.json',commands)
                for index,command in enumerate(tail):
                    job['stage']=f"Building {visual['id']} · step {index+1} / {len(tail)}";self.run(job,command)
                if job['action']=='build_render':
                    job['stage']='Rendering PNG'
                    command=[cfg['blender'],'--background',str(output/'ready.blend'),'--python-exit-code','1','--python',str(self.root/'scripts/aim_render_scene.py'),'--','--preset',str(directory/'preset.yaml'),'--run','web_view','--output-dir',str(directory/'renders')]
                    commands.append(command);write_json(directory/'commands.json',commands);self.run(job,command)
            if self.library.recipe(layout,cfg,opts)!=snapshot['recipe']:raise ValueError('Input or preprocessing code changed while processing; result is not marked complete')
            self.library.visual(layout,visual['id'])
            if job['cancel']:raise InterruptedError('Cancelled')
            expected=directory/('preview.json' if job['action']=='preview' else 'build/ready.blend')
            if not expected.is_file():raise RuntimeError('Pipeline did not produce the expected output')
            if job['action']=='build_render' and not list((directory/'renders').glob('*.png')):raise RuntimeError('Renderer produced no PNG')
            job['outputs']=[dict(name=p.name,url=f'/api/jobs/{job_id}/files/{p.relative_to(directory).as_posix()}') for p in sorted(directory.rglob('*')) if p.is_file() and (p.suffix in ('.blend','.png') or p.name in ('preset.yaml','request.json','commands.json'))]
            job.update(status='completed',stage=f"Complete · {visual['id']} {'reused' if job['visual_reused'] else 'generated'}")
        except InterruptedError:job.update(status='cancelled',stage='Cancelled')
        except Exception as e:job.update(status='failed',stage=str(e));job['log'].append(str(e))
        finally:
            job['finished']=time.time();write_json(directory/'result.json',job)
            with self.lock:self.active=None;self.process=None

    def cancel(self):
        with self.lock:
            if self.active:
                self.jobs[self.active]['cancel']=True
                p=self.process
                if p and p.poll() is None:
                    os.killpg(p.pid,signal.SIGTERM)
                    def kill_remaining():
                        try:p.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            try:os.killpg(p.pid,signal.SIGKILL)
                            except ProcessLookupError:pass
                    threading.Thread(target=kill_remaining,daemon=True).start()
        return {'ok':True}


class Handler(BaseHTTPRequestHandler):
    server_version='GDSStudio/1'
    def log_message(self,*args):pass
    @property
    def studio(self):return self.server.studio
    def valid_host(self):
        return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')
    def json(self,data,status=200):
        raw=json.dumps(data,allow_nan=False).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def file(self,path,content_type=None):
        if not path.is_file():raise ValueError('File not found')
        mime=content_type or mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        self.send_response(200);self.send_header('Content-Type',mime);self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(path.stat().st_size));self.end_headers()
        with path.open('rb') as f:shutil.copyfileobj(f,self.wfile)
    def bounded(self,root,raw):
        path=(root/raw).resolve();path.relative_to(root.resolve())
        if not path.is_file():raise ValueError('File not found')
        return path
    def do_GET(self):
        try:
            if not self.valid_host():return self.json({'error':'Loopback host required'},403)
            path=unquote(urlsplit(self.path).path)
            if path=='/api/status':return self.json(self.studio.status())
            if path=='/setup-guide':return self.file(self.studio.root/'docs/web-app.md','text/plain; charset=utf-8')
            if path=='/api/health':return self.json({'app':'gds-web-studio','root':str(self.studio.root)})
            if path=='/api/layouts':return self.json(self.studio.library.catalog())
            match=re.fullmatch(r'/api/layouts/([^/]+)(?:/files/(.+))?',path)
            if match:
                layout=self.studio.library.layout(match[1])
                if match[2]:return self.file(self.studio.library.file(layout,match[2]))
                return self.json(self.studio.history(match[1]))
            if path=='/api/presets':return self.json(self.studio.presets())
            if path.startswith('/api/presets/'):return self.json(self.studio.get_preset(path.removeprefix('/api/presets/')))
            m=re.fullmatch(r'/api/jobs/([a-f0-9]{16})(?:/(preview|files/(.+)))?',path)
            if m:
                job_id=m[1]
                if job_id not in self.studio.jobs:raise ValueError('Unknown job')
                if not m[2]:return self.json(copy.deepcopy(self.studio.jobs[job_id]))
                root=self.studio.job_directories[job_id]
                if m[2]=='preview':return self.file(root/'preview.json')
                file=self.bounded(root,m[3])
                if file.suffix not in ('.png','.blend') and file.name not in ('preset.yaml','request.json','commands.json','result.json'):raise ValueError('File is not a deliverable')
                return self.file(file)
            if path.startswith('/vendor/'):return self.file(self.bounded(self.studio.root/'webapp/node_modules/three',path.removeprefix('/vendor/')))
            return self.file(self.bounded(self.studio.root/'webapp/static','index.html' if path=='/' else path.lstrip('/')))
        except (ValueError,OSError,KeyError) as e:self.json({'error':str(e)},400)
    def do_POST(self):
        try:
            origin=self.headers.get('Origin')
            if not self.valid_host() or self.headers.get('X-Studio-Token')!=self.studio.token or (origin and origin not in (f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}')):
                return self.json({'error':'Open the local studio page to perform this action'},403)
            path=urlsplit(self.path).path
            if path=='/api/layouts/upload':return self.upload()
            if self.headers.get('Content-Type')!='application/json':raise ValueError('JSON required')
            size=int(self.headers.get('Content-Length','0'))
            if size<=0 or size>1000000:raise ValueError('Invalid request size')
            data=json.loads(self.rfile.read(size))
            if not isinstance(data,dict):raise ValueError('JSON object required')
            if path=='/api/layouts/import':return self.json(self.studio.import_layout(data.get('path','')))
            if path=='/api/layouts/select':return self.json(self.studio.select_layout(data.get('id','')))
            if path=='/api/visual/check':return self.json(self.studio.check_visual(data))
            if self.path=='/api/config':return self.json(self.studio.save_config(data))
            if self.path=='/api/presets':return self.json(self.studio.save_preset(data))
            if self.path=='/api/jobs':return self.json(self.studio.start(data),202)
            if self.path=='/api/cancel':return self.json(self.studio.cancel())
            raise ValueError('Unknown endpoint')
        except (ValueError,OSError,KeyError) as e:self.json({'error':str(e)},400)

    def upload(self):
        from urllib.parse import parse_qs
        name=parse_qs(urlsplit(self.path).query).get('name',[''])[0]
        if not name or Path(name).name!=name or Path(name).suffix.lower()!='.gds':raise ValueError('Choose a .gds file')
        if self.headers.get('Content-Type')!='application/octet-stream':raise ValueError('Binary GDS upload required')
        size=int(self.headers.get('Content-Length','0'))
        if size<6 or size>1024**3:raise ValueError('GDS upload must be between 6 bytes and 1 GiB; use a local path for larger files')
        directory=self.studio.local/'uploads';directory.mkdir(exist_ok=True)
        temporary=directory/(secrets.token_hex(16)+'.gds')
        try:
            with temporary.open('xb') as stream:
                remaining=size
                while remaining:
                    block=self.rfile.read(min(1024*1024,remaining))
                    if not block:raise ValueError('Upload interrupted')
                    stream.write(block);remaining-=len(block)
            return self.json(self.studio.import_layout(temporary,original_name=name,origin='File picker: '+name))
        finally:temporary.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=8768);parser.add_argument('--mesh-job',type=Path);args=parser.parse_args()
    if args.mesh_job:
        data=json.loads((args.mesh_job/'request.json').read_text())
        lineage=data.get('lineage',{})
        visual=ROOT/lineage['visual_path'] if lineage else args.mesh_job/'visual.gds'
        stack=ROOT/lineage['stack_path'] if lineage else args.mesh_job/'stack.yaml'
        result=preview_mesh(visual,stack,data['options'])
        (args.mesh_job/'preview.json').write_text(json.dumps(result,separators=(',',':'),allow_nan=False));return
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler);server.studio=Studio()
    print(f'GDS Studio ready at http://127.0.0.1:{server.server_port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.studio.cancel();server.server_close()
if __name__=='__main__':main()
