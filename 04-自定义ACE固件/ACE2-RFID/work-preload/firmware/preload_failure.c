#ifndef PRELOAD_OFFLINE_TEST
#error "Preload guard lacks verified RAM, startup, motion fence and STOP adapter; offline tests only"
#endif
#include "preload_failure.h"

static int exact(const struct pl_state *s, const struct pl_ticket *t) {
    return t && s->phase!=PL_IDLE && s->boot==t->boot &&
           s->generation==t->generation && s->slot==t->slot;
}
static void ticket(const struct pl_state *s, struct pl_ticket *t) {
    t->boot=s->boot; t->generation=s->generation; t->slot=s->slot;
}
static int rf_exact(const struct rf_operation *a,const struct rf_operation *b) {
    return a->ticket.boot==b->ticket.boot && a->ticket.generation==b->ticket.generation &&
           a->ticket.connection==b->ticket.connection && a->ticket.session==b->ticket.session &&
           a->sequence==b->sequence;
}
static int serial(struct pl_state *s) {
    if(s->sequence==UINT32_MAX) { s->phase=PL_UNKNOWN; return 0; }
    ++s->sequence; return 1;
}
static void expire(struct pl_state *s,uint32_t now) {
    if(s->phase==PL_ABORTING && (int32_t)(now-s->stop_deadline)>=0) s->phase=PL_UNKNOWN;
}
void pl_init_exclusive(struct pl_state *s,uint32_t boot,uint32_t slot) {
    struct pl_state blank={0}; *s=blank; s->boot=boot; s->slot=slot;
    if(!boot || slot>3) s->phase=PL_UNKNOWN;
}
int pl_begin(struct pl_state *s,struct pl_ticket *out) {
    if(!out) return PL_INVALID;
    uint32_t key=rf_platform_enter(); int result=PL_BUSY;
    if(s->phase==PL_IDLE) {
        if(!s->boot || s->generation==UINT32_MAX || s->sequence==UINT32_MAX) {
            s->phase=PL_UNKNOWN; result=PL_FAULT;
        } else {
            ++s->generation; s->phase=PL_ACTIVE; s->reason=PL_REASON_NONE;
            s->start_pending=s->auth_pending=s->stop_pending=0;
            s->motion_fence=0; ticket(s,out); result=PL_OK;
        }
    } else if(s->phase==PL_UNKNOWN) result=PL_FAULT;
    rf_platform_leave(key); return result;
}
int pl_claim_start(struct pl_state *s,const struct pl_ticket *t,struct pl_start *out) {
    if(!out) return PL_INVALID;
    uint32_t key=rf_platform_enter(); int result=PL_STALE;
    if(exact(s,t)) {
        result=PL_BUSY;
        if(s->phase==PL_ACTIVE && !s->start_pending && !s->stop_pending) {
            if(serial(s)) {
                s->start_pending=1; s->start_sequence=s->sequence;
                out->ticket=*t; out->sequence=s->sequence; result=PL_OK;
            } else result=PL_FAULT;
        } else if(s->phase==PL_UNKNOWN) result=PL_FAULT;
    }
    rf_platform_leave(key); return result;
}
int pl_retire_start(struct pl_state *s,const struct pl_start *op,enum pl_start_proof proof) {
    if(!op) return PL_INVALID;
    uint32_t key=rf_platform_enter(); int result=PL_STALE;
    if(exact(s,&op->ticket) && s->start_pending && op->sequence==s->start_sequence) {
        s->start_pending=0;
        if(proof!=PL_START_NO_LATE_WRITES || s->motion_fence==UINT32_MAX) {
            s->phase=PL_UNKNOWN; result=PL_FAULT;
        } else {
            /* Any STOP sampled before the final possible start write is stale. */
            ++s->motion_fence;
            result=s->phase==PL_ACTIVE ? PL_OK :
                   s->phase==PL_ABORTING ? PL_NEEDS_STOP : PL_FAULT;
        }
    }
    rf_platform_leave(key); return result;
}
int pl_begin_auth(struct pl_state *s,const struct pl_ticket *t,
                  const struct rf_operation *rf,uint32_t actual_slot,struct pl_auth *out) {
    if(!rf || !out || !rf->ticket.boot || !rf->ticket.generation || !rf->sequence || actual_slot>3)
        return PL_INVALID;
    uint32_t key=rf_platform_enter(); int result=PL_STALE;
    if(exact(s,t) && actual_slot==s->slot) {
        result=PL_BUSY;
        if(s->phase==PL_ACTIVE && !s->auth_pending) {
            if(serial(s)) {
                s->auth_pending=1; s->auth_sequence=s->sequence; s->auth_rf=*rf;
                out->ticket=*t; out->rf=*rf; out->sequence=s->sequence; result=PL_OK;
            } else result=PL_FAULT;
        }
    }
    rf_platform_leave(key); return result;
}
int pl_complete_auth(struct pl_state *s,const struct pl_auth *op,enum pl_auth_result auth,
                     uint32_t now,uint32_t budget) {
    if(!op || auth<PL_AUTH_OK || auth>PL_AUTH_TRANSPORT_UNKNOWN || !budget || budget>0x7fffffffU)
        return PL_INVALID;
    uint32_t key=rf_platform_enter(); int result=PL_STALE;
    if(exact(s,&op->ticket) && s->phase==PL_ACTIVE && s->auth_pending &&
       op->sequence==s->auth_sequence && rf_exact(&op->rf,&s->auth_rf)) {
        s->auth_pending=0; result=PL_OK;
        if(auth==PL_AUTH_REJECTED || auth==PL_AUTH_TRANSPORT_UNKNOWN) {
            s->reason=auth==PL_AUTH_REJECTED ? PL_REASON_AUTH_REJECTED : PL_REASON_TRANSPORT_UNKNOWN;
            s->phase=PL_ABORTING; s->stop_deadline=now+budget; result=PL_NEEDS_STOP;
        }
    }
    rf_platform_leave(key); return result;
}
int pl_claim_stop(struct pl_state *s,const struct pl_ticket *t,uint32_t now,struct pl_stop *out) {
    if(!out) return PL_INVALID;
    uint32_t key=rf_platform_enter(); int result=PL_STALE;
    if(exact(s,t)) {
        expire(s,now); result=PL_BUSY;
        if((s->phase==PL_ABORTING || s->phase==PL_UNKNOWN) && !s->stop_pending) {
            if(serial(s)) {
                s->stop_pending=1; s->stop_sequence=s->sequence;
                out->ticket=*t; out->sequence=s->sequence; out->motion_fence=s->motion_fence;
                result=PL_OK;
            } else result=PL_FAULT;
        }
    }
    rf_platform_leave(key); return result;
}
int pl_complete_stop(struct pl_state *s,const struct pl_stop *op,uint32_t now,uint32_t proof) {
    if(!op) return PL_INVALID;
    uint32_t key=rf_platform_enter(); int result=PL_STALE;
    if(exact(s,&op->ticket) && s->stop_pending && op->sequence==s->stop_sequence) {
        expire(s,now); s->stop_pending=0;
        if(s->phase==PL_UNKNOWN) result=PL_FAULT;
        else if(op->motion_fence!=s->motion_fence) result=PL_STALE;
        else if(s->start_pending) result=PL_BUSY;
        else if(proof!=PL_STOP_PROVEN) { s->phase=PL_UNKNOWN; result=PL_FAULT; }
        else { s->phase=PL_STOPPED; result=PL_OK; }
    }
    rf_platform_leave(key); return result;
}
int pl_poll(struct pl_state *s,const struct pl_ticket *t,uint32_t now) {
    uint32_t key=rf_platform_enter(); int result=PL_STALE;
    if(exact(s,t)) {
        expire(s,now);
        result=s->phase==PL_UNKNOWN ? PL_FAULT : s->phase==PL_ABORTING ? PL_NEEDS_STOP : PL_OK;
    }
    rf_platform_leave(key); return result;
}
int pl_finish_normal(struct pl_state *s,const struct pl_ticket *t,uint32_t proof) {
    uint32_t key=rf_platform_enter(); int result=PL_STALE;
    if(exact(s,t)) {
        result=PL_BUSY;
        if(s->phase==PL_ACTIVE && !s->start_pending && !s->auth_pending && !s->stop_pending) {
            if(proof==PL_STOP_PROVEN) { s->phase=PL_IDLE; result=PL_OK; }
            else { s->phase=PL_UNKNOWN; result=PL_FAULT; }
        }
    }
    rf_platform_leave(key); return result;
}
int pl_release_stopped(struct pl_state *s,const struct pl_ticket *t) {
    uint32_t key=rf_platform_enter(); int result=PL_STALE;
    if(exact(s,t)) {
        result=PL_BUSY;
        if(s->phase==PL_STOPPED && !s->start_pending && !s->auth_pending && !s->stop_pending) {
            s->phase=PL_IDLE; result=PL_OK;
        }
    }
    rf_platform_leave(key); return result;
}
