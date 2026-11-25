#if !defined(PRELOAD_INTEGRATION_EXPERIMENT) || PRELOAD_INTEGRATION_EXPERIMENT != 1
#error "Offline experiment only: no firmware startup/RAM/owned AUTH binding"
#endif
.syntax unified
.thumb
.text

/* All inline hooks save the full caller-clobbered register set, APSR NZCVQ,
 * and prior PRIMASK. No FPU instructions or calls occur within these gates.
 * Keep the test-to-effect interval under the same IRQ mask. */
.macro ENTER
    push.w {r0-r3,r12,lr}
    mrs r0,APSR
    mrs r1,PRIMASK
    push {r0,r1}
    cpsid i
    dmb
.endm
.macro LEAVE
    dmb
    pop {r0,r1}
    msr APSR_nzcvq,r0
    msr PRIMASK,r1
    pop.w {r0-r3,r12,lr}
.endm
.macro CHECK lane
    mov r0,\lane
    cmp r0,#3
    bhi 9f
    ldr r1,=ph_storage
    ldr r0,[r1,r0,lsl #2]
    cmp r0,#0
    bne 9f
.endm

/* F726 is after normal sibling/self/reader8 guards and before queue mutation.
 * Healthy INSERT still executes every original instruction. */
.global ph_insert_hook
.thumb_func
ph_insert_hook:
    ENTER
    CHECK r4
    LEAVE
    movw r0,#0x7ea4
    b.w stock_insert_resume
9:  LEAVE
    b.w stock_insert_return

/* Whole ECE8 prologue is replayed only when this lane is not latched. */
.global ph_start_hook
.thumb_func
ph_start_hook:
    ENTER
    ldr r0,[sp,#8]
    ldrb r0,[r0,#4]
    CHECK r0
    LEAVE
    push.w {r4-r10,lr}
    b.w stock_start_resume
9:  LEAVE
    bx lr

/* ED38 is after the 200-tick wait and before any active flag/drive write.
 * Original STRD is four bytes, no PC-relative or FPU relocation required. */
.global ph_after_wait_hook
.thumb_func
ph_after_wait_hook:
    ENTER
    ldrb r0,[r4,#4]
    CHECK r0
    LEAVE
    strd r0,r0,[r4,#28]
    b.w stock_after_wait_resume
9:  LEAVE
    b.w stock_start_return

/* ED46: prevent a fault after the post-wait check from being overwritten by
 * the subsequent motor active=1 write at ED4A. Both writes are indivisible
 * with respect to the failure publisher. */
.global ph_active_hook
.thumb_func
ph_active_hook:
    ENTER
    ldrb r0,[r4,#4]
    CHECK r0
    ldr r0,[sp,#8]
    strb r0,[r4,#52]
    strb r0,[r4,#6]
    LEAVE
    b.w stock_active_resume
9:  LEAVE
    b.w stock_start_return

/* ED76: power GPIO BSRR write, then displaced lane load. */
.global ph_enable_gpio_hook
.thumb_func
ph_enable_gpio_hook:
    ENTER
    ldrb r0,[r4,#4]
    CHECK r0
    ldr r1,[sp,#12]
    ldr r2,[sp,#16]
    str r1,[r2,#16]
9:  LEAVE
    ldrb r1,[r4,#4]
    b.w stock_gpio_resume

/* EE98: ORR plus TIM4 DIER write. Preserve flags from displaced ORRS.
 * The calculation executes after restoring APSR but IRQs remain disabled
 * until the optional effect is complete. Dedicated sequence below. */
.global ph_enable_timer_hook
.thumb_func
ph_enable_timer_hook:
    /* Save original r0/r1/r2/r3 and flags for the check. */
    ENTER
    ldrb r0,[r4,#4]
    CHECK r0
    ldr r0,[sp,#8]
    ldr r1,[r6]            /* Live DIER under lock, not stale stock EE96 read. */
    ldr r2,[sp]
    msr APSR_nzcvq,r2
    orrs r0,r1
    str r0,[r6]
    b 8f
9:  ldr r0,[sp,#8]
    ldr r1,[sp,#12]
    ldr r2,[sp]
    msr APSR_nzcvq,r2
    orrs r0,r1
8:  /* Both allowed/rejected paths reproduce original r0 and ORRS flags. */
    str r0,[sp,#8]
    mrs r0,APSR
    str r0,[sp]
    LEAVE
    b.w stock_timer_resume

/* EFA4: a nominal DISABLE can also resurrect sibling DIER bits if stock
 * EFA2's read precedes a higher-priority failure. Do the read/BIC/write as a
 * single masked operation, even when failure publishing runs in an IRQ. */
.global ph_disable_timer_hook
.thumb_func
ph_disable_timer_hook:
    ENTER
    ldr r0,[sp,#8]
    ldr r1,[sp,#12]
    ldr r2,[r1]
    bic r0,r2,r0
    str r0,[r1]
    str r0,[sp,#8]
    str r2,[sp,#16]
    LEAVE
    b.w stock_disable_timer_resume

/* 9D62: combine encoder DIER enable + encoder-active flag before any unlock.
 * Failure after this atomic commit clears both through the 9EB8 stop leaf.
 * Skip displaced 9D64..9D68, replay their r0/r2/NZ effects explicitly. */
.global ph_encoder_enable_hook
.thumb_func
ph_encoder_enable_hook:
    ENTER
    ldrb r0,[r4,#8]
    CHECK r0
    ldr r2,[sp,#16]
    ldr r0,[r2,#12]       /* Preserve unrelated DIER changes since 9D5A. */
    orr r0,r0,#1
    str r0,[r2,#12]
    movs r0,#1
    strb r0,[r4,#4]
    str r0,[sp,#8]
    ldr r2,[r4]
    str r2,[sp,#16]
    ldr r0,[sp]
    bic r0,r0,#0xc0000000 /* MOVS #1 clears NZ, keeps CVQ. */
    str r0,[sp]
    LEAVE
    b.w stock_encoder_resume
9:  LEAVE
    b.w stock_encoder_return

/* EECE: last ECE8 PWM write. Replace 16-bit STR and first half of VPOP
 * would split an instruction, so hook the preceding four-byte MOVT instead.
 * EECa loads ip? Actual site EECa: MOVT r2,#0xffff; resume EECE.
 * We reproduce MOVT then the following STR and jump over that STR. */
.global ph_start_pwm_hook
.thumb_func
ph_start_pwm_hook:
    movt r2,#0xffff
    ENTER
    ldrb r0,[r4,#4]
    CHECK r0
    ldr r0,[sp,#8]
    ldr r1,[sp,#12]
    ldr r2,[sp,#16]
    str r1,[r0,r2]
9:  LEAVE
    b.w stock_start_return

/* F012: same final PWM write in periodic PID control. */
.global ph_control_pwm_hook
.thumb_func
ph_control_pwm_hook:
    movt r2,#0xffff
    ENTER
    ldrb r0,[r4,#4]
    CHECK r0
    ldr r0,[sp,#8]
    ldr r1,[sp,#12]
    ldr r2,[sp,#16]
    str r1,[r0,r2]
9:  LEAVE
    b.w stock_control_resume

.global ph_irq_enter
.thumb_func
ph_irq_enter:
    mrs r0,PRIMASK
    cpsid i
    dmb
    bx lr
.global ph_irq_leave
.thumb_func
ph_irq_leave:
    dmb
    msr PRIMASK,r0
    bx lr

/* Call bounded stock leaves, skipping active/move-count gates. F088 through
 * F0C8 resets GPIO and writes inverted PWM=1000; 9EB8 disables encoder UIE.
 * This is stop effects applied, not a fabricated PL_STOP_PROVEN result. */
.global ph_stock_power_disable
.thumb_func
ph_stock_power_disable:
    push {r4,lr}
    mov r4,r0
    bl stock_power_disable
    /* Disable only this lane's TIM4 interrupt, preserving sibling lanes. */
    ldrb r0,[r4,#4]
    ldr r1,=0x080196d0
    add r1,r1,r0,lsl #4
    ldr r0,[r1,#20]
    ldr r1,=0x4000080c
    ldr r2,[r1]
    bic r2,r2,r0
    str r2,[r1]
    mvn r0,r0
    str r0,[r1,#4]        /* TIM4 SR W0C for this lane, hardware not emulated. */
    ldr r0,[r4,#56]
    bl stock_encoder_disable
    pop {r4,pc}

/* Deliberately unresolved production links, even with experimental macro. */
.section .binding_required,"a"
.word ph_verified_reserved_ram
.word ph_verified_exclusive_startup
.word ph_verified_owned_failure_source
.word ph_verified_all_motion_writers
.word ph_verified_controller_quiescence
