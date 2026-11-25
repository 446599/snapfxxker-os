#include "event_mock.h"
#include <assert.h>
#include <stdio.h>
int main(void) {
    unsigned checks=0;
    mock_storage_reset();uint32_t p=ex_event_factory();assert(p);++checks;
    assert(!mock_handle && ex_storage_current()==0);++checks;
    assert(mock_barriers==1 && mock_last_store==p+44 && mock_last_value==0x52445932u);++checks;
    mock_handle=p;struct ex_storage *b=ex_storage_current();assert(b);++checks;
    assert(ph_platform_state()==&b->preload);++checks;
    assert(EX_STORAGE_MAGIC==0x45585432u && EX_STORAGE_READY==0x52445932u);++checks;
    for(unsigned i=0;i<4;i++){assert(!b->preload.blocked[i]);++checks;}
    for(unsigned i=0;i<2;i++){
        assert(!b->preload.reader[i].owned && !b->preload.reader[i].epoch && b->preload.reader[i].lane==0xffffffffu);++checks;
    }
    const unsigned offset[]={0,4,12};const uint32_t old[]={0x45585431u,144,0x52445931u};
    for(unsigned i=0;i<3;i++){
        uint32_t address=p+32+offset[i],save=ex_test_load32(address);
        ex_test_store32(address,old[i]);assert(!ex_storage_current() && !ph_platform_state());++checks;
        ex_test_store32(address,save);
    }
    uint32_t saved=ex_test_load32(p-4);
    const uint32_t oldblocks[]={40,48,56,184,192,200};
    for(unsigned i=0;i<6;i++){
        ex_test_store32(p-4,oldblocks[i]|0x80000000u);mock_tail_reads=0;
        assert(!ex_storage_current() && !ph_platform_state() && !mock_tail_reads);++checks;
    }
    ex_test_store32(p-4,saved);assert(ex_storage_current()==b);++checks;
    printf("PASS %u independent v2 factory/ready-last/old-version/accessor checks. Zero blocked is NOT motor-ready.\n",checks);
}
