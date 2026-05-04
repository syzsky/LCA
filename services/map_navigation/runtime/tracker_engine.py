# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
import logging
import os
import sys

import cv2
import numpy as np

_ONNXRUNTIME_DLL_HANDLES = []
logger = logging.getLogger(__name__)

_CUDA_PROVIDER_OPTIONS = {
    "device_id": 0,
    "arena_extend_strategy": "kNextPowerOfTwo",
    "cudnn_conv_algo_search": "EXHAUSTIVE",
    "do_copy_in_default_stream": True,
}
_PROVIDER_ALIASES = {
    "auto": "auto",
    "cpu": "CPUExecutionProvider",
    "cpuexecutionprovider": "CPUExecutionProvider",
    "cuda": "CUDAExecutionProvider",
    "cudaexecutionprovider": "CUDAExecutionProvider",
    "dml": "DmlExecutionProvider",
    "dmlexecutionprovider": "DmlExecutionProvider",
    "directml": "DmlExecutionProvider",
}
_PROVIDER_PRIORITY = (
    "CUDAExecutionProvider",
    "DmlExecutionProvider",
    "CPUExecutionProvider",
)


def _empty_match_result():
    return {
        "keypoints0": np.empty((0, 2), dtype=np.float32),
        "keypoints1": np.empty((0, 2), dtype=np.float32),
        "confidence": np.empty((0,), dtype=np.float32),
    }


def _prepend_path_once(current_path: str, path_to_add: str) -> str:
    parts = [entry for entry in str(current_path or "").split(os.pathsep) if entry]
    normalized_target = os.path.normcase(os.path.abspath(path_to_add))
    normalized_parts = {
        os.path.normcase(os.path.abspath(entry))
        for entry in parts
    }
    if normalized_target in normalized_parts:
        return os.pathsep.join(parts)
    return os.pathsep.join([path_to_add] + parts)


def _prepare_onnxruntime_dll_dirs() -> None:
    spec = importlib.util.find_spec("onnxruntime")
    if spec is None:
        return

    search_locations = tuple(spec.submodule_search_locations or ())
    seen_dirs: set[str] = set()
    for location in search_locations:
        capi_dir = os.path.join(str(location), "capi")
        if not os.path.isdir(capi_dir):
            continue

        normalized_dir = os.path.normcase(os.path.abspath(capi_dir))
        if normalized_dir in seen_dirs:
            continue
        seen_dirs.add(normalized_dir)

        os.environ["PATH"] = _prepend_path_once(os.environ.get("PATH", ""), capi_dir)
        if hasattr(os, "add_dll_directory"):
            try:
                _ONNXRUNTIME_DLL_HANDLES.append(os.add_dll_directory(capi_dir))
            except Exception:
                pass


_prepare_onnxruntime_dll_dirs()
import onnxruntime as ort


def _normalize_provider_name(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return "auto"
    return _PROVIDER_ALIASES.get(normalized, str(value or "").strip())


def _provider_name_from_entry(provider_entry) -> str:
    if isinstance(provider_entry, tuple):
        return str(provider_entry[0])
    return str(provider_entry)


def _build_provider_entry(provider_name: str):
    if provider_name == "CUDAExecutionProvider":
        return (provider_name, dict(_CUDA_PROVIDER_OPTIONS))
    return provider_name


def _build_provider_attempts(
    available_providers: tuple[str, ...],
    *,
    preferred_provider: str = "auto",
) -> list[list[object]]:
    available_set = {str(provider) for provider in available_providers}
    attempts: list[list[object]] = []
    seen_attempts: set[tuple[str, ...]] = set()

    def add_attempt(primary_provider: str) -> None:
        names = [str(primary_provider)]
        if primary_provider != "CPUExecutionProvider":
            names.append("CPUExecutionProvider")

        if primary_provider != "CPUExecutionProvider" and primary_provider not in available_set:
            return
        if "CPUExecutionProvider" not in available_set and primary_provider != "CPUExecutionProvider":
            return

        key = tuple(names)
        if key in seen_attempts:
            return
        seen_attempts.add(key)
        attempts.append([_build_provider_entry(name) for name in names])

    normalized_preference = _normalize_provider_name(preferred_provider)
    if normalized_preference != "auto":
        add_attempt(normalized_preference)

    for provider_name in _PROVIDER_PRIORITY:
        add_attempt(provider_name)

    if not attempts:
        attempts.append(["CPUExecutionProvider"])
    return attempts


class LoftrEngine:
    def __init__(self) -> None:
        base_dir = str(os.environ.get("LCA_LKMAPTOOLS_BASE_DIR", "") or "").strip()
        if not base_dir:
            base_dir = str(getattr(sys, "_MEIPASS", "") or "").strip()
        if not base_dir:
            base_dir = os.getcwd()

        model_path = os.path.join(base_dir, "loftr_model.onnx")
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"找不到模型文件: {model_path}")

        sess_options = ort.SessionOptions()
        try:
            sess_options.intra_op_num_threads = 2
        except Exception:
            pass
        try:
            sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        except Exception:
            pass
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        try:
            available_providers = tuple(str(provider) for provider in (ort.get_available_providers() or ()))
        except Exception:
            available_providers = ()

        preferred_provider = _normalize_provider_name(os.environ.get("LCA_LKMAPTOOLS_ORT_PROVIDER", "auto"))

        self.model_path = model_path
        self.sess_options = sess_options
        self.available_providers = available_providers
        self.preferred_provider = preferred_provider
        self.requested_providers: tuple[str, ...] = ()
        self.active_providers: tuple[str, ...] = ()
        self.backend_label = "unknown"
        self._fallback_to_cpu_attempted = False
        self._last_match_error_signature = ""
        self.input_names: tuple[str, ...] = ()
        self.output_names: tuple[str, ...] = ()
        self._iobinding_enabled = False
        self.session = None

        self._initialize_session(preferred_provider, reason="初始化")

    def _initialize_session(self, preferred_provider: str, *, reason: str) -> None:
        normalized_preference = _normalize_provider_name(preferred_provider)
        provider_attempts = _build_provider_attempts(
            self.available_providers,
            preferred_provider=normalized_preference,
        )

        logger.info(
            "[地图导航引擎] %s ONNX Runtime: preferred=%s available=%s model=%s",
            str(reason or "").strip() or "初始化",
            normalized_preference,
            list(self.available_providers),
            self.model_path,
        )

        session = None
        init_errors: list[str] = []
        for provider_chain in provider_attempts:
            provider_names = tuple(_provider_name_from_entry(entry) for entry in provider_chain)
            try:
                session = ort.InferenceSession(
                    self.model_path,
                    sess_options=self.sess_options,
                    providers=provider_chain,
                )
                self.preferred_provider = normalized_preference
                self.requested_providers = provider_names
                self.active_providers = tuple(session.get_providers() or ())
                primary_provider = self.active_providers[0] if self.active_providers else provider_names[0]
                self.backend_label = str(primary_provider)
                self.session = session
                self._cache_session_io_metadata(session)
                logger.info(
                    "[地图导航引擎] ONNX Runtime 初始化成功: requested=%s active=%s io_binding=%s",
                    list(provider_names),
                    list(self.active_providers),
                    bool(self._iobinding_enabled),
                )
                return
            except Exception as exc:
                error_text = str(exc).strip() or exc.__class__.__name__
                init_errors.append(f"{' -> '.join(provider_names)}: {error_text}")
                logger.warning(
                    "[地图导航引擎] Provider 初始化失败: requested=%s error=%s",
                    list(provider_names),
                    error_text,
                )

        detail = "; ".join(init_errors) if init_errors else "没有可用的 ONNX Runtime provider"
        raise RuntimeError(f"初始化 ONNX Runtime 失败: {detail}")

    def _cache_session_io_metadata(self, session: ort.InferenceSession) -> None:
        input_names: tuple[str, ...] = ()
        output_names: tuple[str, ...] = ()
        try:
            input_names = tuple(
                str(meta.name)
                for meta in (session.get_inputs() or ())
                if getattr(meta, "name", None)
            )
        except Exception:
            input_names = ()

        try:
            output_names = tuple(
                str(meta.name)
                for meta in (session.get_outputs() or ())
                if getattr(meta, "name", None)
            )
        except Exception:
            output_names = ()

        self.input_names = input_names
        self.output_names = output_names
        self._iobinding_enabled = len(input_names) >= 2 and bool(output_names)
        self.input_shapes = tuple(getattr(meta, "shape", ()) for meta in (session.get_inputs() or ()))

    def _can_fallback_to_cpu(self) -> bool:
        if self._fallback_to_cpu_attempted:
            return False
        if "CPUExecutionProvider" not in self.available_providers:
            return False
        current_primary = self.active_providers[0] if self.active_providers else self.backend_label
        return str(current_primary) != "CPUExecutionProvider"

    def _log_match_failure(self, exc: Exception, *, will_retry_on_cpu: bool) -> None:
        current_backend = self.active_providers[0] if self.active_providers else self.backend_label or "unknown"
        error_text = str(exc).strip() or exc.__class__.__name__
        signature = f"{current_backend}|{error_text}|retry={int(bool(will_retry_on_cpu))}"
        if signature == self._last_match_error_signature:
            return
        self._last_match_error_signature = signature
        if will_retry_on_cpu:
            logger.exception(
                "[地图导航引擎] 推理失败，准备降级到 CPU 重试: backend=%s error=%s",
                current_backend,
                error_text,
            )
            return
        logger.exception(
            "[地图导航引擎] 推理失败: backend=%s error=%s",
            current_backend,
            error_text,
        )

    def describe_backend(self) -> str:
        preferred = self.preferred_provider or "auto"
        requested = ",".join(self.requested_providers) if self.requested_providers else "unknown"
        active = ",".join(self.active_providers) if self.active_providers else "unknown"
        return f"preferred={preferred}; requested={requested}; active={active}"

    def _get_target_size(self, input_index: int) -> tuple[int, int] | None:
        try:
            shape = self.input_shapes[int(input_index)]
            height = int(shape[2])
            width = int(shape[3])
        except Exception:
            return None
        if width <= 0 or height <= 0:
            return None
        return width, height

    def preprocess(self, img_bgr, input_index: int = 0):
        target_size = self._get_target_size(input_index)
        try:
            umat_img = cv2.UMat(img_bgr)
            umat_gray = cv2.cvtColor(umat_img, cv2.COLOR_BGR2GRAY)
            h, w = umat_gray.get().shape
            if target_size is not None:
                new_w, new_h = target_size
            else:
                new_h = h - (h % 8)
                new_w = w - (w % 8)
            if new_h != h or new_w != w:
                umat_gray = cv2.resize(umat_gray, (new_w, new_h))
            img_gray = umat_gray.get()
        except Exception:
            img_gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
            h, w = img_gray.shape
            if target_size is not None:
                new_w, new_h = target_size
            else:
                new_h = h - (h % 8)
                new_w = w - (w % 8)
            if new_h != h or new_w != w:
                img_gray = cv2.resize(img_gray, (new_w, new_h))

        img_float = img_gray.astype(np.float32) / 255.0
        tensor = np.expand_dims(img_float, axis=0)
        tensor = np.expand_dims(tensor, axis=0)
        return tensor

    def _run_session_once(self, inputs: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        if self.session is None:
            raise RuntimeError("ONNX Runtime session 未初始化")

        if self._iobinding_enabled and len(self.input_names) >= 2 and self.output_names:
            try:
                io_binding = self.session.io_binding()
                io_binding.bind_cpu_input(self.input_names[0], inputs["image0"])
                io_binding.bind_cpu_input(self.input_names[1], inputs["image1"])
                for name in self.output_names:
                    io_binding.bind_output(name)
                self.session.run_with_iobinding(io_binding)
                outputs = io_binding.copy_outputs_to_cpu()
                if len(outputs) != len(self.output_names):
                    raise RuntimeError(
                        f"IO Binding 输出数量异常: expected={len(self.output_names)} actual={len(outputs)}"
                    )
                return {name: value for name, value in zip(self.output_names, outputs)}
            except Exception as exc:
                self._iobinding_enabled = False
                logger.warning(
                    "[地图导航引擎] IO Binding 不可用，已回退到常规推理: backend=%s error=%s",
                    self.active_providers[0] if self.active_providers else self.backend_label,
                    str(exc).strip() or exc.__class__.__name__,
                )

        outputs = self.session.run(None, inputs)
        output_names = self.output_names
        if not output_names:
            output_names = tuple(
                str(output.name)
                for output in (self.session.get_outputs() or ())
                if getattr(output, "name", None)
            )
            self.output_names = output_names
        if len(output_names) != len(outputs):
            if len(outputs) == 3:
                output_names = ("keypoints0", "keypoints1", "confidence")
            else:
                raise RuntimeError(
                    f"ONNX Runtime 输出数量异常: expected={len(output_names)} actual={len(outputs)}"
                )
        return {name: value for name, value in zip(output_names, outputs)}

    def match(self, mini_tensor, local_tensor):
        inputs = {
            "image0": mini_tensor,
            "image1": local_tensor,
        }
        try:
            return self._run_session_once(inputs)
        except Exception as exc:
            retry_on_cpu = self._can_fallback_to_cpu()
            self._log_match_failure(exc, will_retry_on_cpu=retry_on_cpu)
            if retry_on_cpu:
                self._fallback_to_cpu_attempted = True
                try:
                    self._initialize_session("cpu", reason="推理失败后自动降级")
                    logger.warning("[地图导航引擎] 已切换到 CPUExecutionProvider，重试当前帧")
                    return self._run_session_once(inputs)
                except Exception as retry_exc:
                    self._log_match_failure(retry_exc, will_retry_on_cpu=False)
            return _empty_match_result()
