"""
Helpers for KAYA GenTL + Harvesters (open privilege fallback, acquisition prep).
"""

from __future__ import annotations

import traceback
from typing import Any, Tuple, Union

SearchKey = Union[int, dict[str, str]]


def create_image_acquirer(
    h: Any,
    search_key: SearchKey,
) -> Tuple[Any, str]:
    """
    Try Harvesters create() with DEVICE_ACCESS_EXCLUSIVE, then CONTROL.
    Some producers return GC_ERR_NOT_IMPLEMENTED for one mode but not the other.
    """
    from harvesters.core import ParameterKey, ParameterSet

    last_err: Exception | None = None
    for priv in ("exclusive", "control"):
        try:
            cfg = ParameterSet()
            cfg.add(ParameterKey.DEVICE_OWNERSHIP_PRIVILEGE, priv)
            cfg.add(ParameterKey.ENABLE_AUTO_CHUNK_DATA_UPDATE, False)
            # KAYA / some CoaXPress stacks need a non-trivial EventGetData wait;
            # Harvesters default 1 ms can be marginal for NEW_BUFFER polling.
            cfg.add(ParameterKey.TIMEOUT_PERIOD_ON_UPDATE_EVENT_DATA_CALL, 250)
            # Large CXP frames: deeper queue reduces drops if the host is briefly busy.
            cfg.add(ParameterKey.NUM_BUFFERS_FOR_FETCH_CALL, 8)
            ia = h.create(search_key, config=cfg)
            return ia, priv
        except Exception as e:
            last_err = e
    assert last_err is not None
    raise last_err


def prepare_camera_for_triggered_frames(nm: Any) -> list[str]:
    """
    Best-effort GenICam setup for repeated external triggers.
    Continuous + triggered frame start is the usual CoaXPress pattern.
    """
    lines: list[str] = []
    try:
        nm.AcquisitionMode.value = "Continuous"
        lines.append("AcquisitionMode = Continuous")
    except Exception as e:
        lines.append(f"AcquisitionMode: skipped ({e})")
    try:
        nm.TriggerSelector.value = "FrameStart"
        lines.append("TriggerSelector = FrameStart")
    except Exception as e:
        lines.append(f"TriggerSelector: skipped ({e})")
    return lines


def try_set_software_trigger(nm: Any) -> list[str]:
    """
    GenICam setup for frame capture on each software trigger pulse.

    TriggerSource is not always the literal \"Software\" — vendors use different
    enumeration symbols; we pick one that looks software-related.
    """
    lines: list[str] = []
    try:
        nm.TriggerSelector.value = "FrameStart"
        lines.append("TriggerSelector = FrameStart")
    except Exception as e:
        lines.append(f"TriggerSelector: skipped ({e})")

    try:
        ts = nm.get_node("TriggerSource")
        syms = [str(s) for s in ts.symbolics]
        chosen: str | None = None
        for s in syms:
            u = s.upper()
            if "SOFTWARE" in u or u in ("SW", "SWTRIGGER", "TRIGGERSOFTWARE"):
                chosen = s
                break
        if chosen is None:
            for guess in ("Software", "SoftwareTrigger", "SWTrigger", "TriggerSoftware"):
                if guess in syms:
                    chosen = guess
                    break
        if chosen is not None:
            ts.value = chosen
            lines.append(f"TriggerSource = {chosen!r} (from {len(syms)} enum entries)")
        else:
            lines.append(
                f"TriggerSource: no software-like symbolic in {syms!r} — try Vision Point"
            )
    except Exception as e:
        lines.append(f"TriggerSource: failed ({e})")

    try:
        nm.TriggerMode.value = "On"
        lines.append("TriggerMode = On")
    except Exception as e:
        lines.append(f"TriggerMode: skipped ({e})")
    try:
        nm.TriggerActivation.value = "RisingEdge"
        lines.append("TriggerActivation = RisingEdge")
    except Exception as e:
        lines.append(f"TriggerActivation: skipped ({e})")
    lines.extend(_try_set_kaya_vp_camera_trigger_software(nm))
    return lines


def _try_set_kaya_vp_camera_trigger_software(nm: Any) -> list[str]:
    """
    KAYA Vision Point exposes grabber-side trigger as CameraTrigger* (see KYVPLocalDevice.xml).
    Software source is enum KY_SOFTWARE; pulse uses CameraTriggerSoftware, not SFNC TriggerSoftware.
    """
    lines: list[str] = []
    try:
        nm.get_node("CameraTriggerSoftware")
    except Exception:
        return lines
    try:
        nm.CameraTriggerMode.value = "On"
        lines.append("CameraTriggerMode = On")
    except Exception as e:
        lines.append(f"CameraTriggerMode: skipped ({e})")
    try:
        ts = nm.get_node("CameraTriggerSource")
        syms = [str(s) for s in ts.symbolics]
        chosen: str | None = None
        for s in syms:
            u = s.upper()
            if u == "KY_SOFTWARE" or "SOFTWARE" in u:
                chosen = s
                break
        if chosen is None:
            for guess in ("KY_SOFTWARE", "Software"):
                if guess in syms:
                    chosen = guess
                    break
        if chosen is not None:
            ts.value = chosen
            lines.append(f"CameraTriggerSource = {chosen!r} (KAYA VP local)")
        else:
            lines.append(
                f"CameraTriggerSource: no software-like symbolic in {syms!r}"
            )
    except Exception as e:
        lines.append(f"CameraTriggerSource (KAYA VP): skipped ({e})")
    try:
        nm.CameraTriggerActivation.value = "RisingEdge"
        lines.append("CameraTriggerActivation = RisingEdge")
    except Exception as e:
        lines.append(f"CameraTriggerActivation: skipped ({e})")
    return lines


_SOFTWARE_PULSE_COMMANDS = (
    "CameraTriggerSoftware",
    "TriggerSoftware",
    "ExecuteSoftwareTrigger",
    "SoftwareTrigger",
    "SWTrigger",
    "TriggerSoftwareCommand",
    "SendSoftwareTrigger",
)


def _try_execute_genicam_command(nm: Any, cmd: str) -> bool:
    try:
        nm.get_node(cmd).execute()
        return True
    except Exception:
        pass
    try:
        node = getattr(nm, cmd)
        node.execute()
        return True
    except Exception:
        return False


def pulse_software_trigger(nm: Any) -> bool:
    """Execute the first working software-trigger command; return True if one worked."""
    for cmd in _SOFTWARE_PULSE_COMMANDS:
        if _try_execute_genicam_command(nm, cmd):
            return True
    return False


def try_pulse_software_trigger(nm: Any) -> list[str]:
    """Log-friendly: try each software pulse command and record result."""
    lines: list[str] = []
    for cmd in _SOFTWARE_PULSE_COMMANDS:
        try:
            nm.get_node(cmd).execute()
            lines.append(f"{cmd}.execute() OK (get_node)")
            return lines
        except Exception as e:
            lines.append(f"{cmd} (get_node): {e}")
        try:
            getattr(nm, cmd).execute()
            lines.append(f"{cmd}.execute() OK (getattr)")
            return lines
        except Exception as e:
            lines.append(f"{cmd} (getattr): {e}")
    lines.append("No software trigger command executed.")
    return lines


def trigger_chain_readback(nm: Any) -> list[str]:
    """Post-config readback for debugging software / Line1 trigger."""
    lines: list[str] = []
    for name in ("TriggerSelector", "TriggerMode", "TriggerSource", "TriggerActivation"):
        try:
            lines.append(f"{name} = {nm.get_node(name).value!r} (readback)")
        except Exception as e:
            lines.append(f"{name} readback: n/a ({type(e).__name__})")
    for name in (
        "CameraTriggerMode",
        "CameraTriggerSource",
        "CameraTriggerActivation",
    ):
        try:
            lines.append(f"{name} = {nm.get_node(name).value!r} (readback)")
        except Exception as e:
            lines.append(f"{name} readback: n/a ({type(e).__name__})")
    return lines


_KAYA_OPTIONAL_SELECTORS = frozenset(
    {
        "CameraSelector",
        "DeviceSelector",
        "CxpLinkSelector",
        "CxpConnectionSelector",
        "LinkSelector",
        "SourceSelector",
        "TapSelector",
    }
)


def snapshot_acquisition_and_trigger(nm: Any) -> list[str]:
    """
    Read-only GenICam snapshot for debugging (Vision Point vs Harvesters).
    Best-effort: missing nodes are reported, not raised.

    KAYA merged grabber+camera trees often throw AccessException on selector nodes
    until the correct branch is active; that is normal, not a fatal error.
    """
    lines: list[str] = []
    for name in (
        "CameraSelector",
        "DeviceSelector",
        "CxpLinkSelector",
        "LinkSelector",
        "AcquisitionMode",
        "AcquisitionStatus",
        "AcquisitionFrameCount",
        "Width",
        "Height",
        "PixelFormat",
        "TriggerSelector",
        "TriggerMode",
        "TriggerSource",
        "TriggerActivation",
        "DeviceSerialNumber",
    ):
        try:
            lines.append(f"{name} = {nm.get_node(name).value!r}")
        except Exception:
            if name in _KAYA_OPTIONAL_SELECTORS:
                lines.append(f"{name}: n/a (not on this branch — common on KAYA)")
            else:
                lines.append(f"{name}: n/a")
    return lines


def _norm_serial(s: str) -> str:
    return "".join(c for c in s.upper() if c.isalnum())


def _serial_match(reading: str | None, target: str) -> bool:
    if not reading or not target:
        return False
    a = _norm_serial(reading)
    b = _norm_serial(target)
    return bool(a and b and (a == b or a in b or b in a))


def _read_serial_from_node_map(nm: Any) -> str | None:
    for name in (
        "DeviceSerialNumber",
        "DeviceID",
        "CameraSerialNumber",
    ):
        try:
            v = nm.get_node(name).value
            s = str(v).strip()
            if s:
                return s
        except Exception:
            continue
    return None


def align_genicam_branch_by_serial(nm: Any, target_serial: str) -> list[str]:
    """
    KAYA Komodo + VP often expose one merged GenICam tree for grabber + cameras.
    Acquisition/trigger commands stay invalid until the branch for your camera is
    selected (CameraSelector / link selector, etc.).
    """
    lines: list[str] = []
    target = target_serial.strip()
    if not target:
        lines.append("GenICam branch align: no serial, skipped")
        return lines

    before = _read_serial_from_node_map(nm)
    if before and _serial_match(before, target):
        lines.append(
            f"GenICam branch: serial already matches ({before!r} ~ {target!r})"
        )
        return lines

    selectors = (
        "CameraSelector",
        "DeviceSelector",
        "CxpLinkSelector",
        "CxpConnectionSelector",
        "LinkSelector",
        "SourceSelector",
        "TapSelector",
    )

    for sel_name in selectors:
        try:
            node = nm.get_node(sel_name)
        except Exception as e:
            lines.append(f"GenICam branch: skip {sel_name} ({e})")
            continue

        try:
            syms = list(node.symbolics)
        except Exception:
            syms = []

        candidates: list[object] = list(syms) if syms else list(range(16))
        tried = 0
        for cand in candidates:
            try:
                node.value = cand
            except Exception:
                continue
            tried += 1
            got = _read_serial_from_node_map(nm)
            if got and _serial_match(got, target):
                lines.append(f"GenICam branch: MATCH {sel_name}={cand!r} (serial {got!r})")
                return lines
        if tried:
            lines.append(
                f"GenICam branch: {sel_name} tried {tried} value(s), no serial match"
            )

    lines.append(
        "GenICam branch: no selector matched serial "
        f"{target!r} (last serial read {before!r}). Try --device-index."
    )
    return lines


def align_genicam_branch_by_device_index(nm: Any, index: int) -> list[str]:
    """Select N-th camera branch when opening by GenTL device index."""
    lines: list[str] = []
    if index < 0:
        return lines
    selectors = (
        "CameraSelector",
        "DeviceSelector",
        "CxpLinkSelector",
        "CxpConnectionSelector",
        "LinkSelector",
        "SourceSelector",
        "TapSelector",
    )
    for sel_name in selectors:
        try:
            node = nm.get_node(sel_name)
        except Exception:
            continue
        try:
            syms = list(node.symbolics)
        except Exception:
            syms = []
        try:
            if syms and index < len(syms):
                node.value = syms[index]
                lines.append(
                    f"GenICam branch: {sel_name} = {syms[index]!r} (index {index})"
                )
                return lines
            node.value = index
            lines.append(f"GenICam branch: {sel_name} = {index}")
            return lines
        except Exception as e:
            lines.append(f"GenICam branch: {sel_name} index {index} ({e})")
    return lines


def ensure_trigger_selector_frame_start(nm: Any) -> list[str]:
    """
    Many SFNC tables only expose TriggerMode/Source when TriggerSelector is FrameStart.
    Safe to apply even with --vision-point if VP already uses FrameStart (no-op).
    """
    lines: list[str] = []
    try:
        node = nm.get_node("TriggerSelector")
    except Exception as e:
        lines.append(f"TriggerSelector: absent ({e})")
        return lines
    try:
        cur = node.value
        if str(cur) == "FrameStart":
            lines.append("TriggerSelector already FrameStart")
            return lines
        node.value = "FrameStart"
        lines.append(f"TriggerSelector {cur!r} -> FrameStart")
    except Exception as e:
        lines.append(f"TriggerSelector: could not set FrameStart ({e})")
    return lines


def try_execute_acquisition_commands(
    nm: Any,
    context: str,
    log: Any = None,
) -> tuple[bool, str | None]:
    """
    Try standard / common acquisition commands until one executes without GenApi error.
    Returns (success, command_name).
    """
    import logging

    from genicam.genapi import GenericException as GenApi_GenericException

    logger = log or logging.getLogger(__name__)
    for cmd in ("AcquisitionArm", "AcquisitionStart", "StartAcquisition"):
        try:
            nm.get_node(cmd).execute()
            logger.info("KAYA: %s OK %s", cmd, context)
            return True, cmd
        except GenApi_GenericException as e:
            logger.debug("KAYA: skip %s %s: %s", cmd, context, e)
        except Exception as e:
            logger.debug("KAYA: skip %s %s: %s", cmd, context, e)
    logger.info("KAYA: no acquisition command succeeded %s", context)
    return False, None


def gentle_genicam_before_harvesters_stop(nm: Any) -> None:
    """
    Best-effort release before ``ImageAcquirer.stop()``.

    KAYA + merged GenICam sometimes rejects the standard ``AcquisitionStop`` node the
    same way as ``AcquisitionStart``; trying unlock + alternate stop commands reduces
    the chance the driver leaves the camera \"busy\" for Vision Point after this app
    exits. Safe if the device is already idle.
    """
    try:
        nm.get_node("TLParamsLocked").value = 0
    except Exception:
        pass
    for cmd in ("AcquisitionStop", "StopAcquisition"):
        try:
            nm.get_node(cmd).execute()
            return
        except Exception:
            continue


def try_acquisition_start_execute(nm: Any, context: str) -> tuple[bool, str]:
    """Returns (ok, message). Use after ia.start() to confirm sensor arm."""
    ok, name = try_execute_acquisition_commands(nm, context, log=None)
    if ok and name:
        return True, f"{name}.execute() OK ({context})"
    return (
        False,
        "No AcquisitionArm / AcquisitionStart / StartAcquisition worked "
        f"({context}). If the GenICam tree is merged (KAYA Komodo), run branch "
        "alignment by serial (--serial) or set --device-index, then ensure "
        "TriggerSelector=FrameStart and Line1 trigger in the snapshot.",
    )


def fetch_first_buffer_any_stream(
    ia: Any,
    timeout_s: float,
    *,
    data_stream_index: int | None = None,
) -> Any:
    """
    Harvesters ``ia.fetch()`` waits on each data stream in order and uses the full
    client timeout on the *first* stream. KAYA Komodo often exposes multiple streams
    (e.g. metadata vs image); image data may never arrive on stream 0, so every
    call times out. This helper round-robins short waits across all streams until
    ``timeout_s`` elapses, then raises ``TimeoutException``.

    If ``data_stream_index`` is set (e.g. 0 or 1 for ``VPStrm0`` / ``VPStrm1``),
    only that stream is polled with the full timeout (useful to test which stream
    carries the frame).
    """
    import time

    from genicam.gentl import GenericException as GenTL_GenericException
    from genicam.gentl import TimeoutException as TE

    monitors = list(ia._new_buffer_event_monitor_dict.values())
    if not monitors:
        raise RuntimeError("No NEW_BUFFER monitors (data streams not registered).")

    if data_stream_index is not None:
        if data_stream_index < 0 or data_stream_index >= len(monitors):
            raise IndexError(
                f"data_stream_index={data_stream_index} invalid "
                f"(there are {len(monitors)} stream(s))"
            )
        raw = ia._fetch(
            monitor=monitors[data_stream_index],
            timeout_period_on_client_fetch_call=float(timeout_s),
            throw_except=True,
        )
        if raw is None:
            raise TE()
        buf = ia._finalize_fetching_process(raw, is_raw=False)
        if buf is None:
            raise TE()
        return buf

    if len(monitors) == 1:
        return ia.fetch(timeout=timeout_s, is_raw=False)

    deadline = time.perf_counter() + max(float(timeout_s), 0.01)
    n = len(monitors)
    idx = 0
    slice_s = min(0.25, max(0.05, float(timeout_s) / max(n * 4, 1)))

    while time.perf_counter() < deadline:
        monitor = monitors[idx % n]
        idx += 1
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            break
        use_t = min(slice_s, remaining)
        try:
            raw = ia._fetch(
                monitor=monitor,
                timeout_period_on_client_fetch_call=use_t,
                throw_except=True,
            )
        except TE:
            continue
        except GenTL_GenericException:
            raise
        if raw is None:
            continue
        buf = ia._finalize_fetching_process(raw, is_raw=False)
        if buf is not None:
            return buf

    raise TE()


def try_pulse_software_trigger(nm: Any) -> list[str]:
    """Log-friendly variant of pulse_software_trigger (for diagnostics)."""
    lines: list[str] = []
    for cmd in ("TriggerSoftware", "ExecuteSoftwareTrigger"):
        try:
            nm.get_node(cmd).execute()
            lines.append(f"{cmd}.execute() OK (software trigger test)")
            return lines
        except Exception as e:
            lines.append(f"{cmd}: {e}")
    if not lines:
        lines.append("Software trigger: no command node succeeded")
    return lines


def format_create_failure(search_key: SearchKey, err: BaseException) -> str:
    tb = traceback.format_exc()
    tail = (tb[-4000:] if len(tb) > 4000 else tb) if tb.strip() else ""
    return (
        f"Could not open camera (search_key={search_key!r}):\n{err}\n\n"
        f"If you see GC_ERR_NOT_IMPLEMENTED: close Vision Point completely, wait a few "
        f"seconds, try again. You can also try the legacy CTI "
        f"KYFGLibGenTL_vc141.cti instead of KYVPGenTL.\n\n"
        f"Traceback (tail):\n{tail}"
    )
