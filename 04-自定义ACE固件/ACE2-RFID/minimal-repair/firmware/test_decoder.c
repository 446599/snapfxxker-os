#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <assert.h>

/* Include decoder source directly for unit testing on host */
#include "native_tag_decoder.c"

int main() {
    printf("--- Running Native RFID Decoder Unit Tests on Devbox ---\n");

    uint8_t slot[164];
    uint8_t page_buf[256];

    /* Test 1: Anycubic Native Tag */
    memset(slot, 0, sizeof(slot));
    memset(page_buf, 0, sizeof(page_buf));
    page_buf[0] = 0x7B; page_buf[1] = 0x00; page_buf[2] = 0x65; page_buf[3] = 0x00;
    int res1 = decode_native_tag(slot, page_buf, 144, NULL);
    assert(res1 == 0); /* Must return 0 so native Anycubic unpack runs */
    printf("[PASS] Test 1: Anycubic native passthrough verified (return 0)\n");

    /* Test 2: OpenSpool JSON */
    memset(slot, 0, sizeof(slot));
    memset(page_buf, 0, sizeof(page_buf));
    const char *openspool_payload = 
        "\x03\x75\xd1\x02\x71""application/json{"
        "\"sku\":\"SM99\","
        "\"brand\":\"Polymaker\","
        "\"material\":\"PolyLite PLA\","
        "\"color_hex\":\"#38BDF8\","
        "\"temp_min\":205,"
        "\"temp_max\":225,"
        "\"bed_min\":50,"
        "\"bed_max\":60"
        "}";
    memcpy(page_buf, openspool_payload, strlen(openspool_payload));
    int res2 = decode_native_tag(slot, page_buf, strlen(openspool_payload), NULL);
    assert(res2 == 1);
    assert(slot[OFF_STATUS] == 2);
    assert(strcmp((char *)&slot[OFF_SKU], "SM99") == 0);
    assert(strcmp((char *)&slot[OFF_BRAND], "Polymaker") == 0);
    assert(strcmp((char *)&slot[OFF_TYPE], "PolyLite PLA") == 0);
    assert(*(uint16_t *)&slot[OFF_TEMP_MIN] == 205);
    assert(*(uint16_t *)&slot[OFF_TEMP_MAX] == 225);
    assert(*(uint16_t *)&slot[OFF_BED_MIN] == 50);
    assert(*(uint16_t *)&slot[OFF_BED_MAX] == 60);
    uint32_t color2 = *(uint32_t *)&slot[OFF_COLOR];
    assert(((color2 >> 24) & 0xFF) == 0x38);
    assert(((color2 >> 16) & 0xFF) == 0xBD);
    assert(((color2 >> 8) & 0xFF) == 0xF8);
    printf("[PASS] Test 2: OpenSpool NDEF JSON verified (SKU=%s, Brand=%s, Material=%s, Nozzle=%d-%dC, Bed=%d-%dC)\n",
           &slot[OFF_SKU], &slot[OFF_BRAND], &slot[OFF_TYPE],
           *(uint16_t *)&slot[OFF_TEMP_MIN], *(uint16_t *)&slot[OFF_TEMP_MAX],
           *(uint16_t *)&slot[OFF_BED_MIN], *(uint16_t *)&slot[OFF_BED_MAX]);

    /* Test 3: FilaMan NFC payload with numeric sm_id */
    memset(slot, 0, sizeof(slot));
    memset(page_buf, 0, sizeof(page_buf));
    const char *filaman_payload = 
        "{\"filaman\":1,\"sm_id\":24,\"brand\":\"eSUN\",\"type\":\"PETG\",\"color\":\"#FF0055\"}";
    memcpy(page_buf, filaman_payload, strlen(filaman_payload));
    int res3 = decode_native_tag(slot, page_buf, 144, NULL);
    assert(res3 == 1);
    assert(slot[OFF_STATUS] == 2);
    assert(strcmp((char *)&slot[OFF_SKU], "SM24") == 0);
    assert(strcmp((char *)&slot[OFF_BRAND], "eSUN") == 0);
    assert(strcmp((char *)&slot[OFF_TYPE], "PETG") == 0);
    assert(*(uint16_t *)&slot[OFF_TEMP_MIN] == 230);
    assert(*(uint16_t *)&slot[OFF_TEMP_MAX] == 250);
    printf("[PASS] Test 3: FilaMan numeric sm_id verified (SKU=%s, Brand=%s, Material=%s, Nozzle=%d-%dC)\n",
           &slot[OFF_SKU], &slot[OFF_BRAND], &slot[OFF_TYPE],
           *(uint16_t *)&slot[OFF_TEMP_MIN], *(uint16_t *)&slot[OFF_TEMP_MAX]);

    /* Test 4: Prusament NFC */
    memset(slot, 0, sizeof(slot));
    memset(page_buf, 0, sizeof(page_buf));
    const char *prusa_payload = "Prusament PC Blend Jet Black #101010";
    memcpy(page_buf, prusa_payload, strlen(prusa_payload));
    int res4 = decode_native_tag(slot, page_buf, 144, NULL);
    assert(res4 == 1);
    assert(slot[OFF_STATUS] == 2);
    assert(strcmp((char *)&slot[OFF_BRAND], "Prusament") == 0);
    assert(strcmp((char *)&slot[OFF_TYPE], "PC Blend") == 0);
    assert(*(uint16_t *)&slot[OFF_TEMP_MIN] == 265);
    assert(*(uint16_t *)&slot[OFF_TEMP_MAX] == 285);
    assert(*(uint16_t *)&slot[OFF_BED_MIN] == 100);
    assert(*(uint16_t *)&slot[OFF_BED_MAX] == 115);
    printf("[PASS] Test 4: Prusament verified (Brand=%s, Material=%s, Nozzle=%d-%dC, Bed=%d-%dC)\n",
           &slot[OFF_BRAND], &slot[OFF_TYPE],
           *(uint16_t *)&slot[OFF_TEMP_MIN], *(uint16_t *)&slot[OFF_TEMP_MAX],
           *(uint16_t *)&slot[OFF_BED_MIN], *(uint16_t *)&slot[OFF_BED_MAX]);

    /* Test 5: Creality CFS */
    memset(slot, 0, sizeof(slot));
    memset(page_buf, 0, sizeof(page_buf));
    const char *creality_payload = "Creality Hyper PLA White #FFFFFF";
    memcpy(page_buf, creality_payload, strlen(creality_payload));
    int res5 = decode_native_tag(slot, page_buf, 144, NULL);
    assert(res5 == 1);
    assert(slot[OFF_STATUS] == 2);
    assert(strcmp((char *)&slot[OFF_BRAND], "Creality") == 0);
    assert(strcmp((char *)&slot[OFF_TYPE], "Hyper PLA") == 0);
    assert(*(uint16_t *)&slot[OFF_TEMP_MIN] == 190);
    assert(*(uint16_t *)&slot[OFF_TEMP_MAX] == 230);
    assert(*(uint16_t *)&slot[OFF_BED_MIN] == 45);
    assert(*(uint16_t *)&slot[OFF_BED_MAX] == 60);
    printf("[PASS] Test 5: Creality CFS verified (Brand=%s, Material=%s, Nozzle=%d-%dC, Bed=%d-%dC)\n",
           &slot[OFF_BRAND], &slot[OFF_TYPE],
           *(uint16_t *)&slot[OFF_TEMP_MIN], *(uint16_t *)&slot[OFF_TEMP_MAX],
           *(uint16_t *)&slot[OFF_BED_MIN], *(uint16_t *)&slot[OFF_BED_MAX]);

    /* Unauthenticated UIDs must not produce guessed Bambu metadata. */
    memset(slot, 0, sizeof(slot));
    slot[3] = 1; slot[4] = 2; slot[5] = 3; slot[6] = 4; slot[7] = 4;
    assert(decode_native_tag(slot, page_buf, 0, NULL) == -1);
    uint8_t resp[144] = {0}; uint8_t uid[5] = {1,2,3,4,4};
    assert(decode_cmd68_uid_tag(resp, uid, 3) == 0);
    printf("[PASS] UID-only paths do not invent Bambu metadata\n");

    printf("\n>>> ALL 6 TAG DECODER SUITES PASSED VERIFICATION <<<\n");
    return 0;
}
