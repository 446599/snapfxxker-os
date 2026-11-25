#include "bounded_spi.h"
/* One shared finite budget covers the entire transaction, not each poll.
 * Every callback must itself complete in bounded time. In particular, this
 * reference cannot bound a stalled CPU bus or an old blocking stock helper.
 * The production adapter must verify restore (CS high, control lines safe,
 * reader state) and implement persistent quarantine and reliable STOP. */
static enum spi_result poll(const struct spi_ops *ops, uint32_t mask, uint32_t *budget) {
    uint32_t status;
    while (*budget) {
        --*budget;
        if (ops->cancelled(ops->ctx)) return SPI_CANCELLED;
        if (!ops->status(ops->ctx, &status)) return SPI_IO_ERROR;
        if ((status & mask) == mask) return SPI_OK;
    }
    return SPI_TIMEOUT;
}
enum spi_result bounded_spi_exchange(struct spi_context *state,
    const struct spi_ops *ops, uint8_t address, uint8_t data, uint8_t *out,
    uint32_t tx_ready_mask, uint32_t rx_ready_mask, uint32_t budget) {
    enum spi_result result = SPI_IO_ERROR;
    uint8_t ignored, value;
    if (!state || !ops || !out || !ops->select || !ops->status || !ops->write ||
        !ops->read || !ops->restore || !ops->cancelled || !tx_ready_mask ||
        !rx_ready_mask || !budget) return SPI_IO_ERROR;
    if (state->quarantined) return SPI_QUARANTINED;
    if (ops->cancelled(ops->ctx)) return SPI_CANCELLED;
    if (!ops->select(ops->ctx)) goto cleanup;
    result = poll(ops, tx_ready_mask, &budget); if (result != SPI_OK) goto cleanup;
    if (!ops->write(ops->ctx, address)) {result=SPI_IO_ERROR; goto cleanup;}
    result = poll(ops, rx_ready_mask, &budget); if (result != SPI_OK) goto cleanup;
    if (!ops->read(ops->ctx, &ignored)) {result=SPI_IO_ERROR; goto cleanup;}
    result = poll(ops, tx_ready_mask, &budget); if (result != SPI_OK) goto cleanup;
    if (!ops->write(ops->ctx, data)) {result=SPI_IO_ERROR; goto cleanup;}
    result = poll(ops, rx_ready_mask, &budget); if (result != SPI_OK) goto cleanup;
    if (!ops->read(ops->ctx, &value)) {result=SPI_IO_ERROR; goto cleanup;}
    if (ops->cancelled(ops->ctx)) {result=SPI_CANCELLED; goto cleanup;}
    result = SPI_OK;
cleanup:
    if (!ops->restore(ops->ctx)) {state->quarantined=true; return SPI_QUARANTINED;}
    if (result == SPI_OK && ops->cancelled(ops->ctx)) result = SPI_CANCELLED;
    if (result != SPI_OK) {
        /* Restoring the CS line alone does not prove the reader, FIFO and
         * crypto state were resynchronised. A separately verified recovery
         * is required before any subsequent exchange. */
        state->quarantined = true;
        return result;
    }
    *out = value;
    return SPI_OK;
}
