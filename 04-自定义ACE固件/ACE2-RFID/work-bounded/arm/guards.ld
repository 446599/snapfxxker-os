ENTRY(guarded_transceive_entry)
SECTIONS { . = 0x0801B348; .text : { *(.text*) }
 stock_after_frame = 0x0800F332; stock_irq_read = 0x0800F3E8; stock_result_epilogue = 0x0800F4A2; }
