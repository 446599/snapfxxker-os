@ EXPERIMENTAL offline only: this output boundary cannot prove ownership/cleanup.
@ Reject, including UID and raw/native fallback. Never publish a success sentinel.
.syntax unified
.thumb
.cpu cortex-m3
.text
.global uid_stub
.thumb_func
uid_stub:
    b.w ex_raw_reject_stub
