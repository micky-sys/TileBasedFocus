# ClearCore focus / flight control — USB serial protocol

**Companion doc:** [`gps_sbf_correlator.md`](./gps_sbf_correlator.md)
describes the second COM port the host opens — a Septentrio receiver
on USB CDC at 115200 8N1 streaming SBF — and how each
`ExtEventPVTGeodetic` block is paired with the camera frame triggered
by the same PCB pulse.

**Windows host:** use **`Serial`** only — the ClearCore’s **USB virtual COM port** (115200 baud). That is the correct link for the focus app.

**`Serial0`** is separate hardware: **RS-232** (`Serial0.ttl(false)`) for other onboard / external equipment (e.g. collection / nav). The focus app must **not** use `Serial0`.

Messages are **line-oriented**: the firmware buffers until it sees **LF** (`\n`) only (see `checkSerialMessage()` in the sketch).

Source sketch: [Parallax_CLRCR_V2_2_copy_20260405110355.ino](../Parallax_CLRCR_V2_2_copy_20260405110355/Parallax_CLRCR_V2_2_copy_20260405110355.ino).

## Baud rate

- **115200** 8N1 (default Arduino `Serial.begin`).

## Focus adjustment mode

| Command | Action |
|--------|--------|
| `$M4*` + newline | Enter **mode 4** (“Focus adjustment”). |

**Important:** `setMode()` calls `stop()` then `EnableAndHome()` before switching modes. Entering `$M4*` triggers a **full stop + re-homing** of the sweep motors, not only “assume homing already done.” Plan UX or firmware accordingly if that is disruptive in flight.

## Camera selection (H-bridge mux)

| Command | Action |
|--------|--------|
| `$C1*` + newline | Select focus motor **camera 1** (`HB1Select` low, `HB2Select` low). |
| `$C2*` + newline | Select **camera 2** (`HB1Select` high, `HB2Select` low). |
| `$C3*` + newline | Select **camera 3** (`HB1Select` high, `HB2Select` high). |

## Focus step strength (H-bridge drive level)

| Command | `focusStrength` (firmware) |
|--------|----------------------------|
| `$S1*` + newline | `str1` = **7000** |
| `$S2*` + newline | `str2` = **10000** |
| `$S3*` + newline | `str3` = **14000** |

Firmware prints e.g. `Focus strength 1` on `Serial` when these are received. Map these to coarse / medium / fine in the host UI as you prefer.

## Focus nudge (modes 4 and 5)

Lines that **start with** `+` or `-` (after optional `$…` handling — see below):

- **`+`…** — positive H-bridge pulse on the **currently selected** camera’s focus motor.
- **`-`…** — negative pulse.

Pulse duration is `focusIncCount * focusTimeIncrement` ms, where:

- `focusIncCount` = **length of the whole line string** (number of characters before `\n`).
- `focusTimeIncrement` = **10** ms.

So a single `+\n` is one `+` → length 1 → **10 ms** per pulse. The firmware also allows multiple characters on one line (e.g. `+++\n` → **30 ms** in one command); that was useful for **bench testing**, but **in flight the host should send only a single `+` or `-` per line** (one nudge per key press / action). Same idea for `-`.

If not in mode 4 **or mode 5**, `+` / `-` lines are ignored for motion (no pulse). Mode 5
(ground test) accepts the same focus nudges so host autofocus can run while the mirrors
sweep; the firmware services them between frames of the stepped sweep.

## Time between triggered frames in mode 4

In mode 4, `TakePicsInPlace()` uses global **`picsInPlaceDelay`** (milliseconds) as the
**trigger-to-trigger period**. It is gated the same way as the sweep (a busy-wait on
`lastFrameTime`), so the trigger pulse (`picDelayCurr`) is absorbed *within* the period
rather than added on top.

- **Initial value** in sketch: **250** ms (`picsInPlaceDelay = 250` at startup). Entering mode 4 does **not** reset this; if you previously used mode 6/7, it may still be 1000 ms until you adjust it.
- **`>` + newline** (in mode 4 or 5): increase by **50** ms, capped at **1500** ms (firmware), not 1000 ms.
- **`<` + newline** (in mode 4 or 5): decrease by **50** ms, floored at **`minTimeBetweenFrames`** (**209** ms ≈ the camera's ~4.8 FPS ceiling). This floor was previously an arbitrary **250** ms clamp; it is now tied to the real sensor limit so M4 autofocus can run at max FPS.

Firmware prints `PicDelay:<value>` on `Serial` after `>`/`<`.

**Discrepancy vs. a 1 s cap:** If you want a **1000 ms** maximum, change the firmware cap (currently `1500`) or clamp in the host.

## Other `$…` commands (same Serial, newline-terminated)

The same `Serial` parser handles many commands; only focus-relevant ones are summarized above. Examples present in code:

- `$M0*` … `$M7*` — other operating modes  
- `$M5*` — **ground-test mode**: a continuous, non-blocking *stepped* sweep (one mirror position + one frame per firmware loop) with host autofocus running in the background — `+`/`-`/`>`/`<` are serviced between every frame. Preview-only on the host (not a recording mode).  
- `$P<n>*` — set mode-5 stepped-sweep positions per cycle (clamped to `[2, 64]`). Applied live when already in M5 (recomputes mirror angles and restarts the step index); otherwise stored for the next M5 entry.  
- `$stop*`, `$reset*` — stop / full `setup()`  
- `$Click*` — trigger sweep start flag  

Host software should **only send** the commands you intend, or guard against typos.

## Responses on `Serial` (USB)

After each complete line, the firmware typically prints:

```text
ack: <exact line without newline>;  A# <incrementing counter>
```

Then it may print mode-specific lines (`Camera 1 set`, `PicDelay:300`, motor debug, etc.).

Use these for logging; do not rely on a rigid binary ACK unless you tighten the protocol.

## Quick reference (host sends)

```text
$M4*\n          → focus adjustment mode (after stop + re-home in current firmware)
$C1*\n          → camera 1
$S2*\n          → medium strength
+\n             → one + nudge (flight: use single + per line)
-\n             → one − nudge (flight: use single − per line)
>\n             → +50 ms inter-frame delay (max 1500 in FW)
<\n             → −50 ms inter-frame delay (min 209 = camera max FPS)
```

(Bench only: multiple `+` or `-` on one line lengthens the pulse; not used in flight.)
