#include "experimental_binding.h"
#if defined(__arm__) || defined(__thumb__)
_Static_assert(sizeof(struct ex_storage)==144,"ARM tail layout changed");
_Static_assert(EX_STORAGE_PAYLOAD==176,"ARM extended allocation request");
#endif
_Static_assert(EX_STORAGE_PAYLOAD>=49 && EX_STORAGE_PAYLOAD<=0x57e8,"bounded payload size");

/* These two names bind to the EXACT checked stock image, never fake success. */
extern uint32_t ex_stock_malloc(uint32_t);
extern uint32_t ex_stock_event_factory(void);
#ifdef EX_STORAGE_HOST_TEST
extern uint32_t ex_test_load32(uint32_t);
extern void ex_test_store32(uint32_t,uint32_t);
extern void ex_test_store8(uint32_t,uint8_t);
extern void *ex_test_map(uint32_t,uint32_t);
extern void ex_test_publish_barrier(void);
#define rd(a) ex_test_load32(a)
#define wr(a,v) ex_test_store32((a),(v))
#define wr8(a,v) ex_test_store8((a),(v))
#define mapped(a,n) ex_test_map((a),(n))
#define publication_barrier() ex_test_publish_barrier()
#else
static uint32_t rd(uint32_t a) { return *(const volatile uint32_t *)(uintptr_t)a; }
static void wr(uint32_t a,uint32_t v) { *(volatile uint32_t *)(uintptr_t)a=v; }
static void wr8(uint32_t a,uint8_t v) { *(volatile uint8_t *)(uintptr_t)a=v; }
static void *mapped(uint32_t a,uint32_t n) { (void)n; return (void *)(uintptr_t)a; }
static void publication_barrier(void) { __asm__ volatile("dmb" ::: "memory"); }
#endif

uint32_t ex_storage_allocation_size(uint32_t n) {
    /* Bound BEFORE n+8 or rounding. Stock malloc otherwise wraps near UINT_MAX. */
    if(!n || n>0x57e8u) return 0;
    return (n+8u+7u)&~7u;
}
int ex_storage_header_valid(uint32_t p,uint32_t payload) {
    uint32_t required=ex_storage_allocation_size(payload);
    if(!required || (p&7u) || p<EX_HEAP_FIRST_PAYLOAD ||
       p>EX_HEAP_END-(required-8u)) return 0;
    /* No tail/magic reads before all header checks. Subtractions cannot wrap
     * because the address was constrained to this specific stock heap first. */
    uint32_t next=rd(p-8u), raw=rd(p-4u), size=raw&0x7fffffffu;
    if(next || !(raw&0x80000000u) || (size&7u) || size<required ||
       size-required>16u || size>EX_HEAP_END-(p-8u)) return 0;
    return 1;
}
struct ex_storage *ex_storage_current(void) {
    uint32_t p=rd(EX_HANDLE_ADDRESS);
    if(!ex_storage_header_valid(p,EX_STORAGE_PAYLOAD)) return 0;
    uint32_t tail=p+32u;
    if(rd(tail)!=EX_STORAGE_MAGIC || rd(tail+4u)!=sizeof(struct ex_storage) ||
       rd(tail+8u)!=p || rd(tail+12u)!=EX_STORAGE_READY ||
       rd(EX_HANDLE_ADDRESS)!=p) return 0;
    return mapped(tail,sizeof(struct ex_storage));
}
struct rf_state *rf_platform_state(void) {
    struct ex_storage *b=ex_storage_current();
    return b ? &b->rf : 0;
}
uint32_t ex_event_factory(void) {
    /* Do not rebuild an existing handle and erase its fault/owner generation.
     * Real teardown/free/reuse and reboot uniqueness are still not certified. */
    if(rd(EX_HANDLE_ADDRESS)) return 0;
    uint32_t p=ex_stock_malloc(EX_STORAGE_PAYLOAD);
    if(!p) {
        /* Keep original synchronization alive if only extra-state allocation
         * failed. The small header can NEVER pass the extension accessor. */
        p=ex_stock_event_factory();
        return ex_storage_header_valid(p,32) ? p : 0;
    }
    if(!ex_storage_header_valid(p,EX_STORAGE_PAYLOAD)) return 0;
    /* Replay exactly the stock factory's initialized fields. Untouched padding
     * (24..27 and 29..31) stays untouched, preserving the first-32-byte ABI. */
    wr(p+12u,UINT32_MAX);wr(p+16u,p+12u);wr(p+20u,p+12u);
    wr(p,0);wr(p+4u,0);wr(p+8u,p+12u);wr8(p+28u,0);
    uint32_t tail=p+32u;
    for(uint32_t i=0;i<sizeof(struct ex_storage);i+=4u) wr(tail+i,0);
    wr(tail,EX_STORAGE_MAGIC);wr(tail+4u,sizeof(struct ex_storage));wr(tail+8u,p);
    /* No successful RF boot: retain unknown/revoked until a separate, offline
     * test fixture explicitly initializes an operation. ready is allocation-only. */
    wr(tail+(uint32_t)offsetof(struct ex_storage,rf)+(uint32_t)offsetof(struct rf_state,phase),RF_UNKNOWN);
    wr(tail+(uint32_t)offsetof(struct ex_storage,rf)+(uint32_t)offsetof(struct rf_state,revoked),1);
    publication_barrier();
    wr(tail+12u,EX_STORAGE_READY); /* LAST store before original publishes handle. */
    return p;
}
