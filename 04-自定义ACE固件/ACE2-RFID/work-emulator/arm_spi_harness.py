#!/usr/bin/env python3
"""Offline ARM/Thumb routine runner. Never opens a device or network connection.

Instruction emulation is real; GPIO/SPI peripherals are explicit test doubles.
The stock image is read-only and hash-checked. This is not a board simulator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import unicorn
from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
from unicorn import UC_PROT_READ, UC_PROT_EXEC, UC_PROT_ALL
from unicorn.arm_const import (
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
    UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
    UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
    UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_XPSR,
    UC_CPU_ARM_CORTEX_M3,
)

HERE = Path(__file__).resolve().parent
DEFAULT_BASE = HERE.parent / "input/ACE2_V1.1.31_20260306.bin"
BASE_ADDR = 0x08008000
BASE_SIZE = 71592
BASE_SHA256 = "a468e366226e7c483d3dd96b444223e89cbf0d1b10573d42b6263843ffd6be3e"
READ_ENTRY = 0x0800F574
WRITE_ENTRY = 0x0800F5D0
RETURN_PC = 0x0807F000
RAM_ADDR, RAM_SIZE = 0x20000000, 0x10000
OBJECT = 0x20001000
STACK = 0x2000E000
PENDING_STOP = 0x2000F000
SPI = 0x40013000
CS_PORT, CTRL_PORT = 0x40010800, 0x40010C00
CS_MASK, CTRL_MASK = 1 << 4, 1 << 7
PHASE_NAMES = ("tx_address", "rx_address", "tx_payload", "rx_payload")
CALLEE_REGS = (UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
               UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11)


def hex32(n):
    return f"0x{n:08X}"


@dataclass
class Scenario:
    name: str
    operation: str = "read"
    stuck_phase: int | None = None
    delay_polls: tuple[int, int, int, int] = (0, 0, 0, 0)
    instruction_budget: int = 10000
    stop_after_instruction: int | None = None
    register: int = 0x2A
    payload: int = 0x56
    receive: int = 0xA5
    initial_ram: dict[int, bytes] = field(default_factory=dict)


class SpiRunner:
    def __init__(self, base: Path = DEFAULT_BASE, overlays=()):
        self.base = Path(base)
        self.base_bytes = self.base.read_bytes()
        digest = hashlib.sha256(self.base_bytes).hexdigest()
        if len(self.base_bytes) != BASE_SIZE or digest != BASE_SHA256:
            raise ValueError("Refusing unrecognized base firmware")
        self.overlays = [(int(addr), Path(path), Path(path).read_bytes())
                         for addr, path in overlays]

    def run(self, scenario: Scenario, entry: int | None = None) -> dict:
        entry = entry or (READ_ENTRY if scenario.operation == "read" else WRITE_ENTRY)
        uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        uc.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M3)
        uc.mem_map(0x08000000, 0x100000, UC_PROT_ALL)
        uc.mem_write(BASE_ADDR, self.base_bytes)
        for addr, _, blob in self.overlays:
            if addr < 0x08000000 or addr + len(blob) > 0x08100000:
                raise ValueError("Overlay outside emulated flash")
            uc.mem_write(addr, blob)
        uc.mem_write(RETURN_PC, b"\x00\xbf")
        uc.mem_protect(0x08000000, 0x100000, UC_PROT_READ | UC_PROT_EXEC)
        uc.mem_map(RAM_ADDR, RAM_SIZE)
        uc.mem_map(0x40010000, 0x4000)
        uc.mem_write(OBJECT, struct.pack("<5I", SPI, CS_PORT, CS_MASK,
                                         CTRL_PORT, CTRL_MASK))
        for address, data in scenario.initial_ram.items():
            uc.mem_write(address, data)
        uc.reg_write(UC_ARM_REG_SP, STACK)
        uc.reg_write(UC_ARM_REG_LR, RETURN_PC | 1)
        uc.reg_write(UC_ARM_REG_R0, OBJECT)
        uc.reg_write(UC_ARM_REG_R1, scenario.register)
        uc.reg_write(UC_ARM_REG_R2, scenario.payload)
        uc.reg_write(UC_ARM_REG_R3, 0x33333333)
        uc.reg_write(UC_ARM_REG_XPSR, 1 << 24)
        for i, reg in enumerate(CALLEE_REGS):
            uc.reg_write(reg, 0x44440000 + i)

        events, tx, rx = [], [], []
        polls = [0, 0, 0, 0]
        pc_counts = Counter()
        gpio = {CS_PORT: CS_MASK, CTRL_PORT: CTRL_MASK}
        state = {"instructions": 0, "returned": False, "fault": None,
                 "stop_pending": False}

        def log(kind, **kwargs):
            events.append({"instruction": state["instructions"], "kind": kind, **kwargs})

        def fail(message):
            state["fault"] = message
            uc.emu_stop()

        def stage():
            pair = (len(tx), len(rx))
            try:
                return {(0, 0): 0, (1, 0): 1, (1, 1): 2, (2, 1): 3}[pair]
            except KeyError:
                fail(f"Unexpected SPI transaction sequence {pair}")
                return None

        def on_code(_uc, address, size, _data):
            if address == RETURN_PC:
                state["returned"] = True
                log("return", address=hex32(address))
                uc.emu_stop()
                return
            state["instructions"] += 1
            pc_counts[address] += 1
            if state["instructions"] == 1:
                log("entry", address=hex32(address))
            if state["instructions"] == scenario.stop_after_instruction:
                # A logical pending request only. No IRQ or RTOS is simulated.
                state["stop_pending"] = True
                uc.mem_write(PENDING_STOP, struct.pack("<I", 1))
                log("synthetic_stop_pending", address=hex32(PENDING_STOP))

        def on_read(_uc, access, address, size, value, _data):
            if address == SPI + 8 and size == 4:
                phase = stage()
                if phase is None:
                    return
                polls[phase] += 1
                mask = 2 if phase % 2 == 0 else 1
                ready = phase != scenario.stuck_phase and polls[phase] > scenario.delay_polls[phase]
                status = 3 if ready else 3 & ~mask
                uc.mem_write(address, struct.pack("<I", status))
                if polls[phase] == 1 or (ready and polls[phase] == scenario.delay_polls[phase] + 1):
                    log("status", phase=PHASE_NAMES[phase], value=status, ready=ready,
                        pc=hex32(uc.reg_read(UC_ARM_REG_PC)))
            elif address == SPI + 12 and size == 4:
                if len(tx) != len(rx) + 1:
                    fail("SPI data read without corresponding write")
                    return
                value = scenario.receive if len(rx) == 1 else 0
                rx.append(value)
                uc.mem_write(address, struct.pack("<I", value))
                log("spi_read", value=value)
            else:
                fail(f"Unmodeled peripheral read {hex32(address)} size {size}")

        def on_write(_uc, access, address, size, value, _data):
            if size != 4:
                fail(f"Unmodeled peripheral write width {size}")
            elif address == SPI + 12:
                tx.append(value)
                log("spi_write", value=value)
            elif address in (CS_PORT + 16, CS_PORT + 20, CTRL_PORT + 16, CTRL_PORT + 20):
                port = CS_PORT if address < CTRL_PORT else CTRL_PORT
                if address == port + 16:
                    gpio[port] = (gpio[port] | (value & 0xFFFF)) & ~(value >> 16)
                else:
                    gpio[port] &= ~(value & 0xFFFF)
                log("gpio_write", address=hex32(address), value=value,
                    port_output=gpio[port])
            else:
                fail(f"Unmodeled peripheral write {hex32(address)}")

        uc.hook_add(UC_HOOK_CODE, on_code)
        uc.hook_add(UC_HOOK_MEM_READ, on_read, begin=0x40010000, end=0x40013FFF)
        uc.hook_add(UC_HOOK_MEM_WRITE, on_write, begin=0x40010000, end=0x40013FFF)
        try:
            uc.emu_start(entry | 1, RETURN_PC + 2, count=scenario.instruction_budget)
        except UcError as exc:
            state["fault"] = str(exc)
        if state["fault"]:
            outcome = "emulation_fault"
        elif state["returned"]:
            outcome = "returned"
        else:
            outcome = "instruction_budget_exhausted"
        return {
            "name": scenario.name, "entry": hex32(entry), "operation": scenario.operation,
            "outcome": outcome, "fault": state["fault"],
            "instructions": state["instructions"], "instruction_budget": scenario.instruction_budget,
            "pc": hex32(uc.reg_read(UC_ARM_REG_PC)),
            "r0": uc.reg_read(UC_ARM_REG_R0),
            "sp_restored": uc.reg_read(UC_ARM_REG_SP) == STACK,
            "callee_registers_preserved": all(uc.reg_read(reg) == 0x44440000 + i
                                             for i, reg in enumerate(CALLEE_REGS)),
            "cs_high": bool(gpio[CS_PORT] & CS_MASK),
            "ctrl_high": bool(gpio[CTRL_PORT] & CTRL_MASK),
            "tx_words": tx, "rx_words": rx,
            "polls": dict(zip(PHASE_NAMES, polls)),
            "synthetic_stop_pending": state["stop_pending"],
            "busy_pcs": [{"pc": hex32(pc), "visits": count}
                         for pc, count in pc_counts.most_common(8)],
            "events": events,
        }


def stock_suite(runner):
    results = []
    for operation in ("read", "write"):
        for suffix, delay in (("ready", (0, 0, 0, 0)), ("delayed", (2, 3, 4, 5))):
            r = runner.run(Scenario(f"stock_{operation}_{suffix}", operation, delay_polls=delay))
            assert r["outcome"] == "returned", r
            assert r["cs_high"] and not r["ctrl_high"], r
            assert r["sp_restored"] and r["callee_registers_preserved"], r
            expected_tx = [0xD4, 0xFF] if operation == "read" else [0x54, 0x56]
            assert r["tx_words"] == expected_tx, r
            if operation == "read":
                assert r["r0"] == 0xA5, r
            assert list(r["polls"].values()) == [n + 1 for n in delay], r
            results.append(r)
        for phase in range(4):
            r = runner.run(Scenario(f"stock_{operation}_stuck_{PHASE_NAMES[phase]}",
                                   operation, stuck_phase=phase, stop_after_instruction=2000))
            assert r["outcome"] == "instruction_budget_exhausted", r
            assert r["instructions"] == 10000 and not r["cs_high"], r
            assert r["synthetic_stop_pending"] and r["polls"][PHASE_NAMES[phase]] > 100, r
            results.append(r)
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", type=Path, default=DEFAULT_BASE)
    ap.add_argument("--report", type=Path, default=HERE / "stock-results.json")
    ap.add_argument("--overlay", action="append", default=[], metavar="ADDRESS:FILE")
    args = ap.parse_args()
    overlays = [(int(a, 0), Path(p)) for a, p in (s.split(":", 1) for s in args.overlay)]
    runner = SpiRunner(args.base, overlays)
    results = stock_suite(runner)
    report = {
        "engine": "Unicorn", "version": unicorn.__version__,
        "cpu_model": "Cortex-M3", "mode": "Thumb + MCLASS",
        "base_sha256": BASE_SHA256, "base_size": BASE_SIZE,
        "limitations": ["Synthetic SPI/GPIO; no physical hardware",
                        "No RTOS, interrupts, DMA, wall-clock timing, or electrical bus model",
                        "Instruction count is not elapsed time or proof of STOP/thermal scheduling",
                        "Budget exhaustion bounds the experiment; stock polling has no timeout"],
        "results": results, "passed_assertions": True,
    }
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"tests": len(results), "passed": True, "report": str(args.report)}, indent=2))


if __name__ == "__main__":
    main()
