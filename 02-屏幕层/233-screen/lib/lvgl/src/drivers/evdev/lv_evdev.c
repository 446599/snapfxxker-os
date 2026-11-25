/**
 * @file lv_evdev.c
 *
 */

/**********************
 *      INCLUDES
 **********************/
#include "lv_evdev_private.h"
#if LV_USE_EVDEV

#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <stdlib.h> /* getenv — #943 touch-range diagnostic */
#include <string.h>
#include <sys/types.h>
#include <dirent.h>
#include <sys/stat.h>
#include <sys/param.h> /*To detect BSD*/
#ifdef BSD
    #include <dev/evdev/input.h>
#else
    #include <linux/input.h>
    #include <sys/inotify.h>
#endif /*BSD*/

#include "../../core/lv_global.h"
#include "../../display/lv_display_private.h"

/*********************
 *      DEFINES
 *********************/

#define evdev_discovery LV_GLOBAL_DEFAULT()->evdev_discovery
#define EVDEV_DISCOVERY_PATH "/dev/input/"
#define EVDEV_DISCOVERY_PATH_BUF_SIZE 32
#define REL_XY_MASK ((1 << REL_X) | (1 << REL_Y))
#define ABS_XY_MASK ((1 << ABS_X) | (1 << ABS_Y))
#define MAX_TOUCH_POINTS 5

/* Safety valve for the per-slot coordinate-trust gate (see lv_evdev_t below).
 * A slot can only be untrusted before it has ever received a coordinate, which
 * by construction resolves as soon as the device reports one. If some device
 * nonetheless keeps a pressed slot untrusted, give up after this many read
 * cycles and emit the press anyway. The gate must never be able to make a
 * touchscreen permanently unresponsive: a mispositioned tap is recoverable,
 * a dead panel is not. */
#define EVDEV_XY_TRUST_MAX_FRAMES 8

/**********************
 *      TYPEDEFS
 **********************/

typedef struct {
    /*Device*/
    int fd;
    dev_t st_dev;
    ino_t st_ino;
    lv_evdev_type_t type;
    /*Config*/
    bool swap_axes;
    int min_x;
    int min_y;
    int max_x;
    int max_y;
    /*State*/
    int root_x;
    int root_y;
    int key;
    lv_indev_state_t state;
    bool deleting;
    /* Last coordinate pair handed to _evdev_process_pointer, recorded BEFORE the
     * axis swap and before the linear ABS-range scale. This is the only place the
     * untouched digitizer reading is still available: the scale clamps to the
     * display, so a wrong declared range destroys information the caller would
     * need to re-derive it. A three-point calibration reads these back through
     * lv_evdev_get_last_raw() to solve for the true range and for whether the
     * axes are transposed. Pre-swap, because the swap itself is one of the
     * unknowns being solved for. */
    int last_raw_x;
    int last_raw_y;
    bool has_last_raw;
    /* Multi-touch support */
#if LV_USE_GESTURE_RECOGNITION
    lv_indev_touch_data_t touch_data[MAX_TOUCH_POINTS]; /* Array of touch points for gesture recognition */
    uint8_t touch_count; /* Number of valid touch points */
    uint8_t current_slot; /* Current touch point slot */
    bool touch_data_changed; /* Flag to indicate if touch data has changed since last SYN_REPORT */
    /* Per-slot coordinate trust.
     *
     * The kernel input core suppresses an EV_ABS event whose value equals the
     * value already stored for that axis in that MT slot
     * (input_handle_abs_event() returns INPUT_IGNORE_EVENT when *pold == *pval).
     * For MT protocol B the stored value lives in mt->slots[slot].abs[] and is
     * NOT reset when ABS_MT_TRACKING_ID goes to -1 — destroying a contact frees
     * the slot but leaves its coordinates behind. A new contact landing on a
     * coordinate the previous contact already occupied therefore arrives with
     * ABS_MT_POSITION_X and/or ABS_MT_POSITION_Y simply absent. The kernel MT
     * documentation states this directly: "Since only changes are propagated,
     * the full state of each initiated contact has to reside in the receiving
     * end", and its Protocol Example B shows a frame carrying POSITION_X with
     * no POSITION_Y.
     *
     * So a single-axis press frame is normal traffic, not a hardware glitch,
     * and the receiving end is required to retain the missing axis. Zeroing a
     * released slot's coordinates (which this driver used to do) breaks that
     * contract: the suppressed axis reads back 0 and the press lands at a
     * screen edge — the phantom edge taps seen on Holtek 04d9:8030 / BTT-HDMI5.
     * Released slots now keep their last reported position instead.
     *
     * These flags track the only genuinely untrustworthy state that remains: a
     * slot that has never received a coordinate at all, whose point is still
     * the zero-initialised (0,0). Such a slot is not emitted as PRESSED. They
     * are set once per axis and never cleared, so the gate cannot re-engage
     * after a slot has seen real data. */
    bool touch_x_valid[MAX_TOUCH_POINTS];  /* ABS_MT_POSITION_X ever received for this slot */
    bool touch_y_valid[MAX_TOUCH_POINTS];  /* ABS_MT_POSITION_Y ever received for this slot */
    uint8_t touch_untrusted_frames[MAX_TOUCH_POINTS]; /* read cycles slot 0 has been gated */
#endif
} lv_evdev_t;

#ifndef BSD
struct _lv_evdev_discovery_t {
    lv_evdev_discovery_cb_t cb;
    void * cb_user_data;
    int inotify_fd;
    bool inotify_watch_active;
    lv_timer_t * timer;
};
#endif

/**********************
 *   STATIC FUNCTIONS
 **********************/

static int _evdev_process_key(uint16_t code)
{
    switch(code) {
        case KEY_UP:
            return LV_KEY_UP;
        case KEY_DOWN:
            return LV_KEY_DOWN;
        case KEY_RIGHT:
            return LV_KEY_RIGHT;
        case KEY_LEFT:
            return LV_KEY_LEFT;
        case KEY_ESC:
            return LV_KEY_ESC;
        case KEY_DELETE:
            return LV_KEY_DEL;
        case KEY_BACKSPACE:
            return LV_KEY_BACKSPACE;
        case KEY_ENTER:
            return LV_KEY_ENTER;
        case KEY_NEXT:
        case KEY_TAB:
            return LV_KEY_NEXT;
        case KEY_PREVIOUS:
            return LV_KEY_PREV;
        case KEY_HOME:
            return LV_KEY_HOME;
        case KEY_END:
            return LV_KEY_END;
        default:
            return code + 0xFFFF;
    }
}

static int _evdev_calibrate(int v, int in_min, int in_max, int out_min, int out_max)
{
    if(in_min != in_max) v = (v - in_min) * (out_max - out_min) / (in_max - in_min) + out_min;
    return LV_CLAMP(out_min, v, out_max);
}

static lv_point_t _evdev_process_pointer(lv_indev_t * indev, int x, int y)
{
    LV_ASSERT(indev != NULL);
    lv_display_t * disp = lv_indev_get_display(indev);
    lv_evdev_t * dsc = lv_indev_get_driver_data(indev);
    LV_ASSERT(dsc != NULL);

    /* Record the pre-swap, pre-scale reading before anything lossy happens to it.
     * Written here rather than at the call sites so every path that produces a
     * pointer coordinate (gesture slots, MT slot 0, single-touch root) is covered
     * by construction. */
    dsc->last_raw_x = x;
    dsc->last_raw_y = y;
    dsc->has_last_raw = true;

    int swapped_x = dsc->swap_axes ? y : x;
    int swapped_y = dsc->swap_axes ? x : y;

    int offset_x = disp->offset_x;
    int offset_y = disp->offset_y;
    int width = disp->hor_res;
    int height = disp->ver_res;

    lv_point_t p;
    p.x = _evdev_calibrate(swapped_x, dsc->min_x, dsc->max_x, offset_x, offset_x + width - 1);
    p.y = _evdev_calibrate(swapped_y, dsc->min_y, dsc->max_y, offset_y, offset_y + height - 1);

    /* #943 diagnostic: observe the RAW digitizer coordinates actually emitted vs
     * the ABS range the driver was configured with (dsc->min/max, set by
     * lv_evdev_set_calibration). If a controller over-reports its range —
     * advertises e.g. 0..800 via EVIOCGABS but only ever emits ~0..480 during real
     * touches — the coarse scale over-divides and taps collapse into a fraction of
     * the panel (Qidi Q2, #943). Surfacing the actual emitted range lets
     * reported-vs-emitted be compared directly from a single log capture. Gated on
     * HELIX_DEBUG_TOUCH so it is silent unless a reporter explicitly enables it;
     * routed through LV_LOG_WARN, which is active at the configured WARN level and
     * reaches journalctl via the spdlog print-cb bridge (LV_LOG_INFO/USER/TRACE are
     * compiled out). Observed extremes accumulate for the process lifetime so the
     * max converges on the true emitted range over a calibration session. */
    {
        static int touch_range_dbg = -1;
        if(touch_range_dbg < 0) {
            touch_range_dbg = (getenv("HELIX_DEBUG_TOUCH") || getenv("HELIX_DEBUG_TOUCHES"))
                              ? 1 : 0;
        }
        if(touch_range_dbg) {
            static int rx_min = (1 << 30), rx_max = -(1 << 30);
            static int ry_min = (1 << 30), ry_max = -(1 << 30);
            static unsigned sample_n = 0;
            int extended = 0;
            if(swapped_x < rx_min) { rx_min = swapped_x; extended = 1; }
            if(swapped_x > rx_max) { rx_max = swapped_x; extended = 1; }
            if(swapped_y < ry_min) { ry_min = swapped_y; extended = 1; }
            if(swapped_y > ry_max) { ry_max = swapped_y; extended = 1; }
            /* Log on every newly observed extreme, else every 60th sample. */
            if(extended || (sample_n++ % 60 == 0)) {
                LV_LOG_WARN("[TouchDebug/evdev] raw=(%d,%d) -> px=(%d,%d) | observed raw "
                            "x[%d..%d] y[%d..%d] vs configured x[%d..%d] y[%d..%d]",
                            swapped_x, swapped_y, p.x, p.y,
                            rx_min, rx_max, ry_min, ry_max,
                            dsc->min_x, dsc->max_x, dsc->min_y, dsc->max_y);
            }
        }
    }

    return p;
}

#if LV_USE_GESTURE_RECOGNITION
/* A slot holds a usable coordinate once both axes have been reported for it at
 * least once. Until then its point is the zero-initialised (0,0), which would
 * render as a tap in the top-left corner. Note this asks "has this slot ever
 * held real data", NOT "did both axes arrive since the last TRACKING_ID" — the
 * latter is unsatisfiable on any device that omits unchanged axes. */
static bool _evdev_slot_has_coords(const lv_evdev_t * dsc, int slot)
{
    return dsc->touch_x_valid[slot] && dsc->touch_y_valid[slot];
}
#endif

static void _evdev_async_delete_cb(void * user_data)
{
    lv_indev_t * indev = user_data;
    lv_indev_delete(indev);
}

static void _evdev_read(lv_indev_t * indev, lv_indev_data_t * data)
{
    LV_ASSERT(indev != NULL);
    LV_ASSERT(data != NULL);
    lv_evdev_t * dsc = lv_indev_get_driver_data(indev);
    LV_ASSERT(dsc != NULL);

    /*Update dsc with buffered events*/
    struct input_event in = { 0 };
    ssize_t br;
    while((br = read(dsc->fd, &in, sizeof(in))) > 0) {
        if(in.type == EV_REL) {
            if(in.code == REL_X) dsc->root_x += in.value;
            else if(in.code == REL_Y) dsc->root_y += in.value;
        }
        else if(in.type == EV_ABS) {
#if LV_USE_GESTURE_RECOGNITION
            if(in.code == ABS_MT_SLOT) {
                if(in.value >= MAX_TOUCH_POINTS) {
                    dsc->current_slot = MAX_TOUCH_POINTS - 1;
                    dsc->touch_count = MAX_TOUCH_POINTS;
                    LV_LOG_WARN("Touch point slot out of range, setting to max: %d", MAX_TOUCH_POINTS - 1);
                }
                else {
                    dsc->current_slot = in.value;
                    dsc->touch_count = LV_MAX(dsc->touch_count, dsc->current_slot + 1);
                    LV_LOG_TRACE("Slot changed to %d, touch_count=%d", dsc->current_slot, dsc->touch_count);
                }
            }
            else
#endif
                if(in.code == ABS_X || in.code == ABS_MT_POSITION_X) {
                    dsc->root_x = in.value;
#if LV_USE_GESTURE_RECOGNITION
                    if(in.code == ABS_MT_POSITION_X && dsc->current_slot < MAX_TOUCH_POINTS) {
                        dsc->touch_data[dsc->current_slot].point.x = in.value;
                        dsc->touch_x_valid[dsc->current_slot] = true;
                        dsc->touch_data_changed = true;
                        LV_LOG_TRACE("MT_X update: slot=%d, x=%d", dsc->current_slot, in.value);
                    }
#endif
                }
                else if(in.code == ABS_Y || in.code == ABS_MT_POSITION_Y) {
                    dsc->root_y = in.value;
#if LV_USE_GESTURE_RECOGNITION
                    if(in.code == ABS_MT_POSITION_Y && dsc->current_slot < MAX_TOUCH_POINTS) {
                        dsc->touch_data[dsc->current_slot].point.y = in.value;
                        dsc->touch_y_valid[dsc->current_slot] = true;
                        dsc->touch_data_changed = true;
                        LV_LOG_TRACE("MT_Y update: slot=%d, y=%d", dsc->current_slot, in.value);
                    }
#endif
                }
                else if(in.code == ABS_MT_TRACKING_ID) {
                    if(in.value == -1) dsc->state = LV_INDEV_STATE_RELEASED;
                    else dsc->state = LV_INDEV_STATE_PRESSED;
#if LV_USE_GESTURE_RECOGNITION
                    if(in.value == -1) {
                        if(dsc->current_slot < MAX_TOUCH_POINTS) {
                            dsc->touch_data[dsc->current_slot].state = LV_INDEV_STATE_RELEASED;
                            dsc->touch_data_changed = true;
                            LV_LOG_TRACE("Touch slot %d released", dsc->current_slot);

                            /* RELEASED slots count too: the SYN_REPORT loops below iterate up to
                             * touch_count, so a slot that has just been lifted must stay in range
                             * long enough for the gesture recogniser to see the release.  The slot
                             * is dropped from the count later, once it has been invalidated. */
                            dsc->touch_count = 0;
                            for(int i = 0; i < MAX_TOUCH_POINTS; i++) {
                                if(dsc->touch_data[i].state == LV_INDEV_STATE_PRESSED ||
                                   dsc->touch_data[i].state == LV_INDEV_STATE_RELEASED) {
                                    dsc->touch_count = i + 1;
                                }
                            }
                        }
                    }
                    else {
                        if(dsc->current_slot < MAX_TOUCH_POINTS) {
                            dsc->touch_data[dsc->current_slot].state = LV_INDEV_STATE_PRESSED;
                            dsc->touch_data[dsc->current_slot].id = dsc->current_slot;
                            dsc->touch_count = LV_MAX(dsc->touch_count, dsc->current_slot + 1);
                            dsc->touch_data_changed = true;
                            LV_LOG_TRACE("Touch slot %d pressed, touch_count=%d", dsc->current_slot, dsc->touch_count);
                        }
                    }
#endif
                }
        }
        else if(in.type == EV_KEY) {
            if(in.code == BTN_MOUSE || in.code == BTN_TOUCH) {
                if(in.value == 0) dsc->state = LV_INDEV_STATE_RELEASED;
                else if(in.value == 1) dsc->state = LV_INDEV_STATE_PRESSED;
            }
            else {
                dsc->key = _evdev_process_key(in.code);
                if(dsc->key) {
                    dsc->state = in.value ? LV_INDEV_STATE_PRESSED : LV_INDEV_STATE_RELEASED;
                    data->continue_reading = true; /*Keep following events in buffer for now*/
                    break;
                }
            }
        }
#if LV_USE_GESTURE_RECOGNITION
        else if(in.type == EV_SYN && in.code == SYN_REPORT) {
            /* Protocol-A release: BTN_TOUCH=0 arrived without ABS_MT_TRACKING_ID=-1.
             * Synthesise RELEASED for pressed slots so gesture recogniser and
             * pointer output both observe the lift. Protocol-B is unaffected:
             * TRACKING_ID=-1 already sets touch_data_changed=true. */
            if(dsc->state == LV_INDEV_STATE_RELEASED && !dsc->touch_data_changed) {
                for(int i = 0; i < dsc->touch_count; i++) {
                    if(dsc->touch_data[i].state == LV_INDEV_STATE_PRESSED) {
                        dsc->touch_data[i].state = LV_INDEV_STATE_RELEASED;
                        dsc->touch_data_changed = true;
                        LV_LOG_TRACE("evdev: Protocol-A release synthesised for slot %d", i);
                    }
                }
            }

            /* Handle gesture recognition at sync event */
            if(dsc->touch_data_changed) {
                LV_LOG_TRACE("=== SYN_REPORT: touch_count=%d ===", dsc->touch_count);
                for(int i = 0; i < dsc->touch_count; i++) {
                    if(dsc->touch_data[i].state == LV_INDEV_STATE_PRESSED || dsc->touch_data[i].state == LV_INDEV_STATE_RELEASED) {
                        LV_LOG_TRACE("Slot %d: state=%s, raw(%d, %d)",
                                     i,
                                     dsc->touch_data[i].state == LV_INDEV_STATE_PRESSED ? "PRESSED" : "RELEASED",
                                     dsc->touch_data[i].point.x, dsc->touch_data[i].point.y);
                    }
                }

                /* Create a temporary array with calibrated coordinates for gesture recognition */
                lv_indev_touch_data_t calibrated_touch_data[MAX_TOUCH_POINTS];

                int active_touches = 0;

                for(int i = 0; i < dsc->touch_count; i++) {
                    /* Skip a PRESSED slot that has never received a coordinate: its
                     * point is still (0,0) and would enter the recognizer as a
                     * corner contact. RELEASED slots are always forwarded so lifts
                     * still terminate gestures. */
                    bool emittable = dsc->touch_data[i].state == LV_INDEV_STATE_RELEASED ||
                                     (dsc->touch_data[i].state == LV_INDEV_STATE_PRESSED &&
                                      _evdev_slot_has_coords(dsc, i));
                    if(emittable) {
                        calibrated_touch_data[active_touches] = dsc->touch_data[i];

                        lv_point_t calib_point = _evdev_process_pointer(indev, dsc->touch_data[i].point.x, dsc->touch_data[i].point.y);
                        calibrated_touch_data[active_touches].point = calib_point;

                        LV_LOG_TRACE("Touch %d (slot %d): state=%s, raw(%d, %d) -> calib(%d, %d)",
                                     active_touches, i,
                                     dsc->touch_data[i].state == LV_INDEV_STATE_PRESSED ? "PRESSED" : "RELEASED",
                                     dsc->touch_data[i].point.x, dsc->touch_data[i].point.y,
                                     calib_point.x, calib_point.y);
                        active_touches++;
                    }
                }

                LV_LOG_TRACE("Gesture recognition: %d touches detected", active_touches);
                lv_indev_gesture_recognizers_update(indev, calibrated_touch_data, active_touches);
                lv_indev_gesture_recognizers_set_data(indev, data);

                /* NOTE: Do NOT clear touch_data[] coordinates here — the switch-case
                 * below reads touch_data[0].point after this block and must receive the
                 * correct release position.  Slot invalidation is deferred until after
                 * the switch-case to avoid sending (0,0) to LVGL on Protocol-A release. */

                dsc->touch_data_changed = false;
            }
        }
#endif
    }

    if(!dsc->deleting && br == -1 && errno != EAGAIN) {
        if(errno == ENODEV) {
            LV_LOG_INFO("evdev device was removed");
        }
        else {
            LV_LOG_ERROR("read failed: %s", strerror(errno));
        }
        lv_async_call(_evdev_async_delete_cb, indev);
        dsc->deleting = true;
    }

    /*Process and store in data*/
    switch(lv_indev_get_type(indev)) {
        case LV_INDEV_TYPE_KEYPAD:
            data->state = dsc->state;
            data->key = dsc->key;
            break;
        case LV_INDEV_TYPE_POINTER: {
#if LV_USE_GESTURE_RECOGNITION
            /* Suppress a press from a slot that has never held a coordinate — it
             * would report the zero-initialised (0,0) as a top-left tap. This can
             * only happen before the slot's first ever ABS_MT_POSITION_X/Y, so it
             * resolves itself as soon as the device reports one. The frame counter
             * is a belt-and-braces guarantee that the gate can never wedge a panel
             * permanently: after EVDEV_XY_TRUST_MAX_FRAMES read cycles the press is
             * let through regardless, trading a possibly misplaced tap for
             * certainty that input keeps working. */
            bool slot0_untrusted = dsc->touch_count > 0 &&
                                   dsc->touch_data[0].state == LV_INDEV_STATE_PRESSED &&
                                   !_evdev_slot_has_coords(dsc, 0);
            bool slot0_give_up = false;
            if(slot0_untrusted) {
                if(dsc->touch_untrusted_frames[0] < EVDEV_XY_TRUST_MAX_FRAMES) {
                    dsc->touch_untrusted_frames[0]++;
                }
                else {
                    LV_LOG_WARN("evdev: slot 0 pressed but never reported a coordinate for %d "
                                "read cycles; falling back to ABS/root position to keep input "
                                "responsive", EVDEV_XY_TRUST_MAX_FRAMES);
                    slot0_give_up = true;
                }
            }
            else {
                dsc->touch_untrusted_frames[0] = 0;
            }

            if(dsc->touch_count > 0 && !slot0_untrusted) {
                data->state = dsc->touch_data[0].state;
                data->point = _evdev_process_pointer(indev, dsc->touch_data[0].point.x, dsc->touch_data[0].point.y);
            }
            else {
                /* Untrusted slot 0 lands here. root_x/root_y are fed by BOTH ABS_X/ABS_Y
                 * and ABS_MT_POSITION_X/Y and are never zeroed, so they are the best
                 * position available — strictly better than the slot's (0,0). Until the
                 * frame budget expires report RELEASED: TRACKING_ID/BTN_TOUCH say we are
                 * pressed, but we have no trustworthy position to attach to that press. */
                data->state = (slot0_untrusted && !slot0_give_up) ? LV_INDEV_STATE_RELEASED : dsc->state;
                data->point = _evdev_process_pointer(indev, dsc->root_x, dsc->root_y);
            }
#else
            data->state = dsc->state;
            data->point = _evdev_process_pointer(indev, dsc->root_x, dsc->root_y);
#endif
            break;
        }
        default:
            break;
    }

#if LV_USE_GESTURE_RECOGNITION
    /* Retire RELEASED touch slots AFTER the switch-case has read the final
     * coordinates.  Clearing the contact id prevents stale RELEASED slots from
     * being re-fed into the gesture recognizer as live contacts on subsequent
     * frames, while deferring it until after the switch-case preserves correct
     * release coordinates for pointer output (fixes dropdown mis-selection on
     * Protocol-A devices like GT9xx).
     *
     * The coordinates themselves are deliberately NOT zeroed. MT protocol B
     * makes the reader responsible for retaining per-contact state: the kernel
     * omits an axis whose value has not changed, and it does not reset a slot's
     * stored coordinates when a contact is destroyed. A slot's last reported
     * position is therefore the correct value for the next contact that reuses
     * the slot on an axis the kernel elects not to resend. Zeroing here would
     * make that suppressed axis read back 0, emitting the press at a screen edge
     * (missing Y -> top edge, missing X -> left edge): phantom edge taps on
     * Holtek 04d9:8030 / BTT-HDMI5. */
    for(int i = 0; i < dsc->touch_count; i++) {
        if(dsc->touch_data[i].state == LV_INDEV_STATE_RELEASED) {
            dsc->touch_data[i].id = UINT8_MAX;   /*invalid sentinel*/
            LV_LOG_TRACE("Retired released touch point slot %d, keeping last position (%d, %d)",
                         i, dsc->touch_data[i].point.x, dsc->touch_data[i].point.y);
        }
    }

    /* Recompute touch_count so retired slots are excluded from subsequent reads.
     * Count any slot that is still active (PRESSED or RELEASED but not yet retired). */
    {
        int new_count = 0;
        for(int i = 0; i < dsc->touch_count; i++) {
            if(dsc->touch_data[i].id != UINT8_MAX) {
                new_count = i + 1;
            }
        }
        dsc->touch_count = new_count;
    }
#endif
}

static void _evdev_indev_delete_cb(lv_event_t * e)
{
    LV_ASSERT(e != NULL);
    lv_indev_t * indev = lv_event_get_target(e);
    lv_evdev_t * dsc = lv_indev_get_driver_data(indev);
    LV_ASSERT(dsc != NULL);
    lv_async_call_cancel(_evdev_async_delete_cb, indev);
    close(dsc->fd);
    lv_free(dsc);
}

#ifndef BSD
static void _evdev_discovery_indev_try_create(const char * file_name)
{
    LV_ASSERT(file_name != NULL);
    if(0 != lv_strncmp(file_name, "event", 5)) {
        return;
    }

    char dev_path[EVDEV_DISCOVERY_PATH_BUF_SIZE];
    lv_snprintf(dev_path, sizeof(dev_path), EVDEV_DISCOVERY_PATH "%s", file_name);

    lv_indev_t * indev = lv_evdev_create(LV_INDEV_TYPE_NONE, dev_path);
    if(indev == NULL) return;

    lv_evdev_t * dsc = lv_indev_get_driver_data(indev);

    /* Compare this new evdev's unique identity with the already registered ones.
     * If a match is found, it means the user has already added it and a duplicate
     * should not be added automatically -- although it is valid for `lv_evdev_create`
     * to be explicitly called with the same path by the user -- or an edge case
     * has occurred where discoverey has just been started and a new device was
     * connected between the creation of the inotify watcher and the initial full
     * scan of the directory with `readdir`.
     */
    lv_indev_t * ex_indev = NULL;
    while(NULL != (ex_indev = lv_indev_get_next(ex_indev))) {
        if(ex_indev == indev || lv_indev_get_read_cb(ex_indev) != _evdev_read) continue;
        lv_evdev_t * ex_dsc = lv_indev_get_driver_data(ex_indev);
        if(!ex_dsc->deleting && dsc->st_dev == ex_dsc->st_dev && dsc->st_ino == ex_dsc->st_ino) {
            /* an indev for this exact device instance already exists */
            lv_indev_delete(indev);
            return;
        }
    }

    lv_evdev_discovery_t * ed = evdev_discovery;
    LV_ASSERT(ed->cb != NULL);
    ed->cb(indev, dsc->type, ed->cb_user_data);
}

static bool _evdev_discovery_inotify_try_init_watcher(int inotify_fd)
{
    int inotify_wd = inotify_add_watch(inotify_fd, EVDEV_DISCOVERY_PATH, IN_CREATE);
    if(inotify_wd == -1) {
        if(errno != ENOENT) {
            LV_LOG_ERROR("inotify_add_watch failed: %s", strerror(errno));
        }
        return false;
    }

    DIR * dir = opendir(EVDEV_DISCOVERY_PATH);
    if(dir == NULL) {
        if(errno != ENOENT) {
            LV_LOG_ERROR("opendir failed: %s", strerror(errno));
        }
        inotify_rm_watch(inotify_fd, inotify_wd);
        return false;
    }
    while(1) {
        struct dirent * dirent = readdir(dir);
        if(dirent == NULL) break; /* only possible error is EBADF, so no errno check needed */
        _evdev_discovery_indev_try_create(dirent->d_name);
        if(evdev_discovery == NULL) {
            /* was stopped by the callback. cleanup was already done */
            closedir(dir);
            return false;
        }
    }
    closedir(dir);

    return true;
}

static void _evdev_discovery_timer_cb(lv_timer_t * tim)
{
    LV_UNUSED(tim);
    lv_evdev_discovery_t * ed = evdev_discovery;
    LV_ASSERT(ed != NULL);

    if(!ed->inotify_watch_active) {
        ed->inotify_watch_active = _evdev_discovery_inotify_try_init_watcher(ed->inotify_fd);
        return;
    }

    union {
        struct inotify_event in_ev;
        uint8_t buf[sizeof(struct inotify_event) + NAME_MAX + 1];
    } in_data;
    ssize_t br;
    while((br = read(ed->inotify_fd, &in_data, sizeof(in_data))) > 0) {
        struct inotify_event * in_ev_p;
        for(uint8_t * in_data_buf_p = in_data.buf;
            in_data_buf_p < in_data.buf + br;
            in_data_buf_p += sizeof(struct inotify_event) + in_ev_p->len) {
            in_ev_p = (struct inotify_event *)in_data_buf_p;
            if(in_ev_p->mask & IN_IGNORED) {
                /* /dev/input/ was deleted because the last device was removed.
                 * The watch was removed implicitly. It will try to be
                 * recreated the next time the timer runs.
                 */
                ed->inotify_watch_active = false;
                return;
            }
            if(!(in_ev_p->mask & IN_ISDIR) && in_ev_p->len) {
                _evdev_discovery_indev_try_create(in_ev_p->name);
                if(evdev_discovery == NULL) return; /* was stopped by the callback */
            }
        }
    }
    if(br == -1 && errno != EAGAIN) {
        LV_LOG_ERROR("inotify read failed: %s", strerror(errno));
        lv_evdev_discovery_stop();
    }
}
#endif /*BSD*/

/**********************
 *   GLOBAL FUNCTIONS
 **********************/

lv_indev_t * lv_evdev_create_fd(lv_indev_type_t indev_type, int fd)
{
    lv_evdev_t * dsc = lv_malloc_zeroed(sizeof(lv_evdev_t));
    LV_ASSERT_MALLOC(dsc);
    if(dsc == NULL) goto err_malloc;

    dsc->fd = fd;

    struct stat sb;
    if(0 != fstat(dsc->fd, &sb)) {
        LV_LOG_ERROR("fstat failed: %s", strerror(errno));
        goto err_after_malloc;
    }
    dsc->st_dev = sb.st_dev;
    dsc->st_ino = sb.st_ino;

    if(indev_type == LV_INDEV_TYPE_NONE) {
        uint32_t rel_bits = 0;
        if(ioctl(dsc->fd, EVIOCGBIT(EV_REL, sizeof(rel_bits)), &rel_bits) >= 0) {
            /* if this device can emit relative X and Y events, it shall be a pointer indev */
            if((rel_bits & REL_XY_MASK) == REL_XY_MASK) {
                indev_type = LV_INDEV_TYPE_POINTER;
                dsc->type = LV_EVDEV_TYPE_REL;
            }
        }
        else {
            LV_LOG_WARN("ioctl EVIOCGBIT(EV_REL, ...) failed: %s", strerror(errno));
        }
    }

    if(indev_type == LV_INDEV_TYPE_NONE) {
        uint32_t abs_bits = 0;
        if(ioctl(dsc->fd, EVIOCGBIT(EV_ABS, sizeof(abs_bits)), &abs_bits) >= 0) {
            /* if this device can emit absolute X and Y events, it shall be a pointer indev */
            if((abs_bits & ABS_XY_MASK) == ABS_XY_MASK) {
                indev_type = LV_INDEV_TYPE_POINTER;
                dsc->type = LV_EVDEV_TYPE_ABS;
            }
        }
        else {
            LV_LOG_WARN("ioctl EVIOCGBIT(EV_ABS, ...) failed: %s", strerror(errno));
        }
    }

    if(indev_type == LV_INDEV_TYPE_NONE) {
        uint32_t key_bits[KEY_MAX / 32 + 1] = {0};
        if(ioctl(dsc->fd, EVIOCGBIT(EV_KEY, sizeof(key_bits)), key_bits) >= 0) {
            /* if this device can emit any key events, it shall be a keypad indev */
            for(int32_t i = 0; i < (int32_t)(sizeof(key_bits) / sizeof(uint32_t)); i++) {
                if(key_bits[i]) {
                    indev_type = LV_INDEV_TYPE_KEYPAD;
                    dsc->type = LV_EVDEV_TYPE_KEY;
                    break;
                }
            }
        }
        else {
            LV_LOG_WARN("ioctl EVIOCGBIT(EV_KEY, ...) failed: %s", strerror(errno));
        }
    }

    if(indev_type == LV_INDEV_TYPE_NONE) {
        goto err_after_malloc;
    }

    if(fcntl(dsc->fd, F_SETFL, O_NONBLOCK) < 0) {
        LV_LOG_ERROR("fcntl failed: %s", strerror(errno));
        goto err_after_malloc;
    }

    /* Detect the minimum and maximum values of the input device for calibration. */

    if(indev_type == LV_INDEV_TYPE_POINTER) {
        struct input_absinfo absinfo;
        if(ioctl(dsc->fd, EVIOCGABS(ABS_X), &absinfo) == 0) {
            dsc->min_x = absinfo.minimum;
            dsc->max_x = absinfo.maximum;
        }
        else {
            LV_LOG_INFO("ioctl EVIOCGABS(ABS_X) failed: %s", strerror(errno));
        }
        if(ioctl(dsc->fd, EVIOCGABS(ABS_Y), &absinfo) == 0) {
            dsc->min_y = absinfo.minimum;
            dsc->max_y = absinfo.maximum;
        }
        else {
            LV_LOG_INFO("ioctl EVIOCGABS(ABS_Y) failed: %s", strerror(errno));
        }

        /* MT protocol B panels may expose no single-touch axes at all: the
         * digitizer reports only ABS_MT_POSITION_X/Y and the kernel emulates
         * ABS_X/ABS_Y only when the driver asks it to. _evdev_read already
         * accepts either code, but the range query above did not, so such a
         * panel left min==max==0. _evdev_calibrate skips scaling entirely when
         * in_min == in_max, so raw digitizer units were passed through and
         * merely clamped to the panel — every coordinate past the screen width
         * saturated at the edge and touch read as badly misaligned.
         *
         * A degenerate range is the signal, not ioctl failure: querying an
         * unsupported code commonly succeeds with an all-zero absinfo rather
         * than returning an error. Fall back per axis, so a panel that reports
         * one axis conventionally and the other only via MT still calibrates. */
        if(dsc->max_x <= dsc->min_x &&
           ioctl(dsc->fd, EVIOCGABS(ABS_MT_POSITION_X), &absinfo) == 0 &&
           absinfo.maximum > absinfo.minimum) {
            dsc->min_x = absinfo.minimum;
            dsc->max_x = absinfo.maximum;
            LV_LOG_INFO("evdev: ABS_X range empty, using ABS_MT_POSITION_X [%d..%d]",
                        dsc->min_x, dsc->max_x);
        }
        if(dsc->max_y <= dsc->min_y &&
           ioctl(dsc->fd, EVIOCGABS(ABS_MT_POSITION_Y), &absinfo) == 0 &&
           absinfo.maximum > absinfo.minimum) {
            dsc->min_y = absinfo.minimum;
            dsc->max_y = absinfo.maximum;
            LV_LOG_INFO("evdev: ABS_Y range empty, using ABS_MT_POSITION_Y [%d..%d]",
                        dsc->min_y, dsc->max_y);
        }
    }

    lv_indev_t * indev = lv_indev_create();
    if(indev == NULL) goto err_after_malloc;
    lv_indev_set_type(indev, indev_type);
    lv_indev_set_read_cb(indev, _evdev_read);
    lv_indev_set_driver_data(indev, dsc);
    lv_indev_add_event_cb(indev, _evdev_indev_delete_cb, LV_EVENT_DELETE, NULL);

    return indev;

err_after_malloc:
    lv_free(dsc);
err_malloc:
    close(fd);
    return NULL;
}

lv_indev_t * lv_evdev_create(lv_indev_type_t indev_type, const char * dev_path)
{
    LV_CHECK_ARG(dev_path != NULL, return NULL);

    int fd = open(dev_path, O_RDONLY | O_NOCTTY | O_CLOEXEC);
    if(fd < 0) {
        LV_LOG_WARN("open failed: %s", strerror(errno));
        return NULL;
    }

    return lv_evdev_create_fd(indev_type, fd);
}

lv_result_t lv_evdev_discovery_start(lv_evdev_discovery_cb_t cb, void * user_data)
{
#ifndef BSD
    LV_CHECK_ARG(cb != NULL, return LV_RESULT_INVALID);
    lv_evdev_discovery_t * ed = NULL;
    int inotify_fd = -1;
    lv_timer_t * timer = NULL;

    ed = lv_malloc_zeroed(sizeof(lv_evdev_discovery_t));
    LV_ASSERT_MALLOC(ed);
    if(ed == NULL) return LV_RESULT_INVALID;
    evdev_discovery = ed;

    ed->cb = cb;
    ed->cb_user_data = user_data;

    inotify_fd = inotify_init1(IN_NONBLOCK | IN_CLOEXEC);
    if(inotify_fd == -1) {
        LV_LOG_ERROR("inotify_init1 failed: %s", strerror(errno));
        goto err_out;
    }
    ed->inotify_fd = inotify_fd;

    ed->inotify_watch_active = _evdev_discovery_inotify_try_init_watcher(inotify_fd);
    if(evdev_discovery == NULL) return LV_RESULT_OK; /* was stopped by the callback. cleanup was already done */

    timer = lv_timer_create(_evdev_discovery_timer_cb, LV_DEF_REFR_PERIOD, NULL);
    if(timer == NULL) goto err_out;
    ed->timer = timer;

    return LV_RESULT_OK;

err_out:
    if(timer != NULL) lv_timer_delete(timer);
    if(inotify_fd != -1) close(inotify_fd);
    lv_free(ed);
    evdev_discovery = NULL;
    return LV_RESULT_INVALID;

#else /*BSD*/
    return LV_RESULT_INVALID;
#endif
}

lv_result_t lv_evdev_discovery_stop(void)
{
#ifndef BSD
    lv_evdev_discovery_t * ed = evdev_discovery;
    if(ed == NULL) return LV_RESULT_INVALID;

    if(ed->timer) lv_timer_delete(ed->timer);
    close(ed->inotify_fd);
    lv_free(ed);

    evdev_discovery = NULL;
    return LV_RESULT_OK;
#else
    return LV_RESULT_INVALID;
#endif
}

void lv_evdev_set_swap_axes(lv_indev_t * indev, bool swap_axes)
{
    LV_CHECK_ARG(indev != NULL, return);
    lv_evdev_t * dsc = lv_indev_get_driver_data(indev);
    LV_CHECK_ARG(dsc != NULL, return);
    dsc->swap_axes = swap_axes;
}

void lv_evdev_set_calibration(lv_indev_t * indev, int min_x, int min_y, int max_x, int max_y)
{
    LV_CHECK_ARG(indev != NULL, return);
    lv_evdev_t * dsc = lv_indev_get_driver_data(indev);
    LV_CHECK_ARG(dsc != NULL, return);
    dsc->min_x = min_x;
    dsc->min_y = min_y;
    dsc->max_x = max_x;
    dsc->max_y = max_y;
}

bool lv_evdev_is_raw_key(lv_event_t * e)
{
    LV_CHECK_ARG(e != NULL, return false);

    uint32_t key = lv_event_get_key(e);
    return (key > 0xFFFF);
}

uint16_t lv_evdev_get_raw_key(lv_event_t * e)
{
    LV_CHECK_ARG(e != NULL, return 0);

    uint32_t key = lv_event_get_key(e);

    if(key > 0xFFFF) {
        return (uint16_t)(key - 0xFFFF);
    }
    else {
        LV_LOG_WARN("key is an LV_KEY");
    }

    return 0;
}

bool lv_evdev_get_last_raw(lv_indev_t * indev, int * x, int * y)
{
    if(indev == NULL) return false;
    lv_evdev_t * dsc = lv_indev_get_driver_data(indev);
    if(dsc == NULL || !dsc->has_last_raw) return false;
    if(x) *x = dsc->last_raw_x;
    if(y) *y = dsc->last_raw_y;
    return true;
}

void lv_evdev_delete(lv_indev_t * indev)
{
    lv_indev_delete(indev);
}

void lv_evdev_deinit(void)
{
    lv_evdev_discovery_stop();
}

#endif /*LV_USE_EVDEV*/
