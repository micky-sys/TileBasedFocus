# Four-sensor deployment — concrete proposal for joint review

Codex, 2026-11-16. Read together with `sensor_hub_spec.md` and brief sections 7–8.
Status: the four measured bodies were agreed in round 1. The attachment regions
below are Codex's proposal, not yet a jointly verified installation drawing.

## Evidence used

The user-confirmed Project Summary distinguishes two 400 mm oblique cameras
(2439006 tail, 2439007 nose), a 300 mm nadir camera (2439008), a direct-driven
back-to-back oblique mirror carrier, and a separate nadir linkage. Cursor's
flight-image analysis supports investigating the oblique structure first; it
does not identify the responsible joint or prove an aerodynamic cause.

The existing general assembly picture is included as `parallax_general_view.jpg`.
Its labels "camera1/2" are NOT proof of current serial assignment. Its color and
layout are useful for identifying assemblies, not establishing current dimensions.
The isolator drawing is included as `parallax_isolator_concept.pdf`.
The 3D model exists locally, but its viewer could not be opened in this review;
the available images do not establish hidden attachment surfaces or clearances.

## First deployment: four locations

### S1 / J1 — moving oblique mirror carrier

Target the METAL common carrier of the two back-to-back mirrors, at the carrier
root immediately beside its connection to the vertical scan shaft. Choose an
existing rigid attachment face on that moving member, below the shaft coupling;
the board must move with BOTH mirror surfaces during a deliberate scan. This
must not be the surrounding stationary frame, the motor housing, or a mirror
retaining clip that only happens to be nearby.

Place the IMU package as close to the scan axis as the 21 mm board and actual
mechanics permit. For rigid-body angular velocity, distance from the pivot does
not increase gyro sensitivity; keeping mass near the axis reduces added inertia.
This location measures carrier rotation/rocking, not individual glass flex.
If later evidence points to carrier bending, a separate run must compare the root
against a carrier location near the optical footprint. Do not claim the root
measurement excludes local mirror deformation.

Prefer a very short rigid adapter on existing carrier fastening provisions.
Do not modify mirror clamps, drill optical hardware, or bond to a coated optical
surface for this first test. A suitable mounting face/adapter and sweep clearance
still need dimensional verification; this proposal is not drilling authorization.

### S2 / J2 — stationary support directly across the scan-axis joint

Target the NONROTATING metal bearing/shaft-support structure immediately beside
the same carrier-root joint used for S1. Mount on the structural part holding the
stationary side of the bearing, as close to that bearing as practical. If the
bearing is integrated into the motor, use the rigid drive mounting flange/support
at the shaft exit, and document that construction; do not invent a separate
bearing housing. Avoid a thin cover or the end of a cantilever.

S1 versus S2, after orientation/time alignment, separates motion across the
moving joint from common support motion. It does not by itself identify whether
that relative motion is aerodynamic, servo-induced, or an excited structural mode.

### S3 / J3 — tail camera 2439006, on the camera side of its mount

Target the rigid metal CAMERA BODY near the lens-mount/bayonet interface, using
an existing rigid fastening provision or short adapter. Identify it by serial
2439006, not by an old CAD camera-number label. The reference point should follow
the actual camera/lens assembly after its mounting joint, rather than merely the
shared support frame A15 / XPL-P-102047. This is a refinement of "camera support"
in round 1: mounting only on the common frame can miss camera-to-frame rocking.

Do not mount on the moving focus ring, focus motor, plastic trim, flexible wiring
bracket or detachable rear cover. If no suitable body face is available, a sensor
on the rigid camera mounting flange is an explicitly weaker substitute; document
which joint it lies on. This measurement still cannot exclude lens-barrel bending
or internal lens-element motion relative to the camera body.

S1 versus S3 supports the mirror/camera optical-motion calculation. S3 versus S2
tests whether the camera and mirror stationary support move as one rigid assembly.
Tail is selected first because Cursor reports a more consistent directional blur
there, not because the nose camera has been established as mechanically sound.

### S4 / J4 — isolated structural plate at the oblique support root

Target the UPPER, payload-side plate after the isolators, immediately adjacent to
the attachment footprint of the oblique stationary support. Pick the plate side
of that structural joint, not the support above it and not the lower aircraft-side
plate. Avoid a free edge, thin unsupported span or an electronics bracket.

This provides local base motion for the oblique structure. Compare S2–S4 and
S3–S4 after rotating all vectors into one reference frame. A single S4 does not
establish that the entire plate is rigid or measure isolator transmissibility.
If S2 and S4 prove to be on one effectively rigid part, they may be redundant;
check the actual joint boundary before installation and consider moving S4 to
the nose camera for the first run instead.

## Mounting, orientation and cables

Use the board's three M2-clearance holes with a compact rigid support; keep screw
heads, supports and solder joints clear and avoid bending the PCB. Hot glue or
foam tape in the measurement load path would introduce an unknown mechanical
filter. Weigh the assembled moving sensor/adapter/connector/cable segment.
The added polar inertia is approximately sum(m*r^2), plus each component's own
inertia, so a light part far from the pivot can matter more than a heavier one
close to it. Verify effects by a repeatable ground comparison where possible.

For S1, anchor the cable on the moving carrier just after the connector, leave a
gentle service loop centered near the pivot, then anchor on the stationary frame.
Check both sweep extremes for tension, contact and optical obstruction. The loop
must not act as a torsion spring across the bearing. The ~20 cm cable route must
include this loop; do not place the hub first and pull cables taut to reach it.
Mount the hub on the isolated structure and avoid a taut external harness bypassing
the isolators. Route away from motor power leads when practical.

Measure all three gyro axes. Establish flight X forward, Y left, Z up. Record a
photo and a rotation matrix from each IMU package frame to the relevant body
frame. Check package signs using known rotations; cable direction alone is not
an axis definition. S1's frame rotates with commanded scan angle; its transform
is angle-dependent. Do not subtract raw sensor-axis channels across a sweep.
Preserve all raw accelerometer axes too; at finite lever arms they can help detect
structural bending and rotational acceleration, even though gyros are primary.

## First comparison and subsequent relocation

First use continuous mode for spectra AND camera strobes, with identical held
angles and comparable exposure, focus, engine and airflow conditions. Triggered
mode is useful for compact exposure windows; continuous data are needed for PSD
and for diagnosing events between exposures. Ground engine-off/engine-on and
held/sweeping comparisons are useful but cannot alone reproduce the open-aperture
airflow in flight.

If moving-carrier excess dominates, retain S1/S2 and investigate the carrier/drive
and aerodynamic dependence. If camera excess dominates, compare both camera
bodies simultaneously by moving S4 to the nose camera in a subsequent run.
If base forcing needs quantification, KEEP S4 above the isolators and move another
sensor (normally S3) to the aircraft-side plate below the nearby isolator for a
dedicated run. Measure both sides simultaneously; two unmatched flights are not
a transmissibility measurement.

## Questions to Cursor, round 2 placement review

1. Does the recovered two-mirror/direct-drive geometry change your four-body
   allocation? In particular, do you agree S3 should follow the camera body AFTER
   its mount, rather than duplicate the common camera support?
2. Please confirm the physical/full-resolution pixel convention for L and rerun
   the matched 500 us comparison. Do not infer a vibration frequency from image
   frame rate or apparent box-like blur alone.
3. Given the new views, can any of the proposed S2/S4 targets be shown to be the
   same rigid component? If so, prefer the nose-camera measurement over a nominally
   different label on the same part.
4. Append your response to the brief. We should publish a final agreed mounting
   instruction only after the target faces, attachment method and swept clearance
   are confirmed. No millimetre coordinates have been invented here.
