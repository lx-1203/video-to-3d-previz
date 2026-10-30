import importlib.util,json,sys,tempfile,unittest,copy
from pathlib import Path
from unittest.mock import patch
import torch,numpy as np
ROOT=Path(__file__).resolve().parents[1];PRIVATE=ROOT/'scripts'/'motion';sys.path.insert(0,str(PRIVATE))
import export_gem_bvh as exporter
import motion_workbench as mw

def fixture_result(postprocessed=True):
 n=3;source={'path':'fixture.mp4','fps':30.,'frame_count':3,'width':1280,'height':720,'duration':.1,'requested_start':0.,'requested_duration':.1,'selected_duration':.1}
 r={'format':'gem_body_video_predictions_v1','length':torch.tensor([n]),'sampling_fps':30.,'frame_times':torch.arange(n,dtype=torch.float64).reshape(1,n)/30,'video':{'source':source,'normalized':{'frame_count':3,'fps':30.,'width':1280,'height':720,'duration':.1}},'coordinates':{'global':'raw_world_rollout_meters'},'pred_soma_params_global':{'body_pose':torch.zeros(1,n,228),'global_orient':torch.zeros(1,n,3),'identity_coeffs':torch.zeros(1,n,128),'scale_params':torch.zeros(1,n,57),'transl':torch.zeros(1,n,3)},'tracking':{'frame_ids':np.arange(n,dtype=np.int64)},'byte_metadata':b''}
 if postprocessed:r['postprocessing']={'method':'soma_contact_translation_ccd_ik'};r['raw_predictions']={'pred_soma_params_global':r['pred_soma_params_global']}
 return r

class UnexpectedCheckpointType:
 pass

class RestrictedLoading(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.pt=Path(self.tmp.name)/'motion.pt';torch.save(fixture_result(),self.pt)
 def test_old_policy_rejects_bytes(self):
  core=np._core if hasattr(np,'_core') else np.core
  symbols=[core.multiarray._reconstruct,np.ndarray,np.dtype]+[type(np.dtype(x)) for x in (np.float32,np.int32,np.int64,np.bool_)]
  with torch.serialization.safe_globals(symbols),self.assertRaisesRegex(Exception,'bytes'):
   torch.load(self.pt,map_location='cpu',weights_only=True)
 def test_scoped_bytes_policy_accepts_and_restores_globals(self):
  before=list(torch.serialization.get_safe_globals());r=exporter.load_prediction_checkpoint(self.pt)
  # PyTorch exposes a list created from a set; registration membership is the contract.
  self.assertEqual(r['byte_metadata'],b'');self.assertCountEqual(before,torch.serialization.get_safe_globals())
 def test_scoped_loader_preserves_preexisting_overlapping_globals(self):
  with torch.serialization.safe_globals([bytes,np.dtype]):
   before=list(torch.serialization.get_safe_globals());exporter.load_prediction_checkpoint(self.pt)
   self.assertEqual(set(before),set(torch.serialization.get_safe_globals()))
 def test_unexpected_global_still_rejected(self):
  torch.save({'unexpected':UnexpectedCheckpointType()},self.pt)
  with self.assertRaises(Exception):exporter.load_prediction_checkpoint(self.pt)

class BackendTree(unittest.TestCase):
 def test_sparse_absence_is_bound_and_present_files_hashed(self):
  import export_resume as recovery
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);present=root/'code.py';present.write_text('fixed');missing=root/'docs.md'
   before=recovery.tree_digest(root,[present,missing],absent_skip_worktree=[missing])
   present.write_text('changed');after=recovery.tree_digest(root,[present,missing],absent_skip_worktree=[missing])
   self.assertNotEqual(before['sha256'],after['sha256']);self.assertEqual(before['file_count'],2)
 def test_unmarked_missing_tracked_file_is_rejected(self):
  import export_resume as recovery
  with tempfile.TemporaryDirectory() as temp:
   with self.assertRaises(ValueError):recovery.tree_digest(Path(temp),[Path(temp)/'missing.py'])
 def test_backend_revision_failure_uses_controller_error_type(self):
  import export_resume as recovery
  from types import SimpleNamespace
  with patch.object(recovery.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='different-head')):
   with self.assertRaises(mw.WorkbenchError):recovery.backend_snapshot({'backend':{'root':str(ROOT),'revision':'selected-head'}},wb=mw)

class ResumeGuards(unittest.TestCase):
 def setUp(self):
  import export_resume as recovery
  self.recovery=recovery;self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.jobdir=self.root/'job';self.attempt=self.jobdir/'attempts/extract-123456789abc';self.attempt.mkdir(parents=True)
  self.video=self.root/'fixture.mp4';self.video.write_bytes(b'video');self.template=self.root/'template.blend';self.template.write_bytes(b'template');self.profile=self.root/'profile.json';self.profile.write_text('{}')
  self.backend=self.root/'backend';self.backend.mkdir();self.backend_source=self.backend/'code.py';self.backend_source.write_text('fixed backend');self.yaml=self.root/'backend.yaml';self.yaml.write_text('fixed config');self.assets=self.root/'assets';self.assets.mkdir();(self.assets/'body.npz').write_bytes(b'assets')
  self.config=self.root/'config.json';self.config.write_text(json.dumps({'schema_version':1,'ffmpeg':str(sys.executable),'backend':{'python':sys.executable,'root':str(self.backend),'motion_config':str(self.yaml),'soma_assets':str(self.assets),'revision':'fixture-commit','device':'cuda'}}))
  self.job={'schema_version':1,'job_id':'fixture-job','inputs':{k:mw.artifact(v) for k,v in {'video':self.video,'template':self.template,'profile':self.profile,'config':self.config}.items()},'video_metadata':{'fps_num':30,'fps_den':1,'frame_count':3,'width':1280,'height':720,'duration':.1},'interval':{'start_seconds':0.,'duration':.1},'outputs':{},'motion_provenance':None,'review':None,'history':[],'failure':{'stage':'extract','code':'PROCESS_FAILED','message':f'Process exited 1. Inspect {self.attempt/"export.log"}'}}
  mw.write_json(self.jobdir/'job.json',self.job)
  for filename,pp in [('motion_raw.pt',False),('motion.pt',True)]:
   r=fixture_result(pp);r['video']['source']['path']=str(self.video);torch.save(r,self.attempt/filename)
  config=mw.read_config(self.config);commands=self.recovery.expected_commands(mw,self.jobdir,self.job,self.attempt,config,original_exporter=PRIVATE/'export_gem_bvh.py')
  for stem,argv in commands.items():mw.write_json(self.attempt/(stem+'.command.json'),argv)
  (self.attempt/'inference.log').write_text(f'Saved motion-video predictions to {self.attempt/"motion_raw.pt"}\n')
  (self.attempt/'contact_postprocess.log').write_text(f'Saved motion-cache predictions to {self.attempt/"motion.pt"}\n')
  (self.attempt/'export.log').write_text('Traceback\n_pickle.UnpicklingError: Weights only load failed.\nUnsupported global: GLOBAL bytes\n')
  self.snapshot_patch=patch.object(self.recovery,'backend_snapshot',lambda c,**kw:{'revision':c['backend']['revision'],'motion_config':mw.artifact(self.yaml),'source':mw.artifact(self.backend_source),'assets':mw.artifact(self.assets/'body.npz')});self.snapshot_patch.start();self.addCleanup(self.snapshot_patch.stop)
 def make_receipt(self):return self.recovery.audit_checkpoint(mw,self.jobdir,self.attempt.name,mw.digest(self.attempt/'motion_raw.pt'),mw.digest(self.attempt/'motion.pt'),original_exporter=PRIVATE/'export_gem_bvh.py')
 def test_valid_checkpoint_binds_stage_and_source(self):
  r=self.make_receipt();self.assertEqual(r['attempt']['path'],str(self.attempt.resolve()));self.assertEqual(r['processed_parameters']['sha256'],mw.digest(self.attempt/'motion.pt'))
 def test_wrong_sha_rejected(self):
  with self.assertRaises(mw.WorkbenchError):self.recovery.audit_checkpoint(mw,self.jobdir,self.attempt.name,'0'*64,mw.digest(self.attempt/'motion.pt'),original_exporter=PRIVATE/'export_gem_bvh.py')
 def test_wrong_attempt_argv_rejected(self):
  p=self.attempt/'inference.command.json';v=mw.read_json(p);v[v.index('--start')+1]='1';mw.write_json(p,v)
  with self.assertRaises(mw.WorkbenchError):self.make_receipt()
 def test_missing_postprocess_rejected(self):
  r=fixture_result(False);r['video']['source']['path']=str(self.video);torch.save(r,self.attempt/'motion.pt')
  with self.assertRaises(mw.WorkbenchError):self.make_receipt()
 def test_missing_postprocess_saved_marker_rejected(self):
  (self.attempt/'contact_postprocess.log').write_text('postprocess started but did not finish')
  with self.assertRaises(mw.WorkbenchError):self.make_receipt()
 def test_processed_raw_link_mismatch_rejected(self):
  r=fixture_result(True);r['video']['source']['path']=str(self.video);r['raw_predictions']=copy.deepcopy(r['raw_predictions']);r['raw_predictions']['pred_soma_params_global']['transl']+=1;torch.save(r,self.attempt/'motion.pt')
  with self.assertRaises(mw.WorkbenchError):self.make_receipt()
 def test_checkpoint_source_interval_mismatch_rejected(self):
  r=fixture_result(True);r['video']['source']['path']=str(self.video);r['video']['source']['requested_start']=1;torch.save(r,self.attempt/'motion.pt')
  with self.assertRaises(mw.WorkbenchError):self.make_receipt()
 def test_receipt_wrong_sha_rejected_before_export(self):
  receipt=self.root/'receipt.json';mw.write_json(receipt,self.make_receipt())
  with patch.object(mw,'run_logged') as run,self.assertRaises(mw.WorkbenchError):
   mw.extract(self.jobdir,self.job,resume_checkpoint=receipt,receipt_sha256='0'*64)
  run.assert_not_called()
 def test_input_change_after_receipt_rejected(self):
  r=self.make_receipt();self.video.write_bytes(b'changed')
  with self.assertRaises(mw.WorkbenchError):self.recovery.validate_receipt(mw,self.jobdir,self.job,r)
 def test_backend_change_after_receipt_rejected(self):
  r=self.make_receipt();self.backend_source.write_text('changed')
  with self.assertRaises(mw.WorkbenchError):self.recovery.validate_receipt(mw,self.jobdir,self.job,r)
 def test_processed_checkpoint_change_after_receipt_rejected(self):
  r=self.make_receipt();(self.attempt/'motion.pt').write_bytes(b'changed')
  with self.assertRaises(mw.WorkbenchError):self.recovery.validate_receipt(mw,self.jobdir,self.job,r)
 def test_non_export_failure_rejected(self):
  self.job['failure']['message']=f'Process exited 1. Inspect {self.attempt/"contact_postprocess.log"}';mw.write_json(self.jobdir/'job.json',self.job)
  with self.assertRaises(mw.WorkbenchError):self.make_receipt()
 def test_failed_resume_preserves_original_snapshot_for_retry(self):
  receipt=self.root/'receipt.json';r=self.make_receipt();mw.write_json(receipt,r)
  before=mw.digest(self.jobdir/'job.json');directory,job=mw.load_job(self.jobdir)
  with patch.object(mw,'run_logged',side_effect=mw.WorkbenchError('PROCESS_FAILED','temporary CPU export failure')),self.assertRaises(mw.WorkbenchError):
   mw.extract(directory,job,resume_checkpoint=receipt,receipt_sha256=mw.digest(receipt))
  self.assertEqual(before,mw.digest(self.jobdir/'job.json'));self.assertEqual(dict(job),self.job)
  self.assertFalse((self.jobdir/'.operation.lock').exists())
  self.recovery.validate_receipt(mw,directory,job,r)
 def test_resume_registers_through_extract_without_inference(self):
  r=self.make_receipt();receipt=self.root/'receipt.json';mw.write_json(receipt,r);directory,job=mw.load_job(self.jobdir);calls=[]
  def exporter_only(argv,log,**kwargs):
   calls.append(list(map(str,argv)));self.assertTrue(str(argv[1]).endswith('export_gem_bvh.py'));self.assertNotIn('gem.inference',argv)
   out=Path(argv[argv.index('--output')+1]);world=np.tile(np.eye(4),(3,1,1,1));rest=np.eye(4)[None];info=exporter.write_motion_bvh(out,['Hips'],[-1],world,rest,30);info.update(input_sha256=mw.digest(self.attempt/'motion.pt'),postprocessed=True);mw.write_json(out.with_suffix('.bvh.json'),info)
  with patch.object(mw,'run_logged',exporter_only):res=mw.extract(directory,job,resume_checkpoint=receipt,receipt_sha256=mw.digest(receipt))
  self.assertEqual(res['state'],'motion_ready');self.assertIsNone(job['failure']);self.assertEqual(job['motion_provenance']['route'],'gem_video_inference');self.assertEqual(job['history'][-1]['stage'],'extract');self.assertEqual(len(calls),1)

if __name__=='__main__':unittest.main(verbosity=2)
