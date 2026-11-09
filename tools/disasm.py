"""Disassemble a range of the K7 firmware (Cortex-M0 Thumb), annotating PC-relative literal loads.

    python tools/disasm.py <stock updater .exe | firmware .bin> <start_hex> <end_hex>
    e.g. python tools/disasm.py K7-A2-68K-WHILE-US-OA-V1.05_20210611.exe 2c22 2cc4
"""
import struct
import sys

from capstone import CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS, Cs

FW_OFFSET, FW_SIZE = 0x1AC378, 0x10000   # RCDATA 4000 inside the stock updater

data = open(sys.argv[1], "rb").read()
fw = data[FW_OFFSET:FW_OFFSET + FW_SIZE] if sys.argv[1].lower().endswith(".exe") else data
start, end = int(sys.argv[2], 16), int(sys.argv[3], 16)
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
addr = start
while addr < end:
    for ins in md.disasm(fw[addr:end], addr):
        note = ""
        if ins.mnemonic.startswith("ldr") and "[pc" in ins.op_str:
            imm = int(ins.op_str.split("#")[-1].rstrip("]"), 0) if "#" in ins.op_str else 0
            lit = ((ins.address + 4) & ~3) + imm
            if lit + 4 <= len(fw):
                note = f"   ; =0x{struct.unpack_from('<I', fw, lit)[0]:08x}"
        print(f"{ins.address:05x}: {ins.bytes.hex():<10} {ins.mnemonic:<8} {ins.op_str}{note}")
        addr = ins.address + ins.size
    if addr < end:  # data / undecodable halfword
        print(f"{addr:05x}: .hword 0x{struct.unpack_from('<H', fw, addr)[0]:04x}")
        addr += 2
