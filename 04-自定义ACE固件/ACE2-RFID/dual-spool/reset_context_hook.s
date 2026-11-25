@ Offline candidate only; link address in tests is emulator-only.
@ Hook 0x0800FE1A (vstr s0,[r2]), resume at 0x0800FE1E.
@ Native FDFC arrives here only when selecting a new identity context.
@ r4 == r2 == ctx, ctx+4 already set NaN; s0 is the new reference.
.syntax unified
.thumb
.fpu fpv4-sp-d16
.text
.global reset_context_hook
.thumb_func
reset_context_hook:
    push.w {r0-r3, r12, lr}
    mrs r12, APSR
    mrs r3, PRIMASK
    cpsid i
    movw r0, #0x2200
    movt r0, #0x48a1            @ original stock default cap = 330000.0f
    str r0, [r4, #8]
    movs r0, #0
    str r0, [r4, #12]
    str r0, [r4, #16]
    movw r1, #0
    movt r1, #0x7fc0            @ stock qNaN sentinel
    add.w r2, r4, #20
    movs r0, #10
1:  str r1, [r2], #4
    subs r0, #1
    bne 1b
    vstr s0, [r4]               @ displaced stock instruction; r4 == r2
    msr PRIMASK, r3
    msr APSR_nzcvq, r12
    pop.w {r0-r3, r12, lr}
    b.w resume_fdfc             @ 0x0800FE1E
