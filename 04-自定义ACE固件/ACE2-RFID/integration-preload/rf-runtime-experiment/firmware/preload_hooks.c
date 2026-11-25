#include "preload_hooks.h"
#define MOTOR_BASE ((uintptr_t)0x20001014u)
#define MOTOR_STRIDE 64u
int ph_claim_reader(uint32_t reader,uint32_t lane,uint32_t epoch) {
    if(reader>1 || lane>3 || (lane>>1)!=reader || !epoch) return PH_INVALID;
    uint32_t k=ph_irq_enter();
    struct ph_storage *s=ph_platform_state();
    if(!s) { ph_irq_leave(k);return PH_INVALID; }
    struct ph_reader *r=&s->reader[reader];
    int result=PH_BUSY;
    if(!r->owned && !s->blocked[lane] && epoch>r->epoch) {
        r->epoch=epoch;r->lane=lane;r->owned=1;result=PH_OK;
    }
    ph_irq_leave(k);return result;
}
int ph_release_reader(uint32_t reader,uint32_t epoch) {
    if(reader>1 || !epoch) return PH_INVALID;
    uint32_t k=ph_irq_enter();
    struct ph_storage *s=ph_platform_state();
    if(!s) { ph_irq_leave(k);return PH_INVALID; }
    struct ph_reader *r=&s->reader[reader];
    int result=PH_STALE;
    if(r->owned && r->epoch==epoch) {r->owned=0;result=PH_OK;}
    ph_irq_leave(k);return result;
}
int ph_publish_failure(uint32_t reader,uint32_t lane,uint32_t epoch,enum ph_failure reason) {
    if(reader>1 || lane>3 || !epoch ||
       (reason!=PH_AUTH_NEGATIVE && reason!=PH_TRANSPORT_UNKNOWN)) return PH_INVALID;
    uint32_t k=ph_irq_enter();
    struct ph_storage *s=ph_platform_state();
    if(!s) { ph_irq_leave(k);return PH_INVALID; }
    struct ph_reader *r=&s->reader[reader];
    int result=PH_STALE;
    if(r->owned && r->lane==lane && r->epoch==epoch) {
        s->blocked[lane]=(uint32_t)reason;
        /* No waits, queues, stock state gate or cleanup call. Atomic against
         * the fenced GPIO/timer/PWM writes on a single-core Cortex-M. */
        ph_stock_power_disable((void *)(MOTOR_BASE+MOTOR_STRIDE*lane));
        result=PH_APPLIED;
    }
    ph_irq_leave(k);return result;
}
