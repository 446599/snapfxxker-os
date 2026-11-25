#include "preload_hooks.h"
#define MOTOR_BASE ((uintptr_t)0x20001014u)
#define MOTOR_STRIDE 64u
void ph_init_exclusive(void) {
    /* May only be called with exclusive startup ownership. NOT hooked to an
     * arbitrary existing task entry or inferred from zero-filled RAM. */
    for (unsigned i=0;i<4;++i) ph_storage.blocked[i]=0;
    for (unsigned i=0;i<2;++i) {
        ph_storage.reader[i].epoch=0;
        ph_storage.reader[i].lane=0xffffffffu;
        ph_storage.reader[i].owned=0;
    }
}
int ph_claim_reader(uint32_t reader,uint32_t lane,uint32_t epoch) {
    if(reader>1 || lane>3 || (lane>>1)!=reader || !epoch) return PH_INVALID;
    uint32_t k=ph_irq_enter();
    struct ph_reader *r=&ph_storage.reader[reader];
    int result=PH_BUSY;
    if(!r->owned && !ph_storage.blocked[lane] && epoch>r->epoch) {
        r->epoch=epoch;r->lane=lane;r->owned=1;result=PH_OK;
    }
    ph_irq_leave(k);return result;
}
int ph_release_reader(uint32_t reader,uint32_t epoch) {
    if(reader>1 || !epoch) return PH_INVALID;
    uint32_t k=ph_irq_enter();
    struct ph_reader *r=&ph_storage.reader[reader];
    int result=PH_STALE;
    if(r->owned && r->epoch==epoch) {r->owned=0;result=PH_OK;}
    ph_irq_leave(k);return result;
}
int ph_publish_failure(uint32_t reader,uint32_t lane,uint32_t epoch,enum ph_failure reason) {
    if(reader>1 || lane>3 || !epoch ||
       (reason!=PH_AUTH_NEGATIVE && reason!=PH_TRANSPORT_UNKNOWN)) return PH_INVALID;
    uint32_t k=ph_irq_enter();
    struct ph_reader *r=&ph_storage.reader[reader];
    int result=PH_STALE;
    if(r->owned && r->lane==lane && r->epoch==epoch) {
        ph_storage.blocked[lane]=(uint32_t)reason;
        /* No waits, queues, stock state gate or cleanup call. Atomic against
         * the fenced GPIO/timer/PWM writes on a single-core Cortex-M. */
        ph_stock_power_disable((void *)(MOTOR_BASE+MOTOR_STRIDE*lane));
        result=PH_APPLIED;
    }
    ph_irq_leave(k);return result;
}
