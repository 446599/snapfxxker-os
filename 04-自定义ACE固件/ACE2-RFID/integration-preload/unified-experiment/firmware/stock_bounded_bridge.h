#ifndef STOCK_BOUNDED_BRIDGE_H
#define STOCK_BOUNDED_BRIDGE_H
#include "bounded_rc522.h"
/* NOT a production integration. A caller must explicitly opt in to offline
 * development; no stock hook is installed by this module or its test builder. */
#ifndef BR_OFFLINE_TEST
#error "Stock bounded bridge lacks verified SRAM/owner/STOP binding. Offline tests only."
#endif
struct br_stock_context { uint32_t index; const struct br_reader *reader; };
/* Mandatory platform contracts. These are deliberately unresolved for ARM.
 * transaction returns the SAME transaction/budget/state across every stock
 * register call, SELECT, decoder and fallback of one whole RF operation.
 * It verifies rf_operation identity: boot/generation/connection/session/sequence.
 * Both physical readers share a sticky transport-fault latch. No new budget or
 * clearing of quarantine is allowed here. The owner cannot be revoked/replaced
 * during peripheral operations except by a cancellation observed by this driver.
 */
struct br_transaction *br_platform_transaction(const struct br_reader *);
/* Must latch whole-RF unknown state if ownership/transaction lookup is invalid.
 * Metadata only; no hardware waits, no fake cleanup or RF_QUIESCENT proof. */
void br_platform_denied(const struct br_reader *, enum br_result);
int br_stock_select_original(struct br_stock_context *,uint8_t *,int);

/* Original byte/void ABI, safe ONLY when every consuming outcome/publication
 * checks the shared latch. 0xff is NOT an error code in the byte ABI. */
uint8_t br_stock_read(const struct br_reader *,uint8_t);
void br_stock_write(const struct br_reader *,uint8_t,uint8_t);
int br_stock_select(struct br_stock_context *,uint8_t *,int);
int br_stock_halt(struct br_stock_context *);
int br_stock_transceive_checked(const struct br_reader *,uint32_t,
    const uint8_t *,uint32_t,uint8_t *,uint32_t *,uintptr_t return_pc);
/* Appended decoder must call this explicit-capacity route instead of inventing
 * a return-address allowlist entry for a differently built decoder. */
int br_stock_transceive_sized(const struct br_reader *,uint32_t,
    const uint8_t *,uint32_t,uint8_t *,uint32_t,uint32_t *,uint32_t expected_bits);
#endif
