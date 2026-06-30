"""
Workarounds for KAYA Vision Point GenTL (KYVPGenTL) with Harvesters.

1) Empty XML URL on some ports -> LogicalErrorException handled in Harvesters
   retrieve path.

2) register_event() often fails with ErrorException / GenericException (-1001 etc.)
   where Harvesters only treats NotImplementedException as "skip module events".
   We map those GenTL errors to NotImplementedException for event registration.

3) After DataStream_open, Harvesters logs via _family_tree(node) which reads node.id_.
   KAYA may throw InvalidParameterException (-1009) on id_; Harvesters only catches
   AttributeError. We wrap _family_tree to fall back safely.

4) ImageAcquirer.start() only catches GenTL_GenericException around GenICam reads
   (AcquisitionMode, buffer counts, TLParamsLocked) and has no guard on
   AcquisitionStart.execute(). KAYA often raises genapi AccessException /
   GenericException there; we treat GenApi the same as GenTL where Harvesters does,
   and log-and-continue if AcquisitionStart.execute() fails (external trigger often
   needs only DSStartAcquisition).

The "GenTL producer does not implement DSAnnounceCompositeBuffer" lines on stderr
are normal probes of optional GenTL 1.6 APIs — not the root error.

Call apply_kaya_gentl_patch() before any harvesters.Harvester() usage.
"""

from __future__ import annotations

import sys
from typing import Any

_retrieve_patch_applied = False
_register_event_patch_applied = False
_family_tree_patch_applied = False
_start_patch_applied = False

_PATCH_NOTICE = (
    "[TileBasedFocus] KAYA GenTL + Harvesters patch active "
    "(XML URL + register_event + _family_tree + ImageAcquirer.start).\n"
)


def _looks_like_gentl_register_failure(e: BaseException) -> bool:
    s = (str(e) + " " + repr(e)).upper()
    keys = (
        "GENTL",
        "GC_ERR",
        "NOT_IMPLEMENTED",
        "-1001",
        "-1003",
        "UNSPECIFIED",
        "NO ERROR",
        "ERROREXCEPTION",
        "GENICAM",
    )
    if any(k in s for k in keys):
        return True
    m = getattr(e, "__module__", "") or ""
    if m.startswith(("genicam", "_gentl")):
        return True
    return False


def _patch_register_event_on_class(cls: Any, class_name: str) -> None:
    import logging

    from genicam.gentl import (
        ErrorException,
        GenericException,
        NotImplementedException,
    )

    log = logging.getLogger(__name__)
    attr = "_kaya_original_register_event"
    if not hasattr(cls, attr):
        setattr(cls, attr, cls.register_event)
    orig = getattr(cls, attr)

    def register_event(self, event_id):
        try:
            return orig(self, event_id)
        except NotImplementedException:
            raise
        except (GenericException, ErrorException) as e:
            log.debug(
                "KAYA GenTL: %s.register_event -> NotImplemented (%s)",
                class_name,
                str(e)[:160],
            )
            raise NotImplementedException(str(e)) from e
        except Exception as e:
            if _looks_like_gentl_register_failure(e):
                log.debug(
                    "KAYA GenTL: %s.register_event (generic) -> NotImplemented (%s)",
                    class_name,
                    str(e)[:160],
                )
                raise NotImplementedException(str(e)) from e
            raise

    cls.register_event = register_event


def _apply_family_tree_patch() -> None:
    global _family_tree_patch_applied

    if _family_tree_patch_applied:
        return

    import logging

    import harvesters.core as hc

    log = logging.getLogger(__name__)
    orig = hc._family_tree

    def _kaya_family_tree(node, tree=""):
        try:
            return orig(node, tree)
        except BaseException as e:
            log.debug("KAYA GenTL: _family_tree fallback (%s)", e)
            try:
                return str(node)
            except BaseException:
                return "<GenTL object>"

    hc._family_tree = _kaya_family_tree
    _family_tree_patch_applied = True


def _apply_image_acquirer_start_patch() -> None:
    global _start_patch_applied

    if _start_patch_applied:
        return

    import harvesters.core as hc
    from genicam.genapi import GenericException as GenApi_GenericException
    from genicam.gentl import ACQ_START_FLAGS_LIST
    from genicam.gentl import GenericException as GenTL_GenericException

    from gentl_acq_helpers import try_execute_acquisition_commands

    _logger = hc._logger
    _family_tree = hc._family_tree

    def _kaya_image_acquirer_start(self, *, run_as_thread: bool = False) -> None:
        if not self.is_acquiring():
            self._num_images_to_acquire = 0

        if self._num_images_to_acquire == 0:
            try:
                acq_mode = self.remote_device.node_map.AcquisitionMode.value
            except (GenTL_GenericException, GenApi_GenericException, AttributeError) as e:
                num_images_to_acquire = -1
                _logger.warning(e, exc_info=True)
            else:
                if acq_mode == "Continuous":
                    num_images_to_acquire = -1
                elif acq_mode == "SingleFrame":
                    num_images_to_acquire = 1
                elif acq_mode == "MultiFrame":
                    try:
                        num_images_to_acquire = (
                            self.remote_device.node_map.AcquisitionFrameCount.value
                        )
                    except GenApi_GenericException as e:
                        num_images_to_acquire = -1
                        _logger.warning(e, exc_info=True)
                else:
                    num_images_to_acquire = -1

            self._num_images_to_acquire = num_images_to_acquire

        if not self.is_armed():
            if not self._create_ds_at_connection:
                self._setup_data_streams(file_dict=self._file_dict)

            for ds in self._data_streams:
                try:
                    if ds.defines_payload_size():
                        buffer_size = ds.payload_size
                    else:
                        buffer_size = self.remote_device.node_map.PayloadSize.value
                except GenApi_GenericException as e:
                    _logger.warning(
                        "KAYA: payload size via stream failed (%s); trying PayloadSize",
                        e,
                        exc_info=True,
                    )
                    try:
                        buffer_size = self.remote_device.node_map.PayloadSize.value
                    except GenApi_GenericException as e2:
                        raise RuntimeError(
                            "Could not read PayloadSize from device (KAYA / GenICam)."
                        ) from e2

                num_required_buffers = self._num_buffers
                try:
                    num_buffers = ds.buffer_announce_min
                    if num_buffers < num_required_buffers:
                        num_buffers = num_required_buffers
                except (GenTL_GenericException, GenApi_GenericException) as e:
                    num_buffers = num_required_buffers
                    _logger.warning(e, exc_info=True)

                num_buffers = max(num_buffers, self._num_images_to_acquire)

                raw_buffers = self._create_raw_buffers(num_buffers, buffer_size)
                buffer_tokens = self._create_buffer_tokens(raw_buffers)
                self._announced_buffers = self._announce_buffers(
                    data_stream=ds, _buffer_tokens=buffer_tokens
                )
                self._queue_announced_buffers(
                    data_stream=ds, buffers=self._announced_buffers
                )

                try:
                    self.remote_device.node_map.TLParamsLocked.value = 1
                except (GenTL_GenericException, GenApi_GenericException):
                    pass
                except AttributeError:
                    _logger.debug(
                        "no TLParamsLocked: {}".format(
                            _family_tree(self._device_proxy.module)
                        )
                    )

                try_execute_acquisition_commands(
                    self.remote_device.node_map,
                    "after TL lock, before DS start",
                    _logger,
                )

                ds.start_acquisition(
                    ACQ_START_FLAGS_LIST.ACQ_START_FLAGS_DEFAULT, -1
                )

            self._has_attached_chunk = False
            self._is_acquiring = True

            if run_as_thread:
                if self._event_new_buffer_thread:
                    self._event_new_buffer_thread.start()

        _logger.info("started acquisition: {0}".format(self))

        try_execute_acquisition_commands(
            self.remote_device.node_map,
            "after DS acquisition start",
            _logger,
        )

        _logger.debug(
            "started streaming: {0}".format(_family_tree(self._device_proxy.module))
        )

        if self._profiler:
            self._profiler.print_diff()

    hc.ImageAcquirer.start = _kaya_image_acquirer_start
    _start_patch_applied = True


def apply_kaya_gentl_patch() -> None:
    global _retrieve_patch_applied, _register_event_patch_applied

    import harvesters.core as hc
    from genicam.genapi import LogicalErrorException

    if not _retrieve_patch_applied:
        rf = hc.Module._retrieve_file_path
        orig = rf.__func__ if isinstance(rf, staticmethod) else rf

        @staticmethod
        def _retrieve_file_path_patched(
            *,
            port=None,
            url=None,
            file_path_to_load=None,
            xml_dir_to_store=None,
            file_dict=None,
        ):
            try:
                return orig(
                    port=port,
                    url=url,
                    file_path_to_load=file_path_to_load,
                    xml_dir_to_store=xml_dir_to_store,
                    file_dict=file_dict,
                )
            except LogicalErrorException as e:
                m = str(e)
                if "does not hold any URL" in m:
                    return False, None
                raise

        hc.Module._retrieve_file_path = _retrieve_file_path_patched
        _retrieve_patch_applied = True

    _apply_family_tree_patch()
    _apply_image_acquirer_start_patch()

    if not _register_event_patch_applied:
        import genicam.gentl as gl

        # Do not patch DataStream: NEW_BUFFER registration is not wrapped in
        # try/except in Harvesters; NotImplemented there would abort init.
        for c, name in (
            (gl.System, "System"),
            (gl.Interface, "Interface"),
            (gl.Device, "Device"),
        ):
            _patch_register_event_on_class(c, name)
        _register_event_patch_applied = True
        sys.stderr.write(_PATCH_NOTICE)
        sys.stderr.flush()
