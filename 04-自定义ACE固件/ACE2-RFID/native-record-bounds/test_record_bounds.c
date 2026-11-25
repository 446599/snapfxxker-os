#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
#include <sys/mman.h>
#include <unistd.h>
#define __arm__ 1
#define ACE_DECODER_TEST 1
#include "native_tag_decoder.c"

static const uintptr_t records[] = {0x2000160c,0x200016b0,0x20001754,0x200017f8};
static unsigned char uid[5] = {0x55,0xf3,0x28,0xe6,0x68};
static unsigned char material[16] = "PLA Basic";
static unsigned char data5[16] = {0x6f,0xca,0xef,255,0xe8,3,0,0,0,0,0xe0,0x3f};
static int reads, stops, fail_at, mismatch, select_mismatch;
int test_auth(void *r, uint8_t m, uint8_t b, const uint8_t *k, const uint8_t *u) {(void)r;(void)m;(void)b;(void)k;(void)u;return 1;}
int test_read(void *r,uint8_t block,uint8_t *out) {(void)r;reads++;if(reads==fail_at)return 0;memcpy(out,block==4?material:data5,16);if(mismatch && reads==4)out[0]^=1;return 1;}
void test_stop(void *r) {(void)r;stops++;}
int rfid_select(void *ctx,uint8_t *buf,int mode) {(void)ctx;(void)mode;memcpy(buf+13,uid,5);if(select_mismatch)buf[13]^=1;return 0;}

static int allowed(int i) {
 return i == 22 || (i >= 26 && i < 28) || (i >= 28 && i < 88) ||
        (i >= 88 && i < 92) || (i >= 104 && i < 108) ||
        (i >= 124 && i < 130) || (i >= 132 && i < 136);
}
static void check_one(uintptr_t addr, void *ctx, int json) {
 uint8_t *mem=(uint8_t *)0x20001000, *rec=(uint8_t *)addr;
 uint8_t before[4096], page[192]={0};
 memset(mem,0xa5,4096);memcpy(rec+3,uid,5);
 if(json) strcpy((char *)page,"{\"filaman\":1,\"sm_id\":24,\"brand\":\"eSUN\",\"type\":\"PETG\"}");
 memcpy(before,mem,4096);reads=stops=fail_at=mismatch=select_mismatch=0;
 assert(decode_native_tag(rec,page,json?144:0,ctx)==1);
 assert(rec[22]==2); if(!json){assert(reads==4 && stops==1);assert(!memcmp(rec+28,"SM55F328E6",10));}
 for(size_t i=0;i<4096;i++) if(mem[i]!=before[i]) {
   uintptr_t at=0x20001000+i;
   assert(at>=addr && at<addr+164 && allowed((int)(at-addr)));
 }
}
int main(void) {
 void *p=mmap((void *)0x20000000,16384,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON|MAP_FIXED,-1,0);
 if(p==MAP_FAILED){perror("mmap fixed fake SRAM");return 1;}
 unsigned char context[16]={0}, reader[64]={0};void *reader_ptr=reader;memcpy(context+4,&reader_ptr,sizeof reader_ptr);
 for(int i=0;i<4;i++){check_one(records[i],context,0);check_one(records[i],context,1);}
 uint8_t *mem=(uint8_t *)p+4096,before[4096],page[192]={0};
 uintptr_t bad[]={0,0x20001608,0x2000160d,0x20001610,0x200016af,0x20001748,0x2000189c,0x200018a4,0x20001900};
 for(unsigned i=0;i<sizeof bad/sizeof bad[0];i++){
   memset(mem,0x5a,4096);memcpy(before,mem,4096);reads=stops=0;
   assert(decode_native_tag((uint8_t *)bad[i],page,0,context)==-1);
   assert(!memcmp(mem,before,4096) && reads==0 && stops==0);
 }
 for(int i=0;i<4;i++) for(int mode=1;mode<=6;mode++){
   memset(mem,0xa5,4096);uint8_t *rec=(uint8_t *)records[i];memcpy(rec+3,uid,5);memcpy(before,mem,4096);
   reads=stops=0;fail_at=mode<=4?mode:0;mismatch=mode==5;select_mismatch=mode==6;
   assert(decode_native_tag(rec,page,0,context)==-1);assert(!memcmp(mem,before,4096));
   assert(stops==(mode==6?0:1));
 }
 puts("four record addresses, field boundaries, invalid pointers and Bambu failures: PASS");
 assert(!munmap(p,16384));return 0;
}
