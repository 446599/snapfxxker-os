/* Independent review: use literal ARM v2 payload=216, not host sizeof. */
#include "event_mock.h"
#include <assert.h>
#include <stdio.h>
#include <stdint.h>
static unsigned checks;
int main(void) {
    const uint32_t payload=216, d=224;
    assert(ex_storage_allocation_size(payload)==d);
    const uint32_t sizes[]={0,32,40,48,56,176,184,192,200,216,224,232,240,248,256,0x7ffffff8u};
    for(uint32_t p=EX_HEAP_FIRST_PAYLOAD;p<=EX_HEAP_END-8;p+=8) {
        for(unsigned i=0;i<sizeof(sizes)/sizeof(sizes[0]);i++) {
            for(unsigned allocated=0;allocated<2;allocated++) {
                mock_storage_reset();mock_allocate_at=p;
                ex_test_store32(p-8,0);ex_test_store32(p-4,sizes[i]|(allocated?0x80000000u:0));
                uint64_t begin=(uint64_t)p-8, end=begin+sizes[i];
                int good=allocated && sizes[i]>=d && sizes[i]<=d+16 && !(sizes[i]&7) &&
                    (uint64_t)p+payload<=EX_HEAP_END && end<=EX_HEAP_END;
                assert(ex_storage_header_valid(p,payload)==good);++checks;
            }
        }
    }
    for(unsigned i=0;i<3;i++) {
        mock_storage_reset();uint32_t p=EX_HEAP_END-(224+8*i)+8;
        mock_allocate_at=p;ex_test_store32(p-8,0);ex_test_store32(p-4,0x80000000u|(224u+8*i));
        assert(ex_storage_header_valid(p,payload));++checks;
        mock_allocate_at=p+8;ex_test_store32(p,0);ex_test_store32(p+4,0x80000000u|(224u+8*i));
        assert(!ex_storage_header_valid(p+8,payload));++checks;
    }
    printf("PASS %u independent literal ARM216/224 header checks; no allocator/lifecycle proof.\n",checks);
}
