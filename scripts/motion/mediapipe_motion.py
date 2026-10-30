"""Optional local MediaPipe visible-body motion DRAFT; no world root trajectory.

CPU Tasks PoseLandmarker -> measured hip-relative 3D directions -> 20-joint BVH.
Twist is an anatomical body-plane hint, not observed axial rotation. The legacy
SOMA sidecar format tag identifies the shared BVH wire format, NOT the estimator.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import time

# Importlib callers must already use -B or disable bytecode: this module may
# be cached before its code executes. This setting protects the sibling import.
sys.dont_write_bytecode = True

import numpy as np

# Resolve the sibling exporter for CLI and bytecode-disabled importlib callers.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_gem_bvh import sha256, write_motion_bvh

NAMES = ('Hips','Chest','Neck','Head',
         'LeftShoulder','LeftArm','LeftForeArm','LeftHand',
         'RightShoulder','RightArm','RightForeArm','RightHand',
         'LeftUpLeg','LeftLeg','LeftFoot','LeftToeBase',
         'RightUpLeg','RightLeg','RightFoot','RightToeBase')
PARENTS = np.array([-1,0,1,2,1,4,5,6,1,8,9,10,0,12,13,14,0,16,17,18])
UPPER_REQUIRED = (7,8,11,12,13,14,15,16,23,24)
FULL_REQUIRED = UPPER_REQUIRED + (25,26,27,28,31,32)
MODEL_URL = ('https://storage.googleapis.com/mediapipe-models/pose_landmarker/'
             'pose_landmarker_lite/float16/latest/pose_landmarker_lite.task')
LIMITATIONS = [
    'Optional MediaPipe single-person visible-body draft; not GEM inference.',
    'Root is fixed in place. Hip-relative model estimates are not a recovered world trajectory.',
    'Camera motion, foot contacts, ground plane, fingers and true limb twist are not solved.',
    'Spine, neck and clavicle centers are anatomical interpolations of model landmarks.',
    'Occluded joints and monocular depth can be inaccurate even with high confidence.',
    'Axial roll uses torso-plane hints; bone lengths are fixed sequence-median estimates.',
    'No post-hoc smoothing or foot locking; visual acceptance is required.',
    'Requires a single-person input; num_poses=1 is not identity tracking and may switch people in a crowd.',
]


def prepare_landmarks(raw, confidence, fps, coverage='full_body', threshold=.5):
    """Reject weak clips, interpolate only <=0.2s internal missing/low-vis runs."""
    raw = np.asarray(raw,dtype=float)
    confidence = np.asarray(confidence,dtype=float)
    if raw.ndim != 3 or raw.shape[1:] != (33,3) or confidence.shape != raw.shape[:2]:
        raise ValueError('Expected (frames,33,3) landmarks and (frames,33) confidence')
    if not math.isfinite(fps) or fps <= 0 or len(raw) < 2:
        raise ValueError('Need at least two frames at a finite positive fps')
    if coverage not in ('full_body','upper_body'):
        raise ValueError('coverage must be full_body or upper_body')
    required = FULL_REQUIRED if coverage == 'full_body' else UPPER_REQUIRED
    detected = np.isfinite(raw[:,required]).all(axis=(1,2))
    good = detected & np.isfinite(confidence[:,required]).all(axis=1) & (confidence[:,required]>=threshold).all(axis=1)
    ratio = float(good.mean())
    if ratio < .8:
        raise ValueError(f'Insufficient {coverage} coverage: {ratio:.1%}; need >=80% frames with every required landmark >= {threshold}')
    if not good[0] or not good[-1]:
        raise ValueError('Missing/low-confidence boundary frame; choose an observed interval')
    missing = np.flatnonzero(~good)
    runs = np.split(missing,np.flatnonzero(np.diff(missing)>1)+1) if len(missing) else []
    if any(len(run)/fps > .2+1e-9 for run in runs):
        raise ValueError('Missing/low-confidence gap exceeds 0.2 seconds')
    filled = raw.copy()
    for run in runs:
        lo, hi = run[0]-1, run[-1]+1
        for i in run:
            filled[i] = raw[lo] + (raw[hi]-raw[lo])*((i-lo)/(hi-lo))
    # Optional peripheral landmarks may be unseen. They are not permitted to
    # introduce NaN; directions below use only required joint endpoints.
    info = {'coverage':coverage,'confidence_threshold':threshold,'required_landmarks':list(required),
            'detected_frame_count':int(detected.sum()),'accepted_observed_frame_count':int(good.sum()),
            'accepted_observed_fraction':ratio,'total_frame_count':len(raw),
            'interpolated_frame_indices':missing.tolist(),
            'low_confidence_frame_indices':np.flatnonzero(detected & ~good).tolist(),
            'missing_pose_frame_indices':np.flatnonzero(~detected).tolist(),
            'max_interpolation_seconds':.2,'per_frame_required_min_confidence':np.nan_to_num(confidence[:,required].min(axis=1),nan=0).tolist()}
    return filled, info


def _unit(value):
    length = np.linalg.norm(value)
    if not np.isfinite(length) or length < 1e-7:
        raise ValueError('Degenerate measured body direction; cannot infer a rotation')
    return value/length


def _frame_y(direction,forward):
    y = _unit(direction)
    z = forward-y*np.dot(forward,y)
    if np.linalg.norm(z) < 1e-5:
        hint = np.array([1.,0,0]) if abs(y[0]) < .9 else np.array([0.,0,1])
        z = hint-y*np.dot(hint,y)
    z = _unit(z)
    x = _unit(np.cross(y,z))
    return np.column_stack((x,y,np.cross(x,y)))


def _body_frame(left,right,up):
    y = _unit(up)
    x = _unit((left-right)-y*np.dot(left-right,y))
    return np.column_stack((x,y,np.cross(x,y)))


def build_motion(landmarks,coverage='full_body'):
    """Create fixed-length in-place transforms from measured model world points.

    Input is MediaPipe world coordinates in meters. diag(1,-1,-1) is a proper
    180-degree X rotation: camera right/down/away -> right/up/toward-camera.
    The skeleton rest faces +Z; left is the subject's anatomical left.
    """
    p = np.asarray(landmarks,dtype=float)*np.array([1.,-1.,-1.])
    p = p-(p[:,23]+p[:,24])[:,None]/2
    frames = len(p)
    hip = (p[:,23]+p[:,24])/2
    shoulder = (p[:,11]+p[:,12])/2
    ears = (p[:,7]+p[:,8])/2
    torso = shoulder-hip
    torso_len = float(np.median(np.linalg.norm(torso,axis=1)))
    width = float(np.median(np.linalg.norm(p[:,11]-p[:,12],axis=1)))
    hip_width = float(np.median(np.linalg.norm(p[:,23]-p[:,24],axis=1)))
    head_len = float(np.median(np.linalg.norm(ears-shoulder,axis=1)))
    if not .12 < torso_len < 1.2 or not .08 < width < 1.2:
        raise ValueError('Implausible or degenerate model body proportions')
    # Bind-frame orientations are identity; offsets encode a symmetric T pose.
    positions = np.zeros((20,3))
    positions[1] = (0,.8*torso_len,0)
    positions[2] = (0,torso_len+.1*head_len,0)
    positions[3] = (0,torso_len+head_len,0)
    directions = [(None,None)]*20
    directions[0] = (torso, np.array([0.,1,0]))
    directions[1] = (torso, np.array([0.,1,0]))
    directions[2] = (ears-shoulder, np.array([0.,1,0]))
    directions[3] = (ears-shoulder, np.array([0.,1,0]))
    for base,sign,sh,el,wr,hand,hp,knee,ank,toe,legbase in [
        (4,1,11,13,15,19,23,25,27,31,12), (8,-1,12,14,16,20,24,26,28,32,16)]:
        positions[base] = (sign*width*.25,torso_len,0)
        positions[base+1] = (sign*width*.5,torso_len,0)
        upper_len = float(np.median(np.linalg.norm(p[:,el]-p[:,sh],axis=1)))
        lower_len = float(np.median(np.linalg.norm(p[:,wr]-p[:,el],axis=1)))
        positions[base+2] = positions[base+1]+(sign*upper_len,0,0)
        positions[base+3] = positions[base+2]+(sign*lower_len,0,0)
        directions[base] = (p[:,sh]-shoulder,np.array([sign,0.,0]))
        directions[base+1] = (p[:,el]-p[:,sh],np.array([sign,0.,0]))
        directions[base+2] = (p[:,wr]-p[:,el],np.array([sign,0.,0]))
        # Hand knuckles are optional and not quality-gated. Inherit forearm:
        # this is an explicitly unsolved wrist, never claimed hand tracking.
        directions[base+3] = directions[base+2]
        if coverage == 'full_body':
            thigh = float(np.median(np.linalg.norm(p[:,knee]-p[:,hp],axis=1)))
            shin = float(np.median(np.linalg.norm(p[:,ank]-p[:,knee],axis=1)))
            foot = float(np.median(np.linalg.norm(p[:,toe]-p[:,ank],axis=1)))
            directions[legbase] = (p[:,knee]-p[:,hp],np.array([0.,-1,0]))
            directions[legbase+1] = (p[:,ank]-p[:,knee],np.array([0.,-1,0]))
            directions[legbase+2] = (p[:,toe]-p[:,ank],np.array([0.,0,1]))
            directions[legbase+3] = directions[legbase+2]
        else:
            thigh,shin,foot = torso_len*.9,torso_len*.9,torso_len*.4
        positions[legbase] = (sign*hip_width*.5,0,0)
        positions[legbase+1] = positions[legbase]+(0,-thigh,0)
        positions[legbase+2] = positions[legbase+1]+(0,-shin,0)
        positions[legbase+3] = positions[legbase+2]+(0,0,foot)
    height = -min(positions[:,1])
    positions[:,1] += height
    rest = np.tile(np.eye(4),(20,1,1))
    rest[:,:3,3] = positions
    # The shared writer gives leaves a +Y End Site. Rotate the leaf bind
    # frames so that bridge aim-mode sees the anatomical hand/toe direction.
    for j,axis in [(7,[1.,0,0]),(11,[-1.,0,0]),(15,[0.,0,1]),(19,[0.,0,1])]:
        rest[j,:3,:3] = _frame_y(np.array(axis),np.array([0.,0,1]))
    world = np.tile(np.eye(4),(frames,20,1,1))
    for i in range(frames):
        pelvis_frame = _body_frame(p[i,23],p[i,24],torso[i])
        chest_frame = _body_frame(p[i,11],p[i,12],torso[i])
        for j in range(20):
            if j == 0:
                rotation = pelvis_frame
            elif j == 1:
                rotation = chest_frame
            elif j == 3:
                rotation = _body_frame(p[i,7],p[i,8],ears[i]-shoulder[i])
            elif coverage == 'upper_body' and j >= 12:
                rotation = pelvis_frame
            else:
                values,bind_axis = directions[j]
                rotation = _frame_y(values[i],chest_frame[:,2]) @ _frame_y(bind_axis,np.array([0.,0,1])).T
            if j in (7,11,15,19):
                rotation = rotation @ rest[j,:3,:3]
            world[i,j,:3,:3] = rotation
            parent = PARENTS[j]
            world[i,j,:3,3] = positions[0] if parent < 0 else world[i,parent,:3,3]+world[i,parent,:3,:3]@(positions[j]-positions[parent])
    return world,rest


def write_bvh(path,world,rest,fps,details):
    path = Path(path)
    info = write_motion_bvh(path,NAMES,PARENTS,world,rest,fps)
    info.update(details)
    info.update(estimator='mediapipe_pose',body_model='none',
                root_motion='in_place_hip_relative',root_motion_mode='in_place',
                world_translation_estimated=False,visual_review_passed=False,
                identity_tracking='none_single_person_input_required',
                status='draft_requires_visual_acceptance',limitations=LIMITATIONS,
                unobserved_dofs=['wrist','fingers','toe_flexion','axial_limb_twist'],
                script_sha256=sha256(Path(__file__)))
    path.with_suffix(path.suffix+'.json').write_text(json.dumps(info,indent=2,ensure_ascii=False),encoding='utf8')
    return info


def infer(video,start,duration,output,model,coverage='full_body'):
    import cv2
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision

    video,model,output = map(lambda x:Path(x).resolve(),(video,model,output))
    if not math.isfinite(start) or start < 0 or not math.isfinite(duration) or duration <= 0:
        raise ValueError('start must be >=0 and duration must be positive and finite')
    if output.suffix.lower() != '.bvh' or not video.is_file() or not model.is_file():
        raise ValueError('Need readable video/model and .bvh output')
    prior_outputs = [output, output.with_suffix(output.suffix+'.json'),
                     output.with_name(output.stem+'.landmarks.npz'),
                     output.with_suffix(output.suffix+'.failure.json')]
    if output.parent.is_dir():
        overlay_prefix = (output.stem+'.overlay_').casefold()
        prior_outputs.extend(path for path in output.parent.iterdir()
                             if path.name.casefold().startswith(overlay_prefix)
                             and path.name.casefold().endswith('.jpg'))
    if any(path.exists() for path in prior_outputs):
        raise ValueError('Output already exists; use a new draft path to preserve provenance')
    output.parent.mkdir(parents=True,exist_ok=True)
    begin = time.monotonic()
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise ValueError('Video cannot be opened')
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not math.isfinite(fps) or fps <= 0:
        cap.release(); raise ValueError('Video has no valid constant frame rate')
    first = math.ceil(start*fps-1e-7)
    stop = math.ceil((start+duration)*fps-1e-7)
    if stop > int(cap.get(cv2.CAP_PROP_FRAME_COUNT)):
        cap.release(); raise ValueError('Requested interval exceeds decoded source length')
    cap.set(cv2.CAP_PROP_POS_FRAMES,first)
    options = vision.PoseLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_buffer=model.read_bytes(),delegate=python.BaseOptions.Delegate.CPU),
        running_mode=vision.RunningMode.VIDEO,num_poses=1,
        min_pose_detection_confidence=.5,min_pose_presence_confidence=.5,min_tracking_confidence=.5)
    raw,normalized,visibility,presence = [],[],[],[]
    samples = {0,(stop-first)//2,stop-first-1}
    edges = [(11,12),(11,13),(13,15),(12,14),(14,16),(11,23),(12,24),(23,24),(23,25),(25,27),(27,31),(24,26),(26,28),(28,32)]
    try:
        with vision.PoseLandmarker.create_from_options(options) as landmarker:
            for offset in range(stop-first):
                ok,frame = cap.read()
                if not ok:
                    raise ValueError(f'Decode failed at source frame {first+offset}')
                image = mp.Image(image_format=mp.ImageFormat.SRGB,data=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB))
                result = landmarker.detect_for_video(image,round((first+offset)*1000/fps))
                if result.pose_world_landmarks and result.pose_landmarks:
                    w,n = result.pose_world_landmarks[0],result.pose_landmarks[0]
                    raw.append([(q.x,q.y,q.z) for q in w])
                    normalized.append([(q.x,q.y,q.z) for q in n])
                    visibility.append([float(q.visibility or 0) for q in n])
                    presence.append([float(q.presence or 0) for q in n])
                else:
                    raw.append(np.full((33,3),np.nan)); normalized.append(np.full((33,3),np.nan))
                    visibility.append(np.zeros(33)); presence.append(np.zeros(33))
                if offset in samples:
                    h,w = frame.shape[:2]
                    for a,b in edges:
                        if np.isfinite(normalized[-1][a]).all() and np.isfinite(normalized[-1][b]).all():
                            pts = [tuple(np.round(np.array(normalized[-1][q][:2])*[w,h]).astype(int)) for q in (a,b)]
                            color = (0,220,0) if min(visibility[-1][a],visibility[-1][b])>=.5 else (0,0,255)
                            cv2.line(frame,*pts,color,2)
                    cv2.putText(frame,f'MediaPipe draft {coverage}; frame {first+offset}; green >=0.5; root IN PLACE',(12,24),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,255,255),1)
                    cv2.imencode('.jpg',frame)[1].tofile(str(output.with_name(output.stem+f'.overlay_{offset:04d}.jpg')))
                if offset % max(1,round(fps)) == 0:
                    print(f'MediaPipe CPU: {offset+1}/{stop-first} frames',flush=True)
    finally:
        cap.release()
    raw,normalized,visibility,presence = map(np.asarray,(raw,normalized,visibility,presence))
    confidence = np.minimum(visibility,presence)
    in_frame = ((normalized[...,:2]>=0)&(normalized[...,:2]<=1)).all(axis=-1)
    confidence[~in_frame] = 0
    raw_path = output.with_name(output.stem+'.landmarks.npz')
    np.savez_compressed(raw_path,world_landmarks_m=raw,normalized_landmarks=normalized,
                        visibility=visibility,presence=presence,confidence=confidence,
                        source_frame_indices=np.arange(first,stop),source_fps=np.array(fps))
    details = {'source_video':str(video),'source_video_sha256':sha256(video),
               'source_interval_start_seconds':first/fps,'requested_start_seconds':start,'requested_duration_seconds':duration,
               'source_frame_start':first,'source_frame_stop_exclusive':stop,
               'model_file':str(model),'model_sha256':sha256(model),'model_download_url':MODEL_URL,
               'mediapipe_version':mp.__version__,'delegate':'CPU','coverage':coverage,
               'raw_landmarks_file':str(raw_path),'raw_landmarks_sha256':sha256(raw_path),
               'source_coordinate_mapping':'MediaPipe (x,y,z) meters -> BVH (x,-y,-z), hip midpoint removed',
               'world_translation_estimated':False,'root_motion':'in_place_hip_relative'}
    try:
        filled,quality = prepare_landmarks(raw,confidence,fps,coverage)
        world,rest = build_motion(filled,coverage)
    except Exception as exc:
        details.update(ok=False,error=str(exc),status='failed_quality_gate_no_bvh',elapsed_seconds=time.monotonic()-begin)
        output.with_suffix(output.suffix+'.failure.json').write_text(json.dumps(details,indent=2,ensure_ascii=False),encoding='utf8')
        raise
    details.update(quality=quality,elapsed_seconds=time.monotonic()-begin,
                   inferred_rotation_change_matrix_max=float(np.max(np.abs(world[...,:3,:3]-world[0:1,...,:3,:3]))))
    if coverage == 'upper_body':
        details['unobserved_body_regions_held_at_bind'] = ['both_legs','feet','toes']
    info = write_bvh(output,world,rest,fps,details)
    print(json.dumps({'ok':True,'output':str(output),'sidecar':str(output)+'.json',
                      'frames':len(world),'fps':fps,'root_motion':info['root_motion'],
                      'status':info['status']},ensure_ascii=False),flush=True)
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video',required=True,type=Path)
    parser.add_argument('--start',type=float,default=0)
    parser.add_argument('--duration',type=float,required=True)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--model',required=True,type=Path)
    parser.add_argument('--coverage',choices=('full_body','upper_body'),default='full_body')
    args = parser.parse_args()
    try:
        infer(args.video,args.start,args.duration,args.output,args.model,args.coverage)
    except Exception as exc:
        print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False),file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
