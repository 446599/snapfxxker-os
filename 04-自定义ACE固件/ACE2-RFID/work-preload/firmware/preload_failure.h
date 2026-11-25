/* OFFLINE candidate. This contract does not allocate device RAM or authorize motion. */
#ifndef PRELOAD_FAILURE_H
#define PRELOAD_FAILURE_H
#include <stdint.h>
#include "rf_owner.h"

enum pl_phase { PL_IDLE, PL_ACTIVE, PL_ABORTING, PL_STOPPED, PL_UNKNOWN };
enum pl_result { PL_OK, PL_BUSY, PL_STALE, PL_INVALID, PL_FAULT, PL_NEEDS_STOP };
enum pl_auth_result {
    PL_AUTH_OK, PL_AUTH_REJECTED, PL_AUTH_NOT_ATTEMPTED,
    PL_AUTH_TRANSPORT_UNKNOWN
};
enum pl_reason { PL_REASON_NONE, PL_REASON_AUTH_REJECTED, PL_REASON_TRANSPORT_UNKNOWN };
/* Each bit must be a real platform observation for this exact stop operation.
 * An RF_QUIESCENT result, slot state, elapsed time or successful STOP enqueue is
 * NEVER any of these proofs. Mechanical coast is outside this software model. */
enum pl_stop_proof {
    PL_DRIVE_DISABLED=1, PL_CONTROL_QUIESCENT=2, PL_NO_DELAYED_ENABLE=4,
    PL_STOP_PROVEN=7
};
enum pl_start_proof { PL_START_UNVERIFIED, PL_START_NO_LATE_WRITES };
struct pl_ticket { uint32_t boot, generation, slot; };
struct pl_start { struct pl_ticket ticket; uint32_t sequence; };
struct pl_auth {
    struct pl_ticket ticket;
    struct rf_operation rf;
    uint32_t sequence;
};
struct pl_stop { struct pl_ticket ticket; uint32_t sequence, motion_fence; };
struct pl_state {
    uint32_t boot, generation, slot, phase, reason;
    uint32_t sequence, start_sequence, auth_sequence, stop_sequence;
    uint32_t start_pending, auth_pending, stop_pending, motion_fence, stop_deadline;
    struct rf_operation auth_rf;
};
/* Metadata functions use rf_platform_enter/leave: no callback, wait or I/O
 * inside the shared IRQ-state-preserving lock. No function below calls STOP.
 * State is caller-owned, initialized only in verified exclusive startup. */
void pl_init_exclusive(struct pl_state *, uint32_t boot, uint32_t slot);
int pl_begin(struct pl_state *, struct pl_ticket *);
int pl_claim_start(struct pl_state *, const struct pl_ticket *, struct pl_start *);
int pl_retire_start(struct pl_state *, const struct pl_start *, enum pl_start_proof);
/* RF ownership and actual lane must first be validated at the effect-dispatch
 * fence by an adapter. No inference of lane from reader*2 is permitted. */
int pl_begin_auth(struct pl_state *, const struct pl_ticket *,
                  const struct rf_operation *, uint32_t actual_slot, struct pl_auth *);
int pl_complete_auth(struct pl_state *, const struct pl_auth *, enum pl_auth_result,
                     uint32_t now, uint32_t stop_budget);
int pl_claim_stop(struct pl_state *, const struct pl_ticket *, uint32_t now, struct pl_stop *);
int pl_complete_stop(struct pl_state *, const struct pl_stop *, uint32_t now, uint32_t proof);
int pl_poll(struct pl_state *, const struct pl_ticket *, uint32_t now);
int pl_finish_normal(struct pl_state *, const struct pl_ticket *, uint32_t proof);
/* Only after PL_STOPPED. An insertion edge/reader success/state9 cannot clear a
 * pending stop. Caller must observe the stock job retired and a fresh operation. */
int pl_release_stopped(struct pl_state *, const struct pl_ticket *);
#endif
