#ifndef BOUNDED_SPI_H
#define BOUNDED_SPI_H
#include <stdbool.h>
#include <stdint.h>
/* Offline reference contract only. Hardware bindings and integration absent. */
enum spi_result { SPI_OK, SPI_TIMEOUT, SPI_CANCELLED, SPI_IO_ERROR, SPI_QUARANTINED };
struct spi_ops {
    void *ctx;
    bool (*select)(void *ctx);
    bool (*status)(void *ctx, uint32_t *value);
    bool (*write)(void *ctx, uint8_t value);
    bool (*read)(void *ctx, uint8_t *value);
    bool (*restore)(void *ctx);
    bool (*cancelled)(void *ctx);
};
struct spi_context { bool quarantined; };
enum spi_result bounded_spi_exchange(struct spi_context *state,
    const struct spi_ops *ops, uint8_t address, uint8_t data, uint8_t *out,
    uint32_t tx_ready_mask, uint32_t rx_ready_mask, uint32_t budget);
#endif
