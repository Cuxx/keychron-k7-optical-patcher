"""Emulate the --snap-tap hooks of k7_patch.py on the real stock firmware (instructions decoded by capstone,
the firmware's key event processor 0x1FF4 and LED converter 0x3B70 stubbed) and check the behaviour:
A/D + W/S snap tap, Fn2+4 toggle, LED frames, pass-through of all other keys, register/stack hygiene.

    python tools/socd_emu.py [wasd_dark|cs2] [--exe path/to/K7-A2-68K-WHILE-US-OA-V1.05_20210611.exe]
"""
import argparse
import importlib.util
import os
import random
import struct

from capstone import CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS, Cs
from capstone.arm import ARM_OP_REG

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("socd_asm", os.path.join(HERE, "socd_asm.py"))
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)

ap = argparse.ArgumentParser()
ap.add_argument("style", nargs="?", default="wasd_dark", choices=("wasd_dark", "cs2"))
ap.add_argument("--exe", default=os.path.join(HERE, os.pardir, "K7-A2-68K-WHILE-US-OA-V1.05_20210611.exe"))
args = ap.parse_args()
STYLE = args.style
A.code, A.labels, A.site, A.led_site = A.build(STYLE)

FW_OFFSET, FW_SIZE = 0x1AC378, 0x10000   # RCDATA 4000 inside the stock updater
fw = bytearray(open(args.exe, "rb").read()[FW_OFFSET:FW_OFFSET + FW_SIZE])
assert sum(struct.unpack(f"<{FW_SIZE // 2}H", fw)) & 0xFFFF == 0x135D, "not the stock V1.05 updater"
assert fw[A.BASE:A.BASE + len(A.code)] == bytes(len(A.code)), "target area not empty"
assert fw[A.HOOK_SITE:A.HOOK_SITE + 4] == bytes.fromhex("fff7a5f9")
assert fw[A.LED_SITE:A.LED_SITE + 4] == bytes.fromhex("fef74bfe")
dst, size = struct.unpack_from("<II", fw, A.ZI_SIZE_ADDR - 4)
assert (dst, size) == (0x20000118, A.ZI_SIZE_OLD) and dst + A.ZI_SIZE_NEW >= A.STATE + 3


def led_of_code(code):
    """LED slot of the key with this internal code (keymap 0x5F58 -> tables 0x6732 / 0x6822)."""
    (m,) = [m for m in range(120) if fw[0x5F58 + m] == code]
    return fw[0x6822 + fw[0x6732 + 2 * m] * 8 + fw[0x6732 + 2 * m + 1]]


# the LED numbers used by the patch really belong to these keys
for code, led in ((0x1A, A.LED_W), (0x04, A.LED_A), (0x16, A.LED_S), (0x07, A.LED_D)):
    assert led_of_code(code) == led
CS2_CODES = {"LCtrl": 0xA0, "LShift": 0xA1, "Space": 0x2C, "1": 0x1E, "2": 0x1F, "3": 0x20, "4": 0x21, "E": 0x08,
             "W": 0x1A, "A": 0x04, "S": 0x16, "D": 0x07}
assert {k: led_of_code(c) for k, c in CS2_CODES.items()} == A.CS2_KEEP
assert all(led % 21 < 16 for led in A.CS2_KEEP.values()), "LED slot not driven by the multiplexer"
fw[A.BASE:A.BASE + len(A.code)] = A.code
fw[A.HOOK_SITE:A.HOOK_SITE + 4] = A.site
fw[A.LED_SITE:A.LED_SITE + 4] = A.led_site

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
md.detail = True
DEC = {}
code_end = A.labels["lit_s"]
for a in range(A.BASE, code_end, 2):
    for i in md.disasm(bytes(fw[a:a + 4]), a, count=1):
        DEC[a] = i
RET = 0x0FFF_FFF0
M32 = 0xFFFFFFFF
REGIDX = {f"r{k}": k for k in range(13)} | {"sb": 9, "sl": 10, "fp": 11, "ip": 12, "sp": 13, "lr": 14, "pc": 15}


class CPU:
    def __init__(self):
        self.r = [0] * 16
        self.ram = {}
        self.N = self.Z = self.C = 0
        self.calls, self.led_frames = [], []

    def rd8(self, a):
        if a < 0x10000:
            return fw[a]
        assert 0x20000000 <= a < 0x20000800, hex(a)
        return self.ram.get(a, 0)

    def wr8(self, a, v):
        assert 0x20000000 <= a < 0x20000800, hex(a)
        self.ram[a] = v & 0xFF

    def rd32(self, a):
        assert a % 4 == 0, hex(a)
        return sum(self.rd8(a + k) << (8 * k) for k in range(4))

    def wr32(self, a, v):
        for k in range(4):
            self.wr8(a + k, v >> (8 * k))

    def nz(self, v):
        v &= M32
        self.N, self.Z = v >> 31, int(v == 0)
        return v

    def run(self, entry):
        self.r[14], self.r[15] = RET, entry
        steps = 0
        while self.r[15] != RET:
            steps += 1
            assert steps < 5000
            pc = self.r[15]
            if pc in (A.EVENT, A.LED_CONVERT):  # firmware routines -> stubs
                if pc == A.EVENT:
                    self.calls.append((self.rd8(A.S + 6), self.rd8(A.S + 0xA)))
                else:
                    self.led_frames.append({i: self.rd8(A.EFFECT + i) for i in range(138)})
                for k in (0, 1, 2, 3, 12):
                    self.r[k] = random.getrandbits(32)
                self.N, self.Z, self.C = (random.getrandbits(1) for _ in range(3))
                self.r[15] = self.r[14] & ~1
                continue
            self.step(DEC[pc])

    def step(self, i):
        m, ops, r = i.mnemonic, i.operands, self.r
        nxt = i.address + i.size
        reg = lambda o: REGIDX[i.reg_name(o.reg)]
        val = lambda o: r[reg(o)] if o.type == ARM_OP_REG else o.imm & M32
        if m == "push":
            for o in reversed(ops):
                r[13] -= 4; self.wr32(r[13], r[reg(o)])
        elif m == "pop":
            for o in ops:
                v = self.rd32(r[13]); r[13] += 4
                if reg(o) == 15:
                    nxt = v & ~1
                else:
                    r[reg(o)] = v
        elif m in ("ldr", "ldrb", "strb"):
            mem = ops[1].mem
            base = (i.address + 4) & ~3 if i.reg_name(mem.base) == "pc" else r[REGIDX[i.reg_name(mem.base)]]
            addr = (base + mem.disp + (r[REGIDX[i.reg_name(mem.index)]] if mem.index else 0)) & M32
            if m == "ldr":
                r[reg(ops[0])] = self.rd32(addr)
            elif m == "ldrb":
                r[reg(ops[0])] = self.rd8(addr)
            else:
                self.wr8(addr, r[reg(ops[0])])
        elif m == "cmp":
            a, b = r[reg(ops[0])], val(ops[1])
            self.nz(a - b); self.C = int(a >= b)
        elif m == "movs":
            r[reg(ops[0])] = self.nz(val(ops[1]))
        elif m == "mov":      # high-register move, flags unchanged
            r[reg(ops[0])] = r[reg(ops[1])]
        elif m in ("adds", "subs"):
            d = reg(ops[0])
            a, b = (r[d], val(ops[1])) if len(ops) == 2 else (r[reg(ops[1])], val(ops[2]))
            res = a + b if m == "adds" else a - b
            self.C = int(res > M32) if m == "adds" else int(a >= b)
            r[d] = self.nz(res)
        elif m in ("lsrs", "lsls"):
            d = reg(ops[0])
            src, n = (r[d], r[reg(ops[1])] & 0xFF) if len(ops) == 2 else (r[reg(ops[1])], ops[2].imm)
            if m == "lsrs":
                if n:
                    self.C = (src >> (n - 1)) & 1 if n <= 32 else 0
                r[d] = self.nz(src >> n if n < 32 else 0)
            else:
                if n:
                    self.C = (src >> (32 - n)) & 1
                r[d] = self.nz(src << n)
        elif m in ("ands", "orrs", "bics", "eors"):
            d = reg(ops[0]); b = val(ops[1])
            r[d] = self.nz({"ands": r[d] & b, "orrs": r[d] | b, "bics": r[d] & ~b & M32, "eors": r[d] ^ b}[m])
        elif m == "bl":
            r[14] = nxt | 1; nxt = ops[0].imm
        elif m == "bx":
            nxt = r[reg(ops[0])] & ~1
        elif m in ("beq", "bne", "blo", "bhs", "bls", "b"):
            take = {"beq": self.Z, "bne": not self.Z, "blo": not self.C, "bhs": self.C,
                    "bls": (not self.C) or self.Z, "b": 1}[m]
            if take:
                nxt = ops[0].imm
        else:
            raise NotImplementedError(f"{i.address:x} {m} {i.op_str}")
        r[15] = nxt


KA, KD, KW, KS, KQ, KE, K4 = 0x04, 0x07, 0x1A, 0x16, 0x14, 0x08, 0x21
POS = {KQ: (2, 1), KW: (2, 2), KE: (2, 3), KA: (3, 1), KS: (3, 2), KD: (3, 3)}  # (row, col)
PAIR_OF = {KA: 0, KD: 0, KW: 1, KS: 1}
PAIRS = [{KA, KD}, {KW, KS}]


class Board:
    """Keyboard model: physical keys -> 0x2C22-style events -> hook -> report; plus the reference behaviour."""

    def __init__(self):
        self.cpu = CPU()
        self.cpu.r[13] = 0x200006E0
        self.held, self.report = set(), set()
        self.enabled, self.last = True, [None, None]
        self.ref = [set(), set()]
        self.fn2 = self.mac = 0

    def event(self, code, pressed, row=None):
        c = self.cpu
        if row is not None:
            c.wr32(A.DEBOUNCED + 4 * row, sum(1 << POS[k][1] for k in self.held if POS[k][0] == row))
        c.wr8(A.S + 6, code); c.wr8(A.S + 0xA, int(pressed)); c.wr8(A.S + 9, self.fn2); c.wr8(A.S + 0x10, self.mac)
        regs = [random.getrandbits(32) for _ in range(8)]
        c.r[:8] = regs
        sp = c.r[13]
        c.calls = []
        c.run(A.BASE)
        assert c.r[4:8] == regs[4:8] and c.r[13] == sp, "callee-saved registers / sp not preserved"
        return c.calls

    def scan_row(self, row, changes):
        """changes: dict code -> pressed for keys on `row`; processed in ascending column order like 0x2C22."""
        for code, p in changes.items():
            (self.held.add if p else self.held.discard)(code)
        touched = set()
        for code in sorted(changes, key=lambda k: POS[k][1]):
            p = changes[code]
            calls = self.event(code, p, row)
            if code not in PAIR_OF:
                assert calls == [(code, p)], ("pass-through", code, calls)
            seen_press = False
            for k, kp in calls:
                if k in PAIR_OF:
                    assert (k in self.report) != kp, f"redundant {'press' if kp else 'release'} of {k:#x}"
                    if kp:
                        seen_press = True
                    else:
                        assert not seen_press, "release after press within one event"
                (self.report.add if kp else self.report.discard)(k)
            if code in PAIR_OF:  # reference: re-evaluate this pair on each of its events
                i = PAIR_OF[code]
                if p:
                    self.last[i] = code
                hp = self.held & PAIRS[i]
                self.ref[i] = {self.last[i]} if self.enabled and len(hp) == 2 else hp
                touched.add(i)
        for i in range(2):
            assert self.report & PAIRS[i] == self.ref[i], (i, self.report, self.ref, self.held, self.enabled)
        assert self.report - {KA, KD, KW, KS} == self.held - {KA, KD, KW, KS}

    def press_fn2_4(self):
        self.fn2 = 1
        calls = self.event(K4, 1)
        if self.mac:
            assert calls == [(K4, 1)]
        else:
            assert calls == [], calls  # swallowed
            self.enabled = not self.enabled
        assert self.event(K4, 0) == [(K4, 0)]  # release always passes through (harmless)
        self.fn2 = 0

    def led_frame(self):
        c = self.cpu
        for i in range(138):
            c.wr8(A.EFFECT + i, 0xFF)  # static effect: everything full
        c.led_frames = []
        c.run(A.labels["led_hook"])
        (frame,) = c.led_frames
        dark = {i for i, v in frame.items() if v == 0}
        if STYLE == "wasd_dark":
            off_dark = {A.LED_W, A.LED_A, A.LED_S, A.LED_D}
        else:
            off_dark = set(range(A.N_LEDS)) - set(A.CS2_KEEP.values())
        assert dark == (set() if self.enabled else off_dark), sorted(dark)


random.seed(7)
# 1) directed cases
b = Board()
b.scan_row(3, {KA: 1}); b.scan_row(3, {KD: 1}); assert b.report == {KD}
b.scan_row(3, {KD: 0}); assert b.report == {KA}
b.scan_row(2, {KW: 1}); b.scan_row(3, {KS: 1}); assert b.report == {KA, KS}
b.scan_row(3, {KS: 0}); assert b.report == {KA, KW}
b.led_frame()
b.press_fn2_4(); assert not b.enabled; b.led_frame()            # OFF: WASD dark
b.scan_row(3, {KD: 1}); assert b.report == {KA, KD, KW}       # OFF: plain behaviour, both reported
b.press_fn2_4(); assert b.enabled; b.led_frame()               # ON again
b.scan_row(3, {KS: 1}); assert b.report == {KA, KD, KS}       # pair W/S re-evaluated -> S wins
b.scan_row(3, {KA: 0, KD: 0, KS: 0}); b.scan_row(2, {KW: 0}); assert b.report == set()
b.mac = 1; b.press_fn2_4(); assert b.enabled; b.mac = 0        # Mac mode: Fn2+4 untouched
b.fn2 = 0; assert b.event(K4, 1) == [(K4, 1)] and b.event(K4, 0) == [(K4, 0)]  # plain 4
assert b.event(0xAF, 1) == [(0xAF, 1)]                           # Fn1 key passes
# 2) random fuzzing over both rows, with toggles in between
n = 0
for _ in range(4000):
    b = Board()
    for _ in range(random.randint(1, 15)):
        if random.random() < 0.12:
            b.press_fn2_4()
            b.led_frame()
            continue
        row = random.choice((2, 3))
        keys = [k for k in POS if POS[k][0] == row]
        changes = {k: int(k not in b.held) for k in random.sample(keys, random.randint(1, 3))}
        b.scan_row(row, changes)
    n += 1
print(f"OK [{STYLE}]: directed cases + {n} random sequences (rows 2+3, Q/E pass-through, toggling, LED frames)")
