#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <assert.h>
#define ACE_DECODER_TEST 1
#include "native_tag_decoder.c"
static unsigned char material[16] = "PETG Basic";
static unsigned char data5[16] = {0,0,0,255,0xe8,0x03,0,0,0,0,0xe0,0x3f,0,0,0,0};
static int auth_fail, reads, fail_read, mutate, stops;
int test_auth(void *r, uint8_t m, uint8_t b, const uint8_t *k, const uint8_t *u) {(void)r;(void)m;(void)b;(void)k;(void)u;return !auth_fail;}
int test_read(void *r, uint8_t block, uint8_t *out) {
    (void)r; reads++; if (reads == fail_read) return 0;
    memcpy(out, block == 4 ? material : data5, 16);
    if (mutate && reads == 4) out[0] ^= 1;
    return 1;
}
void test_stop(void *r) {(void)r; stops++;}
static void reset(void) {auth_fail=reads=fail_read=mutate=stops=0;}
int main(void) {
    uint8_t uid[] = {1,2,3,4,4}; uint8_t selected[64] = {0}; decoded_tag_t t;
    assert(rc522_block_result_ok(0, 128, 0));
    assert(!rc522_block_result_ok(2, 128, 0));
    assert(!rc522_block_result_ok(238, 128, 0));
    assert(!rc522_block_result_ok(0, 127, 0));
    for (int e = 1; e < 256; ++e) assert(!rc522_block_result_ok(0, 128, e));
    memcpy(selected+13, uid, 5);
    assert(selection_matches(uid, selected, 0));
    assert(!selection_matches(uid, selected, 1));
    selected[13] ^= 1; assert(!selection_matches(uid, selected, 0)); selected[13] ^= 1;
    reset(); memset(&t, 0xA5, sizeof(t));
    assert(decode_bambu_classic(&t, uid, (void *)1, 0));
    assert(reads == 4 && stops == 1 && t.version == 0x0102);
    assert(!strcmp(t.type,"PETG Basic") && !strcmp(t.brand,"Bambu Lab"));
    assert(t.color == 0x000000ff && t.total_grams == 1000 && t.diameter == 175);
    assert(t.temp_min == 0 && t.temp_max == 0 && t.bed_min == 0 && t.bed_max == 0);
    data5[0]=data5[1]=data5[2]=255; data5[4]=0xf4; data5[5]=0x01; reset();
    assert(decode_bambu_classic(&t, uid, (void *)1, 0)); assert(t.color == 0xffffffff && t.total_grams == 500);
    memcpy(material, "0123456789ABCDEF", 16); reset();
    assert(decode_bambu_classic(&t, uid, (void *)1, 0) && !strcmp(t.type,"0123456789ABCDEF"));
    memset(material, 0, 16); memcpy(material, "PETG Basic", 10);
    for(int f=1; f<=4; ++f) {reset(); fail_read=f; memset(&t,0xA5,sizeof(t)); assert(!decode_bambu_classic(&t,uid,(void *)1,0)); assert(stops==1 && (unsigned char)t.sku[0]==0xA5);}
    reset(); auth_fail=1; assert(!decode_bambu_classic(&t,uid,(void *)1,0)); assert(stops==1 && reads==0);
    reset(); mutate=1; assert(!decode_bambu_classic(&t,uid,(void *)1,0)); assert(stops==1);
    reset(); assert(!decode_bambu_classic(&t,uid,0,0) && stops==0);
    assert(!decode_bambu_classic(&t,uid,(void *)1,1));
    uid[4] ^= 1; assert(!decode_bambu_classic(&t,uid,(void *)1,0)); uid[4] ^= 1;
    material[1]=1; reset(); assert(!decode_bambu_classic(&t,uid,(void *)1,0)); assert(stops==1); material[1]='E';
    memset(material, 0, sizeof(material)); memcpy(material, "   ", 3); reset(); assert(!decode_bambu_classic(&t,uid,(void *)1,0)); assert(stops==1);
    data5[3]=0; reset(); assert(!decode_bambu_classic(&t,uid,(void *)1,0)); assert(stops==1);
    puts("Bambu verified decode, failures, and SELECT binding: PASS");
}
