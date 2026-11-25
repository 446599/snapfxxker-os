#include "rf_owner.h"
#define MAX_TICKS UINT32_C(0x7fffffff)
static int due(uint32_t now, uint32_t deadline) { return (int32_t)(now-deadline) >= 0; }
static int exact(const struct rf_state *s, const struct rf_ticket *t) {
    return t && s->phase != RF_IDLE && s->boot == t->boot &&
        s->generation == t->generation && s->connection == t->connection &&
        s->session == t->session;
}
static void ticket(const struct rf_state *s, struct rf_ticket *t) {
    t->boot=s->boot; t->generation=s->generation;
    t->connection=s->connection; t->session=s->session;
}
static void revoke(struct rf_state *s) {
    s->revoked=1;
    if(s->phase==RF_ACTIVE) s->phase=s->in_flight ? RF_DRAINING : RF_CLEANING;
}
static void expire_locked(struct rf_state *s, uint32_t now) {
    if((s->phase==RF_ACTIVE || s->phase==RF_CLEANING || s->phase==RF_DRAINING) &&
       (due(now,s->deadline) || due(now,s->hard_deadline))) revoke(s);
}
static int next_sequence(struct rf_state *s) {
    if(s->sequence==UINT32_MAX) { s->phase=RF_UNKNOWN; s->revoked=1; return 0; }
    ++s->sequence; return 1;
}
void rf_init_exclusive(struct rf_state *s, uint32_t boot) {
    struct rf_state blank={0}; *s=blank; s->boot=boot;
    if(!boot) s->phase=RF_UNKNOWN;
}
int rf_acquire(struct rf_state *s, uint32_t owner, uint32_t slot,
               uint32_t connection, uint32_t session, uint32_t now,
               uint32_t lease_ticks, uint32_t hard_ticks, struct rf_ticket *out) {
    int result=RF_BUSY;
    if(!out || owner<RF_BACKGROUND || owner>RF_CACHE || slot>3 ||
       !lease_ticks || lease_ticks>MAX_TICKS || hard_ticks<lease_ticks || hard_ticks>MAX_TICKS ||
       (owner==RF_HOST ? (!connection || !session) : (connection || session))) return RF_INVALID;
    uint32_t key=rf_platform_enter();
    expire_locked(s,now);
    if(s->phase==RF_IDLE) {
        if(!s->boot || s->generation==UINT32_MAX) { s->phase=RF_UNKNOWN; result=RF_FAULT; }
        else {
            ++s->generation; s->owner=owner; s->slot=slot;
            s->connection=connection; s->session=session; s->deadline=now+lease_ticks;
            s->hard_deadline=now+hard_ticks; s->in_flight=0; s->cleaning_claimed=0;
            s->cleanup_step=0; s->revoked=0; s->finished=0; s->phase=RF_ACTIVE;
            ticket(s,out); result=RF_OK;
        }
    } else if(s->phase==RF_UNKNOWN) result=RF_FAULT;
    rf_platform_leave(key); return result;
}
int rf_begin_operation(struct rf_state *s,const struct rf_ticket *t,uint32_t now,
                       struct rf_operation *out) {
    if(!out) return RF_INVALID;
    uint32_t key=rf_platform_enter(); int result=RF_BUSY;
    expire_locked(s,now);
    if(!exact(s,t)) result=RF_STALE;
    else if(s->phase==RF_ACTIVE && !s->in_flight) {
        if(next_sequence(s)) { s->in_flight=1; out->ticket=*t; out->sequence=s->sequence; result=RF_OK; }
        else result=RF_FAULT;
    }
    rf_platform_leave(key); return result;
}
int rf_complete_operation(struct rf_state *s,const struct rf_operation *op,
                          uint32_t now,enum rf_proof proof) {
    if(!op) return RF_INVALID;
    uint32_t key=rf_platform_enter(); int result=RF_STALE;
    expire_locked(s,now);
    if(exact(s,&op->ticket) && s->in_flight && op->sequence==s->sequence &&
       (s->phase==RF_ACTIVE || s->phase==RF_DRAINING)) {
        if(proof!=RF_QUIESCENT) { s->phase=RF_UNKNOWN; s->revoked=1; result=RF_FAULT; }
        else { s->in_flight=0; if(s->phase==RF_DRAINING) s->phase=RF_CLEANING; result=RF_OK; }
    }
    rf_platform_leave(key); return result;
}
int rf_finish(struct rf_state *s,const struct rf_ticket *t,uint32_t now) {
    uint32_t key=rf_platform_enter(); int result=RF_BUSY;
    expire_locked(s,now);
    if(!exact(s,t)) result=RF_STALE;
    else if(s->phase==RF_ACTIVE && !s->in_flight) { s->finished=1; s->phase=RF_CLEANING; result=RF_OK; }
    rf_platform_leave(key); return result;
}
int rf_cancel(struct rf_state *s,const struct rf_ticket *t) {
    uint32_t key=rf_platform_enter(); int result=RF_STALE;
    if(exact(s,t)) { revoke(s); result=RF_OK; }
    rf_platform_leave(key); return result;
}
int rf_renew(struct rf_state *s,const struct rf_ticket *t,uint32_t now,uint32_t ticks) {
    if(!ticks || ticks>MAX_TICKS) return RF_INVALID;
    uint32_t key=rf_platform_enter(); int result=RF_BUSY;
    expire_locked(s,now);
    if(!exact(s,t)) result=RF_STALE;
    else if(s->phase==RF_ACTIVE && s->owner==RF_HOST) {
        /* Both deadlines are < half a uint32 range ahead of now. */
        uint32_t remaining=s->hard_deadline-now;
        s->deadline=now+(ticks<remaining ? ticks : remaining); result=RF_OK;
    }
    rf_platform_leave(key); return result;
}
int rf_disconnect(struct rf_state *s,uint32_t connection) {
    uint32_t key=rf_platform_enter(); int result=RF_STALE;
    if(connection && s->phase!=RF_IDLE && s->owner==RF_HOST && s->connection==connection) {
        revoke(s); result=RF_OK;
    }
    rf_platform_leave(key); return result;
}
void rf_expire(struct rf_state *s,uint32_t now) {
    uint32_t key=rf_platform_enter(); expire_locked(s,now); rf_platform_leave(key);
}
static int claim_cleanup(struct rf_state *s,const struct rf_ticket *t,struct rf_cleanup *out) {
    if(!out) return RF_INVALID;
    uint32_t key=rf_platform_enter(); int result=RF_BUSY;
    if(t && !exact(s,t)) result=RF_STALE;
    else if(s->phase==RF_CLEANING && !s->in_flight && !s->cleaning_claimed && s->cleanup_step<RF_CLEAN_COUNT) {
        if(next_sequence(s)) {
            s->cleaning_claimed=1; ticket(s,&out->ticket); out->sequence=s->sequence;
            out->step=s->cleanup_step; result=RF_OK;
        } else result=RF_FAULT;
    }
    rf_platform_leave(key); return result;
}
int rf_claim_cleanup(struct rf_state *s,struct rf_cleanup *out) {
    return claim_cleanup(s,0,out);
}
int rf_claim_cleanup_for(struct rf_state *s,const struct rf_ticket *t,struct rf_cleanup *out) {
    if(!t) return RF_INVALID;
    return claim_cleanup(s,t,out);
}
int rf_complete_cleanup(struct rf_state *s,const struct rf_cleanup *c,uint32_t now,int verified,int *success) {
    if(!c || !success) return RF_INVALID;
    *success=0;
    uint32_t key=rf_platform_enter(); int result=RF_STALE;
    expire_locked(s,now);
    if(exact(s,&c->ticket) && s->phase==RF_CLEANING && s->cleaning_claimed &&
       !s->in_flight && c->sequence==s->sequence && c->step==s->cleanup_step) {
        s->cleaning_claimed=0;
        if(verified!=1) { s->phase=RF_UNKNOWN; s->revoked=1; result=RF_FAULT; }
        else {
            ++s->cleanup_step; result=RF_OK;
            if(s->cleanup_step==RF_CLEAN_COUNT) {
                *success=(int)(s->finished && !s->revoked);
                s->phase=RF_IDLE; s->owner=RF_NONE;
            }
        }
    }
    rf_platform_leave(key); return result;
}
