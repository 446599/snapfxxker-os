#ifndef ACE_EXPERIMENTAL_BINDING_H
#define ACE_EXPERIMENTAL_BINDING_H
#if !defined(ACE_EXPERIMENTAL_OFFLINE) || ACE_EXPERIMENTAL_OFFLINE != 1
#error "EXPERIMENTAL DO NOT FLASH: no verified SRAM, STOP or physical cleanup contract"
#endif
#include "stock_bounded_bridge.h"
#include "rf_stock_adapter.h"
#include "preload_hooks.h"

/* This object is the tail of a checked, expanded stock event-group allocation.
 * Its physical lifetime, free/reuse behavior and boot identity remain UNPROVEN. */
struct ex_state {
    uint32_t initialized, stop_epoch, stop_requested, last_result, offline_now;
    struct br_state transport;
    struct br_transaction transaction;
    struct rf_operation operation;
};
struct ex_storage {
    uint32_t magic, bytes, self, ready;
    struct rf_state rf;
    struct ex_state ex;
    struct ph_storage preload;
};
#define EX_HANDLE_ADDRESS UINT32_C(0x20001e64)
#define EX_HEAP_FIRST_PAYLOAD UINT32_C(0x200026a0)
#define EX_HEAP_END UINT32_C(0x20007e88)
#define EX_STORAGE_MAGIC UINT32_C(0x45585432)
#define EX_STORAGE_READY UINT32_C(0x52445932)
#define EX_STORAGE_PAYLOAD (32u+(uint32_t)sizeof(struct ex_storage))
/* Storage-ready means ONLY initialized allocation metadata, never RF-ready. */
struct ex_storage *ex_storage_current(void);
uint32_t ex_event_factory(void);
uint32_t ex_storage_allocation_size(uint32_t payload);
int ex_storage_header_valid(uint32_t address,uint32_t payload);
struct rf_state *rf_platform_state(void);
/* Test fixture setup, not a device startup/recovery API. One-shot, refuses
 * reinitialization and any previously recorded fault/STOP. */
int ex_offline_begin(const struct br_reader *, uint32_t polls);
void ex_stop_request(void);
int ex_finalize(void);
int ex_decoder_ready(void);
int ex_reject_select(struct br_stock_context *, uint8_t *, int);
int ex_reject_halt(void *);
void ex_reject_worker(void);
void experiment_reset_gate(void);
#endif

#if defined(__arm__) || defined(__thumb__)
_Static_assert(offsetof(struct ex_storage,rf)==16,"RF offset preserved");
_Static_assert(offsetof(struct ex_storage,ex)==76,"EX offset preserved");
_Static_assert(offsetof(struct ex_storage,preload)==144,"Preload offset");
_Static_assert(sizeof(struct ex_storage)==184,"Combined ARM tail");
_Static_assert(EX_STORAGE_PAYLOAD==216,"Combined ARM payload");
#endif
