ENTRY(decode_native_tag)
SECTIONS {
  . = 0x080197A0;
  .text : { *(.text*) *(.rodata*) }
  epilogue = 0x0800E904;
  rc522_read_reg = 0x0800F574;
  rc522_write_reg = 0x0800F5D0;
  rc522_transceive = 0x0800F32C;
  rc522_timer = 0x0800F50C;
  rc522_flush = 0x0800F30A;
  rfid_select = 0x0800DEB6;
  rfid_pageread = 0x0800E18C;
  memcpy = 0x08008AA8;
  resume = 0x0800E846;
  cache_resume = 0x0800FE40;
  pageread_resume = 0x0800E22C;
  pageread_fail = 0x0800E23C;
  extract_resume = 0x0800FE3A;
  scan_exit = 0x0800FE98;
}
