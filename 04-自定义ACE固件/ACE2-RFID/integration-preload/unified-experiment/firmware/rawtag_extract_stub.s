@ EXPERIMENTAL offline only: reject cache publication and stock parse fallback.
@ No memcpy: original source slot contains only 140 valid bytes, not 144.
.syntax unified
.thumb
.cpu cortex-m3
.text
.global rawtag_extract_stub
.thumb_func
rawtag_extract_stub:
    bl ex_stop_request
    movs r6,#0
    b.w scan_fail
