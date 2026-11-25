#!/usr/bin/env python3
"""Build the checked ACE application extension from the local 98O base image."""
# SPDX-License-Identifier: MIT
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess

HERE = Path(__file__).resolve().parent
BASE_ADDRESS = 0x08008000
BASE_SIZE = 78898
BASE_SHA256 = "5400720e96b5f82895bb412c8f802ed1864b2f83e1a73c682f06f6148fde79c6"
APPLICATION_LIMIT = 0x08024000
IMAGE_TRAILER_ADDRESS = 0x08022000
COUNTER_PAGES = [0x08022800, 0x08023000]
MAGIC = bytes.fromhex("61a5635a65a5325a")
VERSION = "V1.1.99O"

HOOKS = [
    (0x080116A8, "04f049fd", "cm_factory", "bl", "Extended native event allocation"),
    (0x08015AF6, "f5f755fe", "cm_registration_hook", "bl", "Register raw CMD92 and control worker"),
    (0x08009106, "0ef091f9", "cm_systick_hook", "bl", "Device-local deadline and position supervision"),
    (0x0800F698, "f0b581b0", "cm_insert_captured", "b", "Capture debounced INSERT encoder origin and replace native long preload"),
    (0x0800B578, "b0b582b0", "cm_feed_entry", "b", "Authorize feed and both assist modes"),
    (0x0800B400, "b0b582b0", "cm_generic_busy", "b", "Fence raw MOTOR_TEST"),
    (0x0800B4A0, "70b50020", "cm_speed_entry", "b", "Authorize in-motion speed updates"),
    (0x0800B644, "10b50020", "cm_stop_entry", "b", "Native STOP revokes controlled motion"),
    (0x0800D4E4, "2de9f041", "cm_ota_entry", "b", "Stop motion before native IAP transfer"),
    (0x0800ECE8, "2de9f047", "cm_start_entry", "b", "Fence all native motor starts"),
    (0x0800ED38, "c4e90700", "cm_after_wait_hook", "b", "Recheck authority after native setup wait"),
    (0x0800ED46, "84f83400", "cm_active_hook", "b", "Atomic motor-active authorization"),
    (0x0800ED76, "11612179", "cm_gpio_hook", "b", "Fence motor power-enable GPIO"),
    (0x0800EE98, "08433060", "cm_timer_hook", "b", "Atomic input-capture interrupt enable"),
    (0x0800EFA4, "22ea0000", "cm_disable_timer_hook", "b", "Atomic sibling-safe interrupt disable"),
    (0x08009D2E, "b0b50446", "cm_encoder_entry", "b", "Carry signed position across native counter resets"),
    (0x08009D62, "d0600120", "cm_encoder_enable_hook", "b", "Fence encoder interrupt enable"),
    (0x0800EECA, "cff6ff72", "cm_start_pwm_hook", "b", "Fence initial motor PWM"),
    (0x0800F012, "cff6ff72", "cm_control_pwm_hook", "b", "Fence periodic motor PWM"),
]

NATIVE_CONTRACTS = [
    (0x0800B480, "6569266a002100220bf0d2f8b6fa86f040094000002d08bf0120", "Either native worker can accept assist"),
    (0x0800B23E, "05ebc404bcf1000f84f8240084f82530e284a16208d041f6a031c2f2000101eb8010012180f83c10", "Per-worker slot descriptor and rollback suppression flag"),
    (0x08009D7C, "0079002800f08f8094f83c00002840f08a80", "Active odometer and rollback suppression gate"),
    (0x08009ED2, "00210171416380f83c10", "Native stop clears rollback suppression"),
    (0x08011324, "00ee100ab8eec00a", "Encoder getter uses signed conversion"),
    (0x080139D8, "b0b541f6a0350024c2f200052859c168281988474034b4f5807ff7d1b0bd", "Native global odometer update visits four slots"),
]

SYMBOLS = {
    "cm_stock_malloc": 0x080132C5,
    "cm_stock_event_factory": 0x0801613F,
    "cm_stock_register": 0x0800B7A5,
    "cm_stock_delay": 0x08013C71,
    "cm_stock_create_task": 0x080171C5,
    "cm_stock_native_stop": 0x0800B1BF,
    "cm_stock_power_stop": 0x0800F089,
    "cm_stock_tick": 0x0801742D,
    "cm_stock_motor_start": 0x0800ECE9,
    "cm_stock_feed_handler": 0x0800B579,
    "cm_stock_speed_handler": 0x0800B4A1,
    "cm_stock_start_resume": 0x0800ECEC,
    "cm_stock_start_return": 0x0800EED0,
    "cm_stock_after_wait_resume": 0x0800ED3C,
    "cm_stock_active_resume": 0x0800ED4C,
    "cm_stock_gpio_resume": 0x0800ED7A,
    "cm_stock_timer_resume": 0x0800EE9C,
    "cm_stock_disable_timer_resume": 0x0800EFAA,
    "cm_stock_encoder_start_resume": 0x08009D32,
    "cm_stock_encoder_resume": 0x08009D6A,
    "cm_stock_encoder_return": 0x08009D72,
    "cm_stock_control_resume": 0x0800F018,
    "cm_stock_feed_resume": 0x0800B57C,
    "cm_stock_speed_resume": 0x0800B4A4,
    "cm_stock_stop_resume": 0x0800B648,
    "cm_stock_ota_resume": 0x0800D4E8,
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode_branch(source, target, link):
    displacement = target - source - 4
    if (source | target) & 1 or not -(1 << 24) <= displacement < (1 << 24):
        raise ValueError("Unaligned or out-of-range Thumb branch")
    encoded = displacement >> 1
    sign = (encoded >> 23) & 1
    first = 0xF000 | (sign << 10) | ((encoded >> 11) & 0x3FF)
    second = (0xD000 if link else 0x9000)
    second |= ((1 ^ ((encoded >> 22) & 1) ^ sign) << 13)
    second |= ((1 ^ ((encoded >> 21) & 1) ^ sign) << 11)
    second |= encoded & 0x7FF
    return struct.pack("<HH", first, second)


def decode_branch(source, data):
    first, second = struct.unpack("<HH", data)
    sign = (first >> 10) & 1
    first_bit = 1 ^ ((second >> 13) & 1) ^ sign
    second_bit = 1 ^ ((second >> 11) & 1) ^ sign
    displacement = (sign << 24) | (first_bit << 23) | (second_bit << 22)
    displacement |= (first & 0x3FF) << 12
    displacement |= (second & 0x7FF) << 1
    if sign:
        displacement -= 1 << 25
    return source + 4 + displacement


def crc16(data, initial):
    value = initial
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ 0x8408 if value & 1 else value >> 1
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--installation-nonce", required=True,
                        help="Recorded 64-bit hexadecimal installation identity")
    args = parser.parse_args()
    nonce = int(args.installation_nonce, 16)
    if not 0 < nonce < 1 << 64 or not nonce >> 32:
        parser.error("installation nonce must be a 64-bit value with nonzero upper 32 bits")
    base = args.base.read_bytes()
    if len(base) != BASE_SIZE or digest(base) != BASE_SHA256 or base[-8:] != MAGIC:
        raise ValueError("The supplied image is not the checked 98O application base")
    if base.count(b"V1.1.98O\0") != 1:
        raise ValueError("98O version record is ambiguous")
    for address, expected, _, _, _ in HOOKS:
        offset = address - BASE_ADDRESS
        if base[offset:offset + 4].hex() != expected:
            raise ValueError("Hook input mismatch at " + hex(address))
    for address, expected, _ in NATIVE_CONTRACTS:
        offset = address - BASE_ADDRESS
        if base[offset:offset + len(expected) // 2].hex() != expected:
            raise ValueError("Native contract mismatch at " + hex(address))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise FileExistsError("Use a new output directory to preserve prior firmware artifacts")
    compiler = shutil.which("arm-none-eabi-gcc")
    if not compiler:
        raise FileNotFoundError("arm-none-eabi-gcc is required")
    prefix = str(Path(compiler).with_name("arm-none-eabi-"))
    log = []

    def run(tool, *arguments):
        command = [prefix + tool, *map(str, arguments)]
        result = subprocess.run(command, text=True, capture_output=True)
        log.append({"command": command, "returncode": result.returncode,
                    "stdout": result.stdout, "stderr": result.stderr})
        (output / "commands.json").write_text(json.dumps(log, indent=2) + "\n")
        result.check_returncode()
        return result.stdout

    (output / "installation.h").write_text(
        "#define CM_INSTALLATION_NONCE UINT64_C(0x%016x)\n" % nonce)
    origin = (BASE_ADDRESS + len(base) - 8 + 7) & ~7
    script = "ENTRY(cm_raw_dispatch)\nSECTIONS {\n . = 0x%x;\n" % origin
    script += " .text : { *(.text*) *(.rodata*) }\n"
    script += " .data : { *(.data*) }\n .bss (NOLOAD) : { *(.bss*) *(COMMON) }\n"
    script += ' ASSERT(SIZEOF(.data)==0,"Initialized RAM lacks native reservation")\n'
    script += ' ASSERT(SIZEOF(.bss)==0,"Global RAM lacks native reservation")\n'
    script += ' ASSERT(.<=0x08022000,"Payload reaches the image trailer reservation")\n'
    script += " /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) }\n}\n"
    script += "".join("%s = 0x%08x;\n" % pair for pair in SYMBOLS.items())
    (output / "payload.ld").write_text(script)
    flags = ["-mcpu=cortex-m3", "-mthumb", "-Os", "-ffreestanding", "-fno-builtin",
             "-fno-common", "-fno-unwind-tables", "-fno-asynchronous-unwind-tables",
             "-fno-tree-loop-distribute-patterns", "-fstack-usage", "-Wall", "-Wextra", "-Werror",
             "-I" + str(output), "-I" + str(HERE)]
    objects = []
    for source in ["controller.c", "boot_counter.c", "runtime.c", "hooks.S"]:
        object_path = output / (source.replace(".", "-") + ".o")
        run("gcc", *flags, "-c", HERE / source, "-o", object_path)
        objects.append(object_path)
    elf = output / "controlled-motion.elf"
    run("gcc", "-mcpu=cortex-m3", "-mthumb", "-nostdlib", "-Wl,-T," + str(output / "payload.ld"),
        "-Wl,-Map," + str(output / "payload.map"), *objects, "-lgcc", "-o", elf)
    payload_file = output / "controlled-motion.bin"
    run("objcopy", "-O", "binary", elf, payload_file)
    payload = payload_file.read_bytes()
    symbol_values = {}
    for line in run("nm", "-n", elf).splitlines():
        fields = line.split()
        if len(fields) == 3:
            symbol_values[fields[2]] = int(fields[0], 16)
    disassembly = run("objdump", "-d", elf)
    (output / "controlled-motion.dis").write_text(disassembly)
    image = bytearray(base[:-8])
    image.extend(b"\xff" * (origin - BASE_ADDRESS - len(image)))
    image.extend(payload)
    if BASE_ADDRESS + len(image) > IMAGE_TRAILER_ADDRESS:
        raise ValueError("Payload exceeds the checked application allocation")
    changes = []
    for address, expected, symbol, kind, purpose in HOOKS:
        replacement = encode_branch(address, symbol_values[symbol], kind == "bl")
        if decode_branch(address, replacement) != symbol_values[symbol]:
            raise ValueError("Independent branch decode failed")
        offset = address - BASE_ADDRESS
        image[offset:offset + 4] = replacement
        changes.append({"address": hex(address), "expected": expected, "replacement": replacement.hex(),
                        "symbol": symbol, "target": hex(symbol_values[symbol]), "kind": kind,
                        "purpose": purpose})
    version_offset = base.index(b"V1.1.98O\0")
    image[version_offset:version_offset + 9] = VERSION.encode() + b"\0"
    image.extend(b"\xff" * (IMAGE_TRAILER_ADDRESS - BASE_ADDRESS - len(image)))
    image.extend(MAGIC)
    if image[:512] != base[:512] or BASE_ADDRESS + len(image) > APPLICATION_LIMIT:
        raise ValueError("Application-vector or range guard failed")
    layout_offset = symbol_values["cm_runtime_layout"] - origin
    layout = struct.unpack_from("<10I", payload, layout_offset)
    image_file = output / "ACE2-99O-controlled-candidate.bin"
    image_file.write_bytes(image)
    compiler_version = run("gcc", "--version").splitlines()[0]
    sources = {path.name: digest(path.read_bytes()) for path in sorted(HERE.iterdir()) if path.is_file()}
    manifest = {
        "version": VERSION, "status": "offline application candidate",
        "base_sha256": BASE_SHA256, "base_bytes": BASE_SIZE,
        "image_bytes": len(image), "image_sha256": digest(image),
        "image_first_address": hex(BASE_ADDRESS), "image_end_exclusive": hex(BASE_ADDRESS + len(image)),
        "application_limit_exclusive": hex(APPLICATION_LIMIT), "vector_prefix_512_identical": True,
        "iap_trailer": image[-8:].hex(), "crc16_kermit_init0000": "%04x" % crc16(image, 0),
        "crc16_kermit_initffff": "%04x" % crc16(image, 0xFFFF),
        "installation_nonce": "%016x" % nonce, "payload_origin": hex(origin),
        "payload_bytes": len(payload), "payload_sha256": digest(payload),
        "runtime_tail_bytes": layout[0], "native_allocation_payload_bytes": layout[1],
        "extra_worker_stack_and_tcb_bytes": layout[2],
        "runtime_offsets": dict(zip(["controller", "expected_start", "control_task", "boot_ready",
                                     "ota_active", "iap_registered"], layout[3:9])),
        "controller_bytes": layout[9],
        "counter_pages": [hex(address) for address in COUNTER_PAGES],
        "counter_page_bytes": 2048, "counter_pages_outside_announced_image": True,
        "counter_pages_below_staging": COUNTER_PAGES[-1] + 2048 <= APPLICATION_LIMIT,
        "hooks": changes, "stock_symbols": {name: hex(address) for name, address in SYMBOLS.items()},
        "native_contracts": [{"address": hex(address), "expected": expected, "purpose": purpose}
                             for address, expected, purpose in NATIVE_CONTRACTS],
        "native_worker_admission": "Direct finite and grip moves wait for their own slot's native worker to drain; assist accepts either free native worker",
        "rollback_slip_check": "Direct reverse sets the native suppression byte; direct forward and scoped cleanup clear it; native forward slip detection stays active",
        "sources": sources, "compiler": compiler_version,
        "hardware_verified": False, "device_connected": False,
        "insertion_reference": {
            "sensor": "Original ADC hysteresis and IIR(alpha=0.2), two-sample key-task debounce",
            "edge_hook": "0x0800F698 captures the current material encoder count",
            "poll_fallback": "10-tick supervisor observes a missed callback with its own sampling uncertainty",
            "setup_accounting": "Capture-to-start manual displacement is accumulated and carried through native counter resets",
            "grip_target_um": 8000, "grip_total_limit_um": 20000,
            "grip_start": "After the native 200-tick setup delay, add 8mm to the current measured position within the total limit",
            "physical_zero_error_verified": False
        },
        "iap_startup": {
            "command_registration": "Native commands 2,3,4,5 must be present before boot-journal initialization",
            "query_without_motion_ready": True,
            "motion_gated_on_boot_counter_failure": True,
            "ota_entry_stops_all_motors": True,
            "arbitrary_early_application_fault_recovery_verified": False
        },
        "required_physical_checks": ["Native callback frame ABI", "Cold and warm boot counter",
            "IAP reachability and upgrade rollback", "Persistent-page flash operations",
            "Per-slot encoder sign and insertion repeatability", "50 and 80 mm/s stopping distance",
            "Expired setup and stale command rejection", "Buffer-following assist under lease",
            "Shared-route isolation and all native motor writers"]
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "symbols.json").write_text(json.dumps(symbol_values, indent=2) + "\n")
    print(json.dumps({"image": str(image_file), "sha256": manifest["image_sha256"],
                      "bytes": len(image), "hooks": len(changes), "hardware_verified": False}))


if __name__ == "__main__":
    main()
