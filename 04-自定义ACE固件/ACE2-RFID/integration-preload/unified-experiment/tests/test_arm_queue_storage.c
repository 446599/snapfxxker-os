/* Literal ARM boundaries, independent of host pointer-width sizeof(ex_state). */
#include "event_mock.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static unsigned count;
#define CHECK(x) do {assert(x);++count;} while(0)
int main(void) {
    const uint32_t payload=640,required=648;
    CHECK(ex_storage_allocation_size(payload)==required);
    const uint32_t sizes[]={40,48,56,184,192,200,224,232,240,616,624,632,640,648,656,664,672};
    for(uint32_t p=EX_HEAP_FIRST_PAYLOAD;p<=EX_HEAP_END-8;p+=8) {
        for(unsigned i=0;i<sizeof(sizes)/sizeof(sizes[0]);i++) {
            uint32_t size=sizes[i];mock_storage_reset();mock_allocate_at=p;
            ex_test_store32(p-8,0);ex_test_store32(p-4,0x80000000u|size);
            int want=size>=required && size<=required+16 &&
                p<=EX_HEAP_END-(required-8) && size<=EX_HEAP_END-(p-8);
            CHECK(ex_storage_header_valid(p,payload)==want);
            CHECK(mock_tail_reads==0);
        }
    }
    mock_storage_reset();mock_handle=ex_event_factory();CHECK(mock_handle);
    struct ex_storage *s=ex_storage_current();CHECK(s);
    struct pq_storage zero={0};CHECK(!memcmp(&s->queue,&zero,sizeof(zero)));
    CHECK(s->rf.phase==RF_UNKNOWN && s->rf.revoked && !s->ex.owner_task);
    CHECK(mock_barriers==1 && mock_last_store==mock_handle+44 && mock_last_value==EX_STORAGE_READY);
    uint32_t old_magic[]={0x45585431,0x45585432,0x45585433,0x45585231,0x45585434};
    uint32_t old_ready[]={0x52445931,0x52445932,0x52445933,0x52445231,0x52445934};
    for(unsigned i=0;i<5;i++) {
        uint32_t magic=s->magic,ready=s->ready;
        s->magic=old_magic[i];CHECK(!ex_storage_current());s->magic=magic;
        s->ready=old_ready[i];CHECK(!ex_storage_current());s->ready=ready;
    }
    uint32_t bytes=s->bytes;
    for(unsigned i=0;i<3;i++) {const uint32_t old[]={144,184,576};s->bytes=old[i];CHECK(!ex_storage_current());}
    s->bytes=bytes;CHECK(ex_storage_current()==s);
    printf("PASS %u literal ARM640/648 storage/version/zero-state checks. No heap margin/lifetime proof.\n",count);
}
