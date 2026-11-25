#ifndef RF_STOCK_ADAPTER_H
#define RF_STOCK_ADAPTER_H
#if !defined(RF_ADAPTER_OFFLINE_TEST) || RF_ADAPTER_OFFLINE_TEST != 1
#error "Unintegrated RF adapter: requires RF_ADAPTER_OFFLINE_TEST=1; not firmware"
#endif
#include "rf_owner.h"
/* Simulation/ABI research ONLY. The wrappers below are NOT drop-in hooks:
 * - E244 does not cover EA14's other cache writes.
 * - E70C contention loses a control request; CMD14/E70C can wait forever on
 *   a full queue whose consumer is E244, causing owner/consumer deadlock.
 * - the legacy raw tunnel is rejected, not made compatible.
 * Defining RF_ADAPTER_OFFLINE_TEST does not resolve any of these defects. */
/* Deliberately UNDEFINED in the candidate. No guessed RAM reservation. */
#ifdef ACE_EXPERIMENTAL_OFFLINE
struct rf_state *rf_platform_state(void);
int rf_platform_bind_operation(const struct rf_operation *,uint32_t owner,uint32_t slot);
#else
extern struct rf_state rf_device_state;
#endif
/* Deliberately UNDEFINED platform proofs. All must be bounded and called with
 * interrupts enabled. A function's stock return value is not quiescence proof. */
uint32_t rf_platform_now(void);
int rf_platform_ready(void);
uint32_t rf_platform_budget(uint32_t owner);
/* Resolve a queued event's actual lane, not reader_index*2. A mutable global
 * read after dequeue is not sufficient provenance. Missing binding rejects. */
int rf_platform_background_lane(uint32_t reader,uint32_t *lane);
enum rf_proof rf_platform_quiescence(const struct rf_operation *);
int rf_platform_cleanup(const struct rf_cleanup *);
void rf_platform_bad_outcome(uint32_t owner); /* cannot wait or issue unowned RF */
void rf_background_original(uint32_t index);
int rf_cmd68_original(uint32_t, const uint32_t *, uint8_t *);
int rf_cmd13_original(uint32_t, const uint32_t *, uint8_t *);
int rf_cmd14_original(uint32_t, const uint32_t *, uint8_t *);
void rf_requeue_original(uint32_t slot);
void rf_background_entry(uint32_t index);
int rf_cmd68_entry(uint32_t, const uint32_t *, uint8_t *);
int rf_cmd13_entry(uint32_t, const uint32_t *, uint8_t *);
int rf_cmd14_entry(uint32_t, const uint32_t *, uint8_t *);
void rf_requeue_entry(uint32_t slot);
#endif
