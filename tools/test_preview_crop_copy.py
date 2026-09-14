"""The preview-ROI crop must own its data, and must be copied exactly once.

The pipeline cuts AF patches out of a SHM slot that is released -- and reused by the
next frame -- in the `finally` of the same block. A crop that still shares memory with
that slot is a use-after-free whose symptom is the NEXT frame's pixels turning up in
THIS frame's score, which is indistinguishable from a real focus change.

It must also not be copied twice. `np.ascontiguousarray(view).copy()` did exactly that
on every patch on every frame, on the thread that delivers frames, which is what put
~12 ms per patch into the frame period and cost ~14% of frames to conflation.

    py -3 tools/test_preview_crop_copy.py
"""
import sys

import numpy as np


def crop(view):
    """The form pipeline.py uses. `np.array` copies by default; order='C' fixes the
    layout. One allocation, always owned, regardless of the input's strides."""
    return np.array(view, order="C")


def test_crop_owns_its_data_even_when_the_slice_is_contiguous() -> None:
    slot = np.arange(8 * 6, dtype=np.uint16).reshape(8, 6)
    cases = (("strided ROI", slot[2:6, 1:4]), ("full-width ROI", slot[2:6, 0:6]))
    for name, view in cases:
        c = crop(view)
        assert not np.shares_memory(c, slot), f"{name}: crop aliases the SHM slot"
        assert c.flags["C_CONTIGUOUS"], f"{name}: crop is not C-contiguous"
        assert np.array_equal(c, view), f"{name}: crop has the wrong pixels"
        expect = c.copy()
        slot[:] = 999                     # the slot is recycled for the next frame
        assert np.array_equal(c, expect), \
            f"{name}: crop changed when the slot was reused"


def test_the_old_form_is_why_this_is_not_a_bare_copy_deletion() -> None:
    """`ascontiguousarray` passes an ALREADY-contiguous slice straight through.

    So on a full-width ROI the old form's trailing `.copy()` was the only thing
    detaching the crop from the slot. Deleting just the `.copy()` would have been a
    smaller diff and a latent use-after-free; this pins the reason.
    """
    slot = np.arange(8 * 6, dtype=np.uint16).reshape(8, 6)
    assert np.shares_memory(np.ascontiguousarray(slot[2:6, 0:6]), slot), \
        "numpy no longer passes a contiguous slice through ascontiguousarray"
    assert not np.shares_memory(np.ascontiguousarray(slot[2:6, 1:4]), slot), \
        "a strided slice must already be copied by ascontiguousarray alone"


def test_crop_is_a_plain_owner_not_a_view_of_a_copy() -> None:
    """`base is None` is the one locally checkable trace of a single copy.

    `ascontiguousarray(v).copy()` also returned a base-less array, so this cannot tell
    one copy from two -- that property is a performance claim and its real check is the
    rig's `conflated=` counter, not this file. What this does pin is that the crop is a
    self-owned buffer rather than a view onto some intermediate that could be freed.
    """
    slot = np.zeros((2048, 2048), dtype=np.uint16)
    c = crop(slot[100:1124, 100:1124])              # a live-sized AF patch
    assert c.base is None, "crop must own its buffer outright"
    assert c.nbytes == 1024 * 1024 * 2, f"unexpected crop size {c.nbytes}"


def main() -> int:
    tests = sorted((n, f) for n, f in globals().items()
                   if n.startswith("test_") and callable(f))
    for name, fn in tests:
        fn()
        print(f"ok   {name}")
    print(f"\n{len(tests)} preview-crop tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
