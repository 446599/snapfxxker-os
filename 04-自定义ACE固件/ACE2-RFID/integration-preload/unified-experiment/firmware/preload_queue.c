#include "experimental_binding.h"

#ifdef PQ_HOST_TEST
extern uint32_t pq_test_read(uint32_t,uint32_t);
#define R32(a) pq_test_read((a),4)
#define R8(a) pq_test_read((a),1)
extern void pq_test_write(uint32_t,uint32_t);
#define W32(a,v) pq_test_write((a),(v))
extern uint32_t pq_test_ipsr;
#define IPSR() pq_test_ipsr
#define SEND_RETURN UINT32_C(0x0801ffff)
#else
#define R32(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define R8(a) (*(volatile uint8_t *)(uintptr_t)(a))
#define W32(a,v) (*(volatile uint32_t *)(uintptr_t)(a)=(v))
static uint32_t ipsr(void) {uint32_t x;__asm__ volatile("mrs %0,IPSR":"=r"(x));return x;}
#define IPSR() ipsr()
extern char pq_stock_send_return;
#define SEND_RETURN ((uint32_t)(uintptr_t)&pq_stock_send_return|1u)
#endif
extern uint32_t pq_stock_send(uint32_t,uint32_t,uint32_t);
extern uint32_t pq_enqueue_original(uint32_t,uint32_t);
extern uint32_t pq_stock_copy(uint32_t,uint32_t,uint32_t);
static struct pq_storage *storage(void) {
    struct ex_storage *b=ex_storage_current();return b ? &b->queue : 0;
}
static int reader_for(uint32_t q) {
    uint32_t a=R32(0x2000189cu),b=R32(0x200018a0u);
    if(!q || a==b) return -1;
    return q==a ? 0 : q==b ? 1 : -1;
}
static uint32_t node(uint32_t lane) {
    return 0x200012e8u+(lane>>1)*208u+lane*32u+140u;
}
static void poison(struct pq_reader *r,uint32_t fault) {
    r->fault|=fault;r->candidate.present=0;
}
/* Caller must validate the allocation header once, under the SAME PRIMASK
 * interval, before calling this bounded field-only check. */
static int queue_fields_valid(uint32_t q,uint32_t count) {
    return R32(q+60)==2 && R32(q+64)==1 && R32(q+56)==count && count<=2 &&
      R32(q)==q+80 && R32(q+8)==q+82 &&
      R32(q+4)>=q+80 && R32(q+4)<q+82 &&
      R32(q+12)>=q+80 && R32(q+12)<q+82;
}
static int live(struct pq_storage *s,const struct pq_token *t) {
    return t->present && t->source==PQ_PRELOAD && t->command==1 && t->lane<4 &&
      t->generation && s->lane[t->lane].generation==t->generation &&
      s->lane[t->lane].live && R8(node(t->lane))==t->lane &&
      R8(node(t->lane)+28)==1;
}
void pq_job_enqueued(uint32_t lane) {
    uint32_t k=ph_irq_enter();struct pq_storage *s=storage();
    if(s && lane<4) {
        struct pq_reader *r=&s->reader[lane>>1];
        if(r->candidate.lane==lane) r->candidate.present=0;
        if(s->lane[lane].generation==UINT32_MAX) {
            s->lane[lane].live=0;poison(r,PQ_EXHAUSTED);
        } else { ++s->lane[lane].generation;s->lane[lane].live=1; }
    }
    ph_irq_leave(k);
}
void pq_job_unlinked(uint32_t lane) {
    uint32_t k=ph_irq_enter();struct pq_storage *s=storage();
    if(s && lane<4) {
        s->lane[lane].live=0;
        if(s->reader[lane>>1].candidate.lane==lane)
            s->reader[lane>>1].candidate.present=0;
    }
    ph_irq_leave(k);
}
uint32_t pq_send(uint32_t q,uint32_t item,uint32_t wait,
                 uint32_t source,uint32_t lane,uint32_t job) {
    uint32_t k=ph_irq_enter(),slot=8;
    struct pq_storage *s=storage();int reader=reader_for(q);
    if(s && reader>=0) {
        struct pq_reader *r=&s->reader[reader];uint32_t command=R8(item);
        int known=(source==PQ_PRELOAD && command==1) ||
          (source==PQ_CLEANUP && command==0) ||
          (source==PQ_REQUEUE && command==6) ||
          (source==PQ_CONTROL && (command==0 || command==8));
        uint32_t generation=0,tcb=R32(0x20001dd0u);
        if(IPSR() || (tcb&3u) || tcb<0x200026a0u || tcb>=0x20007e88u) known=0;
        if(source==PQ_PRELOAD) {
            if(lane>=4 || (lane>>1)!=(uint32_t)reader || job!=node(lane) ||
               !s->lane[lane].live || !s->lane[lane].generation ||
               R8(job)!=lane || R8(job+28)!=1) known=0;
            else generation=s->lane[lane].generation;
        } else {
            lane=UINT32_MAX;
            if(source==PQ_REQUEUE) {
                unsigned matches=0;
                for(unsigned i=0;i<8;i++) {
                    struct pq_pending *ctx=&s->pending[i];
                    if(ctx->used==3 && ctx->queue==q && ctx->source==PQ_REQUEUE && ctx->tcb==tcb) {
                        lane=ctx->lane;generation=ctx->generation;++matches;
                    }
                }
                if(matches>1) known=0;
                if(matches==1 && (!live(s,&r->candidate) || r->candidate.lane!=lane ||
                                  r->candidate.generation!=generation)) known=0;
            }
        }
        for(unsigned i=0;i<8;i++) {
            if(s->pending[i].used && s->pending[i].item==item) known=0;
            if(!s->pending[i].used && slot==8) slot=i;
        }
        if(!known || slot==8) { poison(r,!known?PQ_UNKNOWN_SOURCE:PQ_EXHAUSTED);slot=8; }
        else s->pending[slot]=(struct pq_pending){item,q,generation,lane,source,command,1,tcb};
    }
    ph_irq_leave(k);
    /* May block: NEVER hold PRIMASK across this stock queue-send operation. */
    uint32_t result=pq_stock_send(q,item,wait);
    k=ph_irq_enter();s=storage();
    if(s && reader>=0 && slot<8) {
        struct pq_pending *p=&s->pending[slot];
        if(p->item!=item || p->queue!=q || !p->used ||
           (result==1 && p->used!=2) || (result!=1 && p->used!=1))
            poison(&s->reader[reader],PQ_MISSING_COMMIT);
        p->used=0;
    }
    ph_irq_leave(k);return result;
}
uint32_t pq_enqueue_entry(uint32_t q,uint32_t item,uint32_t commit_return,uint32_t sender_return) {
    int reader=reader_for(q);
    if(reader<0) return pq_enqueue_original(q,item); /* Exact non-RF pass-through. */
    uint32_t k=ph_irq_enter();struct pq_storage *s=storage();
    struct pq_reader *r=s?&s->reader[reader]:0;
    uint32_t count=0,index=0;struct pq_pending *pending=0;
    int valid=reader_for(q)==reader && ex_storage_header_valid(q,82);
    if(valid) { count=R32(q+56);valid=count<2 && queue_fields_valid(q,count); }
    if(r && valid) {
        if(!r->handle && count==0) r->handle=q;
        if(r->handle!=q) valid=0;
        index=R32(q+4)-(q+80);
        if(r->ring[index].present) valid=0;
        uint32_t tcb=R32(0x20001dd0u);
        int origin_ok=!IPSR() && commit_return==0x080167fbu && sender_return==SEND_RETURN;
        for(unsigned i=0;i<8;i++) if(origin_ok && s->pending[i].used==1 &&
          s->pending[i].queue==q && s->pending[i].item==item && s->pending[i].tcb==tcb) {
            if(pending) { valid=0;break; } pending=&s->pending[i];
        }
        if(!pending || pending->command!=R8(item)) valid=0;
    }
    if(r && !valid) poison(r,PQ_BAD_QUEUE|PQ_UNKNOWN_SOURCE);
    /* Only exact RF queues, size one, have a bounded data-copy path. A malformed
     * queue is NOT executed under the added mask and grants no association. */
    if(!valid) {
        ph_irq_leave(k);return pq_enqueue_original(q,item);
    }
    uint32_t result=pq_enqueue_original(q,item);
    if(r) {
        if(!queue_fields_valid(q,count+1) || R32(q+4)!=q+80+((index+1)&1))
            poison(r,PQ_DESYNC);
        else if(pending) {
            r->ring[index]=(struct pq_token){pending->generation,pending->lane,
              pending->source,pending->command,1};pending->used=2;
        } else poison(r,PQ_UNKNOWN_SOURCE);
    }
    ph_irq_leave(k);return result;
}
uint32_t pq_receive_copy(uint32_t dest,uint32_t src,uint32_t bytes,
                         uint32_t q,uint32_t count) {
    int reader=reader_for(q);
    if(reader<0) { (void)pq_stock_copy(dest,src,bytes);W32(q+56,count-1);return count-1; }
    uint32_t k=ph_irq_enter();struct pq_storage *s=storage();
    struct pq_reader *r=s?&s->reader[reader]:0;
    int valid=reader_for(q)==reader && bytes==1 && count>=1 && ex_storage_header_valid(q,82) && queue_fields_valid(q,count) &&
      src>=q+80 && src<q+82 && R32(q+12)==src;
    uint32_t index=valid?src-(q+80):0;
    if(r && (!valid || r->handle!=q || !r->ring[index].present))
        poison(r,PQ_DESYNC);
    if(!valid) ph_irq_leave(k);
    (void)pq_stock_copy(dest,src,bytes);W32(q+56,count-1);
    if(valid) {
        if(r) {
            /* Both records remain inside this one masked interval. Inspect the
             * ring in place, consume it last, and avoid two 20-byte stack copies. */
            struct pq_token *t=&r->ring[index],*old=&r->candidate;
            if(t->command!=R8(dest)) poison(r,PQ_DESYNC);
            else if(!r->fault && live(s,t)) r->candidate=*t;
            else if(r->fault || t->source!=PQ_REQUEUE || t->command!=6 || !t->generation ||
                    t->lane!=old->lane || t->generation!=old->generation || !live(s,old))
                old->present=0;
            /* Otherwise preserve the same consumed preload, not a new owner. */
            t->present=0;
        }
        ph_irq_leave(k);
    }
    return count-1;
}
int pq_candidate(uint32_t reader,struct pq_token *out) {
    if(reader>1 || !out) return 0;
    uint32_t k=ph_irq_enter();struct pq_storage *s=storage();
    int ok=s && !s->reader[reader].fault &&
      reader_for(s->reader[reader].handle)==(int)reader &&
      s->reader[reader].candidate.lane<4 &&
      (s->reader[reader].candidate.lane>>1)==reader &&
      live(s,&s->reader[reader].candidate);
    if(ok) *out=s->reader[reader].candidate;
    ph_irq_leave(k);return ok;
}
extern void rf_requeue_entry(uint32_t);
void pq_preload_requeue(uint32_t lane,uint32_t job) {
    uint32_t k=ph_irq_enter(),slot=8;struct pq_storage *s=storage();
    uint32_t tcb=R32(0x20001dd0u),q=lane<4?R32(0x2000189cu+4*(lane>>1)):0;
    if(s && lane<4 && job==node(lane) && !IPSR() && !(tcb&3u) && tcb>=0x200026a0u && tcb<0x20007e88u && reader_for(q)==(int)(lane>>1)) {
        struct pq_reader *r=&s->reader[lane>>1];
        if(!r->fault && live(s,&r->candidate) && r->candidate.lane==lane) {
            for(unsigned i=0;i<8;i++) if(!s->pending[i].used) {slot=i;break;}
            if(slot<8) s->pending[slot]=(struct pq_pending){0,q,r->candidate.generation,lane,PQ_REQUEUE,6,3,tcb};
            else poison(r,PQ_EXHAUSTED);
        }
    }
    ph_irq_leave(k);
    rf_requeue_entry(lane); /* Retain the RF adapter's existing fail-closed gate. */
    k=ph_irq_enter();s=storage();
    if(s && slot<8) {
        struct pq_pending *p=&s->pending[slot];
        if(p->used==3 && p->tcb==tcb && p->queue==q && p->lane==lane) p->used=0;
        else poison(&s->reader[lane>>1],PQ_DESYNC);
    }
    ph_irq_leave(k);
}
extern char pq_verified_queue_lifetime,pq_verified_all_queue_sources;
__attribute__((used,section(".binding_required")))
static const void *const proof[]={&pq_verified_queue_lifetime,&pq_verified_all_queue_sources};
