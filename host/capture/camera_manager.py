"""Open Iron 661 cameras through the KYFGLib backend.

Why KYFGLib (and not Harvesters / KYVPGenTL)?
---------------------------------------------
The Harvesters path through KYVPGenTL exposes a *merged* GenICam tree; the
camera-side ``AcquisitionStart`` GenICam command on that tree fails with
AccessException on KAYA Komodo (documented in ``trigger_preview_app.py``).
Without ``AcquisitionStart`` the sensor never arms and external Line1
triggers from the PCB are silently dropped -- which is exactly what we
saw at runtime in this app's earlier Harvesters-based bring-up.

KYFGLib is the proven path on this hardware: the legacy single-camera
focus tool (``main.py`` / ``frame_grabber.py``) has been streaming reliably
under PCB Line1 triggers for months using ``KYFG_CameraStart``.

Stage of the unified app
------------------------
Stage 2: open and configure up to N cameras (typically all three Iron
661s on this grabber). Each opened camera lives in its own
``KyfgCamera`` with its own stream + buffer callback, so they can be
armed independently. The GUI hot-swaps which one is armed when the
operator changes the preview radio (only one armed at a time for now;
simultaneous arming for M0/M1 capture is a follow-up).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from .kyfg_backend import (
    CameraInfo,
    KyfgCamera,
    KyfgGrabber,
)
from .pipeline import CameraConfig

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------


def find_cti_from_env() -> Optional[str]:
    """Compatibility shim for the legacy launcher (``main_capture.py``).

    KYFGLib doesn't actually need a CTI path -- it loads the producer
    internally from the Vision Point bin directory. We return that path
    when the env var is set so the launcher's "GenTL CTI: ..." status
    line still has something to show, and ``None`` otherwise so callers
    can skip the display.
    """
    bin_path = os.environ.get("KAYA_VISION_POINT_BIN_PATH", "")
    return bin_path or None


# ---------------------------------------------------------------------------


@dataclass
class OpenedCamera:
    """One opened camera, ready to be wrapped in a CameraPipeline.

    Holds a KYFGLib :class:`KyfgCamera` (the "backend"); ``CameraPipeline``
    talks to it via ``arm(callback)`` / ``disarm()`` to start and stop the
    live data stream. The legacy Harvesters-era ``ia`` field is gone.
    """

    backend: KyfgCamera
    config: CameraConfig
    setup_log: list[str]
    """Per-camera configure() readback lines, surfaced in the GUI log."""


# ---------------------------------------------------------------------------


class CameraManager:
    """Owns the KYFG grabber + a list of opened :class:`KyfgCamera` instances.

    ``open_all(expected_count=N)`` opens, configures, and readies that
    many cameras (subject to ``len(grabber.cameras) >= N``). Each opened
    camera gets its own :class:`KyfgCamera`, which holds its own stream
    + per-buffer callback so it can be armed independently from the
    others.
    """

    def __init__(
        self,
        cti_path: Optional[str] = None,
        grabber_index: int = 0,
    ) -> None:
        # ``cti_path`` is accepted for backwards compatibility with the
        # legacy launcher / MainWindow signature. KYFGLib doesn't actually
        # consume a CTI path -- it loads its producer internally from the
        # KAYA bin directory -- so we just stash it for ``cti_path``
        # display in the GUI status bar.
        self._cti_path_hint = cti_path
        self._grabber_index = int(grabber_index)
        self._grabber: Optional[KyfgGrabber] = None
        self._opened: list[OpenedCamera] = []

    # -- compatibility shims for the existing GUI code ----------------------

    @property
    def cti_path(self) -> str:
        """Returned for the GUI status bar; meaningless under KYFGLib but
        kept so MainWindow's existing format strings still work.
        """
        if self._cti_path_hint:
            return self._cti_path_hint
        bin_path = os.environ.get("KAYA_VISION_POINT_BIN_PATH", "")
        return bin_path or "<KAYA_VISION_POINT_BIN_PATH unset>"

    @property
    def opened(self) -> list[OpenedCamera]:
        return list(self._opened)

    # -- bring-up ----------------------------------------------------------

    def open_all(self, expected_count: int = 3) -> list[OpenedCamera]:
        """Open the grabber, enumerate cameras, then open + configure
        each of the first ``expected_count`` of them. Returns one
        :class:`OpenedCamera` per camera that came up successfully.

        Behaviour:
        * 0 cameras visible -> ``RuntimeError`` (likely a hardware /
          driver issue, no point continuing).
        * fewer cameras visible than ``expected_count`` -> we open
          whatever we have and log a warning. The GUI will see fewer
          rows fill in than expected, which is still useful (it lets
          the operator work with the cameras that did come up rather
          than blocking on the ones that didn't).
        * a ``KyfgCamera.open()`` / ``configure()`` raises -> we skip
          that camera, log it, and keep going. The grabber stays open,
          and the previously-opened cameras remain valid. This mirrors
          how the legacy focus tool degrades gracefully when one cable
          is bad.
        """
        target = max(0, int(expected_count))

        grabber = KyfgGrabber(self._grabber_index)
        grabber.open()
        self._grabber = grabber

        infos: list[CameraInfo] = grabber.cameras
        if not infos:
            self._teardown()
            raise RuntimeError(
                "Grabber sees 0 cameras. Check coax cables, that Vision "
                "Point is closed, and that the frame grabber is powered."
            )
        if len(infos) < target:
            log.warning(
                "Asked for %d camera(s) but grabber only sees %d. "
                "Continuing with what's available: %s",
                target, len(infos),
                ", ".join(c.label for c in infos),
            )

        to_open = infos[: min(target, len(infos))]
        opened: list[OpenedCamera] = []
        for idx, info in enumerate(to_open):
            try:
                cam = KyfgCamera(grabber, info)
                cam.open()
                # Defensive: load the factory user-set BEFORE configure.
                # Iron 661 firmware can get into a state where the SDK's
                # readback returns the values we last wrote but the
                # sensor / acquisition state machine is configured
                # differently -- the camera then claims TriggerMode=On /
                # TriggerSource=Line1 but actually ignores Line1 pulses,
                # producing no buffers under live PCB triggering. The
                # reliable way to clear most stuck states in software
                # is "UserSetSelector=Default ; UserSetLoad" (Vision
                # Point's UserSetLoad button).
                # The full hardware DeviceReset is stronger but breaks
                # the SDK's GenICam node tree until we tear down +
                # rebuild the entire grabber session, so it's reserved
                # for the on-demand "Reset cameras" button rather than
                # eager startup. See MainWindow._on_reset_cameras_clicked.
                userset_ok, userset_detail = cam.load_factory_userset()
                cam_setup_lines: list[str] = [
                    f"factory user-set: {'loaded' if userset_ok else 'skipped'} "
                    f"({userset_detail})"
                ]
                cfg_lines = cam.configure()
                cam_setup_lines.extend(cfg_lines)
            except Exception as exc:
                # Don't tear everything down -- one bad camera shouldn't
                # poison the others. Log and move on.
                log.warning(
                    "Failed to open camera idx %d (%s): %s; skipping",
                    idx, info.label, exc,
                )
                continue
            config = CameraConfig(
                camera_number=idx + 1,
                camera_serial=info.serial or f"idx{info.index}",
                device_index=info.index,
            )
            opened.append(OpenedCamera(
                backend=cam, config=config, setup_log=cam_setup_lines,
            ))
            log.info(
                "  opened cam%d serial=%r idx=%d (%d config writes)",
                config.camera_number, config.camera_serial,
                info.index, len(cam_setup_lines),
            )

        if not opened:
            self._teardown()
            raise RuntimeError(
                "Grabber saw cameras but every open/configure attempt "
                "failed; refusing to continue."
            )

        self._opened = opened
        return opened

    # -- recovery ----------------------------------------------------------

    def recover_cameras(
        self, do_device_reset: bool = False,
    ) -> list[tuple[int, bool, str]]:
        """Run the on-demand recovery sequence on every opened camera.

        This is the routine the "Reset cameras" GUI buttons trigger
        when an Iron 661 has slipped into the stuck-firmware state
        (camera reports armed but ignores Line1 triggers, or starts
        streaming briefly then drops dead).

        Two flavors via ``do_device_reset``:

        ``False`` (SOFT recovery, default; ~10-15 s):
            ``close all cameras`` -> ``KyfgGrabber.reopen`` ->
            ``adopt_handle`` + ``open`` + ``configure`` per camera.
            Equivalent to closing the app, reopening it, and going
            through bring-up again -- but in-process, so writer
            subprocesses + SHM pools survive.  Tested in the wild on
            2026-05-05 to recover the most common stuck state (matches
            what "VP just opening" does).

        ``True`` (DEEP recovery; ~20-25 s):
            Adds ``device_reset_issue`` on every still-valid camera
            handle + 5 s settle window BEFORE the soft sequence above.
            Use when SOFT alone wasn't enough, e.g. a camera that
            stays at ``cb_count=0`` after a soft recovery + re-arm.

        Caller MUST disarm every pipeline first; recovery while a
        camera is streaming is undefined. Caller is also responsible
        for re-arming each pipeline after this returns.

        Returns a list of ``(camera_number, ok, detail)`` tuples,
        one per previously-opened camera, in the order they were
        originally opened. ``ok`` is True only when every step
        through configure() succeeded for that camera.

        Implementation notes:

        * No ``load_factory_userset`` call here -- DeviceReset is
          strictly stronger when used; UserSetLoad on top of
          DeviceReset was observed to hang on Iron 661, and
          UserSetLoad without DeviceReset adds nothing the soft
          path doesn't already get from a fresh ``configure()``.
        * If ``KyfgGrabber.reopen`` fails outright (kernel driver
          wedged etc.), every camera is reported as failed with a
          ``grabber-reopen-failed:...`` detail; the existing
          backends are left in a closed state and the caller's
          re-arm attempts will surface a clear error.
        """
        if self._grabber is None or not self._opened:
            return []

        results: list[tuple[int, bool, str]] = []
        snapshot = list(self._opened)
        import time as _time

        # Optional Step 0 (DEEP only): issue DeviceReset on every
        # camera. Best-effort -- if a camera doesn't expose the
        # command we just skip its reset; the rest of the recovery
        # (grabber rebuild + reconfigure) still happens and may
        # unstick whatever software-state issue led the user to
        # escalate to deep recovery.
        reset_issued: dict[str, str] = {}
        if do_device_reset:
            for cam in snapshot:
                ok, detail = cam.backend.device_reset_issue()
                reset_issued[cam.config.camera_serial] = (
                    "reset-issued" if ok else f"reset-skipped:{detail}"
                )
            # Shared 5 s settle window for the cameras to come back
            # on the CXP link. Done once for all cameras (not per
            # camera in a loop) to keep wall-clock bounded at one
            # settle window.
            _time.sleep(5.0)
        else:
            for cam in snapshot:
                reset_issued[cam.config.camera_serial] = (
                    "no-device-reset (soft)"
                )

        # Step 1: close every camera. Required because
        # KyfgGrabber.reopen below assumes no cameras are open. Also
        # drops any post-DeviceReset stale GenICam node trees on the
        # deep path.
        for cam in snapshot:
            try:
                cam.backend.close()
            except Exception as exc:
                log.warning(
                    "%s: close during recovery raised: %s",
                    cam.backend.label, exc,
                )

        # Step 2: grabber teardown + rebuild. This is the step that
        # actually unsticks the most common stuck state -- KAYA's SDK
        # caches per-grabber state that survives a per-camera close,
        # and the only reliable way to drop it is a full
        # KYFG_Close/KYFG_Open cycle followed by KYFG_UpdateCameraList.
        try:
            self._grabber.reopen()
        except Exception as exc:
            log.exception("grabber.reopen during recovery failed: %s", exc)
            for cam in snapshot:
                results.append((
                    cam.config.camera_number, False,
                    f"grabber-reopen-failed:{exc}",
                ))
            return results

        # Step 3: per-camera adopt + open + configure. We match the
        # freshly-enumerated CameraInfo by serial so the GUI's
        # camera-number ordering stays stable even if KAYA decides
        # to enumerate in a different order this time.
        for cam in snapshot:
            serial = cam.config.camera_serial
            new_info = self._grabber.find_by_serial(serial)
            if new_info is None:
                detail = (
                    f"not-re-enumerated (was {serial}; "
                    f"reopen saw {[c.serial for c in self._grabber.cameras]})"
                )
                log.warning("cam%d: %s", cam.config.camera_number, detail)
                results.append((cam.config.camera_number, False, detail))
                continue
            try:
                cam.backend.adopt_handle(new_info)
                cam.backend.open()
                cfg_lines = cam.backend.configure()
            except Exception as exc:
                detail = f"adopt/open/configure raised: {exc}"
                log.exception(
                    "cam%d %s: %s", cam.config.camera_number,
                    cam.backend.label, detail,
                )
                results.append((cam.config.camera_number, False, detail))
                continue
            results.append((
                cam.config.camera_number, True,
                f"{reset_issued.get(serial, 'reset-unknown')}, "
                f"reconfigured ({len(cfg_lines)} writes)",
            ))

        return results

    # -- teardown ----------------------------------------------------------

    def close_all(self) -> None:
        """Disarm + close every opened camera, then close the grabber."""
        for cam in self._opened:
            try:
                cam.backend.close()
            except Exception:
                log.warning("Error closing %s", cam.backend.label, exc_info=True)
        self._opened = []
        self._teardown()

    def _teardown(self) -> None:
        if self._grabber is not None:
            try:
                self._grabber.close()
            except Exception:
                log.warning("KyfgGrabber.close failed", exc_info=True)
            self._grabber = None
