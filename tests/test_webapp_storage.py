"""Behavioral checks for source retention, visual invalidation and artifact lineage."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'webapp'))
import pipeline
from storage import Library, PREPROCESS_SCRIPTS, VISUAL_FILES, read_json, write_json, automatic_preset, digest
spec=importlib.util.spec_from_file_location('lineage_server',ROOT/'webapp/server.py')
server=importlib.util.module_from_spec(spec);spec.loader.exec_module(server)
CAMERA={'location':[10,-20,30],'rotation_degrees':[30,0,25],'lens_mm':80}

class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.workspace=Path(self.temp.name);self.root=self.workspace/'repo';self.root.mkdir()
        (self.root/'scripts').mkdir();(self.root/'webapp').mkdir()
        for name in PREPROCESS_SCRIPTS:(self.root/'scripts'/name).write_text('# preprocessing\n')
        (self.root/'webapp/pipeline.py').write_text('# pipeline\n')
        (self.root/'configs/finishing').mkdir(parents=True)
        for name in ('aim_fill_cheese.json','aim_diam_rules.json'):
            shutil.copyfile(ROOT/'configs/finishing'/name,self.root/'configs/finishing'/name)
        shutil.copyfile(ROOT/'scripts/aim_fill_cheese.py',self.root/'scripts/aim_fill_cheese.py')
        self.source=self.workspace/'Chip Raw.GDS';self.gds(self.source)
        self.library=Library(self.root);self.layout=self.library.import_raw(self.source)
        self.opts=pipeline.options({});self.cfg={'python':sys.executable,'blender':sys.executable,'gds':str(self.library.raw(self.layout))}
        for key in pipeline.FILES:
            if key!='gds':
                p=self.root/(key+'.yaml');p.write_text('test: original\n');self.cfg[key]=str(p)
        self.env=patch.object(Library,'environment',return_value={'python':'test','packages':{'gdstk':'1'}});self.env.start();self.addCleanup(self.env.stop)

    def gds(self,path,extent=10):
        import gdstk
        lib=gdstk.Library();cell=lib.new_cell('CHIP');cell.add(gdstk.rectangle((0,0),(extent,10),layer=10));lib.write_gds(str(path))

    def ready_visual(self,force=False):
        recipe=self.library.recipe(self.layout,self.cfg,self.opts)
        value,directory=self.library.new_visual(self.layout,recipe,force)
        shutil.copyfile(self.library.raw(self.layout),directory/(self.layout['name']+'_visual.gds'))
        for name in VISUAL_FILES:(directory/name).write_text('test: generated\n')
        return self.library.finish_visual(self.layout,value)

    def studio(self):
        studio=server.Studio(self.root);write_json(studio.config_path,self.cfg);return studio

    def test_external_import_and_local_raw_share_identity(self):
        source_bytes=self.source.read_bytes();raw=self.library.raw(self.layout)
        self.assertTrue(raw.name.endswith('_raw.gds'));self.assertEqual(raw.read_bytes(),source_bytes)
        self.assertEqual(self.library.import_raw(raw)['id'],self.layout['id'])
        self.assertEqual(self.library.import_raw(self.source)['id'],self.layout['id'])
        self.source.unlink();self.assertEqual(self.library.raw(self.layout).read_bytes(),source_bytes)
        self.assertEqual(len(self.library.catalog()),1)

    def test_same_filename_new_content_preserves_both_raw_versions(self):
        old=self.library.raw(self.layout).read_bytes();self.gds(self.source,extent=20)
        newer=self.library.import_raw(self.source)
        self.assertNotEqual(newer['id'],self.layout['id']);self.assertEqual(self.library.raw(self.layout).read_bytes(),old)
        self.assertEqual(len(self.library.catalog()),2)

    def test_invalid_gds_and_path_escape_are_refused(self):
        bad=self.workspace/'bad.gds';bad.write_text('Not a GDS')
        with self.assertRaises(ValueError):self.library.import_raw(bad)
        with self.assertRaises(ValueError):self.library.layout('../outside')
        with self.assertRaises(ValueError):self.library.visual_dir(self.layout,'../../outside')
        with self.assertRaises(ValueError):self.library.file(self.layout,'../../tech.yaml')
        linked=self.library.layout_dir(self.layout['id'])/'presets';linked.symlink_to(self.workspace,target_is_directory=True)
        value=pipeline.preset('test',CAMERA,self.opts,[])
        with self.assertRaises(ValueError):self.library.save_preset(self.layout,value)

    def test_source_change_during_copy_leaves_no_partial_archive(self):
        self.gds(self.source,extent=21);original=shutil.copyfile
        def mutate(source,target):
            original(source,target);Path(source).write_bytes(Path(source).read_bytes()+b'changed')
        with patch('storage.shutil.copyfile',side_effect=mutate),self.assertRaisesRegex(ValueError,'changed during import'):
            self.library.import_raw(self.source)
        self.assertEqual(len(self.library.catalog()),1)
        self.assertFalse(list(self.library.base.glob('.import-*')))

    def test_raw_tampering_is_not_reused(self):
        raw=self.library.raw(self.layout);raw.write_bytes(raw.read_bytes()+b'changed')
        with self.assertRaisesRegex(ValueError,'raw GDS changed'):self.library.recipe(self.layout,self.cfg,self.opts)

    def test_visual_reuse_ignores_camera_render_and_mesh_only_options(self):
        visual=self.ready_visual();altered=pipeline.options({'samples':12,'scheme':'marketing','preview_tolerance':1,'z_scale':3})
        recipe=self.library.recipe(self.layout,self.cfg,altered)
        self.assertEqual(self.library.check_visual(self.layout,recipe)['visual']['id'],visual['id'])
        self.assertEqual(self.library.check_visual(self.layout,recipe)['state'],'ready')

    def test_fill_cheese_has_separate_visual_identity_and_restores_unfilled_cache(self):
        plain=self.ready_visual()
        original=read_json(self.library.visual_dir(self.layout,plain['id'])/'manifest.json')
        self.opts=pipeline.options({'fill_cheese':True})
        filled_recipe=self.library.recipe(self.layout,self.cfg,self.opts)
        self.assertEqual(self.library.check_visual(self.layout,filled_recipe)['state'],'stale')
        filled=self.ready_visual()
        self.assertNotEqual(plain['id'],filled['id'])
        self.assertEqual(self.library.check_visual(self.layout,filled_recipe)['visual']['id'],filled['id'])
        plain_recipe=self.library.recipe(self.layout,self.cfg,pipeline.options({}))
        self.assertEqual(self.library.check_visual(self.layout,plain_recipe)['visual']['id'],plain['id'])
        self.assertEqual(read_json(self.library.visual_dir(self.layout,plain['id'])/'manifest.json'),original)
        preset=pipeline.preset('filled',CAMERA,self.opts,[])
        self.assertTrue(preset['webapp']['options']['fill_cheese'])

    def test_fill_profiles_and_engine_invalidate_only_enabled_visuals(self):
        plain=self.library.recipe(self.layout,self.cfg,self.opts)
        self.opts=pipeline.options({'fill_cheese':True})
        for path in ('configs/finishing/aim_fill_cheese.json','configs/finishing/aim_diam_rules.json','scripts/aim_fill_cheese.py'):
            with self.subTest(path=path):
                filled=self.library.recipe(self.layout,self.cfg,self.opts)
                target=self.root/path;target.write_text(target.read_text()+'\n')
                self.assertNotEqual(self.library.recipe(self.layout,self.cfg,self.opts),filled)
                self.assertEqual(self.library.recipe(self.layout,self.cfg,pipeline.options({})),plain)

    def legacy_visual(self, relocated=False):
        value=self.ready_visual();directory=self.library.visual_dir(self.layout,value['id'])
        recipe=copy.deepcopy(value['recipe']);recipe['format']=1;recipe.pop('plan')
        recipe['scripts']['webapp/pipeline.py']=pipeline.file_hash(self.root/'webapp/pipeline.py')
        value.update(recipe=recipe,fingerprint=digest(recipe));write_json(directory/'manifest.json',value)
        config=self.cfg;command_root=self.root;output=directory
        if relocated:
            config={key:'/previous machine/private inputs/'+key for key in pipeline.FILES}|{'python':'/previous machine/python'}
            command_root=Path('/previous machine/repo');output=Path('/previous machine/archive/visual')
        commands=pipeline.prepare_commands(config,self.opts,output,root=command_root,visual_name=self.layout['name']+'_visual.gds')
        write_json(directory/'commands.json',commands)
        return value

    def test_unrelated_pipeline_source_changes_do_not_invalidate_visual(self):
        visual=self.ready_visual();before=self.library.recipe(self.layout,self.cfg,self.opts)
        (self.root/'webapp/pipeline.py').write_text('# changed render defaults, camera and Blender build code\n')
        after=self.library.recipe(self.layout,self.cfg,pipeline.options({'width':800,'samples':4096,'metal_bevel':False}))
        self.assertEqual(before,after)
        self.assertEqual(self.library.check_visual(self.layout,after)['visual']['id'],visual['id'])

    def test_changed_preprocessing_command_plan_invalidates_visual(self):
        self.ready_visual();original=pipeline.prepare_commands
        def changed(*args,**kwargs):
            commands=original(*args,**kwargs);commands[-1].append('--no-undercut');return commands
        with patch('storage.prepare_commands',side_effect=changed):
            current=self.library.recipe(self.layout,self.cfg,self.opts)
        check=self.library.check_visual(self.layout,current)
        self.assertEqual(check['state'],'stale');self.assertIn('command plan',check['reason'])

    def test_legacy_visual_reused_by_preview_and_build_without_rewriting_history(self):
        visual=self.legacy_visual();directory=self.library.visual_dir(self.layout,visual['id'])
        before=(directory/'manifest.json').read_bytes()
        (self.root/'webapp/pipeline.py').write_text('# unrelated build-only fix\n')
        studio=self.studio();checked=studio.check_visual({'options':self.opts})
        self.assertEqual(checked['visual']['id'],visual['id'])
        with patch.object(server.threading.Thread,'start'):
            job=studio.start(dict(action='build',camera=CAMERA,options=self.opts,visual_id=visual['id']))
        request=read_json(studio.job_directories[job['id']]/'request.json')
        self.assertEqual(request['lineage']['visual_fingerprint'],visual['fingerprint'])
        self.assertEqual(request['recipe']['recipe']['format'],2)
        self.assertEqual((directory/'manifest.json').read_bytes(),before)
        self.assertEqual(len(list((directory.parent).glob('v*'))),1)

    def test_legacy_command_paths_can_move_without_changing_the_recipe(self):
        visual=self.legacy_visual(relocated=True)
        current=self.library.recipe(self.layout,self.cfg,self.opts)
        self.assertEqual(self.library.check_visual(self.layout,current)['visual']['id'],visual['id'])

    def test_legacy_missing_changed_or_inconsistent_commands_are_not_reused(self):
        visual=self.legacy_visual();path=self.library.visual_dir(self.layout,visual['id'])/'commands.json'
        original=read_json(path);current=self.library.recipe(self.layout,self.cfg,self.opts)
        path.unlink();self.assertEqual(self.library.check_visual(self.layout,current)['state'],'stale')
        changed=copy.deepcopy(original);changed[-1].append('--no-undercut')
        write_json(path,changed);self.assertEqual(self.library.check_visual(self.layout,current)['state'],'stale')
        changed=copy.deepcopy(original);changed[1][changed[1].index('--doping')+1]='/other/doping.yaml'
        write_json(path,changed);self.assertEqual(self.library.check_visual(self.layout,current)['state'],'stale')
        path.write_text('not json');self.assertEqual(self.library.check_visual(self.layout,current)['state'],'stale')
        write_json(path,original);self.assertEqual(self.library.check_visual(self.layout,current)['state'],'ready')

    def test_legacy_compatibility_keeps_real_input_and_code_checks(self):
        visual=self.legacy_visual();current=self.library.recipe(self.layout,self.cfg,self.opts)
        for field in ('raw_sha256','inputs','options','scripts','environment'):
            with self.subTest(field=field):
                changed=copy.deepcopy(current);changed['recipe'][field]='changed';changed['fingerprint']=digest(changed['recipe'])
                self.assertFalse(self.library.visual_matches(self.layout,visual,changed))
        changed=copy.deepcopy(current);changed['recipe']['plan'][-1].append('--no-undercut');changed['fingerprint']=digest(changed['recipe'])
        self.assertFalse(self.library.visual_matches(self.layout,visual,changed))

    def test_force_after_legacy_reuse_creates_a_new_version(self):
        visual=self.legacy_visual();directory=self.library.visual_dir(self.layout,visual['id']);before=(directory/'manifest.json').read_bytes()
        current=self.library.recipe(self.layout,self.cfg,self.opts)
        self.assertEqual(self.library.check_visual(self.layout,current)['state'],'ready')
        forced=self.ready_visual(force=True)
        self.assertEqual(forced['id'],'v0002');self.assertTrue(forced['forced']);self.assertEqual(forced['recipe']['format'],2)
        self.assertEqual((directory/'manifest.json').read_bytes(),before)
        self.assertEqual(self.library.check_visual(self.layout,current)['visual']['id'],'v0002')

    def test_preprocess_option_and_config_and_code_changes_invalidate(self):
        self.ready_visual();opts=pipeline.options({'metal_fillet':0.4})
        self.assertIn('options',self.library.check_visual(self.layout,self.library.recipe(self.layout,self.cfg,opts))['reason'])
        Path(self.cfg['tech']).write_text('changed: yes\n')
        self.assertIn('configuration',self.library.check_visual(self.layout,self.library.recipe(self.layout,self.cfg,self.opts))['reason'])
        (self.root/'scripts/aim_preprocess_gds.py').write_text('# changed\n')
        self.assertIn('code',self.library.check_visual(self.layout,self.library.recipe(self.layout,self.cfg,self.opts))['reason'])

    def test_changed_environment_invalidates(self):
        self.ready_visual()
        with patch.object(Library,'environment',return_value={'python':'updated'}):
            check=self.library.check_visual(self.layout,self.library.recipe(self.layout,self.cfg,self.opts))
        self.assertEqual(check['state'],'stale');self.assertIn('environment',check['reason'])

    def test_incomplete_or_corrupt_visual_is_not_reused(self):
        recipe=self.library.recipe(self.layout,self.cfg,self.opts)
        value,directory=self.library.new_visual(self.layout,recipe,False)
        self.assertEqual(self.library.check_visual(self.layout,recipe)['state'],'missing')
        visual=self.ready_visual();path=self.library.visual_dir(self.layout,visual['id'])/(self.layout['name']+'_visual.gds');path.write_bytes(b'corrupt')
        self.assertEqual(self.library.check_visual(self.layout,recipe)['state'],'missing')

    def test_force_makes_new_visual_version_without_changing_old(self):
        first=self.ready_visual();path=self.library.visual_dir(self.layout,first['id'])/'manifest.json';original=path.read_bytes()
        second=self.ready_visual(force=True)
        self.assertEqual(second['id'],'v0002');self.assertTrue(second['forced']);self.assertEqual(path.read_bytes(),original)
        self.assertEqual(self.library.check_visual(self.layout,self.library.recipe(self.layout,self.cfg,self.opts))['visual']['id'],'v0002')

    def test_preset_versions_keep_raw_visual_links_and_stale_save_guard(self):
        visual=self.ready_visual();studio=self.studio()
        data=dict(name='view',camera=CAMERA,options=self.opts,hidden_layers=[],layout_id=self.layout['id'],visual_id=visual['id'])
        first=studio.save_preset(data);value=first['preset'];self.assertEqual(value['webapp']['lineage']['raw_sha256'],self.layout['raw_sha256'])
        with self.assertRaisesRegex(ValueError,'latest'):studio.save_preset(data)
        second=studio.save_preset(data|{'etag':first['etag'],'camera':dict(CAMERA,lens_mm=90)})
        self.assertTrue(first['file'].endswith('p0001'));self.assertTrue(second['file'].endswith('p0002'))
        self.assertEqual(studio.get_preset(first['file'])['preset']['camera']['lens_mm'],80)
        self.assertEqual(studio.get_preset(second['file'])['preset']['camera']['lens_mm'],90)
        self.assertEqual(len(self.library.preset_entries(self.layout)),2)
        self.assertIn(visual['id'],self.library.preset_entries(self.layout)[0]['label'])
        with self.assertRaises(ValueError):studio.get_preset('../private.yaml')

    def test_build_refuses_stale_visual_and_forced_unpreviewed_geometry(self):
        visual=self.ready_visual();studio=self.studio()
        data=dict(action='build',name='view',camera=CAMERA,options=self.opts,layout_id=self.layout['id'],visual_id=visual['id'])
        with self.assertRaisesRegex(ValueError,'Regenerate and preview'):studio.start(data|{'force_visual':True})
        with self.assertRaisesRegex(ValueError,'Reload the visual preview'):studio.start(data|{'options':dict(self.opts,metal_fillet=0.3)})
        (self.root/'scripts/aim_preprocess_gds.py').write_text('# newer code\n')
        with self.assertRaisesRegex(ValueError,'Reload the visual preview'):studio.start(data)
        self.assertFalse(studio.active)
        self.assertEqual(self.library.preset_entries(self.layout),[])

    def test_automatic_name_uses_production_settings_not_preview_or_manual_name(self):
        original=pipeline.preset('old_name',CAMERA,self.opts,['M2AM_RENDER','M1AM_RENDER'])
        first=automatic_preset(original, self.layout['name'])
        other=pipeline.preset('different_name',dict(CAMERA,lens_mm=80.0),self.opts|{'preview_tolerance':5,'preview_limit':1000},['M1AM_RENDER','M2AM_RENDER','M1AM_RENDER'])
        other['output']['directory']='/different/local/path'
        self.assertEqual(first['name'],automatic_preset(other, self.layout['name'])['name'])
        self.assertNotIn('preview_limit',first['webapp']['options'])
        self.assertRegex(first['name'],r'^Chip_Raw_realistic_80mm_3200x2000_[a-f0-9]{12}$')
        for changed in [
            pipeline.preset('view',dict(CAMERA,lens_mm=90),self.opts,['M2AM_RENDER','M1AM_RENDER']),
            pipeline.preset('view',CAMERA,self.opts|{'denoise':False},['M2AM_RENDER','M1AM_RENDER']),
            pipeline.preset('view',CAMERA,self.opts|{'metal_bevel':False},['M2AM_RENDER','M1AM_RENDER']),
            pipeline.preset('view',CAMERA,self.opts,[]),
            pipeline.preset('view',CAMERA,self.opts,['M2AM_RENDER','M1AM_RENDER'],{'lighting':{'sun':{'strength':3}}}),
        ]:
            with self.subTest(changed=changed):self.assertNotEqual(first['name'],automatic_preset(changed, self.layout['name'])['name'])

    def test_current_gds_name_overrides_loaded_preset_name(self):
        value=pipeline.preset('auto_other_layout',CAMERA,self.opts,[])
        first=self.library.save_automatic_preset(self.layout,value)
        second_source=self.workspace/'Second Chip.gds';shutil.copyfile(self.source,second_source)
        second_layout=self.library.import_raw(second_source)
        second=self.library.save_automatic_preset(second_layout,first['preset'])
        self.assertTrue(first['preset']['name'].startswith(self.layout['name']+'_'))
        self.assertTrue(second['preset']['name'].startswith(second_layout['name']+'_'))
        self.assertNotEqual(first['preset']['name'],second['preset']['name'])
        self.assertEqual(first['preset']['webapp']['automatic'],second['preset']['webapp']['automatic'])
    def test_full_long_gds_name_round_trips_without_renaming_old_presets(self):
        source=self.workspace/('long_layout_'*5+'.gds');shutil.copyfile(self.source,source)
        layout=self.library.import_raw(source)
        old=pipeline.preset('auto_legacy_camera',CAMERA,self.opts,[])
        legacy=self.library.save_preset(layout,old);before=(self.root/legacy['path']).read_bytes()
        saved=self.library.save_automatic_preset(layout,old);name=saved['preset']['name']
        self.assertEqual(len(layout['name']),48)
        self.assertTrue(name.startswith(layout['name']+'_'))
        self.assertGreater(len(name),64);self.assertLessEqual(len(name),128)
        studio=self.studio();write_json(studio.config_path,self.cfg|{'gds':str(self.library.raw(layout))})
        self.assertEqual(studio.get_preset(saved['file'])['preset']['name'],name)
        run=self.library.allocate_run(layout,'build_render',name)
        self.assertIn(name,run.name)
        self.assertEqual(self.library.save_automatic_preset(layout,old)['file'],saved['file'])
        self.assertEqual((self.root/legacy['path']).read_bytes(),before)
        self.assertEqual(studio.get_preset(legacy['file'])['preset']['name'],'auto_legacy_camera')
    def test_both_build_actions_save_before_work_and_reuse_exact_preset(self):
        visual=self.ready_visual();studio=self.studio()
        data=dict(camera=CAMERA,options=self.opts,hidden_layers=['M2AM_RENDER'],layout_id=self.layout['id'],visual_id=visual['id'])
        saved=[]
        with patch.object(server.threading.Thread,'start') as worker:
            for action in ['build','build_render']:
                job=studio.start(data|{'action':action})
                reference=job['saved_preset'];saved.append(reference)
                archived=self.root/reference['path'];run=studio.job_directories[job['id']]
                self.assertTrue(archived.is_file())
                self.assertEqual((run/'preset.yaml').read_bytes(),archived.read_bytes())
                request=read_json(run/'request.json')
                self.assertTrue(request['preset']['name'].startswith(self.layout['name']+'_'))
                self.assertIn(request['preset']['name'],run.name)
                self.assertEqual(request['saved_preset'],reference)
                self.assertEqual(request['preset']['camera']['lens_mm'],80)
                self.assertEqual(request['preset']['webapp']['lineage']['visual_id'],visual['id'])
                self.assertIsNone(request['source_preset'])
                studio.active=None
            self.assertEqual(worker.call_count,2)
        self.assertFalse(saved[0]['reused']);self.assertTrue(saved[1]['reused'])
        self.assertEqual(saved[0]['file'],saved[1]['file'])
        self.assertEqual(len(self.library.preset_entries(self.layout)),1)
        restarted=server.Studio(self.root)
        self.assertEqual(restarted.get_preset(saved[0]['file'])['preset']['camera']['lens_mm'],80)
        self.assertEqual(len(restarted.history(self.layout['id'])['runs']),2)

    def test_new_visual_gets_linked_version_without_overwriting_same_settings(self):
        visual=self.ready_visual();studio=self.studio()
        value=pipeline.preset('ignored',CAMERA,self.opts,[])
        value['webapp']['lineage']=studio.lineage(self.layout,visual)
        first=self.library.save_automatic_preset(self.layout,value)
        before=(self.root/first['path']).read_bytes()
        newer=self.ready_visual(force=True)
        updated=copy.deepcopy(value);updated['webapp']['lineage']=studio.lineage(self.layout,newer)
        second=self.library.save_automatic_preset(self.layout,updated)
        self.assertEqual(first['preset']['name'],second['preset']['name'])
        self.assertTrue(second['file'].endswith('p0002'))
        self.assertEqual((self.root/first['path']).read_bytes(),before)
        self.assertEqual(self.library.save_automatic_preset(self.layout,value)['file'],first['file'])

    def test_failed_save_never_starts_build_and_existing_presets_are_preserved(self):
        visual=self.ready_visual();studio=self.studio()
        old=pipeline.preset('manual_camera',CAMERA,self.opts,[])
        saved=self.library.save_preset(self.layout,old);before=(self.root/saved['path']).read_bytes()
        data=dict(action='build',camera=CAMERA,options=self.opts,visual_id=visual['id'],source_preset=saved['file'])
        with patch.object(studio.library,'save_automatic_preset',side_effect=OSError('disk full')),patch.object(server.threading.Thread,'start') as worker:
            with self.assertRaisesRegex(OSError,'disk full'):studio.start(data)
            worker.assert_not_called()
        self.assertFalse(studio.active);self.assertFalse(studio.jobs)
        self.assertEqual((self.root/saved['path']).read_bytes(),before)

    def test_preview_does_not_save_a_preset(self):
        studio=self.studio()
        with patch.object(server.threading.Thread,'start'):
            studio.start(dict(action='preview',options=self.opts))
        self.assertEqual(self.library.preset_entries(self.layout),[])

    def test_history_survives_restart_and_links_exact_run_inputs(self):
        visual=self.ready_visual();studio=self.studio();directory=self.library.allocate_run(self.layout,'build_render','camera_a')
        job=dict(id='a'*16,status='completed',action='build_render',started=1,log=[],outputs=[],lineage=studio.lineage(self.layout,visual),run_name=directory.name)
        snap=dict(job=job,input_hashes={'gds':self.layout['raw_sha256']},lineage=job['lineage'],preset={'name':'camera_a'})
        write_json(directory/'request.json',snap);write_json(directory/'result.json',job)
        restored=server.Studio(self.root);history=restored.history(self.layout['id'])
        self.assertEqual(len(history['runs']),1);self.assertEqual(history['runs'][0]['lineage']['visual_id'],visual['id'])
        self.assertIn('camera_a_build_render',history['runs'][0]['directory'])
        self.assertEqual(restored.job_directories['a'*16],directory)

    def test_interrupted_run_is_visible_after_restart(self):
        directory=self.library.allocate_run(self.layout,'preview','preview')
        job=dict(id='b'*16,status='running',action='preview',started=1)
        write_json(directory/'request.json',dict(job=job,input_hashes={'gds':self.layout['raw_sha256']}))
        restored=server.Studio(self.root)
        self.assertEqual(restored.history(self.layout['id'])['runs'][0]['status'],'interrupted')

    def test_build_commands_consume_archived_visual_and_separate_build_directory(self):
        visual=self.ready_visual();vd=self.library.visual_dir(self.layout,visual['id']);output=self.root/'out'
        # Real GDS needs a corresponding real stack for available-layer detection.
        (vd/'stack.yaml').write_text(yaml.safe_dump({'SILICON':{'index':10,'type':0}}))
        commands=pipeline.build_commands(self.cfg,self.opts,self.root,root=self.root,visual_directory=vd,visual_name=self.layout['name']+'_visual.gds',build_directory=output)[5:]
        self.assertEqual(len(commands),2)
        self.assertIn(str(vd/(self.layout['name']+'_visual.gds')),commands[0])
        self.assertIn(str(output/'ready.blend'),commands[1])
        self.assertTrue(all('aim_preprocess_gds.py' not in ' '.join(command) for command in commands))

if __name__=='__main__':unittest.main()
