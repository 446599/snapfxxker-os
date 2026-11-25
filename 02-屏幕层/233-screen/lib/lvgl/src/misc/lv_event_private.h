/**
 * @file lv_event_private.h
 *
 */

#ifndef LV_EVENT_PRIVATE_H
#define LV_EVENT_PRIVATE_H

#ifdef __cplusplus
extern "C" {
#endif

/*********************
 *      INCLUDES
 *********************/

#include "../lvgl_public.h"
#include "../misc/lv_array.h"

/*********************
 *      DEFINES
 *********************/

/**********************
 *      TYPEDEFS
 **********************/

struct _lv_event_dsc_t {
#if LV_USE_EXT_DATA
    lv_ext_data_t ext_data;
#endif
    lv_event_cb_t cb;
    void * user_data;
    uint32_t filter;
};

struct _lv_event_t {
    void * current_target;
    void * original_target;
    lv_event_code_t code;
    void * user_data;
    void * param;
    lv_event_t * prev;
    uint8_t deleted : 1;
    uint8_t stop_processing : 1;
    uint8_t stop_bubbling : 1;
    uint8_t stop_trickling : 1;
#if LV_USE_EXT_DATA
    lv_ext_data_t ext_data;
#endif
};

struct _lv_event_list_t {
    lv_array_t array;
    uint8_t is_traversing: 1;          /**< True: the list is being nested traversed */
    uint8_t has_marked_deleting: 1;    /**< True: the list has marked deleting objects
                                         when some of events are marked as deleting */
};


/**********************
 * GLOBAL PROTOTYPES
 **********************/

/**********************
 * GLOBAL PROTOTYPES
 **********************/

void lv_event_push(lv_event_t * e);

void lv_event_pop(lv_event_t * e);

/**
 * HelixScreen: idempotent pop — only pops if `e` is still event_head.
 * Safe to call from a __cleanup__ handler on both normal and exception
 * unwind paths. Prevents `event_head` from being stranded on a defunct
 * stack frame when a C++ exception unwinds through lv_event_push_and_send
 * or lv_obj_send_event (root cause of #793/#840/#871/#878/#880).
 */
void lv_event_pop_if_head(lv_event_t * e);


lv_result_t lv_event_push_and_send(lv_event_list_t * event_list, lv_event_code_t code, void * original_target,
                                   void * param);

/**
 * Nested events can be called and one of them might belong to an object that is being deleted.
 * Mark this object's `event_temp_data` deleted to know that its `lv_obj_send_event` should return `LV_RESULT_INVALID`
 * @param target     pointer to an event target which was deleted
 */
void lv_event_mark_deleted(void * target);

/**
 * HelixScreen: true when `addr` can be a live lv_event_t in a caller's frame on
 * the stack whose current frame is `sp`. Stacks grow downward, so a live event
 * sits at or above `sp` and within 16 MB of it (Linux thread default is 8 MB).
 * Measured as a distance from `sp` so a stack near the top of a 32-bit address
 * space cannot wrap the upper bound below `sp`.
 */
static inline bool lv_event_addr_on_stack(uintptr_t addr, uintptr_t sp)
{
    return addr >= sp && addr - sp < ((uintptr_t)16U << 20);
}

/**********************
 *      MACROS
 **********************/

#ifdef __cplusplus
} /*extern "C"*/
#endif

#endif /*LV_EVENT_PRIVATE_H*/
