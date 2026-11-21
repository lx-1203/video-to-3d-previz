"""Shot-local observations from OrderFlow YOLOX, RTMPose17 and optional YuNet."""

import math

import av
import cv2
import numpy as np
import onnxruntime as ort
from rtmlib import RTMPose

if __package__:
    from .fullclip_people_faces import undo_rotation
else:
    from fullclip_people_faces import undo_rotation


JOINTS = "nose left_eye right_eye left_ear right_ear left_shoulder right_shoulder left_elbow right_elbow left_wrist right_wrist left_hip right_hip left_knee right_knee left_ankle right_ankle".split()


class BodyPose(RTMPose):
    """Use the local checkpoint and stock RTMPose SimCC decoder with bounded CPU threads."""

    def __init__(self, path):
        self.model_input_size = (192, 256)
        self.mean = np.array((123.675, 116.28, 103.53))
        self.std = np.array((58.395, 57.12, 57.375))
        self.backend, self.device, self.to_openpose = "onnxruntime", "cpu", False
        options = ort.SessionOptions()
        options.intra_op_num_threads, options.inter_op_num_threads = 2, 1
        self.session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        if self.session.get_inputs()[0].shape[1:] != [3, 256, 192]:
            raise ValueError("Expected 256x192 RTMPose input model")
        outputs = self.session.get_outputs()
        if len(outputs) != 2 or [o.shape[-1] for o in outputs] != [384, 512]:
            raise ValueError("Expected COCO17 SimCC model")

    def measure(self, rgb, box, turns=0):
        view = np.rot90(rgb, turns)
        h, w = rgb.shape[:2]
        # A whole-image proposal has no detector identity and is gated below.
        region = box if not turns else [0, 0, view.shape[1], view.shape[0]]
        crop, center, scale = self.preprocess(view, region)
        kp, score = self.postprocess(self.inference(crop), center, scale)
        if kp.shape != (1, 17, 2) or score.shape != (1, 17):
            raise ValueError("Invalid runtime COCO17 output")
        points = undo_rotation(kp[0], turns, w, h)
        result = {}
        for name, (u, v), s in zip(JOINTS, points, score[0]):
            if not np.isfinite([u, v, s]).all():
                raise ValueError("Nonfinite pose checkpoint output")
            visible = 0 <= u < w and 0 <= v < h and s >= 0.35
            result[name] = {
                "u": round(u, 3),
                "v": round(v, 3),
                "score": round(float(s), 5),
                "visibility": "model_estimate" if visible else "unknown",
                "source": "rtmpose",
            }
        return result


def match_faces(bodies, faces):
    choices = []
    for bi, body in enumerate(bodies):
        box = body["bbox"]
        bw, bh = box[2] - box[0], box[3] - box[1]
        for fi, face in enumerate(faces):
            x1, y1, x2, y2 = face["bbox"]
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            if (
                box[0] - 0.13 * bw <= cx <= box[2] + 0.13 * bw
                and box[1] - 0.12 * bh <= cy <= box[3] + 0.12 * bh
            ):
                nose = body["joints2d"]["nose"]
                reference = (
                    [nose["u"], nose["v"]]
                    if nose["visibility"] != "unknown"
                    else [(box[0] + box[2]) / 2, box[1] + 0.2 * bh]
                )
                cost = math.dist(reference, [cx, cy]) / max(y2 - y1, x2 - x1, bh * 0.09)
                if cost < 2:
                    choices.append((cost, bi, fi))
    used = set()
    for _, bi, fi in sorted(choices):
        if fi not in used and "face" not in bodies[bi]:
            bodies[bi]["face"] = faces[fi]
            used.add(fi)
    return used


def face_joints(face, width, height):
    result = {
        k: {"u": None, "v": None, "score": 0.0, "visibility": "unknown", "source": "missing"}
        for k in JOINTS
    }
    for idx, name in enumerate(("right_eye", "left_eye", "nose")):
        u, v = face["landmarks"][idx]
        vis = 0 <= u < width and 0 <= v < height
        result[name] = {
            "u": u,
            "v": v,
            "score": face["score"],
            "visibility": "model_estimate" if vis else "unknown",
            "source": "yunet_face_score",
        }
    return result


def measure_frames(video, model, detection_rows, face_rows, cuts):
    cv2.setNumThreads(2)
    estimator = BodyPose(model)
    result = []
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        stream.thread_count = 2
        w, h = stream.width, stream.height
        for i, frame in enumerate(container.decode(stream)):
            if i >= len(detection_rows):
                raise ValueError("Detector coverage shorter than video")
            row = detection_rows[i]
            if (row["source_zero_based"], row["pts"], row["time_base"]) != (
                i,
                frame.pts,
                str(frame.time_base),
            ):
                raise ValueError(f"Source and detector frame/PTS disagree at {i}")
            rgb = frame.to_ndarray(format="rgb24")
            actors = []
            for track in row["observed_tracks"]:
                box = track["box_xyxy"]
                actors.append(
                    {
                        "body_id": track["window_local_track_id"],
                        "bbox": box,
                        "detector_score": track["score"],
                        "bbox_kind": "person_detection",
                        "joints2d": estimator.measure(rgb, box),
                    }
                )
            faces = []
            if face_rows is not None:
                fr = face_rows[i]
                if (fr["source_frame"], fr["pts"], fr["time_base"]) != (
                    i,
                    frame.pts,
                    str(frame.time_base),
                ):
                    raise ValueError("Face frame/PTS mismatch")
                faces = fr["faces"]
            matched = match_faces(actors, faces)
            for j, face in enumerate(faces):
                if j in matched:
                    continue
                x1, y1, x2, y2 = face["bbox"]
                if max((x2 - x1) / w, (y2 - y1) / h) < 0.18:
                    continue
                actors.append(
                    {
                        "body_id": None,
                        "bbox": face["bbox"],
                        "bbox_kind": "face_detection",
                        "detector_score": face["score"],
                        "face": face,
                        "joints2d": face_joints(face, w, h),
                    }
                )
            if not actors:
                # Probe an unboxed crop in each orientation. Accept only multi-joint
                # anatomical support. A numeric model estimate alone is insufficient.
                ranked = []
                for turns in (0, 1, 3, 2):
                    joints = estimator.measure(rgb, [0, 0, w, h], turns)
                    good = [j for j in joints.values() if j["visibility"] != "unknown"]
                    scores = sorted((j["score"] for j in good), reverse=True)
                    if len(scores) < 5 or scores[4] < 0.47:
                        continue
                    pts = np.array([[j["u"], j["v"]] for j in good])
                    lo, hi = pts.min(axis=0), pts.max(axis=0)
                    if min((hi - lo) / [w, h]) < 0.07:
                        continue
                    box = [float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1])]
                    ranked.append(
                        (
                            sum(scores[:5]),
                            {
                                "body_id": None,
                                "bbox": box,
                                "bbox_kind": "unboxed_pose_extent",
                                "detector_score": None,
                                "joints2d": joints,
                            },
                        )
                    )
                if ranked:
                    actors.append(max(ranked, key=lambda pair: pair[0])[1])
            shot = int(np.searchsorted(cuts, i, side="right") - 1)
            result.append(
                {
                    "source_frame": i,
                    "pts": frame.pts,
                    "time_base": str(frame.time_base),
                    "shot_id": f"S{shot + 1:02d}",
                    "actors": actors,
                }
            )
    if len(result) != len(detection_rows) or len(result) != cuts[-1]:
        raise ValueError("Frame range does not cover the complete source")
    return w, h, result
