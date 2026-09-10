"""VantaFlight V0.5 vision primitives and pipeline.

The package is additive to the V0.3 flight core and deliberately depends only
on NumPy/OpenCV. Camera, inference, and aircraft integration use protocols and
normalized data rather than hardware or MAVSDK imports.
"""
from .association import AssociationResult, CandidateAssociator
from .capture import (
    BaseCameraSource,
    CameraManager,
    CameraSource,
    ExternalCameraSource,
    FileCameraSource,
    FrameBuffer,
    FrameBufferMetrics,
    GazeboCameraSource,
    NetworkCameraSource,
    PreviewBuffer,
    PreviewBufferMetrics,
    SyntheticCameraSource,
    USBCameraSource,
)
from .concepts import (
    AircraftState,
    CameraProfile,
    CoordinateFrame,
    FramePacket,
    ObservedPose3D,
    PoseEstimate,
    PredictedPose3D,
    TargetCandidate,
    TargetProfile,
)
from .detection import ClassicalTargetDetector, DetectorConfig, order_corners_clockwise
from .fusion import FusionResult, VantaFusion, VisionEvidence
from .inference import AsyncDetectionResult, AsyncDetector, ONNXDetector
from .latency import LatencyTimeline, predict_position_for_latency
from .lock import LockConfig, LockSnapshot, LockState, TargetLock
from .optical_flow import OpticalFlowResult, PyramidalLK
from .pipeline import VisionPipeline, VisionPipelineResult
from .pose import PoseConfig, PoseEstimator, PoseResult
from .preprocess import OpenCVPreprocessor, PreprocessConfig, PreprocessResult
from .roi import ROIPolicyConfig, ROISearchPolicy, ROIState
from .scene import SceneObject, VantaScene
from .synthetic import project_planar_target, render_target_image
from .tracking import (
    KalmanTargetTracker,
    MultiTargetTracker,
    TargetTrack,
    TrackerConfig,
    TrackSnapshot,
    TrackStatus,
)
from .transforms import (
    body_to_world,
    camera_to_body,
    camera_to_world,
    default_optical_to_frd,
    invert_transform,
    transform_point,
)

__all__ = [
    "AircraftState", "AssociationResult", "AsyncDetectionResult", "AsyncDetector",
    "BaseCameraSource", "CameraManager", "CameraProfile", "CameraSource",
    "CandidateAssociator", "ClassicalTargetDetector", "CoordinateFrame", "DetectorConfig",
    "ExternalCameraSource", "FileCameraSource", "FrameBuffer", "FrameBufferMetrics",
    "FramePacket", "FusionResult", "GazeboCameraSource",
    "KalmanTargetTracker", "LatencyTimeline", "LockConfig", "LockSnapshot", "LockState",
    "MultiTargetTracker", "NetworkCameraSource", "ObservedPose3D", "ONNXDetector",
    "OpenCVPreprocessor", "OpticalFlowResult", "PoseConfig", "PoseEstimate", "PoseEstimator",
    "PoseResult", "PredictedPose3D", "PreprocessConfig", "PreprocessResult", "PreviewBuffer",
    "PreviewBufferMetrics", "PyramidalLK", "ROIPolicyConfig", "ROISearchPolicy", "ROIState", "SceneObject",
    "SyntheticCameraSource", "TargetCandidate", "TargetLock", "TargetProfile",
    "TargetTrack", "TrackSnapshot", "TrackStatus", "TrackerConfig", "USBCameraSource",
    "VantaFusion", "VantaScene",
    "VisionEvidence", "VisionPipeline", "VisionPipelineResult", "body_to_world",
    "camera_to_body", "camera_to_world", "default_optical_to_frd", "invert_transform",
    "order_corners_clockwise", "predict_position_for_latency", "project_planar_target",
    "render_target_image", "transform_point",
]
