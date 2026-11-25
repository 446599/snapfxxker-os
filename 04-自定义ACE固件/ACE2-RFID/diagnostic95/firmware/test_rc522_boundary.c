#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define ACE_DECODER_HW_TEST 1
#include "native_tag_decoder.c"

/* Exercise the production register wrappers, including the full stock helper
 * receive-count range, rather than replacing rc522_read_block with a stub. */
static uint8_t regs[64], result_error;
static unsigned output_count, result_bits, transfers, writes;
static int result_code;
uint8_t rc522_read_reg(void *r, uint8_t reg) { (void)r; return regs[reg]; }
void rc522_write_reg(void *r, uint8_t reg, uint8_t value) {
    (void)r; ++writes;
    /* Status/IRQ values here model hardware results after AUTH. */
    if (reg != 0x08 && reg != 0x04) regs[reg] = value;
}
void rc522_timer(void *r, uint32_t ms) { (void)r; (void)ms; }
void rc522_flush(void *r) { (void)r; }
int rc522_transceive(void *r, uint8_t cmd, const uint8_t *tx,
                    uint32_t len, uint8_t *rx, uint32_t *bits) {
    (void)r; assert(cmd == 0x0c && len == 2 && tx[0] == 0x30);
    ++transfers;
    for (unsigned i = 0; i < output_count; ++i) rx[i] = (uint8_t)i;
    *bits = result_bits; regs[6] = result_error;
    return result_code;
}
static void reset(void) {
    memset(regs, 0, sizeof regs);
    regs[8] = 8; regs[4] = 0x10;
    output_count = 16; result_bits = 128; result_code = result_error = 0;
    transfers = writes = 0;
}
int main(void) {
    uint8_t out[16], key[6] = {0}, uid[5] = {1,2,3,4,4};
    reset(); assert(rc522_mf_authent((void *)1, 0x60, 4, key, uid));
    for (unsigned e = 1; e < 256; ++e) {
        reset(); regs[6] = e;
        assert(!rc522_mf_authent((void *)1, 0x60, 4, key, uid));
    }
    reset(); regs[8] = 0xff;
    assert(!rc522_mf_authent((void *)1, 0x60, 4, key, uid));
    reset(); regs[4] = 1;
    assert(!rc522_mf_authent((void *)1, 0x60, 4, key, uid));
    reset(); regs[8] = 0;
    assert(!rc522_mf_authent((void *)1, 0x60, 4, key, uid));
    reset(); assert(rc522_read_block((void *)1, 5, out));
    for (unsigned i = 0; i < 16; ++i) assert(out[i] == i);
    for (unsigned n = 0; n <= 255; ++n) {
        reset(); output_count = n; result_bits = n * 8;
        memset(out, 0xa5, sizeof out);
        assert(rc522_read_block((void *)1, 5, out) == (n == 16));
        for (unsigned i = 0; i < 16; ++i) assert(out[i] == (n == 16 ? i : 0xa5));
    }
    for (unsigned e = 1; e < 256; ++e) {
        reset(); result_error = e; memset(out, 0xa5, sizeof out);
        assert(!rc522_read_block((void *)1, 5, out));
        for (unsigned i = 0; i < 16; ++i) assert(out[i] == 0xa5);
    }
    for (int code = 1; code < 256; ++code) {
        reset(); result_code = code;
        assert(!rc522_read_block((void *)1, 5, out));
    }
    for (unsigned bits = 127; bits <= 129; ++bits) {
        reset(); result_bits = bits;
        assert(rc522_read_block((void *)1, 5, out) == (bits == 128));
    }
    reset(); assert(!rc522_read_block(NULL, 5, out));
    assert(!rc522_read_block((void *)1, 5, NULL)); assert(!transfers && !writes);
    reset(); regs[0x12] = regs[0x13] = 0x80;
    rc522_auth_stop((void *)1);
    assert(regs[1] == 0 && regs[0x12] == 0 && regs[0x13] == 0);
    puts("Production RC522 wrappers: AUTH error rejection, RX 0..255 staging, CRC/collision/length rejection PASS");
}
