#ifndef ACE_BOUNDED_RC522_H
#define ACE_BOUNDED_RC522_H
#include <stdint.h>
#include <stddef.h>
/* Checked candidate, NOT a drop-in replacement for the stock byte/void ABI.
 * Caller must supply exclusively owned, initialized, persistent SRAM state.
 * No storage address is guessed and no automatic recovery is implemented. */
enum br_result { BR_OK, BR_INVALID, BR_TIMEOUT, BR_TRANSPORT, BR_CANCELLED,
                 BR_QUARANTINED, BR_READER_ERROR, BR_CAPACITY, BR_NO_REPLY, BR_PROTOCOL };
struct br_state { uint32_t quarantined; uint32_t cause; };
/* Stock offsets proven from V1.1.31: SPI+0, CS GPIO+4/mask+8,
 * auxiliary GPIO+12/mask+16, remaining words through power GPIO+44/mask+48. */
struct br_reader {
    uintptr_t spi, cs_port; uint32_t cs_mask;
    uintptr_t aux_port; uint32_t aux_mask;
    uint32_t other[6]; uintptr_t power_port; uint32_t power_mask;
};
struct br_transaction {
    struct br_state *state;
    const struct br_reader *reader;
    uint32_t polls_left;             /* One budget for all register operations. */
    const volatile uint32_t *stop_epoch;
    uint32_t expected_epoch;
};
/* Caller precondition: valid mapped pointers, exclusive RF+SPI ownership,
 * matching STOP generation. Transaction state must not be reset after fault.
 * The API cannot bound a stalled CPU bus. Atomic publication requires the
 * mandatory platform bindings declared below, not a guessed stock IRQ address. */
enum br_result br_read(struct br_transaction *, uint8_t reg, uint8_t *out);
enum br_result br_write(struct br_transaction *, uint8_t reg, uint8_t value);
/* Only Transceive (0x0c), 1..64 TX bytes. Returns no partial/collision identity.
 * On failure rx and *rx_bits remain unchanged. rx capacity is mandatory. */
enum br_result br_transceive(struct br_transaction *, const uint8_t *tx,
    uint32_t tx_len, uint8_t *rx, uint32_t rx_capacity, uint32_t *rx_bits);
/* Exact expected bit length is checked BEFORE output publication. */
enum br_result br_transceive_exact(struct br_transaction *, const uint8_t *tx,
    uint32_t tx_len, uint8_t *rx, uint32_t rx_capacity, uint32_t *rx_bits,
    uint32_t required_bits);
/* Exact HLTA 50 00 using stock command 4 (Transmit), bounded by the SAME poll
 * budget. BR_OK means transmitted + checked register restoration only. It is
 * NOT card-state authentication, RF_QUIESCENT, physical STOP, or release proof. */
enum br_result br_halt_checked(struct br_transaction *);
/* Platform binding is mandatory, deliberately not guessed from the stock image.
 * enter/leave restore the prior IRQ mask; valid checks the live owner, generation,
 * boot and cancellation. These functions must be finite and perform no hardware,
 * scheduling or external work. The short critical section copies <=64 bytes only.
 * A successful SPI read is NOT a proof that cleanup or whole-RF ownership is safe. */
uint32_t br_platform_enter(void);
void br_platform_leave(uint32_t);
int br_platform_commit_valid(const struct br_transaction *);
#ifdef ACE_EXPERIMENTAL_OFFLINE
int br_platform_exchange_claim(const struct br_transaction *);
void br_platform_exchange_finish(const struct br_transaction *);
#endif
enum br_result br_publish(struct br_transaction *, uint8_t *dst,
    const uint8_t *src, uint32_t n, uint32_t *bits, uint32_t value);
#endif
