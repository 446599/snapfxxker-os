#include "event_mock.h"
#include <assert.h>
#include <string.h>
#include <stddef.h>
#define FIRST_HEADER (EX_HEAP_FIRST_PAYLOAD-8u)
#define HEAP_BYTES (EX_HEAP_END-FIRST_HEADER)
static union { max_align_t align; uint8_t bytes[HEAP_BYTES]; } heap;
uint32_t mock_handle,mock_expansion_fail,mock_fallback_fail,mock_extra;
uint32_t mock_allocate_at,mock_raw_override,mock_next_override;
unsigned mock_alloc_calls,mock_fallback_calls,mock_header_reads,mock_tail_reads;
unsigned mock_map_calls,mock_stores,mock_barriers;
uint32_t mock_last_store,mock_last_value;
static void *range(uint32_t a,uint32_t n) {
    assert(a>=FIRST_HEADER && a<=EX_HEAP_END && n<=EX_HEAP_END-a);
    return heap.bytes+(a-FIRST_HEADER);
}
void mock_storage_reset(void) {
    memset(&heap,0xa5,sizeof(heap));
    mock_handle=mock_expansion_fail=mock_fallback_fail=mock_extra=0;
    mock_allocate_at=EX_HEAP_FIRST_PAYLOAD;mock_raw_override=mock_next_override=0;
    mock_alloc_calls=mock_fallback_calls=mock_header_reads=mock_tail_reads=0;
    mock_map_calls=mock_stores=mock_barriers=0;mock_last_store=mock_last_value=0;
}
uint32_t ex_test_load32(uint32_t a) {
    if(a==EX_HANDLE_ADDRESS) return mock_handle;
    if(a==mock_allocate_at-8u || a==mock_allocate_at-4u) ++mock_header_reads;
    if(a>=mock_allocate_at+32u) ++mock_tail_reads;
    uint32_t value;memcpy(&value,range(a,4),4);return value;
}
void ex_test_store32(uint32_t a,uint32_t v) {
    ++mock_stores;mock_last_store=a;mock_last_value=v;
    if(a==EX_HANDLE_ADDRESS) {mock_handle=v;return;}
    memcpy(range(a,4),&v,4);
}
void ex_test_store8(uint32_t a,uint8_t v) {++mock_stores;memcpy(range(a,1),&v,1);}
void *ex_test_map(uint32_t a,uint32_t n) {++mock_map_calls;return range(a,n);}
void ex_test_publish_barrier(void) {++mock_barriers;}
uint32_t ex_stock_malloc(uint32_t n) {
    ++mock_alloc_calls;
    if((n!=32 && mock_expansion_fail) || (n==32 && mock_fallback_fail)) return 0;
    uint32_t p=mock_allocate_at;
    uint32_t size=mock_raw_override ? mock_raw_override :
        (ex_storage_allocation_size(n)+mock_extra)|0x80000000u;
    ex_test_store32(p-8u,mock_next_override);ex_test_store32(p-4u,size);
    return p;
}
uint32_t ex_stock_event_factory(void) {
    ++mock_fallback_calls;
    uint32_t p=ex_stock_malloc(32);if(!p) return 0;
    ex_test_store32(p+12u,UINT32_MAX);ex_test_store32(p+16u,p+12u);ex_test_store32(p+20u,p+12u);
    ex_test_store32(p,0);ex_test_store32(p+4u,0);ex_test_store32(p+8u,p+12u);ex_test_store8(p+28u,0);
    return p;
}
