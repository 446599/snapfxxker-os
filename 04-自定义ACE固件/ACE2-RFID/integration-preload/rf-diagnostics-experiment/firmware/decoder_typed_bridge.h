#ifndef DECODER_TYPED_BRIDGE_H
#define DECODER_TYPED_BRIDGE_H
#include "typed_results.h"
struct br_decoder_report {
    struct ar_result primary;
    enum br_result cleanup;
    uint32_t cleanup_status_valid;
};
/* Real NOFLASH decoder call path, no identity/failure/success authority.
 * primary stage/code/transport describe the AUTH/READ outcome BEFORE cleanup.
 * It retains raw SELECT and AUTH attempt/issue/poll history across READs.
 * When primary.snapshot_valid is set, status2/irq/error are historical AUTH
 * evidence, not a new READ snapshot. Cleanup has its OWN BR result/validity;
 * a quarantine refusal must never overwrite an earlier timeout.
 * AR_BLOCK_READ only denotes coherent read observations, not identity. */
struct br_decoder_report br_decoder_probe(const struct br_reader *,const struct ar_selection *,const uint8_t key[6]);
#endif
