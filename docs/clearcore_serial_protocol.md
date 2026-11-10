# ClearCore focus / flight control — USB serial protocol

**Companion doc:** [`gps_sbf_correlator.md`](./gps_sbf_correlator.md)
describes the second COM port the host opens — a Septentrio receiver
on USB CDC at 115200 8N1 streaming SBF — and how each
`ExtEventPVTGeodetic` block is paired with the camera frame triggered
by the same PCB pulse.

**Windows host:** use **`Serial`** only — the ClearCore’s **USB virtual COM port** (115200 baud). That is the correct link for the focus app.

**`Serial0`** is separate hardware: **RS-232** (`Serial0.ttl(false)`) for other onboard / external equipment (e.g. collection / nav). The focus app must **not** open `Serial0` itself — but it can now *reach* the board through it indirectly, with the navigation app relaying. See [Reaching the board over RS-232](#reaching-the-board-over-rs-232-kmopoflight-bridge) below.

Messages are **line-oriented**: the firmware buffers until it sees **LF** (`\n`) only (see `checkSerialMessage()` in the sketch).

Source sketch: [Parallax_CLRCR_V2_4.ino](../Parallax_CLRCR_V2_4/Parallax_CLRCR_V2_4.ino).

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

Each strength command sets **both** the H-bridge duty (`focusStrength`) **and** the
pulse time increment (`focusTimeIncrement`, ms per `+`/`-` char):

| Command | `focusStrength` (duty) | `focusTimeIncrement` |
|--------|------------------------|----------------------|
| `$S0*` + newline | `str0` = **7000** | **3** ms |
| `$S1*` + newline | `str1` = **7000** | **10** ms |
| `$S2*` + newline | `str2` = **10000** | **10** ms |
| `$S3*` + newline | `str3` = **14000** | **10** ms |

`$S0*` is the **extra-fine** step: duty 7000 at a **3 ms** pulse — the shortest move
that reliably turns the shaft on this rig (below it the motor only buzzes). Selecting
`$S1*`–`$S3*` restores the normal **10 ms** increment. Firmware prints e.g.
`Focus strength 0` on `Serial` when these are received.

## Focus nudge (modes 4 and 5)

Lines that **start with** `+` or `-` (after optional `$…` handling — see below):

- **`+`…** — positive H-bridge pulse on the **currently selected** camera’s focus motor.
- **`-`…** — negative pulse.

A line of **N** `+` (or `-`) characters fires **N discrete pulses**: each pulse drives
the H-bridge ON for `focusTimeIncrement` ms, then OFF for a fixed **~12 ms gap**, repeated
N times. Sequenced non-blocking by `serviceFocusPulse()` so the trigger keeps firing.

- `focusTimeIncrement` = ON time per pulse, set by the last strength command (**3** ms after `$S0*`, **10** ms after `$S1*`–`$S3*`); also settable directly via **`$FT<n>*`** (clamp `[1,100]`).
- The OFF gap lets the shaft coast to rest between pulses, so each pulse is ~**one calibrated increment** (≈24 encoder counts at `$S0*`).

So after `$S0*`, `+\n` is one 3 ms pulse; `++++\n` is **four** 3 ms pulses (~4×24 counts),
NOT one continuous 12 ms drive. This matters because a continuous burst let the motor
accelerate to several rev/s, which made counts/pulse nonlinear **and** spun the shaft past
half a revolution between the 10 Hz `$ENC` samples — aliasing the host's absolute-encoder
unwrap so it dropped whole revolutions. Discrete pulses keep counts/pulse linear and the
encoder trackable, so the autofocus host can safely send a multi-char move as one line.

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
- `$M6*` — **timelapse mode**: mirrors held in the mid position (same as M4) firing one trigger per second (`setMode(6)` sets `picsInPlaceDelay = 1000`). Host records full-res frames to disk (same write path as M0/M1).  
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

All host output goes through a `Host` tee (`TeePrint`), which always writes to USB and
**also** to `Serial0` while the last host command arrived there (`hostOnSerial0`). TopoFlight,
the other possible `Serial0` peer, never sends host commands, so it never sees this chatter —
it only gets the `$Done*` / `$Ready*` it already expects.

## Reaching the board over RS-232 (Kmopoflight bridge)

When the navigation app **Kmopoflight** is running in X‑Parallax mode it holds the ClearCore's
RS-232 port open, and the capture app's ClearCore traffic is relayed through it over loopback
UDP instead of USB (`host/capture/kmopo_bridge.py` ↔ Kmopoflight's `bridge.py`,
127.0.0.1:56611):

```text
Iron661 capture ──UDP 56611──▶ Kmopoflight ──COMn (RS-232 / Serial0)──▶ ClearCore
                ◀── one datagram per printed line ──  ◀── Host tee ──
```

- Capture pings `#PING` once a second; Kmopoflight answers `#PONG` only while its camera port
  is open. PONG → bridged (USB released); 3 s without PONG → back to USB. Automatic both ways.
- Commands cross in the **unchanged USB format** (`$M4*\n`, `+++\n`, `?\n`); Kmopoflight
  re-frames them for RS-232, which cuts on `*` and not on newline. Only ≤64 printable-ASCII
  characters per command are forwarded.
- `checkSerial0Message()` drains **one complete `*`-terminated message per call**, matching
  `checkSerialMessage()` on USB. It previously consumed a single *byte* per call, so a message
  needed as many `loop()` iterations as it had characters — and the sweeping modes block
  inside `Sweep()` for a whole sweep (~3 s), which stretched a 14-character `$Collectionon*`
  to **~45 s** before the board acted on it. After the fix, `$CollectionOFF*` → `$Done*` in
  **2.77 s** (one blocking sweep). A command still waits up to one `Sweep()` to be *read*;
  removing that means calling `CheckMessages()` from inside `Sweep()`'s settle/wait loops.

### Liveness probe — and a prefix hazard

There is no "are you there" command, but the `Serial0` handler passes anything it does not
recognise to `handleHostCommand()`, which unconditionally echoes `ack: <cmd>;  A# <n>` through
the `Host` tee. So **an unrecognised token round-trips**: the sender's `$`…`*` framer lifts the
echoed command back out of the ack, proving the board is powered *and* parsing, with no side
effect beyond incrementing the ack counter. Kmopoflight uses `$KMOPO*` for this (measured
round-trip 13 ms).

⚠️ A probe token must collide with **nothing**. Several commands match by **prefix** —
`$P`, `$W`, `$FT`, `$SV`, `$Click` — so the obvious `$Ping*` is a trap: it matches `$P` and is
read as *set mode-5 positions per sweep*, silently reconfiguring the sweep instead of just
answering. Check any new token against both the prefix list and the exact-match list
(`$stop*`, `$M0*`–`$M6*`, `$T*`, `$E*`, `$X0*`, `$X1*`, `$S0*`–`$S3*`, `$C1*`–`$C3*`,
`$reset*`) before using it.

### Replies the `Serial0` branch produces directly

| Received on `Serial0` | Reply |
|---|---|
| `$Collectionon`, `$CollectionON`, `$CollectionOff`, `$CollectionOFF` | `$Done*` |
| `$Sensor=…` | `$Ready*` |
| `$Click` | *(none)* |
| `$AboutToEnter` | *(silence — ignored)* |
| anything else | `hostOnSerial0 = true`, then `handleHostCommand()` → `ack: <cmd>;  A# <n>` |

Note the branches are **case-sensitive**: `$CollectionOff` and `$CollectionOFF` are matched
separately on purpose.

## Quick reference (host sends)

```text
$M4*\n          → focus adjustment mode (after stop + re-home in current firmware)
$C1*\n          → camera 1
$S2*\n          → medium strength
+\n             → one + pulse (N chars → N discrete pulses, 3/10 ms on + ~12 ms gap each)
-\n             → one − pulse
>\n             → +50 ms inter-frame delay (max 1500 in FW)
<\n             → −50 ms inter-frame delay (min 209 = camera max FPS)
```

(Multiple `+` or `-` on one line = that many discrete pulses; the autofocus host uses this to move a known number of increments in one command.)
