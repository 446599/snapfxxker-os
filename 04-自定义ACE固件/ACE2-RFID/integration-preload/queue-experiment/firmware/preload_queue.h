#ifndef PRELOAD_QUEUE_H
#define PRELOAD_QUEUE_H
#include <stdint.h>
#if !defined(PRELOAD_QUEUE_EXPERIMENT) || PRELOAD_QUEUE_EXPERIMENT != 1
#error "Queue association experiment only; no failure producer or STOP binding"
#endif
enum pq_source { PQ_UNKNOWN, PQ_PRELOAD, PQ_CLEANUP, PQ_REQUEUE, PQ_CONTROL };
enum pq_fault { PQ_BAD_QUEUE=1, PQ_UNKNOWN_SOURCE=2, PQ_DESYNC=4,
                PQ_EXHAUSTED=8, PQ_BAD_JOB=16, PQ_MISSING_COMMIT=32 };
struct pq_token { uint32_t generation,lane,source,command,present; };
struct pq_pending { uint32_t item,queue,generation,lane,source,command,used,tcb; };
struct pq_reader { uint32_t handle,fault; struct pq_token ring[2],candidate; };
struct pq_storage {
    struct { uint32_t generation,live; } lane[4];
    struct pq_reader reader[2];
    struct pq_pending pending[8];
};
_Static_assert(sizeof(struct pq_storage)==424,"Queue sideband exact ARM/host layout");
void pq_job_enqueued(uint32_t lane);
void pq_job_unlinked(uint32_t lane);
uint32_t pq_send(uint32_t queue,uint32_t item,uint32_t wait,
                 uint32_t source,uint32_t lane,uint32_t job);
uint32_t pq_enqueue_entry(uint32_t queue,uint32_t item,uint32_t commit_return,uint32_t sender_return);
uint32_t pq_receive_copy(uint32_t dest,uint32_t source,uint32_t bytes,
                         uint32_t queue,uint32_t old_count);
/* Observation only. Never authorizes ph_claim_reader, ph_publish_failure,
 * decoder success, physical tag identity, or motor control. */
int pq_candidate(uint32_t reader,struct pq_token *out);
void pq_preload_requeue(uint32_t lane,uint32_t job);
#endif
