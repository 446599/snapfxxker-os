/**
 * @file lv_event.c
 *
 */

/*********************
 *      INCLUDES
 *********************/
#include "lv_event_private.h"
#include "../core/lv_global.h"

#include <stdio.h>

/* Telemetry hook provided by helix_lvgl_anomaly.cpp — reports LVGL anomalies
 * to TelemetryManager with a hex backtrace. Weak so unit tests link without it. */
extern void helix_lvgl_anomaly(const char * code, const char * context) __attribute__((weak));

/* HelixScreen: bridge to the binary's text segment bounds, populated by
 * crash_handler::install(). Used by the dispatch-cb gate below to detect a
 * corrupted dsc->cb pointer (heap address instead of text). Weak so non-
 * helixscreen builds (splash/watchdog/tests) link without it. */
extern int helix_get_text_bounds(unsigned long * lo, unsigned long * hi) __attribute__((weak));

/*********************
 *      DEFINES
 *********************/

#define event_head LV_GLOBAL_DEFAULT()->event_header
#define event_last_id LV_GLOBAL_DEFAULT()->event_last_register_id

/* HelixScreen #907: array-backed event stack. event_stack[0..size-1] holds
 * pointers to lv_event_t in caller stack frames, but lv_event_mark_deleted
 * iterates this array instead of walking e->prev — so the only corruption
 * surface left is the stored pointer itself, which we still validate with
 * the stack-bounds guard. e->prev is preserved in the struct for ABI
 * stability but is no longer read by the dispatch path. */
#define event_stack LV_GLOBAL_DEFAULT()->event_stack
#define event_stack_size LV_GLOBAL_DEFAULT()->event_stack_size
#define LV_EVENT_STACK_MAX 256

/**********************
 *      TYPEDEFS
 **********************/

/**********************
 *  STATIC PROTOTYPES
 **********************/

/* Traverse the list to delete the objects marked for deletion */
static void cleanup_event_list(lv_event_list_t * list);
static void cleanup_event_list_core(lv_array_t * array);

static void event_mark_deleting(lv_event_list_t * list, lv_event_dsc_t * dsc);
static bool event_is_marked_deleting(lv_event_dsc_t * dsc);
static uint32_t event_array_size(lv_event_list_t * list);
static lv_event_dsc_t ** event_array_at(lv_event_list_t * list, uint32_t index);

/**********************
 *  STATIC VARIABLES
 **********************/

/**********************
 *      MACROS
 **********************/

#if LV_USE_LOG && LV_LOG_TRACE_EVENT
    #define LV_TRACE_EVENT(...) LV_LOG_TRACE(__VA_ARGS__)
#else
    #define LV_TRACE_EVENT(...)
#endif

/**********************
 *   GLOBAL FUNCTIONS
 **********************/

#if LV_USE_EXT_DATA
void lv_event_desc_set_external_data(lv_event_dsc_t * dsc, void * data, void (* free_cb)(void * data))
{
    LV_CHECK_ARG(dsc != NULL, return);

    dsc->ext_data.data = data;
    dsc->ext_data.free_cb = free_cb;
}
#endif

/* HelixScreen: dispatch-depth counter paired with event_head, lives in
 * `_lv_global_t::event_depth` so post-mortem heap snapshots can read it.
 * Each push/pop pair adjusts this counter; lv_event_mark_deleted() uses it
 * to detect a stale event_head — i.e. event_head non-NULL while no
 * lv_event_send frame is active. Such an inconsistency means a stack-
 * allocated lv_event_t was popped logically but the global head pointer
 * was never reset, and walking its chain SIGBUSes on garbage
 * (cluster:pstat-async-delete / #906, plus #793/#840/#871/#878/#880).
 * Single-threaded — uint32 in the global suffices. */
#define event_dispatch_depth LV_GLOBAL_DEFAULT()->event_depth

void lv_event_push(lv_event_t * e)
{
    /* HelixScreen #907: array-backed event stack. The legacy linked-list head
     * (event_head) and e->prev are still updated for diagnostic and ABI
     * compatibility, but lv_event_mark_deleted no longer reads them — it
     * iterates event_stack[] instead. The corruption-prone chain walk is
     * gone; the only surface left is the stored pointer itself, which is
     * still subject to the stack-bounds guard. */
    e->prev = event_head;
    event_head = e;

    if(event_stack_size < LV_EVENT_STACK_MAX) {
        event_stack[event_stack_size] = e;
    }
    else if(helix_lvgl_anomaly) {
        /* Should never happen in practice — max_depth=256 is already deeper
         * than any plausible nested event chain. If it does, drop the new
         * push (mark_deleted won't see this event but dispatch still works)
         * and surface telemetry so we know the bound needs raising. */
        helix_lvgl_anomaly("event_stack_overflow", "lv_event_push");
    }
    event_stack_size++;
    event_dispatch_depth++;
}

void lv_event_pop(lv_event_t * e)
{
    event_head = e->prev;

    if(event_stack_size > 0) {
        event_stack_size--;
        if(event_stack_size < LV_EVENT_STACK_MAX) {
            event_stack[event_stack_size] = NULL;
        }
    }
    if(event_dispatch_depth > 0) event_dispatch_depth--;
}

/* HelixScreen: idempotent pop. Pops only if `e` is still the head of the
 * stack. Used as the body of __cleanup__-attribute helpers in
 * lv_obj_send_event and lv_event_push_and_send so a C++ exception unwinding
 * through those C frames can't leave the stack pointing at a defunct frame
 * (root cause of #793/#840/#871/#878/#880, bundle RPHAV9T7). With #907 the
 * "head" is now event_stack[event_stack_size-1] rather than event_head. */
void lv_event_pop_if_head(lv_event_t * e)
{
    bool is_top = (event_stack_size > 0 && event_stack_size <= LV_EVENT_STACK_MAX &&
                   event_stack[event_stack_size - 1] == e);
    if(is_top) {
        event_stack_size--;
        event_stack[event_stack_size] = NULL;
        if(event_dispatch_depth > 0) event_dispatch_depth--;
        /* Keep event_head in sync for legacy callers / debugging. */
        if(event_head == e) event_head = e->prev;
    }
}

lv_result_t lv_event_push_and_send(lv_event_list_t * event_list, lv_event_code_t code, void * original_target,
                                   void * param)
{
    LV_ASSERT_NULL(event_list);
    /* HelixScreen: __cleanup__ pop fires on scope exit including C++ exception
     * unwind. Explicit lv_event_pop below still runs on the normal path; the
     * cleanup is a no-op then because event_head no longer equals &e. */
    lv_event_t e __attribute__((__cleanup__(lv_event_pop_if_head)));
    lv_memzero(&e, sizeof(e));
    e.code = code;
    e.current_target = original_target;
    e.original_target = original_target;
    e.param = param;

    lv_event_push(&e);
    lv_result_t res = lv_event_send(event_list, &e, true);
    if(res != LV_RESULT_OK || e.stop_processing) goto ret;

    res = lv_event_send(event_list, &e, false);
    if(res != LV_RESULT_OK) goto ret;

ret:
    lv_event_pop(&e);
    return res;
}

lv_result_t lv_event_send(lv_event_list_t * list, lv_event_t * e, bool preprocess)
{
    if(list == NULL) return LV_RESULT_OK;
    LV_CHECK_ARG(e != NULL, return LV_RESULT_INVALID);
    if(e->deleted) return LV_RESULT_INVALID;

    /* When obj is deleted in its own event, it will cause the `list->array` header to be released,
     * but the content still exists, which leads to memory leakage.
     * Therefore, back up the header in advance,
     * which can strive to release the memory and prevent used-after-free. */
    lv_array_t back_array_head = list->array;

    /* Dealing with the problem of nested event deletion event */
    const bool is_traversing = list->is_traversing;
    list->is_traversing = true;

    lv_result_t res = LV_RESULT_OK;
    const uint32_t size = event_array_size(list);
    for(uint32_t i = 0; i < size && !e->deleted; i++) {
        lv_event_dsc_t * dsc = *event_array_at(list, i);
        /* HelixScreen: a torn (NULL) list entry has to surface as an anomaly
         * naming the widget and slot rather than a fault at address 0. Skip
         * it so the remaining callbacks still run. This treats the symptom;
         * whatever corrupted the list is not addressed here. */
        if(dsc == NULL) {
            if(helix_lvgl_anomaly) {
                char ctx[96];
                snprintf(ctx, sizeof(ctx), "obj=%p i=%u code=%d",
                         (void *)e->current_target, (unsigned)i, (int)e->code);
                helix_lvgl_anomaly("event_dsc_null", ctx);
            }
            continue;
        }
        if(dsc->cb == NULL) continue;
        if(event_is_marked_deleting(dsc)) continue;
        const bool is_preprocessed = (dsc->filter & LV_EVENT_PREPROCESS) != 0;
        if(is_preprocessed != preprocess) continue;
        lv_event_code_t filter = dsc->filter & ~LV_EVENT_PREPROCESS;
        if(filter == LV_EVENT_ALL || filter == e->code) {
            e->user_data = dsc->user_data;
#if LV_USE_EXT_DATA
            e->ext_data.data = dsc->ext_data.data;
#endif
            /* HelixScreen: gate dsc->cb against the binary's text segment.
             * Bundle 3XNZQB2R crashed at the call below via R3==PC indirect
             * call to a heap address — the per-widget event_dsc_t->cb slot
             * was corrupted to a non-text pointer (cluster:pstat-async-delete
             * sub-signature, distinct from the global event_stack surface
             * v0.99.55-v0.99.56 structurally fixed). Convert the crash into
             * a recoverable anomaly: if cb is outside [text_lo, text_hi),
             * report + skip. Snapshot dsc->cb once so a concurrent bg-thread
             * write between the bounds check and the call can't slip a bad
             * pointer past the gate. Bounds-check is best-effort:
             * helix_get_text_bounds() returns 0 during early init before
             * crash_handler::install() runs — falls back to "no check" then,
             * which is fine because the corruption is a runtime mutation. */
            lv_event_cb_t cb_snap = dsc->cb;
            {
                unsigned long _hx_lo = 0, _hx_hi = 0;
                if(helix_get_text_bounds && helix_get_text_bounds(&_hx_lo, &_hx_hi) &&
                   ((unsigned long)(uintptr_t)cb_snap < _hx_lo ||
                    (unsigned long)(uintptr_t)cb_snap >= _hx_hi)) {
                    if(helix_lvgl_anomaly) {
                        char ctx[160];
                        snprintf(ctx, sizeof(ctx),
                                 "obj=%p cb=%p dsc=%p i=%u code=%d",
                                 (void *)e->current_target, (void *)cb_snap,
                                 (void *)dsc, (unsigned)i, (int)e->code);
                        helix_lvgl_anomaly("event_dsc_cb_oob", ctx);
                    }
                    continue;
                }
            }
            cb_snap(e);
            if(e->stop_processing) break;

            /*Stop if the object is deleted*/
            if(e->deleted) {
                res = LV_RESULT_INVALID;
                break;
            }
        }
    }

    if(is_traversing) return res;

    if(e->deleted) cleanup_event_list_core(&back_array_head);
    else {
        list->is_traversing = false;
        cleanup_event_list(list);
    }

    return res;
}

lv_event_dsc_t * lv_event_add(lv_event_list_t * list, lv_event_cb_t cb, lv_event_code_t filter,
                              void * user_data)
{
    LV_CHECK_ARG(list != NULL, return NULL);
    LV_CHECK_ARG(cb != NULL, return NULL);
    lv_event_dsc_t * dsc = lv_malloc(sizeof(lv_event_dsc_t));
    LV_ASSERT_NULL(dsc);
    if(!dsc) {
        return NULL;
    }

    dsc->cb = cb;
    dsc->filter = filter;
    dsc->user_data = user_data;
#if LV_USE_EXT_DATA
    dsc->ext_data.free_cb = NULL;
    dsc->ext_data.data = NULL;
#endif
    lv_result_t res;
    if(event_array_size(list) == 0) {
        /*event list hasn't been initialized.*/
        res = lv_array_init(&list->array, 1, sizeof(lv_event_dsc_t *));
        if(res != LV_RESULT_OK) {
            lv_free(dsc);
            return NULL;
        }
    }

    res = lv_array_push_back(&list->array, &dsc);
    if(res != LV_RESULT_OK) {
        lv_free(dsc);
        return NULL;
    }

    return dsc;
}

bool lv_event_remove_dsc(lv_event_list_t * list, lv_event_dsc_t * dsc)
{
    LV_CHECK_ARG(list != NULL, return false);
    LV_CHECK_ARG(dsc != NULL, return false);

    const uint32_t size = event_array_size(list);
    for(uint32_t i = 0; i < size; i++) {
        lv_event_dsc_t * event = *event_array_at(list, i);
        if(event == dsc) {
#if LV_USE_EXT_DATA
            if(dsc->ext_data.free_cb) {
                dsc->ext_data.free_cb(dsc->ext_data.data);
                dsc->ext_data.data = NULL;
            }
#endif
            event_mark_deleting(list, event);
            cleanup_event_list(list);
            return true;
        }
    }

    return false;
}

uint32_t lv_event_get_count(lv_event_list_t * list)
{
    LV_CHECK_ARG(list != NULL, return 0);
    return event_array_size(list);
}

lv_event_dsc_t * lv_event_get_dsc(lv_event_list_t * list, uint32_t index)
{
    LV_CHECK_ARG(list != NULL, return NULL);
    lv_event_dsc_t ** dsc = event_array_at(list, index);
    return dsc ? *dsc : NULL;
}

lv_event_cb_t lv_event_dsc_get_cb(lv_event_dsc_t * dsc)
{
    LV_CHECK_ARG(dsc != NULL, return NULL);
    return dsc->cb;
}

void * lv_event_dsc_get_user_data(lv_event_dsc_t * dsc)
{
    LV_CHECK_ARG(dsc != NULL, return NULL);
    return dsc->user_data;

}

bool lv_event_remove(lv_event_list_t * list, uint32_t index)
{
    LV_CHECK_ARG(list != NULL, return false);
    lv_event_dsc_t * dsc = lv_event_get_dsc(list, index);
    if(dsc == NULL) return false;
#if LV_USE_EXT_DATA
    if(dsc->ext_data.free_cb) {
        dsc->ext_data.free_cb(dsc->ext_data.data);
        dsc->ext_data.data = NULL;
    }
#endif
    event_mark_deleting(list, dsc);
    cleanup_event_list(list);
    return true;
}

void lv_event_remove_all(lv_event_list_t * list)
{
    LV_CHECK_ARG(list != NULL, return);
    const uint32_t size = event_array_size(list);
    for(uint32_t i = 0; i < size; i++) {
#if LV_USE_EXT_DATA
        lv_event_dsc_t * dsc = lv_event_get_dsc(list, i);
        if(dsc && dsc->ext_data.free_cb) {
            dsc->ext_data.free_cb(dsc->ext_data.data);
            dsc->ext_data.data = NULL;
        }
#endif
        event_mark_deleting(list, *event_array_at(list, i));
    }

    cleanup_event_list(list);
}

void * lv_event_get_current_target(lv_event_t * e)
{
    LV_CHECK_ARG(e != NULL, return NULL);
    return e->current_target;
}

void * lv_event_get_target(lv_event_t * e)
{
    LV_CHECK_ARG(e != NULL, return NULL);
    return e->original_target;
}

lv_event_code_t lv_event_get_code(lv_event_t * e)
{
    LV_CHECK_ARG(e != NULL, return 0);
    return e->code & ~LV_EVENT_PREPROCESS;
}

void * lv_event_get_param(lv_event_t * e)
{
    LV_CHECK_ARG(e != NULL, return NULL);
    return e->param;
}

void * lv_event_get_user_data(lv_event_t * e)
{
    LV_CHECK_ARG(e != NULL, return NULL);
    return e->user_data;
}

void lv_event_stop_bubbling(lv_event_t * e)
{
    LV_CHECK_ARG(e != NULL, return);
    e->stop_bubbling = 1;
}

void lv_event_stop_trickling(lv_event_t * e)
{
    LV_CHECK_ARG(e != NULL, return);
    e->stop_trickling = 1;
}

void lv_event_stop_processing(lv_event_t * e)
{
    LV_CHECK_ARG(e != NULL, return);
    e->stop_processing = 1;
}

void lv_event_free_user_data_cb(lv_event_t * e)
{
    LV_CHECK_ARG(e != NULL, return);
    void * p = lv_event_get_user_data(e);
    lv_free(p);
}

uint32_t lv_event_register_id(void)
{
    if(event_last_id + 1 > (uint32_t)LV_EVENT_LAST_CUSTOM) {
        LV_LOG_ERROR("No more event ids can be registered");
        return LV_EVENT_LAST;
    }

    event_last_id ++;
    return event_last_id;
}

void lv_event_mark_deleted(void * target)
{
    /* HelixScreen #907: array-backed event stack. The original implementation
     * walked event_head -> e->prev -> e->prev -> ... Crash family
     * #190/#80/#776/#840/#873-878/#906 hit SIGSEGV/SIGBUS in this walk when
     * `e->prev` (read from inside a stack frame that may have unwound) was
     * stale or stomped. Heap reuse or unrelated stack writes turned the chain
     * into a wild-pointer ladder.
     *
     * With the array, we no longer dereference `e->prev` at all. We iterate
     * event_stack[0..size-1] which lives in a fixed global slot. The pointers
     * stored there are still potentially stale (a frame may have returned
     * without popping), so we keep the stack-bounds check per slot — but the
     * walk is a fixed-bounded loop rather than a chase through corrupt
     * memory. The depth-coherence and stack-OOB telemetry guards remain in
     * place against the legacy event_head field as canaries; if they fire we
     * learn that something is corrupting BSS-resident state, which would be
     * a different (and more serious) bug class. */

    /* Depth-coherence canary against the legacy linked-list head. With the
     * array, lv_event_mark_deleted no longer depends on event_head being
     * valid, but we still update event_head in push/pop for compatibility;
     * a divergence between event_head and event_dispatch_depth still
     * indicates something pathological. Telemetry-only — does not affect
     * the array walk below. */
    if(event_head != NULL && event_dispatch_depth == 0) {
        if(helix_lvgl_anomaly) {
            char ctx[96];
            snprintf(ctx, sizeof(ctx), "head=%p target=%p (depth=0)",
                     (void *)event_head, target);
            helix_lvgl_anomaly("event_head_stale_leak", ctx);
        }
        event_head = NULL;
        /* Fall through to the array walk — those slots may still have valid
         * events even if event_head got out of sync. */
    }

    /* Capture stack bounds for per-slot validation. User stacks grow
     * downward: a valid lv_event_t pointer must be at an address >= current
     * frame and within ~16 MB of it (Linux thread default is 8 MB). Slots
     * outside this range are stale or corrupt; skip them and surface
     * telemetry. */
    void * sp_now = __builtin_frame_address(0);

    /* Iterate the array. Bounded by event_stack_size (which we check against
     * LV_EVENT_STACK_MAX). Top-of-stack first to match the previous walk
     * order — outer dispatch frames mark first, innermost frames last —
     * preserving any subtle ordering callers depended on. */
    uint16_t stack_size = event_stack_size;
    if(stack_size > LV_EVENT_STACK_MAX) {
        if(helix_lvgl_anomaly) {
            char ctx[96];
            snprintf(ctx, sizeof(ctx), "size=%u target=%p (>max=%u)",
                     (unsigned)stack_size, target, LV_EVENT_STACK_MAX);
            helix_lvgl_anomaly("event_stack_size_oob", ctx);
        }
        stack_size = LV_EVENT_STACK_MAX;
    }

    for(int32_t i = (int32_t)stack_size - 1; i >= 0; i--) {
        lv_event_t * e = event_stack[i];
        if(e == NULL) continue;
        if(((uintptr_t)e & (sizeof(void *) - 1)) != 0u) {
            if(helix_lvgl_anomaly) {
                char ctx[96];
                snprintf(ctx, sizeof(ctx), "slot[%d]=%p target=%p (misaligned)",
                         (int)i, (void *)e, target);
                helix_lvgl_anomaly("event_slot_misaligned", ctx);
            }
            continue;
        }
        if(!lv_event_addr_on_stack((uintptr_t)e, (uintptr_t)sp_now)) {
            if(helix_lvgl_anomaly) {
                char ctx[96];
                snprintf(ctx, sizeof(ctx), "slot[%d]=%p sp=%p target=%p (oob)",
                         (int)i, (void *)e, sp_now, target);
                helix_lvgl_anomaly("event_slot_stack_oob", ctx);
            }
            continue;
        }
        if(e->original_target == target || e->current_target == target) e->deleted = 1;
    }
}

const char * lv_event_code_get_name(lv_event_code_t code)
{
    /*Remove the preprocess flag*/
    code &= ~LV_EVENT_PREPROCESS;

#define ENUM_CASE(x) case LV_##x: return #x

    switch(code) {
            ENUM_CASE(EVENT_ALL);

            /** Input device events*/
            ENUM_CASE(EVENT_PRESSED);
            ENUM_CASE(EVENT_PRESSING);
            ENUM_CASE(EVENT_PRESS_LOST);
            ENUM_CASE(EVENT_SHORT_CLICKED);
            ENUM_CASE(EVENT_SINGLE_CLICKED);
            ENUM_CASE(EVENT_DOUBLE_CLICKED);
            ENUM_CASE(EVENT_TRIPLE_CLICKED);
            ENUM_CASE(EVENT_LONG_PRESSED);
            ENUM_CASE(EVENT_LONG_PRESSED_REPEAT);
            ENUM_CASE(EVENT_CLICKED);
            ENUM_CASE(EVENT_RELEASED);
            ENUM_CASE(EVENT_SCROLL_BEGIN);
            ENUM_CASE(EVENT_SCROLL_THROW_BEGIN);
            ENUM_CASE(EVENT_SCROLL_END);
            ENUM_CASE(EVENT_SCROLL);
            ENUM_CASE(EVENT_GESTURE);
            ENUM_CASE(EVENT_GESTURE_UP);
            ENUM_CASE(EVENT_GESTURE_DOWN);
            ENUM_CASE(EVENT_GESTURE_LEFT);
            ENUM_CASE(EVENT_GESTURE_RIGHT);
            ENUM_CASE(EVENT_KEY);
            ENUM_CASE(EVENT_ROTARY);
            ENUM_CASE(EVENT_FOCUSED);
            ENUM_CASE(EVENT_DEFOCUSED);
            ENUM_CASE(EVENT_LEAVE);
            ENUM_CASE(EVENT_HIT_TEST);
            ENUM_CASE(EVENT_INDEV_RESET);
            ENUM_CASE(EVENT_HOVER_OVER);
            ENUM_CASE(EVENT_HOVER_LEAVE);

            /** Drawing events*/
            ENUM_CASE(EVENT_COVER_CHECK);
            ENUM_CASE(EVENT_REFR_EXT_DRAW_SIZE);
            ENUM_CASE(EVENT_DRAW_MAIN_BEGIN);
            ENUM_CASE(EVENT_DRAW_MAIN);
            ENUM_CASE(EVENT_DRAW_MAIN_END);
            ENUM_CASE(EVENT_DRAW_POST_BEGIN);
            ENUM_CASE(EVENT_DRAW_POST);
            ENUM_CASE(EVENT_DRAW_POST_END);
            ENUM_CASE(EVENT_DRAW_TASK_ADDED);

            /** Special events*/
            ENUM_CASE(EVENT_VALUE_CHANGED);
            ENUM_CASE(EVENT_INSERT);
            ENUM_CASE(EVENT_REFRESH);
            ENUM_CASE(EVENT_READY);
            ENUM_CASE(EVENT_CANCEL);
            ENUM_CASE(EVENT_STATE_CHANGED);
            ENUM_CASE(EVENT_CHECKED);
            ENUM_CASE(EVENT_UNCHECKED);

            /** Other events*/
            ENUM_CASE(EVENT_CREATE);
            ENUM_CASE(EVENT_DELETE);
            ENUM_CASE(EVENT_CHILD_CHANGED);
            ENUM_CASE(EVENT_CHILD_CREATED);
            ENUM_CASE(EVENT_CHILD_DELETED);
            ENUM_CASE(EVENT_SCREEN_UNLOAD_START);
            ENUM_CASE(EVENT_SCREEN_LOAD_START);
            ENUM_CASE(EVENT_SCREEN_LOADED);
            ENUM_CASE(EVENT_SCREEN_UNLOADED);
            ENUM_CASE(EVENT_SIZE_CHANGED);
            ENUM_CASE(EVENT_STYLE_CHANGED);
            ENUM_CASE(EVENT_LAYOUT_CHANGED);
            ENUM_CASE(EVENT_GET_SELF_SIZE);
            ENUM_CASE(EVENT_UPDATE_LAYOUT_COMPLETED);

            /** Events of optional LVGL components*/
            ENUM_CASE(EVENT_INVALIDATE_AREA);
            ENUM_CASE(EVENT_RESOLUTION_CHANGED);
            ENUM_CASE(EVENT_COLOR_FORMAT_CHANGED);
            ENUM_CASE(EVENT_REFR_REQUEST);
            ENUM_CASE(EVENT_REFR_START);
            ENUM_CASE(EVENT_REFR_READY);
            ENUM_CASE(EVENT_RENDER_START);
            ENUM_CASE(EVENT_RENDER_READY);
            ENUM_CASE(EVENT_FLUSH_START);
            ENUM_CASE(EVENT_FLUSH_FINISH);
            ENUM_CASE(EVENT_FLUSH_WAIT_START);
            ENUM_CASE(EVENT_FLUSH_WAIT_FINISH);
            ENUM_CASE(EVENT_SYNC_START);
            ENUM_CASE(EVENT_SYNC_FINISH);
            ENUM_CASE(EVENT_SYNC_WAIT_START);
            ENUM_CASE(EVENT_SYNC_WAIT_FINISH);

            ENUM_CASE(EVENT_VSYNC);
            ENUM_CASE(EVENT_VSYNC_REQUEST);

#if LV_USE_TRANSLATION
            ENUM_CASE(EVENT_TRANSLATION_LANGUAGE_CHANGED);
#endif /*LV_USE_TRANSLATION*/

        /* Special event flags */
        case LV_EVENT_LAST:
        case LV_EVENT_LAST_CUSTOM:
        case LV_EVENT_PREPROCESS:
        case LV_EVENT_MARKED_DELETING:
            break;

            /* Note that default is not added here because when adding new event code,
             * if forget to add case, the compiler will automatically report a warning.
             */
    }

#undef ENUM_CASE

    return "EVENT_UNKNOWN";
}

/**********************
 *   STATIC FUNCTIONS
 **********************/

static void cleanup_event_list_core(lv_array_t * array)
{
    const uint32_t size = lv_array_size(array);
    uint32_t kept_count = 0;
    for(uint32_t i = 0; i < size; i++) {
        lv_event_dsc_t ** dsc_i = lv_array_at(array, i);
        lv_event_dsc_t ** dsc_kept = lv_array_at(array, kept_count);
        if(event_is_marked_deleting(*dsc_i)) lv_free(*dsc_i);
        else {
            *dsc_kept = *dsc_i;
            kept_count++;
        }
    }

    if(kept_count == 0) lv_array_deinit(array);
    else lv_array_resize(array, kept_count);
}

static void cleanup_event_list(lv_event_list_t * list)
{
    if(list->is_traversing) return;
    if(list->has_marked_deleting == false) return;

    cleanup_event_list_core(&list->array);

    list->has_marked_deleting = false;
}

static void event_mark_deleting(lv_event_list_t * list, lv_event_dsc_t * dsc)
{
    list->has_marked_deleting = true;
    dsc->filter |= LV_EVENT_MARKED_DELETING;
}
static bool event_is_marked_deleting(lv_event_dsc_t * dsc)
{
    return (dsc->filter & LV_EVENT_MARKED_DELETING) != 0;
}
static uint32_t event_array_size(lv_event_list_t * list)
{
    return lv_array_size(&list->array);
}
static lv_event_dsc_t ** event_array_at(lv_event_list_t * list, uint32_t index)
{
    return lv_array_at(&list->array, index);
}

