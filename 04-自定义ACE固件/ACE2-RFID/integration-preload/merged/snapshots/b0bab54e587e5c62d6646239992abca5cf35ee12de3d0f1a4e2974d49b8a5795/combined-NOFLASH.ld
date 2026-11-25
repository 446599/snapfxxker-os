ENTRY(experiment_reset_gate)
SECTIONS {
 . = 0x80197a0;
 .text : { rf-00-experimental_gate.o(.text*) rf-01-event_storage.o(.text*) rf-02-experimental_binding.o(.text*) rf-03-bounded_rc522.o(.text*) rf-04-stock_bounded_bridge.o(.text*) rf-05-stock_bounded_thunks.o(.text*) rf-06-rf_owner.o(.text*) rf-07-rf_stock_adapter.o(.text*) rf-08-rf_stock_trampolines.o(.text*) rf-09-native_tag_decoder.o(.text*) rf-10-uid_stub.o(.text*) rf-11-rawtag_stub.o(.text*) rf-12-rawtag_cache_stub.o(.text*) rf-13-rawtag_extract_stub.o(.text*) rf-14-pageread_gate_stub.o(.text*) rf-00-experimental_gate.o(.rodata*) rf-01-event_storage.o(.rodata*) rf-02-experimental_binding.o(.rodata*) rf-03-bounded_rc522.o(.rodata*) rf-04-stock_bounded_bridge.o(.rodata*) rf-05-stock_bounded_thunks.o(.rodata*) rf-06-rf_owner.o(.rodata*) rf-07-rf_stock_adapter.o(.rodata*) rf-08-rf_stock_trampolines.o(.rodata*) rf-09-native_tag_decoder.o(.rodata*) rf-10-uid_stub.o(.rodata*) rf-11-rawtag_stub.o(.rodata*) rf-12-rawtag_cache_stub.o(.rodata*) rf-13-rawtag_extract_stub.o(.rodata*) rf-14-pageread_gate_stub.o(.rodata*) }
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

SECTIONS {
 . = 0x08030000;
 .preload_text : { ph-c.o(.text*) ph-S.o(.text*) ph-c.o(.rodata*) ph-S.o(.rodata*) }
 .binding_required : { KEEP(ph-S.o(.binding_required)) }
 ASSERT(ADDR(.text)+SIZEOF(.text)<=ADDR(.preload_text),"RF/preload text overlap")
 ASSERT(ADDR(.bss)+SIZEOF(.bss)<=0x21000000,"RF/preload RAM overlap")
}
stock_insert_resume = 0x800f72a;
stock_insert_return = 0x800f6c4;
stock_start_resume = 0x800ecec;
stock_start_return = 0x800eed0;
stock_after_wait_resume = 0x800ed3c;
stock_gpio_resume = 0x800ed7a;
stock_timer_resume = 0x800ee9c;
stock_control_resume = 0x800f018;
stock_power_disable = 0x800f088;
stock_encoder_disable = 0x8009eb8;
stock_active_resume = 0x800ed4c;
stock_encoder_resume = 0x8009d6a;
stock_encoder_return = 0x8009d72;
stock_disable_timer_resume = 0x800efaa;
ph_storage = 0x21000000;
ph_verified_reserved_ram = 0x0badf00d;
ph_verified_exclusive_startup = 0x0badf00d;
ph_verified_owned_failure_source = 0x0badf00d;
ph_verified_all_motion_writers = 0x0badf00d;
ph_verified_controller_quiescence = 0x0badf00d;
