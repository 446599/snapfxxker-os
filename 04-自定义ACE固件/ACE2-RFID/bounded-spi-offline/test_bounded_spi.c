#include "bounded_spi.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
struct fake { unsigned statuses, writes, reads, restores, selects; int stall_after, cancel_after, fail_write, fail_read, fail_select, fail_status, fail_restore, cancel_on_restore; };
static bool sel(void *p){struct fake *f=p;f->selects++;return !f->fail_select;}
static bool stat(void *p,uint32_t *v){struct fake *f=p;f->statuses++;if(f->fail_status)return false;*v=(f->stall_after && f->statuses >= (unsigned)f->stall_after)?((f->stall_after & 1)?1:2):3;return true;}
static bool wr(void *p,uint8_t v){struct fake *f=p;(void)v;f->writes++;return !f->fail_write || f->writes != (unsigned)f->fail_write;}
static bool rd(void *p,uint8_t *v){struct fake *f=p;f->reads++;*v=0x42;return !f->fail_read || f->reads != (unsigned)f->fail_read;}
static bool rst(void *p){struct fake *f=p;f->restores++;if (f->cancel_on_restore) f->cancel_after=f->statuses;return !f->fail_restore;}
static bool cancel(void *p){struct fake *f=p;return f->cancel_after && f->statuses >= (unsigned)f->cancel_after;}
static enum spi_result run(struct fake *f,struct spi_context *s,uint8_t *out,unsigned budget){struct spi_ops o={f,sel,stat,wr,rd,rst,cancel};return bounded_spi_exchange(s,&o,0x83,0xff,out,2,1,budget);}
int main(void){struct fake f;struct spi_context s;uint8_t out;
#define RESET() do {memset(&f,0,sizeof(f));memset(&s,0,sizeof(s));out=0xa5;} while(0)
RESET();assert(run(&f,&s,&out,4)==SPI_OK && out==0x42 && f.restores==1 && f.statuses==4);
for(int i=1;i<=4;i++){RESET();f.stall_after=i;assert(run(&f,&s,&out,6)==SPI_TIMEOUT && out==0xa5 && f.restores==1 && f.statuses==6 && s.quarantined); unsigned prev=f.selects; assert(run(&f,&s,&out,6)==SPI_QUARANTINED && prev==f.selects);}
for(int i=1;i<=2;i++){RESET();f.fail_write=i;assert(run(&f,&s,&out,8)==SPI_IO_ERROR && out==0xa5 && f.restores==1 && s.quarantined);RESET();f.fail_read=i;assert(run(&f,&s,&out,8)==SPI_IO_ERROR && out==0xa5 && f.restores==1 && s.quarantined);}
RESET();f.fail_select=1;assert(run(&f,&s,&out,8)==SPI_IO_ERROR && f.restores==1 && out==0xa5 && s.quarantined);
RESET();f.fail_status=1;assert(run(&f,&s,&out,8)==SPI_IO_ERROR && f.restores==1 && out==0xa5 && s.quarantined);
for(int i=1;i<=4;i++){RESET();f.cancel_after=i;assert(run(&f,&s,&out,8)==SPI_CANCELLED && f.restores==1 && out==0xa5 && s.quarantined);}
RESET();f.cancel_on_restore=1;assert(run(&f,&s,&out,8)==SPI_CANCELLED && s.quarantined && out==0xa5);
RESET();f.fail_restore=1;assert(run(&f,&s,&out,8)==SPI_QUARANTINED && s.quarantined && out==0xa5);unsigned count=f.selects;assert(run(&f,&s,&out,8)==SPI_QUARANTINED && f.selects==count);
RESET();assert(run(&f,&s,&out,0)==SPI_IO_ERROR && !f.selects && !f.restores);
puts("PASS: finite polling, cancellation, no fake data, cleanup and quarantine");return 0;}
