# Keychron K7 Optical (White Backlight) Firmware Patcher

A Python script that patches the firmware inside Keychron's **official K7 firmware updater**. It writes a
new updater EXE, and you flash that EXE the same way as the original. You get remapped Fn layers, a Windows 11
mic-mute key, a brighter static backlight, faster key scanning and an optional **Snap Tap (SOCD)** mode for
A/D and W/S that you can switch on and off.

This repository contains **no firmware and no EXE files**. You download the official updater from Keychron
yourself, and the script patches your local copy.

**Input file: `K7-A2-68K-WHILE-US-OA-V1.05_20210611.exe` only.** No other updater, firmware version or
Keychron model works.

> [!IMPORTANT]
> **Only for the Keychron K7 with Keychron optical switches and white backlight (US ANSI, 68 keys).**
> The script works only with this exact official updater:
>
> | | |
> |---|---|
> | File | `K7-A2-68K-WHILE-US-OA-V1.05_20210611.exe` (firmware V1.05, 2021-06-11) |
> | Size | 1,863,680 bytes |
> | SHA-256 | `8f03b2e3da4ec2e4d1ddf854549e0db40f0b4acecb7ade78dae94525d4ae35d2` |
>
> RGB models, the mechanical (non-optical) K7, other layouts and other firmware versions are **not
> supported**. They use different firmware images. The script refuses to patch any other file, but it
> cannot tell which keyboard you plug in. Only flash the result onto the keyboard this updater is meant for.

> [!WARNING]
> You flash modified firmware at your own risk. Every patch was disassembled and checked against the stock
> code, and the code patches were also run in an emulator. The *Tested on hardware* column below shows
> which ones have run on a real keyboard. There is still no guarantee. Keep the original updater: running it restores the stock firmware (see
> [Going back to stock](#going-back-to-stock)). This project is not affiliated with or endorsed by Keychron.

---

## Features

Every build includes the **base layout changes**. Everything else is opt-in through flags.

| Flag | What it does | Kind | Tested on hardware |
|---|---|---|---|
| *(always)* | Fn layers swapped: **Fn1 + number row = F1–F12**, **Fn2 + number row = media/brightness**. **Fn1 + Home = End**. Del functions swapped (Fn1 + Del = backlight on/off). | table edit | ✅ |
| `--mic-mute` | **Fn2 + Del = Win+Alt+K** (Windows 11 microphone mute). Fn2 + 4 becomes a plain `4`. | code patch | ✅ |
| `--static-led` | Only the **static** backlight mode, which is also the default after flashing or a factory reset. Mode cycling is disabled. | table/data edit | ✅ |
| `--bright` | LED multiplexing with 5 instead of 6 phases, so each LED gets **+20 % on-time** (brighter). | code patch | ✅ |
| `--fast-scan` | Key matrix scanned **~3× as often**: a full scan takes about 2.3 ms instead of 7.7 ms, and debounce about 1.9 ms instead of 6.4 ms.¹ | constant edit | ✅ |
| `--snap-tap` | **Snap Tap / SOCD** for **A/D** and **W/S**: the last key pressed wins. **Fn2 + 4** switches it on and off. When it is off, **W/A/S/D are dark**. Requires `--mic-mute`. | code patch | ✅ |
| `--cs2-led` | Use with `--snap-tap`. When snap tap is off, only **LCtrl, LShift, Space, 1–4, E, W/A/S/D** stay lit instead of WASD going dark. | code patch | ✅ |
| `--dry-run` | Shows the old and new Windows Fn layers and the changed bytes. Writes nothing. | — | — |

¹ Timings assume the chip runs at about 48 MHz. That figure is an estimate derived from the firmware's
delay loops.

**Rapid Trigger (as on Wooting) is not possible on this keyboard.** The optical switches report only
on or off (one GPIO bit per key), not how far a key is pressed. Snap Tap is a purely logical feature,
which is why it can be added.

### Windows layout after patching

Only the Windows layer changes. The Mac layers stay exactly as they were.

| Key | Fn1 (stock → patched) | Fn2 (stock → patched) |
|---|---|---|
| `1`…`=` | media/brightness → **F1…F12** | F1…F12 → **media/brightness** |
| `1` / `2` | Display brightness − / + → F1 / F2 | F1 / F2 → **Display brightness − / +** |
| `3` | Task View → F3 | F3 → **Task View** |
| `4` | Explorer (Win+E) → F4 | F4 → **Explorer** · with `--mic-mute`: **4** · with `--snap-tap`: **Snap Tap on/off** |
| `5` / `6` | Backlight − / + → F5 / F6 | F5 / F6 → **Backlight − / +** |
| `7` / `8` / `9` | Prev / Play / Next → F7–F9 | F7–F9 → **Prev / Play / Next** |
| `0` / `-` / `=` | Mute / Vol− / Vol+ → F10–F12 | F10–F12 → **Mute / Vol− / Vol+** |
| `Del` | Cycle backlight mode → **Backlight on/off** | Backlight on/off → **Cycle mode** · with `--static-led`: **Del** · with `--mic-mute`: **Mic mute** |
| `Home` | Home → **End** | Home |

With `--mic-mute`, the Explorer shortcut (Win+E) is gone: its handler is reused to send Win+Alt+K.

### Snap Tap (`--snap-tap`)

- If both keys of a pair (**A/D** or **W/S**) are held, only the **one pressed last** is sent. When you
  release it, the other key is sent again right away. Both changes go out in the same USB report.
- **Fn2 + 4** (Windows mode) switches snap tap on or off. When it is off, every key behaves exactly like
  stock.
- **Indicator:** when snap tap is off, W/A/S/D are dark (or, with `--cs2-led`, only the CS2 key set is
  lit). When it is on, the backlight looks normal. If the backlight is switched off completely
  (Fn1 + Del), there is no indicator.
- **Snap tap is ON after every plug-in or PC restart.** The state lives in RAM and is not saved.
- Other keys are not affected.

> [!CAUTION]
> **Anti-cheat rules:** Valve bans hardware SOCD / Snap Tap on **official Counter-Strike 2 servers**
> (August 2024 update). Players whose input looks automated get **kicked from the match**. FACEIT
> prohibits it as well. **Press Fn2 + 4 before playing CS2 on these servers** and make sure the
> "off" lighting is showing. Check the rules of any other game yourself.
> Sources: [PC Gamer](https://www.pcgamer.com/games/fps/valve-says-weve-decided-to-draw-a-clear-line-and-bans-snap-tap-keyboard-automation-from-counter-strike-2),
> [Dust2.us](https://www.dust2.us/news/51933/cs2-8-19-update-null-binds-snap-tap-and-jump-binds-banned-in-latest-update),
> [TechSpot](https://www.techspot.com/news/104359-valve-bans-keyboard-automation-counter-strike-2.html).

---

## Requirements

- Windows (the updater is a Windows program)
- Python 3.9 or newer (tested with 3.12). `k7_patch.py` uses only the standard library.
- Optional, for the development tools in `tools/`: `pip install capstone`

## Usage

1. Download the official K7 updater `K7-A2-68K-WHILE-US-OA-V1.05_20210611.exe` from Keychron's
   [K7 firmware page](https://www.keychron.com/pages/firmware-for-k7-us-keyboard). It is listed there as
   **K7 Optical Switch White Backlight Version V1.05**; take the **Windows** download. It must be this exact
   file. Check its SHA-256 (see above), for example with `certutil -hashfile <file> SHA256`.
2. Put the EXE next to `k7_patch.py`.
3. Preview the changes:
   ```
   python k7_patch.py --mic-mute --static-led --bright --fast-scan --snap-tap --cs2-led --dry-run
   ```
4. Build. Pick the flags you want. This is the full set:
   ```
   python k7_patch.py --mic-mute --static-led --bright --fast-scan --snap-tap --cs2-led
   ```
   The output EXE is named after the flags, for example
   `K7-A2-68K-WHILE-US-OA-V1.05_20210611_FnSwap_MicMuteDel_StaticLED_Bright_FastScan_SnapTapADWS_CS2LED.exe`.
   Each flag combination gets its own file, so existing builds are never overwritten. The script also
   writes the original and the patched firmware image to `firmware/` for inspection.
5. Flash it: run the new EXE with the keyboard connected by USB and follow the same procedure as for the
   official updater.

More examples:

```
python k7_patch.py                                        # base layout only           -> ..._FnSwap.exe
python k7_patch.py --mic-mute --static-led --bright       # daily driver without gaming -> ..._Bright.exe
python k7_patch.py --mic-mute --static-led --bright --snap-tap   # snap tap, WASD dark when off
```

### Going back to stock

The SN32F26x bootloader (0x7800–0x7FFF) is **not** part of the firmware image, and the updater never
writes to it. Running the **original** updater flashes the stock firmware back. If a build misbehaves,
flash an earlier build or the original updater. For a K7 that no longer types after a firmware update,
Keychron publishes a [hardware recovery procedure](https://www.keychron.com/pages/special-instruction-for-firmware-update-k7).
It has not been needed or tested with this patcher.

---

## How it works

The updater is an unsigned PE32 EXE. The script edits the following resources:

| Resource | Content |
|---|---|
| RCDATA 4000 (file offset `0x1AC378`) | raw firmware, 64 KB, unencrypted, Cortex-M0 (Sonix SN32F26x) |
| RCDATA 4014 (file offset `0x1C69AC`) | firmware checksum as a UTF-16 hex string, the 16-bit sum of all little-endian words (stock `135D`) |

The script recomputes the checksum resource. The PE header checksum is already invalid in the stock EXE,
so the script leaves it alone.

### What gets patched

| Feature | Firmware address | Change |
|---|---|---|
| Fn layers | `0x6500` / `0x6544` (Win Fn1/Fn2), `0x6588` / `0x65CC` (Mac) | Tables of 34 pairs `[key, Fn key]`. The lookup (`0x1E62`) searches only the *from* column of `0x6500`, so all four *from* columns must stay identical. Fn1 + Home uses the slot of the hidden RAlt↔RCtrl swap mode. |
| Mic mute | `0x263E`, `0x2654` | Handler of internal code `0x87` sends Win+Alt+K instead of Win+E |
| Static LED | `0x21EE`, `0x6001`, `0x6201` | Mode cycle wraps after mode 0. The stored and factory-default mode becomes 0 (static). |
| Bright | `0x5572` | LED phase counter wraps 5 → 1 instead of 5 → 0. Phase 0 drives no key LED. |
| Fast scan | `0x5542` | Matrix row scan every 3rd instead of every 10th LED timer interrupt |
| Snap tap | `0x2CA6`, `0x4ED6`, `0x6BDC`, `0x6D00…` | See below |

### Snap tap internals

- **Key hook.** The row-change loop (`0x2C22`) stores the debounced row word (`0x200001A8 + row·4`) and
  then calls the key event processor `0x1FF4` once per changed key. That single call (`0x2CA6`) is
  redirected to new code in free flash at `0x6D00`. The free area starts after the RAM init data, which
  ends at `0x6CFC`, and runs to the image signature at `0x77FC`.
  - **Fn2 + 4 press:** flips the on/off flag and swallows the press. The release passes through, which
    is harmless.
  - **Keys in the pair table** (`A/D`, `W/S`): the hook reads which keys of the pair are physically held,
    from both keys' debounced bits (W and S sit in different matrix rows). It works out what should be
    reported (if snap tap is on and both are held, only the key just pressed). It then compares that
    with what was reported before and emits **releases first, then presses** as ordinary `0x1FF4`
    events. When several changes arrive in the same scan, the first event already settles everything,
    so later ones find nothing to change.
  - **Every other key** goes straight to `0x1FF4`.
- **LED hook.** The LED frame task calls the effect→PWM converter `0x3B70` at `0x4ED6`. In static mode
  the effect writes `0xFF` to every LED on every frame, so the hook can zero selected effect values just
  before conversion: W/A/S/D, or, for `--cs2-led`, every LED not set in an 18-byte keep bitmap.
  LED number = `fw[0x6822 + col·8 + row]`. Multiplex phase *p* drives slots `p·21 … p·21+15`.
- **RAM.** The patch uses 3 bytes at `0x200006E0`, directly above the initial stack pointer: the flag,
  the A/D state and the W/S state. The chip has 2 KB of RAM, and the stock firmware uses less than
  `0x6E0` bytes. The zero-init entry of the C start-up table (`0x6BDC`) grows from `0x5C8` to `0x5CC`
  bytes, so start-up code clears these bytes on every reset.

### Safety checks in `k7_patch.py`

- It accepts only the exact stock updater: size, firmware vector table, firmware checksum and checksum
  resource are all checked.
- Every code or data patch first compares the original bytes and aborts on any mismatch.
- Only whitelisted byte ranges may change. The script aborts if anything else differs.
- The Fn tables keep identical *from* columns, and Mac behaviour must stay unchanged.
- The EXE is written first. If it is locked (for example because it is still open in the updater),
  nothing is written.

---

## Development tools (`tools/`, need `capstone`)

| Command | Purpose |
|---|---|
| `python tools/socd_asm.py --verify` | Re-assembles the snap tap code and checks that `k7_patch.py` contains exactly these bytes |
| `python tools/socd_asm.py [wasd_dark\|cs2]` | Prints the annotated disassembly and the hex blob |
| `python tools/socd_emu.py [wasd_dark\|cs2]` | Runs the patched hooks on the stock firmware in a small Thumb emulator (`0x1FF4` and `0x3B70` stubbed): counter-strafe cases, changes within one scan, 4000 random A/D/W/S sequences across two matrix rows with toggling, no redundant press or release, pass-through of other keys, preserved registers and stack, and LED frames |
| `python tools/disasm.py <updater.exe\|fw.bin> <start> <end>` | Disassembles any firmware range and annotates literal-pool loads |

To change the CS2 key set, edit `CS2_KEEP` in `tools/socd_asm.py`, run `python tools/socd_asm.py cs2`,
paste the printed `code` hex into `SNAP_TAP_CS2_CODE` in `k7_patch.py`, then run `--verify` and the
emulator. The emulator also checks that every LED number belongs to the intended key.

## Known limitations

- Used and tested on **Windows over the USB cable** only. **Bluetooth mode has not been tested.**
- The Windows layer only. Mac layers are untouched, and the Snap Tap toggle works only in Windows mode.
- The Snap Tap state is not saved and is ON again after every plug-in.
- With `--mic-mute`, Fn2 + 4 no longer opens Explorer. With `--snap-tap` it no longer types `4` either:
  it becomes the toggle.
- Firmware timings (scan rate, debounce) are estimates based on an assumed ~48 MHz core clock.

## License

The scripts and documentation in this repository are released under the [MIT License](LICENSE). The
license does **not** cover Keychron's firmware or updater, which are not included here and remain the
property of Keychron. "Keychron" is a trademark of its owner. This project is not affiliated with Keychron.
