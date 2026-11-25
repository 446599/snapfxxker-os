/* SPDX-License-Identifier: MIT */
.syntax unified
.thumb
.text
.global ip_registration_hook
.thumb_func
ip_registration_hook:
    push {r4,lr}
    bl ip_stock_register
    bl ip_register
    pop {r4,pc}

.global ip_insert_hook
.thumb_func
ip_insert_hook:
    push {r0-r4,lr}
    bl ip_insert
    cmp r0,#0
    beq 1f
    pop {r0-r4,pc}
1:  pop {r0-r4,lr}
    push {r4-r7,lr}
    sub sp,#4
    b.w ip_insert_resume

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

.global ip_active_hook
.thumb_func
ip_active_hook:
    ENTER
    mov r0,r4
    bl ip_motor_allowed
    cmp r0,#0
    beq 9f
    ldr r0,[sp,#8]
    strb r0,[r4,#52]
    strb r0,[r4,#6]
    LEAVE
    b.w ip_stock_active_resume
9:  LEAVE
    b.w ip_stock_start_return

.global ip_gpio_hook
.thumb_func
ip_gpio_hook:
    ENTER
    mov r0,r4
    bl ip_motor_allowed
    cmp r0,#0
    beq 9f
    ldr r1,[sp,#12]
    ldr r2,[sp,#16]
    str r1,[r2,#16]
    LEAVE
    ldrb r1,[r4,#4]
    b.w ip_stock_gpio_resume
9:  LEAVE
    b.w ip_stock_start_return

.global ip_start_pwm_hook
.thumb_func
ip_start_pwm_hook:
    movt r2,#0xffff
    ENTER
    mov r0,r4
    bl ip_motor_allowed
    cmp r0,#0
    beq 9f
    ldr r0,[sp,#8]
    ldr r1,[sp,#12]
    ldr r2,[sp,#16]
    str r1,[r0,r2]
9:  LEAVE
    b.w ip_stock_start_return

.global ip_control_pwm_hook
.thumb_func
ip_control_pwm_hook:
    movt r2,#0xffff
    ENTER
    mov r0,r4
    bl ip_motor_allowed
    cmp r0,#0
    beq 9f
    ldr r0,[sp,#8]
    ldr r1,[sp,#12]
    ldr r2,[sp,#16]
    str r1,[r0,r2]
9:  LEAVE
    b.w ip_stock_control_resume


/* Reproduce native store and invalidate in the same masked region. */
.global ip_status_hook
.thumb_func
ip_status_hook:
    ENTER
    ldr r0,[sp,#8]
    adds r0,#4
    bl ip_status_changed
    ldr r0,[sp,#8]
    ldr r1,[sp,#12]
    str r1,[r0,#4]
    LEAVE
    b.w ip_status_resume

.global ip_feed_hook
.thumb_func
ip_feed_hook:
    ENTER
    ldr r0,[sp,#12]
    ldr r0,[r0]
    bl ip_new_feed
    LEAVE
    push {r4,r5,r7,lr}
    sub sp,#8
    b.w ip_feed_resume
