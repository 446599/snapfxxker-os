#if !defined(ACE_EXPERIMENTAL_OFFLINE) || ACE_EXPERIMENTAL_OFFLINE != 1
#error "EXPERIMENTAL DO NOT FLASH: offline-only code"
#endif
.syntax unified
.thumb
.cpu cortex-m3
.section .text.experiment_gate,"ax",%progbits
.global experiment_reset_gate
.thumb_func
experiment_reset_gate:
    /* NON-CONFIGURABLE test image boot barrier. No stack, RAM, peripheral,
     * original startup or scheduler access. This is NOT an emergency stop. */
    b.n experiment_reset_gate
.global br_platform_enter
.thumb_func
br_platform_enter:
    b.w rf_platform_enter
.global br_platform_leave
.thumb_func
br_platform_leave:
    b.w rf_platform_leave
.global ex_raw_reject_stub
.thumb_func
ex_raw_reject_stub:
    bl ex_stop_request
    movs r0,#1
    b.w epilogue
.global ex_event_factory_entry
.thumb_func
ex_event_factory_entry:
    push {r4,lr}
    bl ex_event_factory
    cbz r0,.Lfactory_failed
    pop {r4,pc}
.Lfactory_failed:
    pop {r4,lr}
    b.w experiment_reset_gate
