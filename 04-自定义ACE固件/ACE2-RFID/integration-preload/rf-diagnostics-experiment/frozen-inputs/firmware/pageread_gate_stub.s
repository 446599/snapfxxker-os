@ EXPERIMENTAL offline only: no partial-page or stale-page success publication.
.syntax unified
.thumb
.cpu cortex-m3
.text
.global pageread_gate_stub
.thumb_func
pageread_gate_stub:
    bl ex_stop_request
    mov.w r10,#0
    b.w pageread_fail
