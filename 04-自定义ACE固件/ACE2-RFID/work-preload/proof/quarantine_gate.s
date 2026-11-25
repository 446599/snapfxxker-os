.syntax unified
.thumb
.text
.global quarantine_gate
.thumb_func
quarantine_gate:
    b.n insert_return
    nop
