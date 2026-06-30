# PKG issue — "Could not load PyInstaller's embedded PKG archive"

This is a **build-time** trap, not a code or spec bug. Read this before
publishing `Bay14Reader.exe` (or any other PyInstaller bundle) from this
repo — and before "fixing" the spec file with more `hiddenimports`.

## Symptom

After running PyInstaller successfully, launching the resulting EXE on
**any** Windows machine pops a modal dialog titled **Error** with text:

> Could not load PyInstaller's embedded PKG archive from the executable
> (`<path>\Bay14Reader.exe`)

The process stays alive holding the popup, so naive smoke tests that only
check `proc.HasExited == False` after a few seconds will report success
even though the app never started. **Always check
`(Get-Process -Id $pid).MainWindowTitle` (or eyeball the screen) before
declaring victory.**

## Root cause

The repo lives on **Google Drive for Desktop** (`G:\My Drive\…`). Google
Drive presents a **virtual / streamed filesystem**, not a real NTFS
volume. PyInstaller's `COLLECT` step appends a `PKG` archive (the
embedded Python + bytecode + datafiles) into the *tail* of the produced
`Bay14Reader.exe`. When that write goes through the virtual filesystem
layer, Drive buffers/streams the bytes asynchronously and the bootloader
later can't reliably `fseek` + `fread` its own embedded archive back at
startup — so it aborts with the popup above.

Same trap exists for **OneDrive (Files On-Demand)** and **Dropbox
(Smart Sync)**. Anywhere the kernel sees a file but a userspace shim is
proxying the bytes is dangerous for PyInstaller output.

### Evidence we collected on this repo (2026-05-25)

| Build location | COLLECT duration | EXE size | Launches? |
|---|---|---|---|
| `G:\My Drive\…\tools\dist\Bay14Reader\` | **163 s** | 15,584,256 B | **No — PKG popup** |
| `C:\Build\Bay14Reader\dist\Bay14Reader\` | **<10 s** | 16,078,608 B | Yes |

The local build EXE is **~480 KB larger** because the PKG tail wasn't
truncated. The 16× slower COLLECT on `G:\` is the smoking gun: Drive
was throttling/buffering every write.

## Fix

Redirect PyInstaller's `--workpath` and `--distpath` to a **real local
NTFS directory** (e.g. anywhere under `C:\` that is not inside a
synced-folder mount). The `.spec` file itself can stay where it is on
Google Drive — only the output bytes must land on real disk.

```powershell
py -3 -m PyInstaller --noconfirm --clean `
    --workpath C:\Build\Bay14Reader\build `
    --distpath C:\Build\Bay14Reader\dist `
    Bay14Reader.spec
```

After the build:

1. Zip `C:\Build\Bay14Reader\dist\Bay14Reader\` (the folder must contain
   `Bay14Reader.exe` and the sibling `_internal\` directory).
2. Copy the zip to the target machine via USB / share.
3. Unzip onto **local NTFS** on the target. Same trap applies on the
   target machine — never run the bundle directly from
   `G:\My Drive\…`, `C:\Users\<you>\OneDrive\…`, or a network share.

## Guardrail in this repo

`tools\build_bay14_reader_windows.ps1` now:

- Defaults `-BuildRoot` to `$env:LOCALAPPDATA\Bay14ReaderBuild` (always
  real local disk).
- **Throws** before invoking PyInstaller if the resolved `BuildRoot`
  matches `\My Drive\`, `\OneDrive*\`, or `\Dropbox\` in the path.
- Passes `--workpath` / `--distpath` explicitly so the spec's default
  (which would land in `tools\build\` and `tools\dist\` next to the spec
  file, i.e. on Google Drive) is bypassed.

Do **not** revert these changes "to keep the build artifacts next to
the spec for convenience" — that's exactly how this issue was
introduced in the first place.

## Things this is *not*

When you see the PKG popup, resist the urge to bloat
`Bay14Reader.spec`. The following are **not** the cause and will not
fix it:

- Missing `hiddenimports` (those manifest as
  `ModuleNotFoundError: …` at runtime, *after* the bootloader loads
  the archive — so you'd at least get past the popup).
- Missing DLLs from `collect_all`. Those produce
  `ImportError: DLL load failed while importing …`, not the PKG-archive
  popup.
- Antivirus quarantining the EXE. AV usually deletes the file or blocks
  process creation; you'd see "Access is denied" or no popup at all.
- Wrong bootloader architecture. PyInstaller picks the right one
  automatically when you `pip install pyinstaller` for your Python
  build; this hasn't been a real failure mode in years.
- Mark-of-the-Web / Zone.Identifier. That triggers SmartScreen, a
  totally different "Windows protected your PC" dialog.

The PKG popup specifically means **the bootloader opened the EXE
file, sought to the expected PKG offset, and the bytes there did not
form a valid PKG archive**. On this repo, the only known cause is
"the EXE was written through a virtual filesystem during the build".

## TL;DR for future agents

> Build PyInstaller bundles on **`C:\`** (or any real local NTFS path),
> never directly into `G:\My Drive\…`. If you see
> *"Could not load PyInstaller's embedded PKG archive"*, check
> `--distpath` first — don't go editing the `.spec`.
