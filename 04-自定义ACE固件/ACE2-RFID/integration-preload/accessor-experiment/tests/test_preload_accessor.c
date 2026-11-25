#include "event_mock.h"
#include <assert.h>
#include <stdio.h>
static uint32_t irq;
static unsigned stops;
static uint32_t lastlane;
uint32_t ph_irq_enter(void) {uint32_t old=irq;irq=1;return old;}
void ph_irq_leave(uint32_t old) {irq=old;}
void ph_stock_power_disable(void *motor) {
    assert(irq==1);
    lastlane=((uintptr_t)motor-0x20001014u)/64u;
    assert(lastlane<4);
    assert(ph_platform_state() && ph_platform_state()->blocked[lastlane]);
    ++stops;
}
static void invalid_calls(void) {
    unsigned before=stops;
    assert(ph_platform_state()==0);
    assert(ph_claim_reader(0,0,1)==PH_INVALID);
    assert(ph_release_reader(0,1)==PH_INVALID);
    assert(ph_publish_failure(0,0,1,PH_AUTH_NEGATIVE)==PH_INVALID);
    assert(stops==before && irq==0);
}
int main(void) {
    for(unsigned lane=0;lane<4;++lane) {
        mock_storage_reset();irq=stops=0;
        invalid_calls();
        uint32_t p=ex_event_factory();assert(p);
        assert(mock_last_store==p+44u && mock_last_value==EX_STORAGE_READY);
        assert(mock_handle==0 && !ph_platform_state());
        mock_handle=p;
        struct ex_storage *b=ex_storage_current();
        struct ph_storage *s=ph_platform_state();
        assert(b && s==&b->preload);
        for(unsigned i=0;i<4;++i) assert(!s->blocked[i]);
        for(unsigned i=0;i<2;++i) assert(!s->reader[i].epoch && !s->reader[i].owned && s->reader[i].lane==UINT32_MAX);
        assert(ph_claim_reader(lane>>1,lane,1)==PH_OK);
        assert(ph_publish_failure(lane>>1,lane^1,1,PH_AUTH_NEGATIVE)==PH_STALE);
        assert(ph_publish_failure(lane>>1,lane,2,PH_AUTH_NEGATIVE)==PH_STALE);
        assert(stops==0);
        assert(ph_publish_failure(lane>>1,lane,1,PH_AUTH_NEGATIVE)==PH_APPLIED);
        assert(stops==1 && lastlane==lane && irq==0);
        assert(ph_release_reader(lane>>1,1)==PH_OK);
        assert(ph_claim_reader(lane>>1,lane,2)==PH_BUSY);
        assert(ph_claim_reader(lane>>1,lane^1,2)==PH_OK);
        irq=1;
        assert(ph_publish_failure(lane>>1,lane^1,2,PH_TRANSPORT_UNKNOWN)==PH_APPLIED);
        assert(irq==1);irq=0;
        /* An existing handle cannot rebuild the object and erase latches. */
        assert(ex_event_factory()==0 && s->blocked[lane]);
        uint32_t raw=ex_test_load32(p-4u);
        ex_test_store32(p-4u,raw&0x7fffffffu);invalid_calls();
        ex_test_store32(p-4u,raw);
        assert(ph_platform_state()==s && s->blocked[lane]);
        const uint32_t old[3]={0x45585431u,144u,0x52445931u};
        const unsigned off[3]={32,36,44};
        for(unsigned j=0;j<3;j++) {
            uint32_t saved=ex_test_load32(p+off[j]);
            ex_test_store32(p+off[j],old[j]);invalid_calls();
            ex_test_store32(p+off[j],saved);
        }
    }
    for(unsigned extra=0;extra<=16;extra+=8) {
        mock_storage_reset();mock_expansion_fail=1;mock_extra=extra;
        uint32_t p=ex_event_factory();assert(p);mock_handle=p;
        mock_tail_reads=0;invalid_calls();assert(!mock_tail_reads);
    }
    puts("PASS dynamic preload accessor: initialized layout, invalid/fallback/version rejection, ownership and sticky fault retention. No device proof.");
}
