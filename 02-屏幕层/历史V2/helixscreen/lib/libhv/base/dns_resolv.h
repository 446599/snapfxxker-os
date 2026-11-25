// SPDX-License-Identifier: GPL-3.0-or-later
//
// Minimal DNS resolver fallback for statically-linked glibc builds.
// When getaddrinfo() fails (can't load NSS modules), this provides
// direct UDP DNS resolution as a fallback.

#ifndef DNS_RESOLV_H_
#define DNS_RESOLV_H_

#include "hexport.h"
#include "hplatform.h"

#define DNS_RESOLV_MAX_NAMESERVERS  3
#define DNS_RESOLV_NAMESERVER_LEN   64
#define DNS_RESOLV_MAX_PACKET_LEN   512
#define DNS_RESOLV_TIMEOUT_MS       2000
#define DNS_RESOLV_FALLBACK_NS      "8.8.8.8"

BEGIN_EXTERN_C

// Build a DNS A-record query packet for the given hostname.
// Returns packet length on success, -1 on error.
HV_EXPORT int dns_resolv_build_query(const char* hostname, uint8_t* buf, int buflen);

// Parse a DNS response and extract the first A-record IPv4 address.
// Returns 0 on success, -1 on error.
HV_EXPORT int dns_resolv_parse_response(const uint8_t* buf, int len, struct in_addr* addr);

// Parse nameservers from a resolv.conf-format file.
// Returns number of nameservers found (up to max).
HV_EXPORT int dns_resolv_get_nameservers_from(const char* path,
                                              char nameservers[][DNS_RESOLV_NAMESERVER_LEN],
                                              int max);

// Parse nameservers from /etc/resolv.conf.
HV_EXPORT int dns_resolv_get_nameservers(char nameservers[][DNS_RESOLV_NAMESERVER_LEN], int max);

// Resolve hostname to IPv4 via direct UDP DNS queries.
// Tries nameservers from /etc/resolv.conf, falls back to 8.8.8.8.
// Returns 0 on success, -1 on error.
HV_EXPORT int dns_resolv_resolve(const char* hostname, struct in_addr* addr);

END_EXTERN_C

#endif // DNS_RESOLV_H_
