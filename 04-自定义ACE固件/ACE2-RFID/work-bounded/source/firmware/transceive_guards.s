/* OFFLINE PARTIAL PATCH. Does not replace the unbounded stock SPI helpers.
 * No GPIO power/reset claim, no persistent quarantine, NOT FLASH READY. */
.syntax unified
.thumb
.cpu cortex-m3
.text
.global guarded_transceive_entry
.global bounded_irq_poll
.thumb_func
guarded_transceive_entry:
    cmp r3, #0
    beq reject
    cmp r3, #64
    bhi reject
    cmp r0, #0
    beq reject
    cmp r2, #0
    beq reject
    ldr r12, [sp, #0]
    cmp r12, #0
    beq reject
    ldr r12, [sp, #4]
    cmp r12, #0
    beq reject
    /* Displaced stock push and sub, same 48-byte frame and argument offsets. */
    push {r4-r11, lr}
    sub sp, #12
    movw r12, #1024
    str r12, [sp, #0]  /* Proven unused stock local slot, not persistent state. */
    b.w stock_after_frame
reject:
    ldr r12, [sp, #4]
    cmp r12, #0
    beq 1f
    movs r0, #0
    str r0, [r12]
1:  movs r0, #1
    bx lr
.thumb_func
bounded_irq_poll:
    ldr r0, [sp, #0]
    cbz r0, poll_exhausted
    subs r0, #1
    str r0, [sp, #0]
    /* Displaced stock instructions, then original read helper call. */
    mov r0, r4
    movs r1, #4
    b.w stock_irq_read
poll_exhausted:
    /* Do not run stock cleanup helper: it can itself block forever. */
    ldr r1, [sp, #52]
    movs r0, #0
    str r0, [r1]
    ldrd r0, r1, [r4, #12]
    str r1, [r0, #20]
    ldrd r0, r1, [r4, #4]
    str r1, [r0, #16]
    movs r6, #1
    b.w stock_result_epilogue
