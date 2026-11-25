#ifndef PRELOAD_HOOKS_H
#define PRELOAD_HOOKS_H
#include <stdint.h>
#if !defined(PRELOAD_INTEGRATION_EXPERIMENT) || PRELOAD_INTEGRATION_EXPERIMENT != 1
#error "Unbound preload integration: offline experiment only"
#endif
/* ABI: the first four words must remain lane fault latches for the assembly.
 * No actual firmware RAM address or startup claim is supplied by this module. */
struct ph_reader { uint32_t epoch, lane, owned; };
struct ph_storage {
    uint32_t blocked[4];
    struct ph_reader reader[2];
};
extern struct ph_storage ph_storage;
enum ph_result { PH_OK, PH_INVALID, PH_BUSY, PH_STALE, PH_APPLIED };
enum ph_failure { PH_AUTH_NEGATIVE=1, PH_TRANSPORT_UNKNOWN=2 };
uint32_t ph_irq_enter(void);
void ph_irq_leave(uint32_t);
/* Calls F088 power disable, clears this lane's TIM4 interrupt bits, and calls
 * 9EB8 encoder disable, independently of AF84 cleanup/slot state. Its return
 * does not prove all writers are retired, controller quiescence or rest. */
void ph_stock_power_disable(void *motor);
void ph_init_exclusive(void);
int ph_claim_reader(uint32_t reader, uint32_t lane, uint32_t epoch);
int ph_release_reader(uint32_t reader, uint32_t epoch);
int ph_publish_failure(uint32_t reader, uint32_t lane, uint32_t epoch,
                       enum ph_failure reason);
/* No rearm function: failure latch is sticky until a verified new boot.
 * Stock reader-state queue writes cannot clear it. */
#endif
