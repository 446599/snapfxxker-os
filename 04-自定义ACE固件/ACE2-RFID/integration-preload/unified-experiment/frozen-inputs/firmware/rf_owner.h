/* Offline integration candidate. No fixed RAM address, device or transport ABI. */
#ifndef RF_OWNER_H
#define RF_OWNER_H
#include <stdint.h>
#include <stddef.h>

enum rf_owner { RF_NONE, RF_BACKGROUND, RF_CMD68, RF_HOST, RF_CONTROL, RF_CACHE };
enum rf_phase { RF_IDLE, RF_ACTIVE, RF_DRAINING, RF_CLEANING, RF_UNKNOWN };
enum rf_result { RF_OK, RF_BUSY, RF_STALE, RF_INVALID, RF_FAULT };
enum rf_proof { RF_UNVERIFIED, RF_QUIESCENT };
enum rf_clean { RF_STOP_ENGINE, RF_CLEAR_CRYPTO, RF_FLUSH_FIFO_EVENTS,
                RF_RESTORE_CONFIG, RF_RESTORE_ANTENNA, RF_VERIFY_BASELINE, RF_CLEAN_COUNT };
struct rf_ticket { uint32_t boot, generation, connection, session; };
struct rf_operation { struct rf_ticket ticket; uint32_t sequence; };
struct rf_cleanup { struct rf_ticket ticket; uint32_t sequence, step; };
struct rf_state {
    uint32_t boot, generation, sequence, deadline, hard_deadline;
    uint32_t connection, session, owner, slot, phase;
    uint32_t in_flight, cleaning_claimed, cleanup_step, revoked, finished;
};
/* Implementations serialize metadata only, restore prior IRQ state, cannot wait.
 * Hardware, scheduler, serial, DMA and cleanup callbacks NEVER run under this lock.
 * These are external symbols, not guesses at a stock RTOS mutex. */
uint32_t rf_platform_enter(void);
void rf_platform_leave(uint32_t key);
/* Call once in verified exclusive startup AFTER reader/async work quiescence.
 * boot must never repeat while stale transport work can survive. Not a recovery API. */
void rf_init_exclusive(struct rf_state *, uint32_t boot);
int rf_acquire(struct rf_state *, uint32_t owner, uint32_t slot,
               uint32_t connection, uint32_t session, uint32_t now,
               uint32_t lease_ticks, uint32_t hard_ticks, struct rf_ticket *);
int rf_begin_operation(struct rf_state *, const struct rf_ticket *, uint32_t now,
                       struct rf_operation *);
int rf_complete_operation(struct rf_state *, const struct rf_operation *,
                          uint32_t now, enum rf_proof);
int rf_finish(struct rf_state *, const struct rf_ticket *, uint32_t now);
int rf_cancel(struct rf_state *, const struct rf_ticket *);
int rf_renew(struct rf_state *, const struct rf_ticket *, uint32_t now, uint32_t ticks);
int rf_disconnect(struct rf_state *, uint32_t connection);
void rf_expire(struct rf_state *, uint32_t now);
int rf_claim_cleanup(struct rf_state *, struct rf_cleanup *);
/* Transaction-scoped alternative for an adapter. Never claims a later owner's
 * cleanup after this ticket has completed or been replaced. */
int rf_claim_cleanup_for(struct rf_state *, const struct rf_ticket *, struct rf_cleanup *);
/* Exact checked cleanup stages only. 'verified' is NOT a stock function return.
 * Last stage atomically releases owner and writes success only for normal finish.
 * The platform must implement each proof, including absence of delayed effects. */
int rf_complete_cleanup(struct rf_state *, const struct rf_cleanup *, uint32_t now, int verified,
                        int *transaction_success);
#endif
