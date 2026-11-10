"""Reproducible QtWebEngine/Cesium benchmark for God's Eye View.

Run on the target Windows machine with the web build present:
    python scripts/benchmark_gev_real.py

The JSON retains every sampled CPU/RSS value and every browser/Cesium frame
interval. requestAnimationFrame and Cesium Scene.postRender are reported
separately; only postRender is treated as an actual Cesium render.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import re
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import psutil
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.gods_eye.widget import active_window, get_default_controller, request_open

SAMPLE_INTERVAL_S = 0.5
PROC = psutil.Process()
GPU_SAMPLER: Any | None = None


class _Gpu3DSampler:
    def __init__(self) -> None:
        try:
            import win32pdh
        except ImportError:
            self._pdh = None
            self._query = None
            self._reason = "pywin32 PDH indisponible"
            self._counters: dict[str, Any] = {}
            return
        self._pdh = win32pdh
        self._query = win32pdh.OpenQuery()
        self._reason = "aucun compteur GPU Engine 3D pour le processus Qt/Python"
        self._counters = {}
        self._instances: list[str] = []
        self._instances_refresh_at = 0.0

    def sample(self, pids: set[int]) -> dict[str, Any]:
        if self._pdh is None or self._query is None:
            return {
                "gpu_3d_utilization_pct": f"NON MESURÉE — {self._reason}",
                "gpu_3d_engines": [],
            }
        try:
            if time.perf_counter() >= self._instances_refresh_at:
                _, self._instances = self._pdh.EnumObjectItems(
                    None, None, "GPU Engine", self._pdh.PERF_DETAIL_WIZARD
                )
                self._instances_refresh_at = time.perf_counter() + 5.0
            for instance in self._instances:
                match = re.match(r"pid_(\d+)_", instance)
                if not match or int(match.group(1)) not in pids or "engtype_3D" not in instance:
                    continue
                if instance in self._counters:
                    continue
                path = self._pdh.MakeCounterPath(
                    (None, "GPU Engine", instance, None, 0, "Utilization Percentage")
                )
                self._counters[instance] = self._pdh.AddCounter(self._query, path)
            self._pdh.CollectQueryData(self._query)
            values = []
            for instance, counter in self._counters.items():
                match = re.match(r"pid_(\d+)_", instance)
                if not match or int(match.group(1)) not in pids:
                    continue
                try:
                    status, raw = self._pdh.GetFormattedCounterValue(
                        counter, self._pdh.PDH_FMT_DOUBLE
                    )
                except self._pdh.error:
                    continue
                value = raw.get("doubleValue") if isinstance(raw, dict) else raw
                if isinstance(value, (int, float)) and math.isfinite(value):
                    values.append({"instance": instance, "utilization_pct": round(value, 2)})
            if not values:
                return {
                    "gpu_3d_utilization_pct": (
                        "NON MESURÉE — aucun échantillon 3D PDH valide pour les PIDs du benchmark"
                    ),
                    "gpu_3d_engines": [],
                }
            self._reason = ""
            return {
                "gpu_3d_utilization_pct": round(
                    max(value["utilization_pct"] for value in values), 2
                ),
                "gpu_3d_engines": values,
            }
        except self._pdh.error as error:
            self._reason = f"lecture des compteurs PDH impossible: {error}"
            return {
                "gpu_3d_utilization_pct": f"NON MESURÉE — {self._reason}",
                "gpu_3d_engines": [],
            }

    def close(self) -> None:
        if self._pdh is not None and self._query is not None:
            self._pdh.CloseQuery(self._query)
            self._query = None

HARNESS_JS = r"""
(function() {
  if (window.__gevBench) return 'already_installed';
  const bench = {
    rafFrames: 0,
    rafTimes: [],
    rafLast: null,
    rafHandle: null,
    startedAt: 0,
    actionResult: null,
    actionError: null,
    actionCapture: null,
    longTasks: [],
    longTaskObserver: null,
    tick(ts) {
      if (this.rafLast !== null) {
        this.rafFrames++;
        this.rafTimes.push(ts - this.rafLast);
      }
      this.rafLast = ts;
      this.rafHandle = requestAnimationFrame((next) => this.tick(next));
    },
    startCapture() {
      this.rafFrames = 0;
      this.rafTimes = [];
      this.rafLast = null;
      this.startedAt = performance.now();
      this.longTasks = [];
      this.actionCapture = null;
      if (this.longTaskObserver) this.longTaskObserver.disconnect();
      this.longTaskObserver = null;
      if (window.PerformanceObserver &&
          PerformanceObserver.supportedEntryTypes.includes('longtask')) {
        this.longTaskObserver = new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) this.longTasks.push(entry.duration);
        });
        this.longTaskObserver.observe({ entryTypes: ['longtask'] });
      }
      window.jarvisWorld.__benchmark.startRenderProbe();
      this.rafHandle = requestAnimationFrame((ts) => this.tick(ts));
    },
    stopCapture() {
      if (this.rafHandle !== null) cancelAnimationFrame(this.rafHandle);
      this.rafHandle = null;
      if (this.longTaskObserver) this.longTaskObserver.disconnect();
      const elapsedMs = performance.now() - this.startedAt;
      return JSON.stringify({
        elapsedMs,
        raf: { frames: this.rafFrames, frameTimesMs: this.rafTimes },
        cesium: JSON.parse(window.jarvisWorld.__benchmark.stopRenderProbe()),
        actionCapture: this.actionCapture,
        longTasksMs: this.longTasks,
        longTaskApiAvailable: Boolean(window.PerformanceObserver &&
          PerformanceObserver.supportedEntryTypes.includes('longtask'))
      });
    },
    startAction(action, args) {
      this.actionResult = null;
      this.actionError = null;
      this.actionCapture = null;
      Promise.resolve(window.jarvisWorld.handle(JSON.stringify({ action, args })))
        .then((raw) => {
          this.actionResult = typeof raw === 'string' ? JSON.parse(raw) : raw;
          if (this.rafHandle !== null) {
            this.actionCapture = {
              elapsedMs: performance.now() - this.startedAt,
              raf: { frames: this.rafFrames, frameTimesMs: [...this.rafTimes] },
              cesium: JSON.parse(window.jarvisWorld.__benchmark.snapshotRenderProbe())
            };
          }
        })
        .catch((error) => { this.actionError = String(error); });
      return 'started';
    },
    actionStatus() {
      if (this.actionError) return { error: this.actionError };
      return this.actionResult;
    },
    pageInfo() {
      const canvas = document.querySelector('#jarvis-globe canvas');
      let renderer = null;
      if (canvas) {
        const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
        const debug = gl && gl.getExtension('WEBGL_debug_renderer_info');
        if (debug) renderer = gl.getParameter(debug.UNMASKED_RENDERER_WEBGL);
      }
      return JSON.stringify({
        title: document.title,
        status: document.querySelector('#jarvis-status-text')?.textContent || null,
        canvas: canvas ? { width: canvas.width, height: canvas.height } : null,
        webglRenderer: renderer,
        userAgent: navigator.userAgent,
        dpr: window.devicePixelRatio
      });
    }
  };
  window.__gevBench = bench;
  return 'installed';
})();
"""


def _process_tree_snapshot() -> dict[str, Any]:
    processes = [PROC]
    try:
        processes.extend(PROC.children(recursive=True))
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

    cpu_pct = 0.0
    rss_bytes = 0
    alive = []
    for process in processes:
        try:
            cpu_pct += process.cpu_percent(interval=None)
            rss_bytes += process.memory_info().rss
            alive.append({"pid": process.pid, "name": process.name()})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    logical_cpus = psutil.cpu_count(logical=True) or 1
    snapshot = {
        "process_tree_cpu_pct": round(cpu_pct, 2),
        "process_tree_cpu_pct_machine": round(cpu_pct / logical_cpus, 2),
        "system_cpu_pct": psutil.cpu_percent(interval=None),
        "process_tree_rss_mb": round(rss_bytes / 1_048_576, 1),
        "process_count": len(alive),
        "processes": alive,
    }
    if GPU_SAMPLER is not None:
        snapshot.update(GPU_SAMPLER.sample({process["pid"] for process in alive}))
    else:
        snapshot["gpu_3d_utilization_pct"] = (
            "NON MESURÉE — sonde PDH non initialisée"
        )
        snapshot["gpu_3d_engines"] = []
    return snapshot


def _run_js(page: Any, app: QApplication, script: str, timeout_s: float = 10.0) -> Any:
    result: list[Any] = []
    done = threading.Event()

    def callback(value: Any) -> None:
        result.append(value)
        done.set()

    page.runJavaScript(script, callback)
    deadline = time.perf_counter() + timeout_s
    while not done.is_set() and time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.005)
    if not done.is_set():
        raise TimeoutError(f"QWebEngine JavaScript did not complete in {timeout_s:.1f}s")
    value = result[0]
    if isinstance(value, str) and value.startswith(("{", "[")):
        return json.loads(value)
    return value


def _start_action(page: Any, app: QApplication, action: str, args: dict[str, Any]) -> None:
    encoded_action = json.dumps(action)
    encoded_args = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
    started = _run_js(
        page,
        app,
        f"window.__gevBench.startAction({encoded_action}, {encoded_args});",
    )
    if started != "started":
        raise RuntimeError(f"Could not start action {action}: {started!r}")


def _action_status(page: Any, app: QApplication) -> dict[str, Any] | None:
    raw = _run_js(page, app, "JSON.stringify(window.__gevBench.actionStatus());", timeout_s=2.0)
    if raw in (None, "null"):
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        value = json.loads(raw)
        if value is None:
            return None
        if isinstance(value, dict):
            return value
    raise TypeError(f"Unexpected JavaScript action status: {raw!r}")


def _wait_action(
    page: Any, app: QApplication, action: str, args: dict[str, Any], timeout_s: float = 10.0
) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    _start_action(page, app, action, args)
    deadline = started + timeout_s
    while time.perf_counter() < deadline:
        response = _action_status(page, app)
        if response is not None:
            if response.get("error"):
                raise RuntimeError(f"{action} failed in JavaScript: {response['error']}")
            if not response.get("ok"):
                raise RuntimeError(f"{action} rejected: {response}")
            return response, (time.perf_counter() - started) * 1000
    raise TimeoutError(f"God's Eye View action {action} did not complete in {timeout_s:.1f}s")


def _frame_summary(frame_times_ms: list[float], frame_count: int, elapsed_ms: float) -> dict[str, Any]:
    result: dict[str, Any] = {
        "frames": frame_count,
        "elapsed_ms": round(elapsed_ms, 2),
        "fps": round(frame_count * 1000 / elapsed_ms, 2) if elapsed_ms > 0 else None,
    }
    if not frame_times_ms:
        reason = "aucun intervalle entre frames observé"
        result.update(
            average_frame_time_ms=f"NON MESURÉE — {reason}",
            p95_frame_time_ms=f"NON MESURÉE — {reason}",
            p99_frame_time_ms=f"NON MESURÉE — {reason}",
            max_frame_time_ms=f"NON MESURÉE — {reason}",
        )
        return result
    ordered = sorted(frame_times_ms)
    result.update(
        average_frame_time_ms=round(statistics.fmean(frame_times_ms), 3),
        p95_frame_time_ms=round(ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)], 3),
        p99_frame_time_ms=round(ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.99) - 1)], 3),
        max_frame_time_ms=round(ordered[-1], 3),
    )
    return result


def _begin_capture(page: Any, app: QApplication) -> None:
    result = _run_js(page, app, "window.__gevBench.startCapture(); 'capturing';")
    if result != "capturing":
        raise RuntimeError(f"Could not start frame capture: {result!r}")


def _capture_for(
    page: Any,
    app: QApplication,
    label: str,
    duration_s: float,
    *,
    action: tuple[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    _begin_capture(page, app)
    started = time.perf_counter()
    next_sample = started + SAMPLE_INTERVAL_S
    samples = []
    action_result = None
    action_ms = None
    if action:
        action_started = time.perf_counter()
        _start_action(page, app, action[0], action[1])
    else:
        action_started = None

    while True:
        elapsed_s = time.perf_counter() - started
        action_pending = action is not None and action_result is None
        if elapsed_s >= duration_s and not action_pending:
            break
        app.processEvents()
        now = time.perf_counter()
        if now >= next_sample:
            samples.append({"t_s": round(now - started, 3), **_process_tree_snapshot()})
            next_sample += SAMPLE_INTERVAL_S
        if action and action_result is None and now - action_started > 0:
            status = _action_status(page, app)
            if status is not None:
                action_ms = (time.perf_counter() - action_started) * 1000
                if status.get("error") or not status.get("ok"):
                    raise RuntimeError(f"{action[0]} failed during {label}: {status}")
                action_result = status
        time.sleep(0.01)

    raw = _run_js(page, app, "window.__gevBench.stopCapture();")
    if not isinstance(raw, dict):
        raise TypeError(f"Invalid frame capture for {label}: {raw!r}")
    cesium = raw["cesium"]
    raf = raw["raf"]
    long_task_times = raw["longTasksMs"]
    active_action_capture = raw.get("actionCapture")
    if active_action_capture is not None:
        active_action_capture = {
            "elapsed_ms": round(active_action_capture["elapsedMs"], 2),
            "raf": _frame_summary(
                active_action_capture["raf"]["frameTimesMs"],
                active_action_capture["raf"]["frames"],
                active_action_capture["elapsedMs"],
            ),
            "cesium_post_render": _frame_summary(
                active_action_capture["cesium"]["frameTimesMs"],
                active_action_capture["cesium"]["frames"],
                active_action_capture["cesium"]["elapsedMs"],
            ),
        }
    return {
        "label": label,
        "resources_samples": samples,
        "raf": _frame_summary(raf["frameTimesMs"], raf["frames"], raw["elapsedMs"]),
        "cesium_post_render": _frame_summary(
            cesium["frameTimesMs"], cesium["frames"], cesium["elapsedMs"]
        ),
        "long_tasks": {
            "count": len(long_task_times) if raw["longTaskApiAvailable"] else None,
            "total_ms": round(sum(long_task_times), 3) if raw["longTaskApiAvailable"] else
                "NON MESURÉE — PerformanceObserver longtask indisponible dans QtWebEngine",
            "durations_ms": long_task_times if raw["longTaskApiAvailable"] else
                "NON MESURÉE — PerformanceObserver longtask indisponible dans QtWebEngine",
        },
        "raw_frame_intervals_ms": {
            "raf": raf["frameTimesMs"],
            "cesium_post_render": cesium["frameTimesMs"],
        },
        "action_result": action_result,
        "action_elapsed_ms": round(action_ms, 2) if action_ms is not None else None,
        "active_action_capture": active_action_capture,
    }


def _wait_for_flights(page: Any, app: QApplication, timeout_s: float = 12.0) -> dict[str, Any]:
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        state, _ = _wait_action(page, app, "world_get_state", {}, timeout_s=3.0)
        flights = state.get("state", {}).get("layers", {}).get("flights", {})
        if flights.get("objectCount", 0) > 0 or flights.get("source") not in (None, "none"):
            return flights
        app.processEvents()
        time.sleep(0.2)
    return flights


def _read_benchmark_json(page: Any, app: QApplication, expression: str) -> dict[str, Any]:
    result = _run_js(page, app, f"JSON.stringify({expression});")
    while isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict):
        raise TypeError(f"Expected benchmark JSON object from {expression}: {result!r}")
    return result


def _hardware_environment(page_info: dict[str, Any]) -> dict[str, Any]:
    video = "NON MESURÉE — interrogation CIM indisponible"
    cpu_name = platform.processor() or "NON MESURÉ"
    os_build = platform.version()
    if os.name == "nt":
        command = (
            "Get-CimInstance Win32_Processor | Select-Object -ExpandProperty Name; "
            "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name; "
            "(Get-CimInstance Win32_OperatingSystem).BuildNumber"
        )
        try:
            output = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            ).stdout.splitlines()
            values = [line.strip() for line in output if line.strip()]
            if len(values) >= 3:
                cpu_name, video, os_build = values[0], values[1], values[-1]
        except (OSError, subprocess.SubprocessError):
            pass
    return {
        "os": platform.platform(),
        "windows_build": os_build,
        "cpu": cpu_name,
        "logical_cpu_count": psutil.cpu_count(logical=True),
        "physical_cpu_count": psutil.cpu_count(logical=False),
        "gpu_from_windows": video,
        "webgl_renderer": page_info.get("webglRenderer") or "NON MESURÉE — WebGL renderer string indisponible",
        "ram_total_gb": round(psutil.virtual_memory().total / (1024**3), 2),
        "python": sys.version.split()[0],
        "pyside6": __import__("PySide6").__version__,
        "qtwebengine": __import__("PySide6.QtCore", fromlist=["qVersion"]).qVersion(),
        "screen": None,
    }


def _collect_gpu_profile(results: dict[str, Any]) -> dict[str, Any]:
    records = []
    for scenario in results["scenarios"]:
        samples = list(scenario.get("resources_samples", []))
        samples.extend(scenario.get("flight_update_observation_resources", []))
        for sample in samples:
            value = sample.get("gpu_3d_utilization_pct")
            if isinstance(value, (int, float)):
                records.append(
                    {
                        "scenario": scenario["label"],
                        "t_s": sample.get("t_s"),
                        "max_engine_utilization_pct": value,
                        "engines": sample.get("gpu_3d_engines", []),
                    }
                )
    if not records:
        return {
            "max_engine_utilization_pct": (
                "NON MESURÉE — aucun échantillon de moteur 3D GPU PDH valide pour le processus"
            ),
            "samples": [],
        }
    return {
        "max_engine_utilization_pct": max(
            sample["max_engine_utilization_pct"] for sample in records
        ),
        "mean_engine_utilization_pct": round(
            statistics.fmean(sample["max_engine_utilization_pct"] for sample in records), 2
        ),
        "samples": records,
        "measurement_scope": (
            "Moteur 3D le plus chargé parmi les instances PDH GPU Engine associées aux PID "
            "Python/QtWebEngine du benchmark; ne représente pas l'utilisation globale du GPU."
        ),
    }


def _synthetic_flights(count: int = 600) -> list[dict[str, Any]]:
    return [
        {
            "hex": f"bench-{index:04x}",
            "flight": f"BENCH{index:03d}",
            "lat": 35.0 + (index % 30) * 0.5,
            "lon": -10.0 + (index % 40) * 0.5,
            "alt_baro": 10_000 + (index % 8) * 1_000,
            "track": index % 360,
            "emergency": "none",
        }
        for index in range(count)
    ]


def run_benchmark(output_path: Path) -> dict[str, Any]:
    global GPU_SAMPLER
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    PROC.cpu_percent(interval=None)
    psutil.cpu_percent(interval=None)
    _process_tree_snapshot()

    started = time.perf_counter()
    if not request_open(timeout=12.0):
        raise RuntimeError("request_open() a échoué; QtWebEngine ou le build web est indisponible")
    opened_ms = (time.perf_counter() - started) * 1000

    controller = get_default_controller()
    ready_deadline = time.perf_counter() + 40
    while controller.lifecycle != "ready" and time.perf_counter() < ready_deadline:
        app.processEvents()
        time.sleep(0.02)
    if controller.lifecycle != "ready":
        raise TimeoutError(f"GEV non prêt après 40 s (lifecycle={controller.lifecycle})")
    ready_ms = (time.perf_counter() - started) * 1000

    window = active_window()
    if window is None:
        raise RuntimeError("La fenêtre GEV n'est pas accessible après request_open")
    page = window._transport._page
    if page is None:
        raise RuntimeError("QWebEnginePage absente après ouverture")
    harness = _run_js(page, app, HARNESS_JS)
    if harness not in ("installed", "already_installed"):
        raise RuntimeError(f"Installation du harness échouée: {harness!r}")
    page_info = _run_js(page, app, "window.__gevBench.pageInfo();")
    if not isinstance(page_info, dict) or not page_info.get("canvas"):
        raise RuntimeError(f"Canvas Cesium absent; la scène n'est pas validée: {page_info!r}")
    benchmark_info = _run_js(page, app, "JSON.stringify(window.jarvisWorld.__benchmark);")
    if not isinstance(benchmark_info, dict) or not benchmark_info.get("cesiumVersion"):
        raise RuntimeError("La sonde Cesium postRender n'est pas disponible")

    screen = app.primaryScreen()
    environment = _hardware_environment(page_info)
    environment["screen"] = (
        {
            "geometry_px": [screen.geometry().width(), screen.geometry().height()],
            "device_pixel_ratio": screen.devicePixelRatio(),
        }
        if screen
        else "NON MESURÉE — écran Qt absent"
    )
    environment["cesium_version"] = benchmark_info["cesiumVersion"]
    GPU_SAMPLER = _Gpu3DSampler()
    _process_tree_snapshot()
    initial_world_state, _ = _wait_action(page, app, "world_get_state", {})

    results: dict[str, Any] = {
        "schema_version": 1,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip(),
        "git_branch": subprocess.run(
            ["git", "branch", "--show-current"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip(),
        "environment": environment,
        "conditions": {
            "window_size_px": [window.width(), window.height()],
            "web_build_index_bytes": (REPO_ROOT / "web" / "gods_eye_view" / "dist" / "index.html").stat().st_size,
            "web_build_index_mtime": datetime.fromtimestamp(
                (REPO_ROOT / "web" / "gods_eye_view" / "dist" / "index.html").stat().st_mtime,
                timezone.utc,
            ).isoformat(),
            "measurement": (
                "QWebEngineView avec build Vite/Cesium; postRender de Cesium est la source "
                "de FPS/frame-time Cesium; rAF est rapporté séparément."
            ),
            "gpu_utilization": (
                "PDH GPU Engine 3D par PID benchmark; l'utilisation globale de l'adaptateur "
                "n'est pas représentée par ce compteur."
            ),
            "external_services": "Esri/OSM et adsb.lol selon disponibilité réseau; source inscrite par scénario",
            "instrumentation": "Sonde postRender et mesures de frames actives uniquement pendant chaque intervalle",
        },
        "activation": {
            "request_open_ms": round(opened_ms, 2),
            "ready_from_open_ms": round(ready_ms, 2),
            "canvas": page_info["canvas"],
            "page_status": page_info["status"],
            "user_agent": page_info["userAgent"],
            "process_resources_at_ready": _process_tree_snapshot(),
        },
        "world_state_at_ready": initial_world_state.get("state"),
        "scenarios": [],
    }

    print("[Bench] QtWebEngine/Cesium prêt; attente de stabilisation des tuiles…", file=sys.stderr)
    for _ in range(3):
        app.processEvents()
        time.sleep(1)
    results["scenarios"].append(_capture_for(page, app, "A_globe_idle", 5.0))

    print("[Bench] Mouvement caméra flyTo Tokyo…", file=sys.stderr)
    _run_js(page, app, "window.jarvisWorld.__benchmark.startCameraProbe(); 'camera-probe';")
    results["scenarios"].append(
        _capture_for(
            page,
            app,
            "B_camera_flyto",
            4.0,
            action=(
                "world_set_camera",
                {
                    "latitude": 35.6762,
                    "longitude": 139.6503,
                    "altitude_m": 2_000_000,
                    "duration_s": 2.5,
                },
            ),
        )
    )
    camera_settle_started = time.perf_counter()
    camera_settle_samples = []
    next_camera_sample = camera_settle_started + SAMPLE_INTERVAL_S
    camera_diagnostics = _read_benchmark_json(
        page, app, "window.jarvisWorld.__benchmark.getDiagnostics()"
    )
    governor_idle_at_capture_end = not camera_diagnostics["governor"]["holds"]
    elapsed_until_idle_ms = 0.0 if governor_idle_at_capture_end else None
    while (
        camera_diagnostics["governor"]["holds"]
        and time.perf_counter() - camera_settle_started < 3.0
    ):
        app.processEvents()
        now = time.perf_counter()
        if now >= next_camera_sample:
            camera_settle_samples.append(
                {"t_s": round(now - camera_settle_started, 3), **_process_tree_snapshot()}
            )
            next_camera_sample += SAMPLE_INTERVAL_S
        time.sleep(0.1)
        camera_diagnostics = _read_benchmark_json(
            page, app, "window.jarvisWorld.__benchmark.getDiagnostics()"
        )
        if not camera_diagnostics["governor"]["holds"]:
            elapsed_until_idle_ms = (time.perf_counter() - camera_settle_started) * 1000
    while time.perf_counter() - camera_settle_started < 2.0:
        app.processEvents()
        now = time.perf_counter()
        if now >= next_camera_sample:
            camera_settle_samples.append(
                {"t_s": round(now - camera_settle_started, 3), **_process_tree_snapshot()}
            )
            next_camera_sample += SAMPLE_INTERVAL_S
        time.sleep(0.1)
    results["camera_governor_settle"] = {
        "elapsed_after_capture_ms": round((time.perf_counter() - camera_settle_started) * 1000, 2),
        "governor_idle_at_capture_end": governor_idle_at_capture_end,
        "elapsed_until_idle_ms": (
            round(elapsed_until_idle_ms, 2)
            if elapsed_until_idle_ms is not None
            else "NON MESURÉE — hold encore actif après 3 s d’observation"
        ),
        "diagnostics": camera_diagnostics,
        "resources_samples": camera_settle_samples,
    }
    results["camera_events_camera_flyto"] = _read_benchmark_json(
        page, app, "window.jarvisWorld.__benchmark.stopCameraProbe()"
    )
    results["scene_diagnostics_after_camera"] = _read_benchmark_json(
        page, app, "window.jarvisWorld.__benchmark.getDiagnostics()"
    )

    print("[Bench] Flights ON; première actualisation réseau/échantillon…", file=sys.stderr)
    _run_js(page, app, "window.jarvisWorld.__benchmark.startFlightProfile(); 'profiling';")
    enable_result, enable_ms = _wait_action(page, app, "world_enable_layer", {"layer_id": "flights"})
    flight_state = _wait_for_flights(page, app)
    flights_on = _capture_for(page, app, "C_flights_on", 5.0)
    update_observation_started = time.perf_counter()
    update_observation_samples = []
    next_sample = update_observation_started + SAMPLE_INTERVAL_S
    update_profile = _read_benchmark_json(
        page, app, "window.jarvisWorld.__benchmark.getFlightProfile()"
    )
    while time.perf_counter() - update_observation_started < 12.5 and len(update_profile["updates"]) < 2:
        app.processEvents()
        now = time.perf_counter()
        if now >= next_sample:
            update_observation_samples.append(
                {"t_s": round(now - update_observation_started, 3), **_process_tree_snapshot()}
            )
            next_sample += SAMPLE_INTERVAL_S
            update_profile = _read_benchmark_json(
                page, app, "window.jarvisWorld.__benchmark.getFlightProfile()"
            )
        time.sleep(0.02)
    update_profile = _read_benchmark_json(
        page, app, "window.jarvisWorld.__benchmark.stopFlightProfile()"
    )
    update_times = [item["atMs"] for item in update_profile.get("updates", [])]
    flights_on["enable_ms"] = round(enable_ms, 2)
    flights_on["enable_result"] = enable_result
    flights_on["flight_state"] = flight_state
    flights_on["flight_update_profile"] = update_profile
    flights_on["flight_update_observation_resources"] = update_observation_samples
    flights_on["measured_update_intervals_ms"] = (
        [round(right - left, 2) for left, right in zip(update_times, update_times[1:])]
        if len(update_times) > 1
        else "NON MESURÉE — aucun deuxième applyRows observé pendant la fenêtre de profil"
    )
    flights_on["scene_diagnostics"] = _read_benchmark_json(
        page, app, "window.jarvisWorld.__benchmark.getDiagnostics()"
    )
    results["scenarios"].append(flights_on)

    print("[Bench] Flights OFF; destruction de la couche…", file=sys.stderr)
    disable_result, disable_ms = _wait_action(page, app, "world_disable_layer", {"layer_id": "flights"})
    flights_off = _capture_for(page, app, "D_flights_off", 3.0)
    flights_off["disable_ms"] = round(disable_ms, 2)
    flights_off["disable_result"] = disable_result
    flights_off["scene_diagnostics"] = _read_benchmark_json(
        page, app, "window.jarvisWorld.__benchmark.getDiagnostics()"
    )
    off_flights = flights_off["scene_diagnostics"]["flights"]
    off_internals = flights_off["scene_diagnostics"]["flightInternals"]
    if (
        off_flights.get("enabled")
        or off_flights.get("objectCount") != 0
        or off_internals.get("timerScheduled")
        or off_internals.get("collectionAlive")
    ):
        raise AssertionError(
            "La couche flights n'est pas entièrement désactivée après flights OFF: "
            f"{flights_off['scene_diagnostics']}"
        )
    results["scenarios"].append(flights_off)

    print("[Bench] Churn ×10 enable/disable…", file=sys.stderr)
    churn = []
    for cycle in range(10):
        on, on_ms = _wait_action(page, app, "world_enable_layer", {"layer_id": "flights"})
        off, off_ms = _wait_action(page, app, "world_disable_layer", {"layer_id": "flights"})
        churn.append(
            {
                "cycle": cycle + 1,
                "enable_ms": round(on_ms, 2),
                "disable_ms": round(off_ms, 2),
                "enable_result": on,
                "disable_result": off,
            }
        )
    churn_before = _process_tree_snapshot()
    for _ in range(2):
        app.processEvents()
        time.sleep(1)
    churn_after = _process_tree_snapshot()
    results["scenarios"].append(
        {
            "label": "E_layer_churn_x10",
            "cycles": churn,
            "average_enable_ms": round(statistics.fmean(row["enable_ms"] for row in churn), 2),
            "average_disable_ms": round(statistics.fmean(row["disable_ms"] for row in churn), 2),
            "resources_before_settle": churn_before,
            "resources_after_2s_settle": churn_after,
            "rss_delta_after_settle_mb": round(
                churn_after["process_tree_rss_mb"] - churn_before["process_tree_rss_mb"], 2
            ),
        }
    )
    results["flight_lifecycle_after_churn"] = _read_benchmark_json(
        page, app, "window.jarvisWorld.__benchmark.getDiagnostics()"
    )["flightInternals"]

    print("[Bench] Chargement contrôlé de 600 contacts via BillboardCollection…", file=sys.stderr)
    focus_result, focus_ms = _wait_action(page, app, "world_set_camera", {
        "latitude": 48.8566,
        "longitude": 2.3522,
        "altitude_m": 8_000_000,
        "duration_s": 2.5,
    })
    enable_600, enable_600_ms = _wait_action(
        page, app, "world_enable_layer", {"layer_id": "flights"}
    )
    rows = json.dumps(_synthetic_flights(), separators=(",", ":"))
    load_started = time.perf_counter()
    load_result = _run_js(
        page,
        app,
        f"window.jarvisWorld.__benchmark.loadSyntheticFlights({rows});",
        timeout_s=20,
    )
    load_elapsed_ms = (time.perf_counter() - load_started) * 1000
    if isinstance(load_result, str):
        load_result = json.loads(load_result)
    if load_result.get("objectCount") != 600:
        raise AssertionError(f"Le scénario maximal n'a pas rendu exactement 600 avions: {load_result}")
    max_flights = _capture_for(page, app, "F_synthetic_600_flights", 5.0)
    max_flights.update(
        camera_action=focus_result,
        camera_action_ms=round(focus_ms, 2),
        enable_ms=round(enable_600_ms, 2),
        enable_result=enable_600,
        load_wall_ms=round(load_elapsed_ms, 2),
        load_profile=load_result,
    )
    results["scenarios"].append(max_flights)

    print("[Bench] 600 flights pendant un mouvement caméra…", file=sys.stderr)
    max_flights_motion = _capture_for(
        page,
        app,
        "G_synthetic_600_flights_camera_move",
        4.0,
        action=(
            "world_set_camera",
            {
                "latitude": 48.8566,
                "longitude": 2.3522,
                "altitude_m": 2_000_000,
                "pitch_deg": -75,
                "duration_s": 2.5,
            },
        ),
    )
    max_flights_diagnostics = _read_benchmark_json(
        page, app, "window.jarvisWorld.__benchmark.getDiagnostics()"
    )
    max_flights_motion["flight_state"] = max_flights_diagnostics["flights"]
    max_flights_motion["scene_diagnostics"] = max_flights_diagnostics
    if max_flights_motion["flight_state"].get("objectCount") != 600:
        raise AssertionError(
            "La couche a perdu les 600 objets pendant le mouvement caméra: "
            f"{max_flights_motion['flight_state']}"
        )
    results["scenarios"].append(max_flights_motion)

    print("[Bench] Capture de l'interface avec les 600 billboards…", file=sys.stderr)
    screenshot_path = output_path.with_name(output_path.stem + "_ui.png")
    page_info = _run_js(page, app, "window.__gevBench.pageInfo();")
    if window.grab().save(str(screenshot_path)):
        try:
            screenshot_name = str(screenshot_path.relative_to(REPO_ROOT))
        except ValueError:
            screenshot_name = str(screenshot_path)
        results["interface"] = {
            "screenshot": screenshot_name,
            "screenshot_bytes": screenshot_path.stat().st_size,
            "status": page_info["status"],
            "canvas": page_info["canvas"],
            "webgl_renderer": environment["webgl_renderer"],
            "benchmark_label_visible": "BENCHMARK" in str(page_info["status"]),
            "flight_count": max_flights_motion["flight_state"]["objectCount"],
        }
    else:
        results["interface"] = {
            "screenshot": "NON MESURÉE — QPixmap.grab().save() a échoué",
            "status": page_info["status"],
            "canvas": page_info["canvas"],
            "webgl_renderer": environment["webgl_renderer"],
        }

    print("[Bench] Destruction après charge 600…", file=sys.stderr)
    destroy_started = time.perf_counter()
    destroy_result, destroy_ms = _wait_action(
        page, app, "world_disable_layer", {"layer_id": "flights"}
    )
    verify_state, _ = _wait_action(page, app, "world_get_state", {})
    destroyed_layer = verify_state.get("state", {}).get("layers", {}).get("flights", {})
    if destroyed_layer.get("enabled") or destroyed_layer.get("objectCount") != 0:
        raise AssertionError(f"La couche n'est pas détruite après flights OFF: {destroyed_layer}")
    destroyed_diagnostics = _read_benchmark_json(
        page, app, "window.jarvisWorld.__benchmark.getDiagnostics()"
    )
    destroyed_internals = destroyed_diagnostics["flightInternals"]
    if (
        destroyed_internals.get("timerScheduled")
        or destroyed_internals.get("collectionAlive")
        or destroyed_internals.get("benchmarkOverride")
    ):
        raise AssertionError(
            "La couche flights conserve des ressources après désactivation des 600: "
            f"{destroyed_internals}"
        )
    results["destruction"] = {
        "flights_600_disable_ms": round(destroy_ms, 2),
        "elapsed_wall_ms": round((time.perf_counter() - destroy_started) * 1000, 2),
        "disable_result": destroy_result,
        "verified_final_layer_state": destroyed_layer,
        "verified_final_flight_internals": destroyed_internals,
    }

    close_started = time.perf_counter()
    window.close()
    app.processEvents()
    close_ms = (time.perf_counter() - close_started) * 1000
    results["destruction"]["window_close_ms"] = round(close_ms, 2)
    results["destruction"]["lifecycle_after_close"] = controller.lifecycle
    if controller.lifecycle != "idle":
        raise AssertionError(f"Le contrôleur n'est pas idle après fermeture ({controller.lifecycle})")
    results["controller_stats"] = controller.stats()
    results["gpu_profile"] = _collect_gpu_profile(results)
    results["metrics_not_measured"] = {
        "gpu_total_utilization": (
            "NON MESURÉE — le compteur PDH retenu suit les moteurs 3D associés aux PID du "
            "benchmark, pas l'ensemble de l'adaptateur."
        ),
        "gpu_frame_time": "NON MESURÉE — seuls les événements Cesium Scene.postRender sont mesurés.",
        "browser_heap": "NON MESURÉE — aucune API heap stable n'est exposée par QtWebEngine.",
        "adsb_live_600": (
            "NON MESURÉE — le scénario 600 est déterministe et synthétique afin de garantir "
            "exactement 600 objets; le flux live est mesuré séparément dans C_flights_on."
        ),
    }
    results["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    if GPU_SAMPLER is not None:
        GPU_SAMPLER.close()
        GPU_SAMPLER = None
    app.quit()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/benchmark_gev_real_results.json"),
        help="destination du résultat JSON (par défaut docs/benchmark_gev_real_results.json)",
    )
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    results = run_benchmark(output)
    print(f"\nRésultats sauvegardés: {output}")
    for scenario in results["scenarios"]:
        cesium = scenario.get("cesium_post_render", {})
        print(
            f"{scenario['label']}: Cesium FPS={cesium.get('fps', 'N/A')}, "
            f"p95={cesium.get('p95_frame_time_ms', 'N/A')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
