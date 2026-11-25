.syntax unified
.thumb
.cpu cortex-m3
.text
/* Offline compile only. There is intentionally no image installation script.
 * Original six arguments preserved, true LR added as seventh C argument. */
.global br_stock_transceive_entry
.thumb_func
br_stock_transceive_entry:
    push {r4,lr}
    sub sp,#16
    ldr r4,[sp,#24]
    str r4,[sp,#0]
    ldr r4,[sp,#28]
    str r4,[sp,#4]
    str lr,[sp,#8]
    bl br_stock_transceive_checked
    add sp,#16
    pop {r4,pc}
.global br_stock_select_original
.thumb_func
br_stock_select_original:
    push {r4-r7,lr}
    sub sp,#20
    b.w stock_select_resume
