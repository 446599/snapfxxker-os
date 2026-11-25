/* SPDX-License-Identifier: MIT */
#include <stdint.h>
#include <stddef.h>

#define EVENT_HANDLE 0x20001e64u
#define MAGIC 0x49504f31u
#define BUILD_ID 0x20261114u
#define PARK_MM 400u
#define PENDING_MS 10000u
#define MOTORS 0x20001014u

struct policy {
    uint32_t magic, bytes, parent, session;
    uint32_t configured, automatic, revision, padding;
    uint32_t epoch[4], deadline[4];
    uint32_t active_epoch[4], active_deadline[4];
    uint8_t pending[4], result[4], active[4], reserved[4];
    uint32_t recovery_epoch[4], recovery_since[4];
    uint8_t recovery_pending[4], recovery_samples[4];
};
_Static_assert(sizeof(struct policy) % 8u == 0, "event tail alignment");
const uint32_t ip_runtime_bytes = sizeof(struct policy);

extern uint32_t ip_stock_malloc(uint32_t);
extern uint32_t ip_stock_factory(void);
extern void ip_stock_register(uint32_t, void *);
extern int ip_stock_feed(uint32_t, const uint32_t *, uint32_t *);
extern void ip_stock_power_stop(void *);

static uint32_t word(uint32_t address) { return *(volatile uint32_t *)(uintptr_t)address; }
static uint8_t byte(uint32_t address) { return *(volatile uint8_t *)(uintptr_t)address; }
static uint32_t now(void) { return word(0x2000820cu); }
static uint32_t lock(void) {
    uint32_t previous;
    __asm__ volatile("mrs %0,PRIMASK\n cpsid i\n dmb" : "=r"(previous) :: "memory");
    return previous;
}
static void unlock(uint32_t previous) {
    __asm__ volatile("dmb\n msr PRIMASK,%0" :: "r"(previous) : "memory");
}
static int allocation(uint32_t parent) {
    const uint32_t required = (32u + sizeof(struct policy) + 15u) & ~7u;
    if ((parent & 7u) || parent < 0x200026a0u || parent > 0x20007e88u - required) return 0;
    uint32_t raw = word(parent - 4u), size = raw & 0x7fffffffu;
    return !word(parent - 8u) && (raw & 0x80000000u) && !(size & 7u) &&
        size >= required && size - required <= 16u;
}
static struct policy *state(void) {
    uint32_t parent = word(EVENT_HANDLE);
    if (!allocation(parent)) return 0;
    struct policy *p = (void *)(uintptr_t)(parent + 32u);
    return p->magic == MAGIC && p->bytes == sizeof(*p) && p->parent == parent ? p : 0;
}
uint32_t ip_factory(void) {
    uint32_t parent = ip_stock_malloc(32u + sizeof(struct policy));
    if (!parent || !allocation(parent)) return ip_stock_factory();
    volatile uint32_t *event = (void *)(uintptr_t)parent;
    event[0] = event[1] = 0;
    event[2] = parent + 12u;
    event[3] = UINT32_MAX;
    event[4] = event[5] = parent + 12u;
    *(volatile uint8_t *)(uintptr_t)(parent + 28u) = 0;
    struct policy *p = (void *)(uintptr_t)(parent + 32u);
    for (uint32_t i = 0; i < sizeof(*p); ++i) ((uint8_t *)p)[i] = 0;
    p->bytes = sizeof(*p); p->parent = parent; p->automatic = 1;
    __asm__ volatile("dmb" ::: "memory");
    p->magic = MAGIC;
    return parent;
}

/* Called in the original key callback. Never starts a motor here: the command
 * task consumes a fresh insertion epoch with START. Falling edges always resume
 * the stock callback, including its error/state cleanup. */
int ip_insert(void *object, uint32_t key, uint32_t before, uint32_t after) {
    (void)object;
    if (key > 12u || key % 4u || before > 1u || after > 1u || before == after) return 0;
    uint32_t slot = key / 4u, previous = lock();
    struct policy *p = state();
    if (!p) { unlock(previous); return after == 1u; }
    if (p->epoch[slot] == UINT32_MAX) {
        p->configured = 0; p->pending[slot] = 0;
        unlock(previous); return after == 1u;
    }
    ++p->epoch[slot]; p->pending[slot] = 0; p->result[slot] = 0;
    p->recovery_pending[slot] = 0; p->recovery_samples[slot] = 0;
    if (!after && word(0x20001004u + slot * 4u) == 129u) {
        p->recovery_pending[slot] = 1; p->recovery_epoch[slot] = p->epoch[slot];
        p->recovery_since[slot] = now();
    }
    if (!after) { unlock(previous); return 0; }
    if (!p->configured) { p->result[slot] = 4; unlock(previous); return 1; }
    if (p->automatic) { unlock(previous); return 0; }
    /* Same slot record span as the existing RC522 clear-cache operation. */
    volatile uint8_t *record = (void *)(uintptr_t)(0x20000054u + slot * 164u + 286u);
    for (uint32_t i = 0; i < 64u; ++i) record[i] = 0;
    if (word(0x20001004u + slot * 4u) == 0u) {
        p->pending[slot] = 1; p->deadline[slot] = now() + PENDING_MS; p->result[slot] = 2;
    } else p->result[slot] = 4;
    unlock(previous);
    return 1;
}

/* Called under the assembly hook's IRQ mask, before native status store. */
void ip_status_changed(uint32_t address) {
    struct policy *p = state();
    if (p && address >= 0x20001004u && address < 0x20001014u && !(address & 3u)) {
        unsigned i = (address - 0x20001004u) / 4u;
        p->recovery_pending[i] = 0; p->recovery_samples[i] = 0;
    }
}
void ip_new_feed(uint32_t slot) {
    struct policy *p = state();
    if (p && slot < 4u) { p->recovery_pending[slot] = 0; p->recovery_samples[slot] = 0; }
}
/* Refuse to infer idle from the status word: both native workers and both
 * group queues must be idle, with structurally empty queue sentinel links. */
static int native_quiet(void) {
    const uint32_t c = 0x200012e8u;
    if (word(c+20u) || word(c+32u)) return 0;
    for (unsigned group=0; group<2; ++group) {
        uint32_t q = c + group*208u + 60u, head = q+8u;
        if (word(q) || word(head+4u) != head || word(head+8u) != head) return 0;
    }
    for (unsigned i=0; i<4; ++i) if (byte(MOTORS+i*64u+6u)) return 0;
    return 1;
}
static void recovery_poll(struct policy *p) {
    for (unsigned i=0; i<4; ++i) {
        if (!p->recovery_pending[i]) continue;
        if (p->epoch[i] != p->recovery_epoch[i] || byte(0x20001a15u+i*16u) != 0u ||
            word(0x20001004u+i*4u) != 129u) {
            p->recovery_pending[i]=0; p->recovery_samples[i]=0; continue;
        }
        if (!native_quiet()) { p->recovery_since[i]=now(); p->recovery_samples[i]=0; continue; }
        if (!p->recovery_samples[i]) p->recovery_since[i] = now();
        if (p->recovery_samples[i] < 3u) ++p->recovery_samples[i];
        if (p->recovery_samples[i] >= 3u && (uint32_t)(now()-p->recovery_since[i]) >= 2000u) {
            *(volatile uint32_t *)(uintptr_t)(0x20001004u+i*4u) = 0u;
            p->recovery_pending[i]=0; p->recovery_samples[i]=0;
        }
    }
}

static uint32_t read32(const uint8_t *b) {
    return b[0] | (uint32_t)b[1] << 8 | (uint32_t)b[2] << 16 | (uint32_t)b[3] << 24;
}
static void write32(uint8_t *b, uint32_t value) {
    for (unsigned i = 0; i < 4; ++i) b[i] = (uint8_t)(value >> (8u * i));
}
static void expire(struct policy *p) {
    for (unsigned i = 0; i < 4; ++i) {
        if (p->pending[i] && (int32_t)(now() - p->deadline[i]) >= 0) {
            p->pending[i] = 0; p->result[i] = 5;
        }
    }
}

/* Hooks at native motor activation, GPIO and both PWM writes run with IRQs
 * masked. Only the newly introduced automatic move is guarded; manual native
 * operations retain their original behavior once DISCARD releases the slot. */
int ip_motor_allowed(void *motor) {
    uint32_t address = (uint32_t)(uintptr_t)motor;
    if (address < MOTORS || address >= MOTORS + 4u * 64u || (address-MOTORS)%64u) return 0;
    unsigned slot = (address-MOTORS)/64u;
    struct policy *p = state();
    if (!p || !p->active[slot]) return 1;
    if (p->configured && !p->automatic && p->active_epoch[slot] == p->epoch[slot]
            && byte(0x20001a15u + slot * 16u) == 1u
            && (int32_t)(now() - p->active_deadline[slot]) < 0) return 1;
    p->result[slot] = 6;
    ip_stock_power_stop(motor);
    return 0;
}

/* CMD93 raw ABI, same dispatch contract as the stock registered handlers.
 * op 0 QUERY, 1 SET policy/session, 2 START(epoch), 3 DISCARD(epoch).
 * Native CMD8 handles queueing, finite-distance control, jam and stop behavior. */
int ip_dispatch(uint32_t unused, const uint8_t *request, uint32_t size,
                uint8_t *response, uint16_t *response_size) {
    (void)unused;
    if (size != 24u || read32(request) != 0x31504941u || request[4] != 1u ||
        request[5] > 3u || request[6] > 3u || request[7] > 1u) {
        *response_size = 0; return 0;
    }
    uint32_t previous = lock(), code = 0, start = 0;
    struct policy *p = state();
    const unsigned op = request[5], slot = request[6];
    const uint32_t session = read32(request + 8), epoch = read32(request + 12);
    if (!p) code = 1;
    else {
        expire(p);
        if (op == 0u) recovery_poll(p);
        if (op == 1u) {
            if (!session || read32(request+16) != p->session || read32(request+20) != p->revision
                    || p->revision == UINT32_MAX) code = 2;
            for (unsigned i = 0; i < 4; ++i)
                if (word(0x20001004u + i * 4u) != 0u) code = 3;
            if (!code && (!p->configured || p->session != session || p->automatic != request[7])) {
                for (unsigned i = 0; i < 4; ++i) { p->pending[i] = 0; p->result[i] = 0; p->active[i] = 0; }
                p->session = session; p->automatic = request[7]; p->configured = 1;
                ++p->revision;
            }
        } else if (op >= 2u) {
            if (!p->configured || !session || p->session != session || p->epoch[slot] != epoch) code = 4;
            else if (op == 3u) {
                if (word(0x20001004u + slot * 4u) || byte(MOTORS + slot*64u + 6u)) code = 3;
                else { p->pending[slot] = 0; p->result[slot] = 3; p->active[slot] = 0; }
            }
            else if (p->result[slot] == 1u && !p->pending[slot]) { /* exact duplicate: query only */ }
            else if (p->automatic || !p->pending[slot] ||
                     byte(0x20001a15u + slot * 16u) != 1u ||
                     word(0x20001004u + slot * 4u) != 0u) code = 5;
            else {
                p->pending[slot] = 0; p->result[slot] = 6; start = 1;
                p->active[slot] = 1; p->active_epoch[slot] = epoch;
                p->active_deadline[slot] = now() + 20000u;
            }
        }
    }
    unlock(previous);
    if (start) {
        /* No masked IRQs while the native RTOS queue/worker admission runs. */
        const uint32_t command[4] = {slot, 50u, PARK_MM, 0u};
        uint32_t result = 1;
        ip_stock_feed(0, command, &result);
        previous = lock();
        if (p->epoch[slot] != epoch || byte(0x20001a15u + slot * 16u) != 1u) {
            p->result[slot] = 6; code = 6;
            ip_stock_power_stop((void *)(uintptr_t)(MOTORS + slot*64u));
        } else { p->result[slot] = result ? 4u : 1u; if (result) code = 6; }
        unlock(previous);
    }
    previous = lock();
    for (unsigned i = 0; i < 48u; ++i) response[i] = 0;
    write32(response, 0x31524941u); response[4] = 1; response[5] = (uint8_t)code;
    write32(response + 12, PARK_MM); write32(response + 44, BUILD_ID);
    if (p) {
        response[6] = (uint8_t)p->configured; response[7] = (uint8_t)p->automatic;
        write32(response + 8, p->session);
        write32(response + 40, p->revision);
        for (unsigned i = 0; i < 4; ++i) {
            write32(response + 16u + i * 4u, p->epoch[i]);
            response[32u + i] = p->pending[i]; response[36u + i] = p->result[i];
        }
    }
    *response_size = 48;
    unlock(previous);
    return 1;
}
void ip_register(void) { ip_stock_register(93u, (void *)(uintptr_t)ip_dispatch); }
