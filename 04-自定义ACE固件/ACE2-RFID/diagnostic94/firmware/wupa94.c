/* Offline-only adapter for private diagnostic request 11. No static SRAM. */
#include "bounded_rc522.h"
#define UNKNOWN 0xafffffffu
static uint32_t load32(uintptr_t p) { return *(volatile uint32_t *)p; }
uint32_t br_platform_enter(void) { uint32_t p; __asm__ volatile("mrs %0, primask\ncpsid i":"=r"(p)::"memory"); return p; }
void br_platform_leave(uint32_t p) { __asm__ volatile("msr primask, %0"::"r"(p):"memory"); }
int br_platform_commit_valid(const struct br_transaction *t) {
    (void)t;
    if (*(volatile uint8_t *)0x20001600u != 8 || *(volatile uint8_t *)0x20001601u != 8) return 0;
    for(unsigned i=0;i<4;i++) if(load32(0x20001004u+4u*i)!=0) return 0;
    return 1;
}
uint32_t wupa94(void *ctx) {
    uintptr_t c=(uintptr_t)ctx;
    if(c<0x20000000u || c>0x2001fff8u) return UNKNOWN;
    const struct br_reader *reader=*(const struct br_reader **)(c+4u);
    uintptr_t p=(uintptr_t)reader;
    if(p<0x20000000u || p>0x2001ffccu) return UNKNOWN;
    struct br_state state={0,0};
    struct br_transaction t={&state,reader,20000u,0,0};
    struct br_wupa_trace trace={0,0,0,0};
    uint32_t k=br_platform_enter();int valid=br_platform_commit_valid(&t);br_platform_leave(k);
    if(!valid) return UNKNOWN;
    if (reader->spi != 0x40003800u || reader->power_port != 0x40011400u ||
        (reader->power_mask != 0x1000u && reader->power_mask != 0x2000u) ||
        reader->aux_port != 0x40010c00u || reader->aux_mask != 0x2000u ||
        !((reader->cs_port == 0x40010c00u && reader->cs_mask == 0x1000u) ||
          (reader->cs_port == 0x40011400u && reader->cs_mask == 0x0400u))) return UNKNOWN;
    volatile uint32_t *power=(volatile uint32_t *)reader->power_port;
    uint32_t was_on=power[3] & reader->power_mask;
    power[4]=reader->power_mask;
    for(volatile uint32_t n=0;n<240000u;n++) __asm__ volatile("nop");
    enum br_result result=br_wupa94(&t,&trace);
    if(!was_on) power[5]=reader->power_mask;
    if((power[3]&reader->power_mask)!=was_on || result!=BR_OK) return UNKNOWN;
    /* 0xA v1 + kind + raw IRQ/Error/bit count, including unknown kind 0. */
    return 0xa0000000u | ((uint32_t)trace.kind<<24) |
           ((uint32_t)trace.irq<<16) | ((uint32_t)trace.error<<8) | trace.bits;
}
