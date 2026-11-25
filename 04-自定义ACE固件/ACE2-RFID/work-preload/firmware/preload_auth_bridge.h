#ifndef PRELOAD_AUTH_BRIDGE_H
#define PRELOAD_AUTH_BRIDGE_H
#include "preload_failure.h"
#include "bounded_rc522.h"
/* Provided by a future bounded, owned AUTH state machine, not by one SPI call. */
enum pl_auth_evidence { PL_AUTH_NONE, PL_AUTH_VERIFIED, PL_AUTH_TERMINAL_NEGATIVE, PL_AUTH_UNCERTAIN };
enum pl_auth_result pl_classify_auth(enum br_result, enum pl_auth_evidence);
#endif
