"""
Keychron K7 (A2 = optical, white backlight, 68 keys, US) - Fn layer patcher for the official updater V1.05.

How the firmware stores the Fn layers (found by disassembling the SN32F26x image):
  * The raw firmware (64 KB, unencrypted) lives in the updater EXE as RCDATA resource 4000.
  * Base layer: 15 columns x 8 row slots at 0x5F58 (only matrix scan uses it).
  * Fn layers are NOT matrices but 4 tables of 34 pairs  [base keycode, fn keycode]:
        0x6500  Windows Fn1      0x6544  Windows Fn2
        0x6588  Mac Fn1          0x65CC  Mac Fn2
    Handler (0x1E62): idx = search key in the FROM column of 0x6500 (count = halfword at 0x00D6 = 34),
    then take TO from the table selected by (Fn1 held? / Fn2 held?) x (Mac mode?).
    => The FROM column is shared by all four tables and must stay identical.
  * Internal codes: 0xAF = Fn1 key, 0xE6 = Fn2 key, 0xA0-0xA7 = modifiers, >= 0xB0 = functions
    (0xB0.. are translated to HID consumer codes via the table at 0x6620).
  * Firmware checksum (RCDATA 4014, UTF-16 hex string) = 16-bit sum of all little-endian words.
  * The bootloader sits at 0x7800 on the chip and is NOT part of the image (the updater never touches it),
    so the original updater can always restore the stock firmware.

Usage:
    python k7_patch.py --dry-run   # show current + new layers, write nothing
    python k7_patch.py             # write ..._FnSwap.exe next to the original
    python k7_patch.py --mic-mute  # + Fn2+Del = Win+Alt+K, Fn2+4 = 4 -> ..._FnSwap_MicMuteDel.exe
    python k7_patch.py --mic-mute --static-led   # + static LED only -> ..._FnSwap_MicMuteDel_StaticLED.exe
    python k7_patch.py --mic-mute --static-led --bright   # + 5-phase LED multiplex -> ..._Bright.exe
    python k7_patch.py --mic-mute --static-led --bright --fast-scan   # + faster key scan -> ..._FastScan.exe
    python k7_patch.py --mic-mute --static-led --bright [--fast-scan] --snap-tap   # + snap tap A/D+W/S -> ..._SnapTapADWS.exe
    python k7_patch.py ... --snap-tap --cs2-led   # off = only the CS2 keys lit -> ..._SnapTapADWS_CS2LED.exe
"""
import argparse
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORIGINAL_EXE = HERE / "K7-A2-68K-WHILE-US-OA-V1.05_20210611.exe"
OUT_STEM = "K7-A2-68K-WHILE-US-OA-V1.05_20210611"
FW_DIR = HERE / "firmware"

# Optional CODE patch (--mic-mute): internal code 0x87 (Fn2+4 after the swap) sends Win+Alt+K
# (Windows 11 mic mute) instead of Win+E. Only the parts of the handler that belong exclusively to 0x87
# are rewritten; the setup at 0x258A (r0=8, r1=0xF7) is shared with Task View (0x86) and stays untouched.
#   0263e movs r0,#0x0c | ldrb r1,[r6] | ldrb r2,[r7,#0xa] | cmp r2,#1 | beq 0x2654 | bics r1,r0 | b 0x2656
#   02654 orrs r1,r0 | strb r1,[r6] | movs r0,#0x0e ('K') | strb r0,[r7,#6]  -> falls into 0x265C (shared tail)
MIC_MUTE_PATCH = [  # (address, original bytes, new bytes)
    (0x263E, "ba7a012a07d032780a403270b871", "0c203178ba7a012a05d0814304e0"),
    (0x2654, "317801433170f6e7", "014331700e20b871"),
]

# Optional (--static-led): only LED mode 0 = static (effect 0x46F6: short wipe-in, then all LEDs 0xFF).
#   * Mode switch table at 0x4CAC: 18 setup functions; the cycle at 0x21E4 wraps "mode+1 >= 17 -> 0".
#     cmp r0,#0x11 -> cmp r0,#1 : Fn2+Del can no longer leave mode 0.
#   * Byte 1 of the settings pages = stored LED mode (boot load at 0x4DB0 only checks the 0xA5 magic, no checksum).
#     0x6000 = factory defaults (copied to 0x6200 by factory reset, 0x2F44), 0x6200 = current settings.
#     Stock default 0x02 (playful) -> 0x00 (static), so flashing / factory reset start static.
STATIC_LED_PATCH = [
    (0x21EE, "1128", "0128"),
    (0x6001, "02", "00"),
    (0x6201, "02", "00"),
]

# Optional (--bright): LED multiplex with 5 instead of 6 row phases (+20 % on-time per LED).
#   Phase driver 0x5528 (timer IRQ): all rows/columns off, every 10th IRQ one key-matrix row scan (own counter,
#   independent of the LED phase), load PWM values of phase p (switch 0x5440), select row p (0x53BC), p+1, wrap at 6.
#   Phase 0 has no LED (key->LED list 0x667D uses only phases 1-5), so wrap to 1 instead of 0:
#   05572 cmp r0,#6 | blo 0x5578 | movs r0,#1 | strb r0,[r4] | nop      (uxtb dropped: p+1 <= 6 never overflows)
#   Runs in the timer interrupt from boot on -> a mistake here would hang the keyboard at start-up.
BRIGHT_PATCH = [
    (0x5572, "c0b22070062800d32570", "062800d30120207000bf"),
]

# Optional (--fast-scan): read one key-matrix row every 3rd instead of every 10th LED-timer IRQ (0x5542).
#   Assuming 48 MHz: IRQ ~7.8 kHz -> full 6-row scan every ~2.3 ms instead of ~7.7 ms.
#   Debounce (0x2CC4, normal main loop path) = stay on the changed row until 5 more identical scans,
#   so debounce time shrinks too: ~6.4 ms -> ~1.9 ms (fine for optical switches, which do not bounce).
#   If the main loop has not consumed the previous row yet, the IRQ simply waits (flag at 0x20000024).
FAST_SCAN_PATCH = [
    (0x5542, "0a28", "0328"),
]

# Optional (--snap-tap): SOCD "last input wins" for A/D and W/S (like Razer Snap Tap / Wooting Snappy Tappy),
# switchable with Fn2+4 (Windows mode), W/A/S/D LEDs dark while it is switched OFF. Needs --mic-mute (Fn2+4 = 4).
#   Rapid Trigger itself is impossible: the optical switches are read as plain GPIO bits (0x2DBC), no travel.
#   Row-change loop 0x2C22 stores the new debounced row word (0x200001A8 + row*4) and then calls the key event
#   processor 0x1FF4 once per changed key (S[6] = code, S[0xA] = pressed). That call (0x2CA6) goes to a hook in
#   free flash at 0x6D00 (RW-init data ends at 0x6CFC, zeros up to the 0x77FC signature):
#     Fn2+4 press (S[9] = Fn2, S[0x10] = 0 Windows) -> toggle flag byte, press swallowed (release passes, harmless)
#     key of a pair (table: code0, code1, matrix idx0, idx1 = col*8+row) -> held = both keys' debounced bits;
#       ON and both held: only the key just pressed; OFF: exactly what is held. Diff against the reported
#       state of the pair (1 byte each): releases first, then presses, each as a normal 0x1FF4 event.
#       Same-scan changes resolve on the first event, later ones find no difference.
#     anything else -> bl 0x1FF4 unchanged
#   LED frame task 0x4ED6 "bl 0x3b70" (effect -> PWM) goes through a hook that zeroes the effect values of
#   LEDs 44/64/65/66 (W/A/S/D via tables 0x6732/0x6822) while OFF; the static effect rewrites 0xFF every frame.
#   RAM 0x200006E0 (flags), E1 (A/D), E2 (W/S): first bytes above the initial SP (2 KB RAM, app uses < 0x6E0);
#   the zero-init entry of the scatter-load table (size at 0x6BDC) grows 0x5C8 -> 0x5CC so start-up clears them
#   on every reset -> snap tap is ON after plugging in.
#   Verified in an emulator (capstone-decoded, 0x1FF4/0x3B70 stubbed): counter-strafe cases, same-scan changes,
#   4000 random sequences over rows 2+3 with toggles, no redundant press/release, other keys pass through once,
#   r4-r7/sp preserved, LED frames dark exactly on W/A/S/D while OFF. (Source: socd2_asm.py in the session scratchpad.)
SNAP_TAP_CODE = (
    "f0b5344c344fa07921280dd1a17a002919d0617a002916d0217c002913d13978012251403970f0bd2c4d7e1c2978002909d0"
    "0122884209d069780222884205d004350136f2e7fbf755f9f0bda87800f01cf88446e87800f018f840006146084339780029"
    "02d1032800d110003378307006001f00b7439e433800002100f00ef83000012100f00af8f0bd4307db0e1449cb58c008c340"
    "012018407047e0b506000f00760804d32878a071a772fbf722f90135002ef5d1e0bd00b507480078002805d0084900200870"
    "087548758875fcf7cffe00bd24000020e0060020e86d0000a80100204204002004070b1b1a16121300000000"
)
SNAP_TAP_PATCH = [
    (0x2CA6, "fff7a5f9", "04f02bf8"),                    # bl 0x1ff4 -> bl 0x6d00 (key hook)
    (0x4ED6, "fef74bfe", "01f06fff"),                    # bl 0x3b70 -> bl 0x6db8 (LED hook)
    (0x6BDC, "c8050000", "cc050000"),                    # zero-init size 0x5C8 -> 0x5CC (clears 0x200006E0..E3)
    (0x6D00, "00" * (len(SNAP_TAP_CODE) // 2), SNAP_TAP_CODE),
]

# Optional with --snap-tap (--cs2-led): while snap tap is OFF ("CS2 mode") only the CS2 keys stay lit instead of
# W/A/S/D going dark. Same key hook; the LED hook (0x6DB8) zeroes every effect value whose bit is
# not set in an 18-byte keep bitmap (LEDs: LCtrl 105, LShift 84, Space 111, 1 22, 2 23, 3 24, 4 25, E 45, W 44, A 64, S 65, D 66).
#   LED numbering: fw[0x6822 + col*8 + row]; phase p drives slots p*21 + 0..15 (load switch 0x5440).
#   led_hook: push {r4,lr} | flags == 0 -> lit | r1 = effect, r2 = keep, i = 0..137:
#             lsrs r3,i,#3 | ldrb r3,[keep,r3] | r4 = i & 7 | lsrs r3,r4 | lsrs r3,#1 | bcs next | effect[i] = 0
#   lit: bl 0x3b70 | pop {r4,pc}     Emulator: same suite as above, LED frames dark = all but the keep set.
SNAP_TAP_CS2_CODE = (
    "f0b5394c394fa07921280dd1a17a002919d0617a002916d0217c002913d13978012251403970f0bd314d7e1c2978002909d0"
    "0122884209d069780222884205d004350136f2e7fbf755f9f0bda87800f01cf88446e87800f018f840006146084339780029"
    "02d1032800d110003378307006001f00b7439e433800002100f00ef83000012100f00af8f0bd4307db0e1949cb58c008c340"
    "012018407047e0b506000f00760804d32878a071a772fbf722f90135002ef5d1e0bd10b50c48007800280ed00d490d4a0020"
    "c308d35c07240440e3405b0801d200230b5401308a28f3d3fcf7c6fe10bd00bf24000020e0060020006e0000a80100201604"
    "00200c6e000004070b1b1a161213000000000000c0030030000007001000008200000000"
)
SNAP_TAP_CS2_PATCH = [
    (0x2CA6, "fff7a5f9", "04f02bf8"),                    # bl 0x1ff4 -> bl 0x6d00 (key hook)
    (0x4ED6, "fef74bfe", "01f06fff"),                    # bl 0x3b70 -> bl 0x6db8 (LED hook, CS2 set)
    (0x6BDC, "c8050000", "cc050000"),                    # zero-init size 0x5C8 -> 0x5CC (clears 0x200006E0..E3)
    (0x6D00, "00" * (len(SNAP_TAP_CS2_CODE) // 2), SNAP_TAP_CS2_CODE),
]

EXE_SIZE = 1_863_680
FW_OFFSET = 0x1AC378          # file offset of RCDATA 4000 (raw firmware)
FW_SIZE = 0x10000
CHECKSUM_RES_OFFSET = 0x1C69AC  # file offset of RCDATA 4014 ("135D" as UTF-16LE, 8 bytes)
EXPECTED_STOCK_CHECKSUM = 0x135D

TABLE_LEN_ADDR = 0x00D6
TABLES = {"win_fn1": 0x6500, "win_fn2": 0x6544, "mac_fn1": 0x6588, "mac_fn2": 0x65CC}

# ---------------------------------------------------------------------------------------------
# Key names (HID usage page 0x07 + the internal codes identified in the firmware; '?' = guessed)
HID = {0x04 + i: chr(ord("A") + i) for i in range(26)}
HID.update({0x1E + i: str((i + 1) % 10) for i in range(10)})
HID.update({0x3A + i: f"F{i + 1}" for i in range(12)})
HID.update({0x28: "Enter", 0x29: "Esc", 0x2A: "Bksp", 0x2B: "Tab", 0x2C: "Space", 0x2D: "-", 0x2E: "=",
            0x2F: "[", 0x30: "]", 0x31: "\\", 0x33: ";", 0x34: "'", 0x35: "`", 0x36: ",", 0x37: ".", 0x38: "/",
            0x39: "Caps", 0x46: "PrtSc", 0x49: "Ins", 0x4A: "Home", 0x4B: "PgUp", 0x4C: "Del", 0x4D: "End",
            0x4E: "PgDn", 0x4F: "Right", 0x50: "Left", 0x51: "Down", 0x52: "Up"})
INTERNAL = {0xA0: "LCtrl", 0xA1: "LShift", 0xA2: "LAlt", 0xA3: "LWin", 0xA4: "RCtrl?", 0xA5: "RShift",
            0xA6: "RAlt", 0xAF: "Fn1", 0xE6: "Fn2",
            0x86: "TaskView", 0x87: "Explorer", 0xCF: "Bright-", 0xCE: "Bright+", 0xD5: "LED-", 0xD4: "LED+",
            0xB1: "Prev", 0xB3: "Play", 0xB0: "Next", 0xB5: "Mute", 0xBA: "Vol-", 0xB9: "Vol+",
            0xD1: "WinLock?", 0xD3: "LED-Mode", 0xE4: "LED-OnOff",0x97: "BT1?", 0x98: "BT2?", 0x99: "BT3?"}
NAMES = {**HID, **INTERNAL}
KEY = {v.rstrip("?"): k for k, v in NAMES.items()}  # e.g. KEY["1"] == 0x1E, KEY["F5"] == 0x3E, KEY["Vol+"] == 0xB9


def key_name(code):
    return NAMES.get(code, f"0x{code:02X}")


# ---------------------------------------------------------------------------------------------
def fw_checksum(fw):
    return sum(struct.unpack(f"<{len(fw) // 2}H", fw)) & 0xFFFF


def pe_checksum(exe):
    """Same algorithm as Windows' CheckSumMappedFile (the 4-byte checksum field is skipped)."""
    pe = struct.unpack_from("<I", exe, 0x3C)[0]
    field = pe + 24 + 64
    words = struct.unpack(f"<{len(exe) // 2}H", exe[:len(exe) & ~1])
    s = sum(words) - struct.unpack_from("<H", exe, field)[0] - struct.unpack_from("<H", exe, field + 2)[0]
    if len(exe) & 1:
        s += exe[-1]
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return field, (s + len(exe)) & 0xFFFFFFFF


def read_tables(fw):
    n = struct.unpack_from("<H", fw, TABLE_LEN_ADDR)[0]
    return {name: [[fw[a + 2 * i], fw[a + 2 * i + 1]] for i in range(n)] for name, a in TABLES.items()}


def write_tables(fw, tables):
    for name, a in TABLES.items():
        for i, (frm, to) in enumerate(tables[name]):
            fw[a + 2 * i], fw[a + 2 * i + 1] = frm, to


def print_windows_layers(tables, title):
    print(f"\n=== {title} ===")
    print(f"{'#':>3}  {'Key':<8} {'Fn1 ->':<10} {'Fn2 ->':<10}")
    for i, ((frm, t1), (_, t2)) in enumerate(zip(tables["win_fn1"], tables["win_fn2"])):
        print(f"{i:>3}  {key_name(frm):<8} {key_name(t1):<10} {key_name(t2):<10}")


# ---------------------------------------------------------------------------------------------
def customize_windows_layers(fn1, fn2):
    """Edit the Windows Fn layers in place.

    fn1 / fn2: lists of 34 entries [from_key, to_key]; index i means the same physical key in both lists.
    Only change to_key (entry[1]). Use KEY["..."] for readable codes, e.g. KEY["1"], KEY["F1"], KEY["Vol+"].
    """
    # Number row: Fn1 gets F1-F12, Fn2 gets brightness/Task View/Explorer/LED/media/volume.
    # Esc stays as it is (Fn1+Esc = `, Fn2+Esc = internal 0xE7).
    # Del: Fn1 = LED on/off (0xE4), Fn2 = cycle LED modes (0xD3). Side effect: the hidden Fn1+L+Del hold
    # combo (0x21B0) is no longer reachable, because 0xD3 only arms it while Fn1 is held.
    number_row = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "="]
    from_keys = [entry[0] for entry in fn1]
    for name in number_row + ["Del"]:
        i = from_keys.index(KEY[name])  # look up by keycode, not by fixed index
        fn1[i][1], fn2[i][1] = fn2[i][1], fn1[i][1]


def add_fn1_home_end(tables):
    """Fn1 + Home = End (Windows). Home is not one of the 34 Fn keys, so slot 'RCtrl' (0xA4) is re-purposed.

    0xA4 only reaches the Fn lookup while the hidden RAlt<->RCtrl swap mode is active (flag S[0x1D],
    swap at 0x2026, toggled at 0x418). In that mode Fn1 + right-Alt key would have given RAlt back -
    that is the only behaviour lost. The FROM key has to change in all four tables (shared lookup column).
    """
    i = [e[0] for e in tables["win_fn1"]].index(0xA4)
    for name in TABLES:
        tables[name][i] = [KEY["Home"], KEY["Home"]]  # identity: Home stays Home in every other layer
    tables["win_fn1"][i][1] = KEY["End"]


# ---------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="only show the changes, write nothing")
    ap.add_argument("--mic-mute", action="store_true",
                    help="Fn2+Del = Win+Alt+K (Windows 11 mic mute), Fn2+4 = plain 4 (code patch)")
    ap.add_argument("--static-led", action="store_true", help="only the static LED mode, also as default")
    ap.add_argument("--bright", action="store_true", help="LED multiplex with 5 instead of 6 phases (+20%% light)")
    ap.add_argument("--fast-scan", action="store_true", help="scan the key matrix ~3x as often")
    ap.add_argument("--snap-tap", action="store_true",
                    help="A/D + W/S: last pressed wins (SOCD), Fn2+4 on/off, WASD dark = off (code patch)")
    ap.add_argument("--cs2-led", action="store_true",
                    help="with --snap-tap: while off only Ctrl/Shift/Space/WASD/1-4/E are lit (instead of WASD dark)")
    args = ap.parse_args()
    patches, variant, notes = [], "FnSwap", []
    if args.mic_mute:
        patches += MIC_MUTE_PATCH
        variant += "_MicMuteDel"
        notes.append("code patch: code 0x87 sends Win+Alt+K, now on Fn2+Del; Fn2+4 = plain 4")
    if args.static_led:
        patches += STATIC_LED_PATCH
        variant += "_StaticLED"
        notes.append("LED: static mode only (default after flashing/factory reset), Fn2+Del = plain Del")
    if args.bright:
        patches += BRIGHT_PATCH
        variant += "_Bright"
        notes.append("LED multiplexing with 5 instead of 6 phases (+20 % light)")
    if args.fast_scan:
        patches += FAST_SCAN_PATCH
        variant += "_FastScan"
        notes.append("key matrix scan every 3rd instead of every 10th timer interrupt (~2.3 ms instead of ~7.7 ms)")
    if args.snap_tap:
        if not args.mic_mute:
            sys.exit("--snap-tap needs --mic-mute (Fn2+4 must be a plain 4, it becomes the on/off switch).")
        variant += "_SnapTapADWS"  # (older _SnapTapAD builds: A/D only, no toggle - code no longer in this script)
        if args.cs2_led:
            patches += SNAP_TAP_CS2_PATCH
            variant += "_CS2LED"
            notes.append("code patch: A/D and W/S -> last pressed wins; Fn2+4 on/off, "
                         "off = only Ctrl/Shift/Space/WASD/1-4/E lit")
        else:
            patches += SNAP_TAP_PATCH
            notes.append("code patch: A/D and W/S -> last pressed wins; Fn2+4 on/off, WASD dark = off")
    elif args.cs2_led:
        sys.exit("--cs2-led only works together with --snap-tap.")
    patched_exe = HERE / f"{OUT_STEM}_{variant}.exe"

    exe = bytearray(ORIGINAL_EXE.read_bytes())
    fw = bytearray(exe[FW_OFFSET:FW_OFFSET + FW_SIZE])

    # Refuse to work on anything but the exact stock updater we analysed.
    stock_sum = exe[CHECKSUM_RES_OFFSET:CHECKSUM_RES_OFFSET + 8].decode("utf-16le")
    if (len(exe) != EXE_SIZE or fw[:8] != bytes.fromhex("e0060020d9000000")
            or fw_checksum(fw) != EXPECTED_STOCK_CHECKSUM or stock_sum != "135D"):
        sys.exit("Unexpected EXE/firmware - this script only works with the stock updater "
                 "K7-A2-68K-WHILE-US-OA-V1.05_20210611.exe.")

    original = read_tables(fw)
    tables = read_tables(fw)
    print_windows_layers(original, "Stock (Windows mode)")

    customize_windows_layers(tables["win_fn1"], tables["win_fn2"])
    add_fn1_home_end(tables)
    if args.static_led:  # no mode cycling any more -> Fn2+Del is a plain Del again (Fn1+Del stays on/off)
        i = [e[0] for e in tables["win_fn2"]].index(KEY["Del"])
        tables["win_fn2"][i][1] = KEY["Del"]
    if args.mic_mute:  # mic mute (0x87, patched to Win+Alt+K) moves from Fn2+4 to Fn2+Del; Fn2+4 = plain 4
        fn2 = tables["win_fn2"]
        keys = [e[0] for e in fn2]
        fn2[keys.index(KEY["4"])][1] = KEY["4"]
        fn2[keys.index(KEY["Del"])][1] = 0x87
        NAMES[0x87] = "MicMute"  # display only

    # Safety checks: the firmware only searches the FROM column of 0x6500, so all four must stay identical;
    # Mac behaviour stays as before (re-purposed slots there must be identity mappings).
    shared_from = [e[0] for e in tables["win_fn1"]]
    for name in TABLES:
        if [e[0] for e in tables[name]] != shared_from:
            sys.exit(f"FROM column of {name} differs from win_fn1 - that would break the lookup.")
        if any(not 0 <= v <= 0xFF for e in tables[name] for v in e):
            sys.exit(f"Invalid key code in {name}.")
    for name in ("mac_fn1", "mac_fn2"):
        if any(new != old and new[1] != new[0] for new, old in zip(tables[name], original[name])):
            sys.exit(f"{name}: Mac behaviour would change - aborting.")

    write_tables(fw, tables)
    allowed = [(0x6500, 0x6610)]
    for addr, old, new in patches:
        old_b, new_b = bytes.fromhex(old), bytes.fromhex(new)
        if len(old_b) != len(new_b) or fw[addr:addr + len(old_b)] != old_b:
            sys.exit(f"Bytes at 0x{addr:04X} are not as expected - aborting.")
        fw[addr:addr + len(new_b)] = new_b
        allowed.append((addr, addr + len(new_b)))
    changed = [a for a in range(FW_SIZE) if fw[a] != exe[FW_OFFSET + a]]
    if not changed:
        print("\nNo changes - nothing to do.")
        return
    print_windows_layers(tables, "Patched (Windows mode)")
    for note in notes:
        print(f"     ({note})")
    print(f"\nChanged firmware bytes: {len(changed)} ({', '.join(hex(a) for a in changed)})")
    if any(not any(lo <= a < hi for lo, hi in allowed) for a in changed):
        sys.exit("Change outside the allowed byte ranges - aborting.")

    new_sum = fw_checksum(fw)
    print(f"Firmware checksum: {EXPECTED_STOCK_CHECKSUM:04X} -> {new_sum:04X}")
    if args.dry_run:
        print("Dry run: nothing written.")
        return

    exe[FW_OFFSET:FW_OFFSET + FW_SIZE] = fw
    exe[CHECKSUM_RES_OFFSET:CHECKSUM_RES_OFFSET + 8] = f"{new_sum:04X}".encode("utf-16le")
    field, stock_pe = pe_checksum(ORIGINAL_EXE.read_bytes())
    if stock_pe == struct.unpack_from("<I", exe, field)[0]:  # only maintain it if the original was valid
        struct.pack_into("<I", exe, field, pe_checksum(exe)[1])

    try:
        patched_exe.write_bytes(exe)  # first the EXE: if it is locked (e.g. open in the updater), write nothing else
    except PermissionError:
        sys.exit(f"{patched_exe.name} is locked (still open in the updater?) - nothing written.")
    FW_DIR.mkdir(exist_ok=True)
    (FW_DIR / "K7_V1.05_original.bin").write_bytes(ORIGINAL_EXE.read_bytes()[FW_OFFSET:FW_OFFSET + FW_SIZE])
    (FW_DIR / f"K7_V1.05_{variant}.bin").write_bytes(fw)
    print(f"Written: {patched_exe.name}  (firmware dumps in {FW_DIR.name}/)")


if __name__ == "__main__":
    main()
