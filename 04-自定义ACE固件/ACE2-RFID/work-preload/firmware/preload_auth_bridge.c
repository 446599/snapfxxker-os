#ifndef PRELOAD_OFFLINE_TEST
#error "No verified owned authentication or motion adapter exists; offline only"
#endif
#include "preload_auth_bridge.h"
enum pl_auth_result pl_classify_auth(enum br_result transport,enum pl_auth_evidence evidence) {
    /* Never turn TIMEOUT/no-reply/cancellation into a claimed rejected key,
     * and never turn a successful register read into authenticated identity. */
    if(transport!=BR_OK) return PL_AUTH_TRANSPORT_UNKNOWN;
    switch(evidence) {
    case PL_AUTH_NONE: return PL_AUTH_NOT_ATTEMPTED;
    case PL_AUTH_VERIFIED: return PL_AUTH_OK;
    case PL_AUTH_TERMINAL_NEGATIVE: return PL_AUTH_REJECTED;
    default: return PL_AUTH_TRANSPORT_UNKNOWN;
    }
}
