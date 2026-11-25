#if !defined(RF_ADAPTER_OFFLINE_TEST) || RF_ADAPTER_OFFLINE_TEST != 1
#error "Research trampolines only: compile with OFFLINE_TEST; do not install"
#endif
.syntax unified
.thumb
.text
/* These replay ONLY whole prologue instructions replaced by B.W. No PC-relative
 * instructions are relocated. Resume addresses are from exact stock 1.1.31. */
.global rf_background_original
.thumb_func
rf_background_original:
    push.w {r4-r11,lr}
    b.w stock_background_resume
.global rf_cmd68_original
.thumb_func
rf_cmd68_original:
    push.w {r4-r10,lr}
    b.w stock_cmd68_resume
.global rf_cmd13_original
.thumb_func
rf_cmd13_original:
    push.w {r4-r9,lr}
    b.w stock_cmd13_resume
.global rf_cmd14_original
.thumb_func
rf_cmd14_original:
    push {r4,lr}
    sub sp,#8
    b.w stock_cmd14_resume
.global rf_requeue_original
.thumb_func
rf_requeue_original:
    push {r7,lr}
    sub sp,#8
    b.w stock_requeue_resume
/* Candidate metadata critical section: save/restore existing PRIMASK, no loops,
 * no stock locks. Hardware operations never occur inside these functions. */
.global rf_platform_enter
.thumb_func
rf_platform_enter:
    mrs r0, PRIMASK
    cpsid i
    dmb
    bx lr
.global rf_platform_leave
.thumb_func
rf_platform_leave:
    dmb
    msr PRIMASK,r0
    bx lr
