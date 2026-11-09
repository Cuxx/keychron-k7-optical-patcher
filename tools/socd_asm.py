"""Thumb-1 mini assembler for the --snap-tap code patch (key hook + LED hook) of k7_patch.py.

    python tools/socd_asm.py [wasd_dark|cs2]   # print the listing (capstone) and the hex blob
    python tools/socd_asm.py --verify          # check that k7_patch.py contains exactly these bytes

LED styles while snap tap is switched OFF: 'wasd_dark' = W/A/S/D dark, 'cs2' = only CS2_KEEP lit.
"""
import struct

from capstone import CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS, Cs

BASE = 0x6D00            # free flash: RW-init copy ends at 0x6CFC, zeros up to the 0x77FC signature
HOOK_SITE = 0x2CA6       # "bl 0x1ff4" in the row-change loop 0x2C22 (only caller of 0x1FF4)
LED_SITE = 0x4ED6        # "bl 0x3b70" in the LED frame task (effect -> PWM conversion)
EVENT = 0x1FF4           # key event processor: S[6] = code, S[0xA] = pressed
LED_CONVERT = 0x3B70     # effect values 0x20000416[i] -> PWM buffer (v * brightness >> 3)
S = 0x20000024           # S[9] = Fn2 held, S[0x10] = Mac mode
DEBOUNCED = 0x200001A8   # debounced matrix, one word per row, bit = column
STATE = 0x200006E0       # [0] flags (bit0 = snap tap OFF), [1] A/D reported, [2] W/S reported
ZI_SIZE_ADDR, ZI_SIZE_OLD, ZI_SIZE_NEW = 0x6BDC, 0x5C8, 0x5CC   # start-up zeroes 0x200006E0..E3 too
KEY_4 = 0x21
EFFECT = 0x20000416      # LED effect values; LED of matrix idx m = fw[0x6822 + col*8 + row]
LED_W, LED_A, LED_S, LED_D = 44, 64, 65, 66
# CS2 light set: LCtrl, LShift, Space, 1-4, E, W/A/S/D (LED = fw[0x6822 + col*8 + row]; driven slots p*21 + 0..15)
CS2_KEEP = {"LCtrl": 105, "LShift": 84, "Space": 111, "1": 22, "2": 23, "3": 24, "4": 25, "E": 45,
            "W": 44, "A": 64, "S": 65, "D": 66}
N_LEDS = 138
PAIRS = [  # (code0, code1, matrix index0, matrix index1); matrix index = col*8 + row (keymap 0x5F58)
    (0x04, 0x07, 11, 27),   # A (row 3 col 1) / D (row 3 col 3)
    (0x1A, 0x16, 18, 19),   # W (row 2 col 2) / S (row 3 col 2)
]

COND = {"beq": 0, "bne": 1, "bcs": 2, "bcc": 3, "bls": 9}


def bl(at, target):
    off = target - (at + 4)
    assert -(1 << 22) <= off < (1 << 22) and off % 2 == 0
    s = 1 if off < 0 else 0
    return struct.pack("<HH", 0xF000 | s << 10 | (off >> 12) & 0x3FF, 0xD000 | 1 << 13 | 1 << 11 | (off >> 1) & 0x7FF)


def size(ins):
    return {"bl": 4, "word": 4, "bytes": len(ins[1]) if ins[0] == "bytes" else 0}.get(ins[0], 2)


def assemble(prog):
    labels, pc = {}, BASE
    for ins in prog:
        if isinstance(ins, str):
            labels[ins[:-1]] = pc
        elif ins[0] == "align4":
            pc = (pc + 3) & ~3
        else:
            pc += size(ins)
    out, pc = bytearray(), BASE
    for ins in prog:
        if isinstance(ins, str):
            continue
        op, *a = ins
        if op == "align4":
            while pc % 4:
                out += struct.pack("<H", 0xBF00); pc += 2   # nop
            continue
        if op == "word":
            out += struct.pack("<I", labels[a[0]] if isinstance(a[0], str) else a[0]); pc += 4; continue
        if op == "bytes":
            out += bytes(a[0]); pc += len(a[0]); continue
        if op == "bl":
            out += bl(pc, labels[a[0]] if isinstance(a[0], str) else a[0]); pc += 4; continue
        if op in COND:
            off = labels[a[0]] - (pc + 4)
            assert -256 <= off <= 254 and off % 2 == 0, (op, a, off)
            h = 0xD000 | COND[op] << 8 | ((off >> 1) & 0xFF)
        elif op == "b":
            off = labels[a[0]] - (pc + 4)
            assert -2048 <= off <= 2046
            h = 0xE000 | ((off >> 1) & 0x7FF)
        elif op == "ldr_lit":
            imm = labels[a[1]] - ((pc + 4) & ~3)
            assert 0 <= imm < 1024 and imm % 4 == 0
            h = 0x4800 | a[0] << 8 | imm >> 2
        elif op == "ldr":    h = 0x6800 | (a[2] >> 2) << 6 | a[1] << 3 | a[0]
        elif op == "ldr_rr": h = 0x5800 | a[2] << 6 | a[1] << 3 | a[0]       # ldr rt, [rn, rm]
        elif op == "ldrb":   h = 0x7800 | a[2] << 6 | a[1] << 3 | a[0]
        elif op == "ldrb_rr": h = 0x5C00 | a[2] << 6 | a[1] << 3 | a[0]      # ldrb rt, [rn, rm]
        elif op == "strb_rr": h = 0x5400 | a[2] << 6 | a[1] << 3 | a[0]      # strb rt, [rn, rm]
        elif op == "strb":   h = 0x7000 | a[2] << 6 | a[1] << 3 | a[0]
        elif op == "cmpi":   h = 0x2800 | a[0] << 8 | a[1]
        elif op == "cmpr":   h = 0x4280 | a[1] << 3 | a[0]                   # cmp rn, rm
        elif op == "movi":   h = 0x2000 | a[0] << 8 | a[1]
        elif op == "addi":   h = 0x3000 | a[0] << 8 | a[1]
        elif op == "addi3":  h = 0x1C00 | a[2] << 6 | a[1] << 3 | a[0]       # adds rd, rn, #imm3
        elif op == "lsrsi":  h = 0x0800 | a[2] << 6 | a[1] << 3 | a[0]
        elif op == "lslsi":  h = 0x0000 | a[2] << 6 | a[1] << 3 | a[0]
        elif op == "lsrsr":  h = 0x40C0 | a[1] << 3 | a[0]                   # lsrs rdn, rm
        elif op == "movr":   h = 0x0000 | a[1] << 3 | a[0]                   # movs rd, rm
        elif op == "mov_to_ip":   h = 0x4680 | a[0] << 3 | 4                # mov ip, rm
        elif op == "mov_from_ip": h = 0x4660 | a[0]                         # mov rd, ip
        elif op == "ands":   h = 0x4000 | a[1] << 3 | a[0]
        elif op == "eors":   h = 0x4040 | a[1] << 3 | a[0]
        elif op == "orrs":   h = 0x4300 | a[1] << 3 | a[0]
        elif op == "bics":   h = 0x4380 | a[1] << 3 | a[0]
        elif op == "bxlr":   h = 0x4770
        elif op in ("push", "pop"):
            h = a[0]
        else:
            raise ValueError(op)
        out += struct.pack("<H", h); pc += 2
    return bytes(out), labels


def prog(led_style="wasd_dark"):
    """led_style: 'wasd_dark' = W/A/S/D dark while OFF, 'cs2' = only CS2_KEEP lit while OFF."""
    p = [
        "hook:",                                       # replaces "bl 0x1ff4" at 0x2CA6
        ("push", 0xB5F0),                              # push {r4-r7, lr}
        ("ldr_lit", 4, "lit_s"),                       # r4 = S
        ("ldr_lit", 7, "lit_state"),                   # r7 = STATE
        ("ldrb", 0, 4, 6),                             # r0 = key code of this event
        ("cmpi", 0, KEY_4), ("bne", "pairs"),
        # Fn2 + 4 pressed in Windows mode: toggle snap tap and swallow the press (releases always pass through)
        ("ldrb", 1, 4, 0xA), ("cmpi", 1, 0), ("beq", "plain"),
        ("ldrb", 1, 4, 9), ("cmpi", 1, 0), ("beq", "plain"),
        ("ldrb", 1, 4, 0x10), ("cmpi", 1, 0), ("bne", "plain"),
        ("ldrb", 1, 7, 0), ("movi", 2, 1), ("eors", 1, 2), ("strb", 1, 7, 0),
        ("pop", 0xBDF0),
        "pairs:",
        ("ldr_lit", 5, "lit_pairs"),                   # r5 = pair table entry
        ("addi3", 6, 7, 1),                            # r6 = reported-state byte of that pair
        "next:",
        ("ldrb", 1, 5, 0), ("cmpi", 1, 0), ("beq", "plain"),   # end of table -> ordinary key
        ("movi", 2, 1), ("cmpr", 0, 1), ("beq", "found"),      # first key of the pair  -> bit 1
        ("ldrb", 1, 5, 1), ("movi", 2, 2), ("cmpr", 0, 1), ("beq", "found"),  # second key -> bit 2
        ("addi", 5, 4), ("addi", 6, 1), ("b", "next"),
        "plain:",
        ("bl", EVENT),
        ("pop", 0xBDF0),
        "found:",                                      # r2 = bit of the key of this event
        ("ldrb", 0, 5, 2), ("bl", "held"), ("mov_to_ip", 0),
        ("ldrb", 0, 5, 3), ("bl", "held"), ("lslsi", 0, 0, 1), ("mov_from_ip", 1), ("orrs", 0, 1),  # r0 = held
        ("ldrb", 1, 7, 0), ("cmpi", 1, 0), ("bne", "store"),   # snap tap OFF: report exactly what is held
        ("cmpi", 0, 3), ("bne", "store"),
        ("movr", 0, 2),                                # both held -> only the key just pressed (last input wins)
        "store:",
        ("ldrb", 3, 6, 0), ("strb", 0, 6, 0),          # r3 = reported so far, store new
        ("movr", 6, 0), ("movr", 7, 3),
        ("bics", 7, 6),                                # r7 = release = old & ~new
        ("bics", 6, 3),                                # r6 = press   = new & ~old
        ("movr", 0, 7), ("movi", 1, 0), ("bl", "emit"),
        ("movr", 0, 6), ("movi", 1, 1), ("bl", "emit"),
        ("pop", 0xBDF0),
        "held:",                                       # r0 = matrix index -> r0 = 1 if held; clobbers r1, r3
        ("lslsi", 3, 0, 29), ("lsrsi", 3, 3, 27),     # r3 = row * 4
        ("ldr_lit", 1, "lit_deb"), ("ldr_rr", 3, 1, 3),  # r3 = debounced row word
        ("lsrsi", 0, 0, 3), ("lsrsr", 3, 0),           # >> column
        ("movi", 0, 1), ("ands", 0, 3),
        ("bxlr",),
        "emit:",                                       # r0 = mask (bit1 = code0, bit2 = code1), r1 = pressed; r4 = S, r5 = entry
        ("push", 0xB5E0),                              # push {r5-r7, lr}
        ("movr", 6, 0), ("movr", 7, 1),
        "eloop:",
        ("lsrsi", 6, 6, 1), ("bcc", "eskip"),
        ("ldrb", 0, 5, 0), ("strb", 0, 4, 6), ("strb", 7, 4, 0xA), ("bl", EVENT),
        "eskip:",
        ("addi", 5, 1), ("cmpi", 6, 0), ("bne", "eloop"),
        ("pop", 0xBDE0),                               # pop {r5-r7, pc}
    ]
    if led_style == "wasd_dark":
        p += [
            "led_hook:",                                   # replaces "bl 0x3b70" at 0x4ED6
            ("push", 0xB500),                              # push {lr}
            ("ldr_lit", 0, "lit_state"), ("ldrb", 0, 0, 0), ("cmpi", 0, 0), ("beq", "lit"),
            ("ldr_lit", 1, "lit_led"), ("movi", 0, 0),     # snap tap OFF: W/A/S/D dark (effect value 0)
            ("strb", 0, 1, 0), ("strb", 0, 1, LED_A - LED_W), ("strb", 0, 1, LED_S - LED_W),
            ("strb", 0, 1, LED_D - LED_W),
            "lit:",
            ("bl", LED_CONVERT),
            ("pop", 0xBD00),                               # pop {pc}
        ]
        extra = [("align4",)] + LITS + ["lit_led:", ("word", EFFECT + LED_W)] + PAIRS_TAB  # = shipped layout
    else:
        keep = [0] * ((N_LEDS + 7) // 8)
        for led in CS2_KEEP.values():
            keep[led >> 3] |= 1 << (led & 7)
        p += [
            "led_hook:",                                   # replaces "bl 0x3b70" at 0x4ED6
            ("push", 0xB510),                              # push {r4, lr}
            ("ldr_lit", 0, "lit_state"), ("ldrb", 0, 0, 0), ("cmpi", 0, 0), ("beq", "lit"),
            ("ldr_lit", 1, "lit_effect"), ("ldr_lit", 2, "lit_keep"), ("movi", 0, 0),
            "lloop:",                                      # snap tap OFF: every LED not in the keep bitmap -> 0
            ("lsrsi", 3, 0, 3), ("ldrb_rr", 3, 2, 3),      # r3 = keep[i >> 3]
            ("movi", 4, 7), ("ands", 4, 0), ("lsrsr", 3, 4),  # >> (i & 7)
            ("lsrsi", 3, 3, 1), ("bcs", "lkeep"),          # carry = keep bit
            ("movi", 3, 0), ("strb_rr", 3, 1, 0),          # effect[i] = 0
            "lkeep:",
            ("addi", 0, 1), ("cmpi", 0, N_LEDS), ("bcc", "lloop"),
            "lit:",
            ("bl", LED_CONVERT),
            ("pop", 0xBD10),                               # pop {r4, pc}
        ]
        extra = ([("align4",)] + LITS + ["lit_effect:", ("word", EFFECT), "lit_keep:", ("word", "keep_tab")]
                 + PAIRS_TAB + ["keep_tab:", ("bytes", keep)])
    return p + extra


LITS = [
    "lit_s:", ("word", S),
    "lit_state:", ("word", STATE),
    "lit_pairs:", ("word", "pairs_tab"),
    "lit_deb:", ("word", DEBOUNCED),
]
PAIRS_TAB = ["pairs_tab:", ("bytes", [b for p in PAIRS for b in p] + [0, 0, 0, 0])]


def build(led_style="wasd_dark"):
    code, labels = assemble(prog(led_style))
    return code, labels, bl(HOOK_SITE, labels["hook"]), bl(LED_SITE, labels["led_hook"])


code, labels, site, led_site = build("wasd_dark")

def verify():
    """Compare the assembled blobs with the ones embedded in k7_patch.py (one directory up)."""
    import importlib.util
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "k7_patch.py")
    spec = importlib.util.spec_from_file_location("k7_patch", path)
    k7 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(k7)
    ok = True
    for style, patch in (("wasd_dark", k7.SNAP_TAP_PATCH), ("cs2", k7.SNAP_TAP_CS2_PATCH)):
        c, _, s_site, s_led = build(style)
        expect = {HOOK_SITE: s_site.hex(), LED_SITE: s_led.hex(), ZI_SIZE_ADDR: struct.pack("<I", ZI_SIZE_NEW).hex(),
                  BASE: c.hex()}
        got = {addr: new for addr, _old, new in patch}
        same = got == expect
        ok &= same
        print(f"{style:9}: {'OK' if same else 'MISMATCH'} ({len(c)} bytes at 0x{BASE:04X})")
    return ok


if __name__ == "__main__":
    import sys
    if "--verify" in sys.argv:
        sys.exit(0 if verify() else 1)
    style = sys.argv[1] if len(sys.argv) > 1 else "wasd_dark"
    code, labels, site, led_site = build(style)
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
    for at, b in ((HOOK_SITE, site), (LED_SITE, led_site)):
        for i in md.disasm(b, at):
            print(f"{i.address:05x}: {i.bytes.hex():<10} {i.mnemonic:<8} {i.op_str}")
    print()
    for i in md.disasm(code[: labels["lit_s"] - BASE], BASE):
        print(f"{i.address:05x}: {i.bytes.hex():<10} {i.mnemonic:<8} {i.op_str}")
    for name in ("lit_s", "lit_state", "lit_pairs", "lit_deb", "lit_led", "lit_effect", "lit_keep"):
        if name in labels:
            a = labels[name]
            print(f"{a:05x}: .word 0x{struct.unpack_from('<I', code, a - BASE)[0]:08x}   ; {name}")
    t = labels["pairs_tab"]
    print(f"{t:05x}: .byte {code[t - BASE:t - BASE + 4 * len(PAIRS) + 4].hex(' ')}   ; pairs")
    if "keep_tab" in labels:
        k = labels["keep_tab"]
        print(f"{k:05x}: .byte {code[k - BASE:].hex(' ')}   ; keep bitmap")
    print(f"\nsize {len(code)} bytes, 0x{BASE:04X}-0x{BASE + len(code):04X}")
    print("site", site.hex(), "led_site", led_site.hex())
    print("code", code.hex())
