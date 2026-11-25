#ifndef DECODER_TYPED_BRIDGE_H
#define DECODER_TYPED_BRIDGE_H
#include "typed_results.h"
/* Real NOFLASH decoder call path. Observes AUTH plus repeated block reads with
 * one live operation/budget; never grants identity, preload failure or success.
 * The returned AR_BLOCK_READ denotes coherent read observations only. */
struct ar_result br_decoder_probe(const struct br_reader *,const struct ar_selection *,const uint8_t key[6]);
#endif
