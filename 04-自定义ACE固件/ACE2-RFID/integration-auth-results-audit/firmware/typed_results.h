#ifndef TYPED_RESULTS_H
#define TYPED_RESULTS_H
#if !defined(AUTH_RESULTS_OFFLINE_TEST) || AUTH_RESULTS_OFFLINE_TEST != 1
#error "Typed AUTH research only: no production entry or owned SELECT binding"
#endif
#include "bounded_rc522.h"

enum ar_stage { AR_STAGE_SELECT, AR_STAGE_SETUP, AR_STAGE_AUTH, AR_STAGE_READ };
enum ar_code {
    AR_INVALID, AR_SELECT_NOT_OK, AR_UID_MISMATCH, AR_CANCELLED,
    AR_TRANSPORT_UNKNOWN, AR_TRANSPORT_TIMEOUT, AR_NO_REPLY,
    AR_READER_ERROR, AR_INVALID_SNAPSHOT, AR_AUTH_POLL_LIMIT,
    AR_AUTH_NOT_ESTABLISHED, AR_AUTH_STATUS_ACCEPTED, AR_BLOCK_READ,
    AR_READ_INVALID,
    /* RESERVED: no implemented observation proves this classification. */
    AR_AUTH_NEGATIVE
};
struct ar_selection {
    int raw_result;
    uint8_t expected_uid[5], selected_uid[5]; /* single-size UID plus BCC */
};
struct ar_result {
    enum ar_stage stage;
    enum ar_code code;
    enum br_result transport;
    uint32_t transport_status_valid; /* zero: no BR result to interpret */
    int select_result;
    uint32_t status_polls, snapshot_valid, auth_command_attempted, auth_command_issued;
    uint8_t status2, irq, error;
};
/* These are NOT production authorization tokens. A caller must independently
 * establish fresh SELECT, real operation ownership, configuration and cleanup.
 * No slot, preload publisher, stock hook or device entry uses these routines.
 * AUTH_STATUS_ACCEPTED means only checked IdleIRq + Crypto1On + ErrorReg==0.
 * Failure never means wrong key/Bambu identity/physical stop. No AUTH_NEGATIVE
 * result is emitted. Poll count is a finite work limit, not milliseconds.
 * Auth leaves Crypto1/config for subsequent reads; cleanup is NOT certified. */
struct ar_result ar_auth_checked(struct br_transaction *, const struct ar_selection *,
    uint8_t mode, uint8_t block, const uint8_t key[6], uint32_t max_polls);
/* Requires independently retained successful AUTH in the same current
 * transaction. This interface does not mint an auth/ownership capability.
 * Copies exactly 16 staged bytes only after the final checked publication. */
struct ar_result ar_read_checked(struct br_transaction *, uint8_t block, uint8_t out[16]);
#endif
