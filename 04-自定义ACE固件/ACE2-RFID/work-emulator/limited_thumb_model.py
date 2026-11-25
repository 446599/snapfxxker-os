#!/usr/bin/env python3
"""Dependency-free behavior model of only the two stock SPI leaf functions.

Uses GNU objdump to decode the exact verified firmware bytes, then interprets
an intentionally tiny instruction subset. NOT a CPU/board/interrupt emulator.
Unknown instructions, memories and MMIO are fatal. No printer/network access.
"""
import hashlib
import json
import re
import struct
import subprocess
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BASE = ROOT / "input/ACE2_V1.1.31_20260306.bin"
OBJDUMP = ROOT / "toolchain/usr/bin/arm-none-eabi-objdump"
SHA256 = "a468e366226e7c483d3dd96b444223e89cbf0d1b10573d42b6263843ffd6be3e"
OBJECT, STACK, RETURN = 0x20001000, 0x2000E000, 0x0807F001
SPI, CS, CTRL = 0x40013000, 0x40010800, 0x40010C00
CS_MASK, CTRL_MASK = 16, 128
PHASES = ["tx_address", "rx_address", "tx_payload", "rx_payload"]


def decode():
    image = BASE.read_bytes()
    assert len(image) == 71592 and hashlib.sha256(image).hexdigest() == SHA256
    output = subprocess.check_output([
        str(OBJDUMP), "-b", "binary", "-marm", "-Mforce-thumb", "-D",
        "--adjust-vma=0x08008000", "--start-address=0x0800f574",
        "--stop-address=0x0800f634", str(BASE)], text=True)
    (HERE / "stock-spi.dis").write_text(output)
    code = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or not re.match(r"\s+[0-9a-f]+:", parts[0]):
            continue
        pc = int(parts[0].strip().rstrip(":"), 16)
        raw = parts[1].split()
        if not all(re.fullmatch("[0-9a-f]{4}", h) for h in raw):
            raise ValueError(f"Unexpected instruction bytes: {line}")
        # Verify the disassembler byte text is the actual source image.
        assert b"".join(struct.pack("<H", int(h, 16)) for h in raw) == image[pc-0x08008000:pc-0x08008000+2*len(raw)]
        operands = parts[3].split("@")[0].strip() if len(parts) > 3 else ""
        code[pc] = (2 * len(raw), parts[2].strip().split(".")[0], operands)
    return code


class LimitedModel:
    def __init__(self, code, operation, stuck=None, delays=(0, 0, 0, 0), stop_at=None):
        self.code, self.operation = code, operation
        self.stuck, self.delays, self.stop_at = stuck, delays, stop_at
        self.r = [0] * 16
        self.r[0:3] = [OBJECT, 0x2A, 0x56]
        for i in range(4, 12):
            self.r[i] = 0x44440000 + i
        self.r[13:16] = [STACK, RETURN, 0x0800F574 if operation == "read" else 0x0800F5D0]
        self.mem = {OBJECT: SPI, OBJECT + 4: CS, OBJECT + 8: CS_MASK,
                    OBJECT + 12: CTRL, OBJECT + 16: CTRL_MASK}
        self.gpio = {CS: CS_MASK, CTRL: CTRL_MASK}
        self.n = self.z = False
        self.returned = self.stop_pending = False
        self.tx, self.rx, self.events = [], [], []
        self.polls = [0] * 4
        self.pc_counts = Counter()
        self.count = 0

    @staticmethod
    def reg(name):
        name = name.strip()
        if name.startswith("r") and name[1:].isdigit():
            return int(name[1:])
        return {"ip": 12, "sp": 13, "lr": 14, "pc": 15}[name]

    def value(self, name):
        return int(name[1:], 0) if name.startswith("#") else self.r[self.reg(name)]

    def event(self, kind, **details):
        self.events.append(dict(instruction=self.count, kind=kind, **details))

    def load(self, addr):
        if addr == SPI + 8:
            phase = {(0, 0): 0, (1, 0): 1, (1, 1): 2, (2, 1): 3}[(len(self.tx), len(self.rx))]
            self.polls[phase] += 1
            ready = phase != self.stuck and self.polls[phase] > self.delays[phase]
            value = 3 if ready else 3 & ~(2 if phase % 2 == 0 else 1)
            if self.polls[phase] == 1 or (ready and self.polls[phase] == self.delays[phase] + 1):
                self.event("status", phase=PHASES[phase], ready=ready, value=value)
            return value
        if addr == SPI + 12:
            assert len(self.tx) == len(self.rx) + 1
            value = 0xA5 if len(self.rx) == 1 else 0
            self.rx.append(value)
            self.event("spi_read", value=value)
            return value
        if not 0x20000000 <= addr < 0x20010000:
            raise ValueError(f"Unmodeled memory read 0x{addr:x}")
        if addr not in self.mem:
            raise ValueError(f"Uninitialized RAM read 0x{addr:x}")
        return self.mem[addr]

    def store(self, addr, value):
        value &= 0xFFFFFFFF
        if addr == SPI + 12:
            self.tx.append(value)
            self.event("spi_write", value=value)
        elif addr in (CS + 16, CS + 20, CTRL + 16, CTRL + 20):
            port = CS if addr < CTRL else CTRL
            if addr == port + 16:
                self.gpio[port] = (self.gpio[port] | (value & 0xFFFF)) & ~(value >> 16)
            else:
                self.gpio[port] &= ~(value & 0xFFFF)
            self.event("gpio_write", address=f"0x{addr:08X}", value=value,
                       port_output=self.gpio[port])
        elif 0x20000000 <= addr < 0x20010000 and not addr & 3:
            self.mem[addr] = value
        else:
            raise ValueError(f"Unmodeled memory write 0x{addr:x}")

    def step(self):
        pc = self.r[15]
        size, op, args = self.code[pc]
        self.count += 1
        self.pc_counts[pc] += 1
        if self.count == 1:
            self.event("entry", address=f"0x{pc:08X}")
        if self.count == self.stop_at:
            self.stop_pending = True
            self.event("synthetic_stop_pending")
        nxt = pc + size
        fields = [x.strip() for x in args.split(",")]
        if op in ("ldr", "str", "ldrd"):
            match = re.fullmatch(r"(.*), \[([^,\]]+)(?:, #([0-9]+))?\]", args)
            if not match:
                raise ValueError((op, args))
            dests, base, off = match.groups()
            addr = self.value(base) + int(off or 0)
            regs = [self.reg(r) for r in dests.split(",")]
            if op == "str":
                self.store(addr, self.r[regs[0]])
            else:
                values = [self.load(addr + i * 4) for i in range(len(regs))]
                for reg, value in zip(regs, values):
                    self.r[reg] = value
        elif op in ("movs", "uxtb"):
            value = self.value(fields[1])
            self.r[self.reg(fields[0])] = value & (255 if op == "uxtb" else 0xFFFFFFFF)
            if op == "movs":
                self.flags(value)
        elif op in ("sub", "subs", "add"):
            dest = self.reg(fields[0])
            left = self.r[dest] if len(fields) == 2 else self.value(fields[1])
            right = self.value(fields[-1])
            value = (left + right if op == "add" else left - right) & 0xFFFFFFFF
            self.r[dest] = value
            if op == "subs":
                self.flags(value)
        elif op == "lsls":
            value = (self.value(fields[1]) << self.value(fields[2])) & 0xFFFFFFFF
            self.r[self.reg(fields[0])] = value
            self.flags(value)
        elif op in ("orr", "and"):
            assert len(fields) == 4 and fields[3].startswith("lsl #")
            left = self.value(fields[1])
            right = (self.value(fields[2]) << int(fields[3][5:])) & 0xFFFFFFFF
            self.r[self.reg(fields[0])] = left | right if op == "orr" else left & right
        elif op in ("b", "bpl", "beq"):
            if op == "b" or (op == "bpl" and not self.n) or (op == "beq" and self.z):
                nxt = int(fields[0], 0)
        elif op == "cbz":
            if not self.value(fields[0]):
                nxt = int(fields[1], 0)
        elif op == "bx":
            assert args == "lr" and self.r[14] == RETURN
            self.returned = True
            nxt = RETURN & ~1
            self.event("return", address=f"0x{nxt:08X}")
        elif op != "nop":
            raise ValueError(f"Unsupported instruction {op} {args}")
        self.r[15] = nxt

    def flags(self, value):
        self.n, self.z = bool(value & 0x80000000), value == 0

    def run(self, budget=10000):
        while not self.returned and self.count < budget:
            self.step()
        return dict(operation=self.operation,
                    scenario="stuck_" + PHASES[self.stuck] if self.stuck is not None else "delayed" if any(self.delays) else "ready",
                    outcome="returned" if self.returned else "instruction_budget_exhausted",
                    instructions=self.count, instruction_budget=budget,
                    pc=f"0x{self.r[15]:08X}", r0=self.r[0],
                    sp_restored=self.r[13] == STACK,
                    callee_registers_preserved=all(self.r[i] == 0x44440000+i for i in range(4, 12)),
                    cs_high=bool(self.gpio[CS] & CS_MASK),
                    ctrl_high=bool(self.gpio[CTRL] & CTRL_MASK),
                    tx_words=self.tx, rx_words=self.rx,
                    polls=dict(zip(PHASES, self.polls)), synthetic_stop_pending=self.stop_pending,
                    busy_pcs=[dict(pc=f"0x{pc:08X}", visits=count) for pc, count in self.pc_counts.most_common(6)],
                    events=self.events)


def main():
    code = decode()
    tests = []
    for operation in ("read", "write"):
        for delays in ((0, 0, 0, 0), (2, 3, 4, 5)):
            r = LimitedModel(code, operation, delays=delays).run()
            assert r["outcome"] == "returned" and r["cs_high"] and not r["ctrl_high"], r
            assert r["sp_restored"] and r["callee_registers_preserved"], r
            assert r["tx_words"] == ([0xD4, 0xFF] if operation == "read" else [0x54, 0x56]), r
            assert operation == "write" or r["r0"] == 0xA5, r
            assert list(r["polls"].values()) == [d+1 for d in delays], r
            tests.append(r)
        for phase in range(4):
            r = LimitedModel(code, operation, stuck=phase, stop_at=2000).run()
            assert r["outcome"] == "instruction_budget_exhausted" and r["instructions"] == 10000, r
            assert not r["cs_high"] and r["synthetic_stop_pending"], r
            assert r["polls"][PHASES[phase]] > 100, r
            tests.append(r)
    report = dict(engine="Hand-written restricted Thumb behavior model driven by GNU objdump",
                  base_sha256=SHA256, decoded_instructions=len(code), test_count=len(tests),
                  passed_assertions=True, results=tests,
                  not_verified=["Independent ARM instruction decoding and CPU fidelity", "Candidate routines outside F574/F5D0", "RTOS/interrupt/STOP delivery", "Thermal tasks or physical hardware", "Wall-clock deadlines, peripheral timing, DMA, electrical faults"])
    (HERE / "limited-model-results.json").write_text(json.dumps(report, indent=2) + "\n")
    for r in tests:
        print(f'{r["operation"]:5} {r["scenario"]:18} {r["outcome"]:28} instructions={r["instructions"]:5} CS_high={r["cs_high"]}')
    print("PASS: 12 restricted behavioral cases; not a CPU or board emulator")


if __name__ == "__main__":
    main()
