ENTRY(quarantine_gate)
SECTIONS {
  . = 0x0800F6AE;
  .text : { *(.text) }
  insert_return = 0x0800F6C4;
}
