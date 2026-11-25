/**
 * @file lv_helix_thread_check.h
 *
 * Thread-ownership hook at the LVGL entry points that must only run on the
 * LVGL thread: subject set/notify, timer create/delete, object delete/clean.
 *
 * The application defines helix_lvgl_thread_check() and decides there whether
 * the caller is on the LVGL thread and what to do if not. The symbol is weak,
 * so a binary that does not define it (splash, watchdog, LVGL demos) never
 * calls it. Builds that leave LV_HELIX_THREAD_CHECK at 0 (release and firmware)
 * compile every check out.
 */

#ifndef LV_HELIX_THREAD_CHECK_H
#define LV_HELIX_THREAD_CHECK_H

#include "../lvgl_public.h"

#ifndef LV_HELIX_THREAD_CHECK
    #define LV_HELIX_THREAD_CHECK 0
#endif

#if LV_HELIX_THREAD_CHECK

/** @param entry the LVGL function that was entered (its __func__)
 *  @param caller the return address into that function's caller */
__attribute__((weak))
extern void helix_lvgl_thread_check(const char * entry, const void * caller);

#define LV_HELIX_ASSERT_LVGL_THREAD() \
    do { \
        if(helix_lvgl_thread_check) helix_lvgl_thread_check(__func__, __builtin_return_address(0)); \
    } while(0)

#else

#define LV_HELIX_ASSERT_LVGL_THREAD() do {} while(0)

#endif

#endif /*LV_HELIX_THREAD_CHECK_H*/
