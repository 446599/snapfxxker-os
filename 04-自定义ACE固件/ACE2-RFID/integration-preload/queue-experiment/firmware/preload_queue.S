#if !defined(PRELOAD_QUEUE_EXPERIMENT) || PRELOAD_QUEUE_EXPERIMENT != 1
#error "Offline queue-association experiment only"
#endif
.syntax unified
.thumb
.text
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
.global pq_job_link_hook
.thumb_func
pq_job_link_hook:
 ENTER
 mov r0,r4
 bl pq_job_enqueued
 ldr r1,[sp,#8]
 ldr r0,[sp,#24]
 strb r0,[r1]
 LEAVE
 b.w pq_stock_job_link_resume
.global pq_job_unlink_hook
.thumb_func
pq_job_unlink_hook:
 ENTER
 mov r0,r4
 bl pq_job_unlinked
 ldr r0,[sp,#12]
 ldr r1,[sp,#20]
 strb r0,[r1]
 LEAVE
 cmp.w lr,#5
 b.w pq_stock_job_unlink_resume

.macro SEND name,source,preload=0
.global \name
.thumb_func
\name:
 push {r4,lr}
 sub sp,#8
 .if \preload
 str r7,[sp]
 str r11,[sp,#4]
 .else
 mov.w r3,#-1
 str r3,[sp]
 str r3,[sp,#4]
 .endif
 movs r3,#\source
 bl pq_send
 add sp,#8
 pop {r4,pc}
.endm
SEND pq_send_preload,1,1
SEND pq_send_cleanup,2
SEND pq_send_requeue,3
SEND pq_send_control,4

.global pq_preload_requeue_hook
.thumb_func
pq_preload_requeue_hook:
 mov r1,r11
 b.w pq_preload_requeue

.global pq_stock_send
.thumb_func
pq_stock_send:
 push {r4,lr}
 bl pq_stock_send_raw
.global pq_stock_send_return
pq_stock_send_return:
 pop {r4,pc}

/* The task send frame at 16630 saves its caller LR at SP+44. Read that slot
 * ONLY for its actual 167F6 call. Other/ISR callers never read a guessed frame. */
.global pq_enqueue_hook
.thumb_func
pq_enqueue_hook:
 mov r2,lr
 ldr r3,=0x080167fb
 cmp r2,r3
 bne 1f
 ldr r3,[sp,#44]
 b.w pq_enqueue_entry
1:
 movs r3,#0
 b.w pq_enqueue_entry

.global pq_enqueue_original
.thumb_func
pq_enqueue_original:
 push.w {r4-r8,lr}
 b.w pq_stock_enqueue_resume

/* Replace stock memcpy call AND retire its following count update atomically
 * for RF queues. Returns to 16B42, not the displaced count-write sequence. */
.global pq_receive_hook
.thumb_func
pq_receive_hook:
 push {r4,lr}
 sub sp,#8
 str r10,[sp]
 mov r3,r4
 bl pq_receive_copy
 add sp,#8
 pop {r4,lr}
 b.w pq_stock_receive_resume
