#ifndef EVENT_MOCK_H
#define EVENT_MOCK_H
#include "experimental_binding.h"
extern uint32_t mock_handle,mock_expansion_fail,mock_fallback_fail,mock_extra;
extern uint32_t mock_allocate_at,mock_raw_override,mock_next_override;
extern unsigned mock_alloc_calls,mock_fallback_calls,mock_header_reads,mock_tail_reads;
extern unsigned mock_map_calls,mock_stores,mock_barriers;
extern uint32_t mock_last_store,mock_last_value;
void mock_storage_reset(void);
uint32_t ex_test_load32(uint32_t);
void ex_test_store32(uint32_t,uint32_t);
void ex_test_store8(uint32_t,uint8_t);
void *ex_test_map(uint32_t,uint32_t);
void ex_test_publish_barrier(void);
uint32_t ex_stock_malloc(uint32_t);
uint32_t ex_stock_event_factory(void);
#endif
