#include "event_mock.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static unsigned assertions;
#define CHECK(x) do {assert(x);++assertions;} while(0)
static void check_first32(uint32_t p) {
    unsigned char expected[32];memset(expected,0xa5,sizeof(expected));
    uint32_t zero=0,sentinel=UINT32_MAX,item=p+12u;
    memcpy(expected,&zero,4);memcpy(expected+4,&zero,4);memcpy(expected+8,&item,4);
    memcpy(expected+12,&sentinel,4);memcpy(expected+16,&item,4);memcpy(expected+20,&item,4);
    expected[28]=0;
    CHECK(!memcmp(ex_test_map(p,32),expected,32));
}
int main(void) {
    uint32_t needed=ex_storage_allocation_size(EX_STORAGE_PAYLOAD);
    CHECK(EX_STORAGE_PAYLOAD>=49 && needed>56);
    for(uint32_t extra=0;extra<=16;extra+=8) {
        mock_storage_reset();mock_extra=extra;
        uint32_t p=ex_event_factory();CHECK(p==EX_HEAP_FIRST_PAYLOAD);
        CHECK(mock_alloc_calls==1 && !mock_fallback_calls);
        check_first32(p);CHECK(mock_handle==0 && ex_storage_current()==0);
        CHECK(mock_barriers==1 && mock_last_store==p+44u && mock_last_value==EX_STORAGE_READY);
        mock_handle=p;struct ex_storage *b=ex_storage_current();CHECK(b!=0);
        CHECK(b->rf.phase==RF_UNKNOWN && b->rf.revoked==1 && !b->ex.initialized);
        CHECK(rf_platform_state()==&b->rf);
        unsigned writes=mock_stores;CHECK(ex_event_factory()==0 && mock_stores==writes);
        for(unsigned field=0;field<4;field++) {
            uint32_t a=p+32u+4u*field, saved=ex_test_load32(a);
            ex_test_store32(a,0);CHECK(ex_storage_current()==0);ex_test_store32(a,saved);
        }
        /* A free or corrupt header is rejected before any magic/tail read. */
        uint32_t good=ex_test_load32(p-4u);
        uint32_t corrupt[]={good&0x7fffffffu,0x80000000u|(needed-8u),
            0x80000000u|(needed+24u),0xffffffffu,0x80000001u};
        for(unsigned k=0;k<sizeof(corrupt)/sizeof(corrupt[0]);k++) {
            ex_test_store32(p-4u,corrupt[k]);mock_tail_reads=0;
            CHECK(ex_storage_current()==0 && !mock_tail_reads);
        }
        ex_test_store32(p-4u,good);ex_test_store32(p-8u,p);
        mock_tail_reads=0;CHECK(ex_storage_current()==0 && !mock_tail_reads);
    }
    /* Expanded allocation failure retains stock sync only. No extension data
     * is touched even if its unallocated bytes happen to look like a magic. */
    for(uint32_t extra=0;extra<=16;extra+=8) {
        mock_storage_reset();mock_expansion_fail=1;mock_extra=extra;
        uint32_t p=ex_event_factory();CHECK(p==EX_HEAP_FIRST_PAYLOAD);
        check_first32(p);CHECK(mock_alloc_calls==2 && mock_fallback_calls==1 && !mock_barriers);
        mock_handle=p;mock_tail_reads=0;
        CHECK(ex_storage_current()==0 && !mock_tail_reads && rf_platform_state()==0);
    }
    mock_storage_reset();mock_expansion_fail=mock_fallback_fail=1;
    CHECK(ex_event_factory()==0 && !mock_stores && !mock_barriers && mock_alloc_calls==2);
    mock_storage_reset();mock_raw_override=0x80000020u;
    CHECK(ex_event_factory()==0 && !mock_fallback_calls && !mock_tail_reads && !mock_barriers);
    for(uint32_t p=0;p<EX_HEAP_FIRST_PAYLOAD;p+=0x1000001u) {
        mock_storage_reset();mock_handle=p;CHECK(ex_storage_current()==0 && !mock_header_reads && !mock_tail_reads);
    }
    const uint32_t badptrs[]={1,EX_HEAP_FIRST_PAYLOAD-8u,EX_HEAP_FIRST_PAYLOAD+1u,
        EX_HEAP_END,EX_HEAP_END-8u,0xfffffff8u,0xffffffffu};
    for(unsigned i=0;i<sizeof(badptrs)/sizeof(badptrs[0]);i++) {
        mock_storage_reset();mock_handle=badptrs[i];
        CHECK(ex_storage_current()==0 && !mock_header_reads && !mock_tail_reads);
    }
    /* Every allowed allocation length and all uint32 overflow-tail inputs. */
    for(uint32_t n=0;n<=0x5800;n++) {
        uint32_t size=ex_storage_allocation_size(n);
        if(!n || n>0x57e8) CHECK(size==0);
        else CHECK(size>=n+8u && size<=n+15u && !(size&7u));
    }
    for(uint32_t n=0xffffffe0u;;n++) {CHECK(ex_storage_allocation_size(n)==0);if(n==UINT32_MAX)break;}
    for(uint32_t p=EX_HEAP_FIRST_PAYLOAD;p<=EX_HEAP_END-8u;p+=8u) {
        for(uint32_t extra=0;extra<=24;extra+=8u) {
            mock_storage_reset();mock_allocate_at=p;mock_handle=p;
            ex_test_store32(p-8u,0);ex_test_store32(p-4u,0x80000000u|(needed+extra));
            int want=extra<=16 && p<=EX_HEAP_END-(needed-8u) && needed+extra<=EX_HEAP_END-(p-8u);
            CHECK(ex_storage_header_valid(p,EX_STORAGE_PAYLOAD)==want);
        }
    }
    printf("PASS %u storage assertions; factory/header/accessor host doubles, not allocator/lifecycle proof.\n",assertions);
}
