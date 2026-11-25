ENTRY(experiment_reset_gate)
SECTIONS {
 . = 0x80197a0;
 .text : { *(.text*) *(.rodata*) }
 .bss (NOLOAD) : { *(.bss*) *(COMMON) }
 .data : { *(.data*) }
 ASSERT(SIZEOF(.data)==0,"No initialized RAM in offline fixture")
 ASSERT(SIZEOF(.bss)==0,"No unreserved state is permitted")
 epilogue = 0x800e904;
 rc522_read_reg = 0x800f574;
 rc522_write_reg = 0x800f5d0;
 rc522_transceive = 0x800f32c;
 rc522_timer = 0x800f50c;
 rc522_flush = 0x800f30a;
 rfid_select = 0x800deb6;
 rfid_pageread = 0x800e18c;
 resume = 0x800e846;
 cache_resume = 0x800fe40;
 pageread_resume = 0x800e22c;
 pageread_fail = 0x800e23c;
 extract_resume = 0x800fe3a;
 scan_exit = 0x800fe98;
 scan_fail = 0x800fe94;
 ex_stock_malloc = 0x80132c5;
 ex_stock_event_factory = 0x801613f;
 stock_select_resume = 0x800deba;
 stock_background_resume = 0x800e248;
 stock_cmd68_resume = 0x800e7ac;
 stock_cmd13_resume = 0x800e914;
 stock_cmd14_resume = 0x800e750;
 stock_requeue_resume = 0x800e710;
}
