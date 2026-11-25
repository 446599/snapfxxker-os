#define main original_integration_main
#include "test_integration.c"
#undef main
#include "decoder_typed_bridge.h"
static const uint8_t auth_key[6]={1,2,3,4,5,6};
static struct ar_selection selected={0,{1,2,3,4,4},{1,2,3,4,4}};
static void typed_reset(void) {
    reset(10000);f.typed_mode=1;f.auth_status=8;f.auth_irq=0x10;f.irq=0;
}
static struct br_decoder_report probe(void) {return br_decoder_probe(&reader,&selected,auth_key);}
int main(void) {
    typed_reset();struct br_decoder_report r=probe();unsigned total=f.sr_reads;
    CHECK(r.cleanup_status_valid && r.cleanup==BR_OK);
    CHECK(r.primary.code==AR_BLOCK_READ && r.primary.transport==BR_OK && r.primary.transport_status_valid);
    CHECK(r.primary.select_result==0 && r.primary.auth_command_attempted==1 && r.primary.auth_command_issued==1);
    CHECK(r.primary.snapshot_valid && r.primary.status2==8 && r.primary.irq==0x10 && r.primary.error==0 && r.primary.status_polls==1);
    CHECK(f.auth_commands==1 && f.read_commands==4);
    CHECK(!(f.registers[8]&8) && !f.registers[1] && !f.registers[10] && !f.irq);
    fault_check(); /* observation never authorizes publication/release */
    for(unsigned p=1;p<=total;p++) {
        typed_reset();f.fail_at=p;f.error_mask=0x40;r=probe();
        CHECK((r.primary.code!=AR_BLOCK_READ || r.cleanup!=BR_OK) && r.primary.code!=AR_AUTH_NEGATIVE);fault_check();
        typed_reset();f.cancel_at=p;r=probe();
        CHECK((r.primary.code!=AR_BLOCK_READ || r.cleanup!=BR_OK) && r.primary.code!=AR_AUTH_NEGATIVE);fault_check();
    }
    typed_reset();f.auth_status=0xff;r=probe();CHECK(r.primary.code==AR_INVALID_SNAPSHOT && !f.read_commands);fault_check();
    typed_reset();f.auth_status=0;r=probe();CHECK(r.primary.code==AR_AUTH_NOT_ESTABLISHED && !f.read_commands);fault_check();
    typed_reset();f.auth_irq=1;r=probe();CHECK(r.primary.code==AR_NO_REPLY && !f.read_commands);fault_check();
    typed_reset();f.auth_status=f.auth_irq=0;r=probe();CHECK(r.primary.code==AR_AUTH_POLL_LIMIT && !f.read_commands);fault_check();
    typed_reset();selected.raw_result=1;r=probe();CHECK(r.primary.code==AR_SELECT_NOT_OK && !f.auth_commands);selected.raw_result=0;fault_check();
    typed_reset();selected.selected_uid[0]^=1;r=probe();CHECK(r.primary.code==AR_UID_MISMATCH && !f.auth_commands);selected.selected_uid[0]^=1;fault_check();
    /* Regression: READ records cannot erase the observed AUTH history. */
    typed_reset();f.fail_at=300;f.error_mask=0x40;r=probe();
    CHECK(r.primary.code!=AR_BLOCK_READ && r.primary.transport_status_valid);
    CHECK(r.primary.select_result==0 && r.primary.auth_command_attempted==1 && r.primary.auth_command_issued==1);
    CHECK(r.primary.snapshot_valid && r.primary.status2==8 && r.primary.irq==0x10 && r.primary.error==0);fault_check();
    /* SELECT=2 performs no AUTH. A real cleanup timeout is nevertheless a
     * valid separate cleanup transport result and must retain raw SELECT. */
    typed_reset();selected.raw_result=2;ex_state.transaction.polls_left=1;r=probe();
    CHECK(r.primary.stage==AR_STAGE_SELECT && r.primary.code==AR_SELECT_NOT_OK);
    CHECK(r.primary.select_result==2 && !r.primary.transport_status_valid);
    CHECK(r.cleanup==BR_TIMEOUT && r.cleanup_status_valid==1);
    CHECK(!r.primary.auth_command_attempted && !r.primary.auth_command_issued && !r.primary.snapshot_valid);
    CHECK(!f.auth_commands && !f.read_commands);selected.raw_result=0;fault_check();
    /* First AUTH BR_TIMEOUT must survive cleanup's BR_QUARANTINED. */
    typed_reset();ex_state.transaction.polls_left=1;r=probe();
    CHECK(r.primary.code==AR_TRANSPORT_TIMEOUT && r.primary.transport==BR_TIMEOUT);
    CHECK(r.primary.transport_status_valid==1 && r.primary.select_result==0);
    CHECK(r.cleanup==BR_QUARANTINED && r.cleanup_status_valid==1);fault_check();
    typed_reset();ex_state.transaction.polls_left=133;r=probe();
    CHECK(r.primary.stage==AR_STAGE_AUTH && r.primary.auth_command_attempted==1 && !r.primary.auth_command_issued);
    CHECK(r.primary.code==AR_TRANSPORT_TIMEOUT && r.primary.transport==BR_TIMEOUT && r.primary.transport_status_valid);
    CHECK(r.cleanup==BR_QUARANTINED && r.cleanup_status_valid);fault_check();
    typed_reset();mock_task+=0x100;r=probe();CHECK(r.primary.code==AR_INVALID && !f.sr_reads);fault_check();
    printf("PASS %u typed-runtime assertions; AUTH/four-read/register-abort fixture %u SPI polls. No identity, SELECT freshness or physical cleanup proof.\n",assertions,total);
}
