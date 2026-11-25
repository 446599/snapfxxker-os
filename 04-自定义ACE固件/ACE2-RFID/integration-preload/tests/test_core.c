#include "preload_hooks.h"
#include <assert.h>
#include <stdio.h>
struct ph_storage ph_storage;
static unsigned primask,stops,lastlane;
uint32_t ph_irq_enter(void) {unsigned old=primask;primask=1;return old;}
void ph_irq_leave(uint32_t p) {primask=p;}
void ph_stock_power_disable(void *p) {
 assert(primask==1);lastlane=((uintptr_t)p-0x20001014u)/64u;
 assert(lastlane<4 && ph_storage.blocked[lastlane]);++stops;
}
int main(void) {
 for(unsigned lane=0;lane<4;++lane) {
  ph_init_exclusive();stops=0;primask=0;
  assert(ph_claim_reader(lane>>1,lane,1)==PH_OK);
  assert(ph_publish_failure(lane>>1,lane^1,1,PH_AUTH_NEGATIVE)==PH_STALE);
  assert(ph_publish_failure(lane>>1,lane,2,PH_AUTH_NEGATIVE)==PH_STALE);
  assert(stops==0 && primask==0);
  assert(ph_publish_failure(lane>>1,lane,1,PH_AUTH_NEGATIVE)==PH_APPLIED);
  assert(stops==1 && lastlane==lane && primask==0);
  for(unsigned other=0;other<4;++other) assert(!!ph_storage.blocked[other]==(other==lane));
  assert(ph_release_reader(lane>>1,1)==PH_OK);
  assert(ph_claim_reader(lane>>1,lane,2)==PH_BUSY);
  assert(ph_claim_reader(lane>>1,lane^1,2)==PH_OK);
  assert(ph_publish_failure(lane>>1,lane,1,PH_TRANSPORT_UNKNOWN)==PH_STALE);
  primask=1;
  assert(ph_publish_failure(lane>>1,lane^1,2,PH_TRANSPORT_UNKNOWN)==PH_APPLIED);
  assert(primask==1);primask=0;
 }
 ph_init_exclusive();assert(ph_claim_reader(0,2,1)==PH_INVALID);
 assert(ph_claim_reader(1,3,0xffffffffu)==PH_OK);
 assert(ph_release_reader(1,0xffffffffu)==PH_OK);
 assert(ph_claim_reader(1,3,1)==PH_BUSY);
 puts("Core: 4 lanes, sibling isolation, stale ownership, sticky latches, epoch exhaustion, nested PRIMASK PASS");
}
