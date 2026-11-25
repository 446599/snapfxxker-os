/**
 * @file lv_sdl_window.c
 *
 */

/**
 * Modified by NXP in 2025
 */

/*********************
 *      INCLUDES
 *********************/
#include "../../lvgl_public.h"

#if LV_USE_SDL
#include LV_STDBOOL_INCLUDE
#include "../../core/lv_global.h"
#include "../../display/lv_display_private.h"

#define SDL_MAIN_HANDLED /*To fix SDL's "undefined reference to WinMain" issue*/
#include "lv_sdl_private.h"

#ifdef __ANDROID__
    #include <android/log.h>
#endif

#if LV_COLOR_DEPTH == 1 && LV_SDL_RENDER_MODE != LV_DISPLAY_RENDER_MODE_PARTIAL
    #error SDL LV_COLOR_DEPTH 1 requires LV_SDL_RENDER_MODE LV_DISPLAY_RENDER_MODE_PARTIAL
#endif

/*********************
 *      DEFINES
 *********************/
#define lv_deinit_in_progress  LV_GLOBAL_DEFAULT()->deinit_in_progress

/**********************
 *      TYPEDEFS
 **********************/

/**********************
 *  STATIC PROTOTYPES
 **********************/
static inline int sdl_render_mode(void);
static lv_result_t window_create(lv_display_t * disp);
static void sdl_event_handler(lv_timer_t * t);
static void release_disp_cb(lv_event_t * e);
static void res_chg_event_cb(lv_event_t * e);
#ifdef __ANDROID__
static void apply_android_logical_resolution(lv_display_t * disp);
#endif

/**********************
 *  STATIC VARIABLES
 **********************/
static bool inited = false;
static lv_timer_t * event_handler_timer;

/* Wayland CSD (client-side decorations) state -- see the SDL_WINDOWEVENT_RESIZED
 * handler.  File-scope rather than per-display because the compensation targets
 * the single top-level application window. */
static int32_t csd_target_w = 0;
static int32_t csd_target_h = 0;
static uint8_t csd_attempts = 0;
static bool    csd_settled  = false;

/* Bounded so a compositor that genuinely cannot honour the size (tiled, maximized,
 * or a screen smaller than the requested resolution) can't trap us in a resize loop. */
#define CSD_MAX_ATTEMPTS 5

/**********************
 *      MACROS
 **********************/

/**********************
 *   GLOBAL FUNCTIONS
 **********************/

lv_display_t * lv_sdl_window_create(int32_t hor_res, int32_t ver_res)
{
    if(!inited) {
#if LV_SDL_USE_EGL && defined(SDL_VIDEO_DRIVER_X11)
        /* We only support x11 with EGL for now*/
        SDL_SetHintWithPriority("SDL_VIDEODRIVER", "x11", SDL_HINT_OVERRIDE);
        SDL_SetHint(SDL_HINT_VIDEO_X11_FORCE_EGL, "1");
#endif
        SDL_Init(SDL_INIT_VIDEO);
        SDL_StartTextInput();
        event_handler_timer = lv_timer_create(sdl_event_handler, 5, NULL);
        lv_tick_set_cb(SDL_GetTicks);
        lv_delay_set_cb(SDL_Delay);

        inited = true;
    }

    lv_sdl_window_t * dsc = lv_malloc_zeroed(sizeof(lv_sdl_window_t));
    LV_ASSERT_MALLOC(dsc);
    if(dsc == NULL) return NULL;

    lv_display_t * disp = lv_display_create(hor_res, ver_res);
    if(disp == NULL) {
        lv_free(dsc);
        return NULL;
    }
    lv_display_set_driver_data(disp, dsc);

    /*Register callbacks before window_create() so that Android display
     *scaling (which calls lv_display_set_resolution inside window_create)
     *triggers res_chg_event_cb and properly resizes the texture/framebuffer.*/
    lv_display_add_event_cb(disp, release_disp_cb, LV_EVENT_DELETE, disp);
    lv_display_add_event_cb(disp, res_chg_event_cb, LV_EVENT_RESOLUTION_CHANGED, NULL);
    lv_display_add_event_cb(disp, res_chg_event_cb, LV_EVENT_COLOR_FORMAT_CHANGED, NULL);

    lv_result_t res = window_create(disp);
    if(res != LV_RESULT_OK) {
        LV_LOG_ERROR("Failed to initialize window");
        /*release_disp_cb (registered above on LV_EVENT_DELETE) owns dsc: it
         *tears down the backend, destroys the window and frees dsc. Freeing dsc
         *here as well would leave that handler reading a dangling pointer, and
         *would free dsc twice.*/
        lv_display_delete(disp);
        return NULL;
    }

    /*Process the initial events*/
    sdl_event_handler(NULL);

    return disp;
}

void lv_sdl_window_set_resizeable(lv_display_t * disp, bool value)
{
    LV_CHECK_ARG(disp != NULL, return);
    lv_sdl_window_t * dsc = lv_display_get_driver_data(disp);
    LV_CHECK_ARG_MSG(dsc != NULL, return, "Invalid display");
    SDL_SetWindowResizable(dsc->window, value);
}

void lv_sdl_window_set_size(lv_display_t * disp, int32_t hor_res, int32_t ver_res)
{
    LV_CHECK_ARG(disp != NULL, return);
    lv_sdl_window_t * dsc = lv_display_get_driver_data(disp);
    LV_CHECK_ARG_MSG(dsc != NULL, return, "Invalid display");
    SDL_SetWindowSize(dsc->window, hor_res, ver_res);
}

void lv_sdl_window_set_zoom(lv_display_t * disp, float zoom)
{
    LV_CHECK_ARG(disp != NULL, return);
    lv_sdl_window_t * dsc = lv_display_get_driver_data(disp);
    LV_CHECK_ARG_MSG(dsc != NULL, return, "Invalid display");
    dsc->zoom = zoom;
    lv_display_send_event(disp, LV_EVENT_RESOLUTION_CHANGED, NULL);
    lv_refr_now(disp);
}

float lv_sdl_window_get_zoom(lv_display_t * disp)
{
    if(disp == NULL) {
        return 1.0f;
    }
    lv_sdl_window_t * dsc = lv_display_get_driver_data(disp);
    if(dsc == NULL) {
        return 1.0f;
    }
    return dsc->zoom;
}

lv_display_t * lv_sdl_get_disp_from_win_id(uint32_t win_id)
{
    lv_display_t * disp = lv_display_get_next(NULL);
    if(win_id == UINT32_MAX) return disp;

    while(disp) {
        lv_sdl_window_t * dsc = lv_display_get_driver_data(disp);
        if(dsc != NULL && SDL_GetWindowID(dsc->window) == win_id) {
            return disp;
        }
        disp = lv_display_get_next(disp);
    }
    return NULL;
}

void lv_sdl_window_set_title(lv_display_t * disp, const char * title)
{
    LV_CHECK_ARG(disp != NULL, return);
    LV_CHECK_ARG(title != NULL, return);

    lv_sdl_window_t * dsc = lv_display_get_driver_data(disp);
    LV_CHECK_ARG_MSG(dsc != NULL, return, "Invalid display");
    SDL_SetWindowTitle(dsc->window, title);
}

void lv_sdl_window_set_icon(lv_display_t * disp, void * icon, int32_t width, int32_t height)
{
    LV_CHECK_ARG(disp != NULL, return);
    LV_CHECK_ARG(icon != NULL, return);

    lv_sdl_window_t * dsc = lv_display_get_driver_data(disp);
    LV_CHECK_ARG_MSG(dsc != NULL, return, "Invalid display");
    SDL_Surface * iconSurface = SDL_CreateRGBSurfaceWithFormatFrom(icon, width, height, 32, width * 4,
                                                                   SDL_PIXELFORMAT_ARGB8888);
    SDL_SetWindowIcon(dsc->window, iconSurface);
    SDL_FreeSurface(iconSurface);
}

void * lv_sdl_window_get_renderer(lv_display_t * display)
{
    LV_CHECK_ARG(display != NULL, return NULL);
    return lv_sdl_backend_ops.get_renderer(display);
}

struct SDL_Window * lv_sdl_window_get_window(lv_display_t * disp)
{
    LV_CHECK_ARG(disp != NULL, return NULL);
    lv_sdl_window_t * dsc = lv_display_get_driver_data(disp);
    LV_CHECK_ARG_MSG(dsc != NULL, return NULL, "Invalid display");
    return dsc->window;
}

void lv_sdl_quit(void)
{
    if(inited) {
        SDL_Quit();
        lv_timer_delete(event_handler_timer);
        event_handler_timer = NULL;
        inited = false;
    }
}

void lv_sdl_backend_set_display_data(lv_display_t * display, void * backend_display_data)
{
    LV_ASSERT(display != NULL);
    lv_sdl_window_t * dsc = lv_display_get_driver_data(display);
    dsc->backend_data = backend_display_data;
}
void * lv_sdl_backend_get_display_data(lv_display_t * display)
{
    LV_ASSERT(display != NULL);
    lv_sdl_window_t * dsc = lv_display_get_driver_data(display);
    return dsc->backend_data;
}

int32_t lv_sdl_window_get_horizontal_resolution(lv_display_t * display)
{
    LV_ASSERT(display != NULL);
    lv_sdl_window_t * dsc = lv_display_get_driver_data(display);
    LV_ASSERT(dsc != NULL);
    return (int32_t)((float)(display->hor_res) * dsc->zoom);
}
int32_t lv_sdl_window_get_vertical_resolution(lv_display_t * display)
{
    LV_ASSERT(display != NULL);
    lv_sdl_window_t * dsc = lv_display_get_driver_data(display);
    LV_ASSERT(dsc != NULL);
    return (int32_t)((float)(display->ver_res) * dsc->zoom);
}

/**********************
 *   STATIC FUNCTIONS
 **********************/

static inline int sdl_render_mode(void)
{
    return LV_SDL_RENDER_MODE;
}

/**
 * SDL main thread. All SDL related task have to be handled here!
 * It initializes SDL, handles drawing and the mouse.
 */
static void sdl_event_handler(lv_timer_t * t)
{
    LV_UNUSED(t);

    /*Refresh handling*/
    SDL_Event event;
    while(SDL_PollEvent(&event)) {
        lv_sdl_mouse_handler(&event);
#if LV_SDL_MOUSEWHEEL_MODE == LV_SDL_MOUSEWHEEL_MODE_ENCODER
        lv_sdl_mousewheel_handler(&event);
#endif
        lv_sdl_keyboard_handler(&event);

        if(event.type == SDL_WINDOWEVENT) {
            lv_display_t * disp = lv_sdl_get_disp_from_win_id(event.window.windowID);
            if(disp == NULL) continue;
            lv_sdl_window_t * dsc = lv_display_get_driver_data(disp);
            switch(event.window.event) {
#if SDL_VERSION_ATLEAST(2, 0, 5)
                case SDL_WINDOWEVENT_TAKE_FOCUS:
#endif
                case SDL_WINDOWEVENT_EXPOSED:
                    lv_sdl_backend_ops.redraw(disp);
                    break;
                case SDL_WINDOWEVENT_RESIZED:
#ifdef __ANDROID__
                    /* The native surface changed (fold/unfold, orientation, IME).
                     * Recompute integer scale + logical resolution so the LVGL
                     * canvas matches the new surface, then redraw.  Firing
                     * lv_display_set_resolution() also triggers
                     * res_chg_event_cb -> resize_display (SW buffers) and
                     * LV_EVENT_SIZE_CHANGED on the root screen, which the
                     * application uses to refresh theme constants and
                     * relayout panels. */
                    apply_android_logical_resolution(disp);
                    lv_refr_now(disp);
#else
                    {
                        int32_t hres = (int32_t)((float)(event.window.data1) / dsc->zoom);
                        int32_t vres = (int32_t)((float)(event.window.data2) / dsc->zoom);

                        /* Wayland CSD (client-side decorations) can shrink the content
                         * area below the requested size.  When that happens, grow the
                         * window to compensate so LVGL keeps its original resolution.
                         *
                         * A single attempt is not enough: a compositor may re-assert the
                         * shrunken size seconds after the first compensation, long after a
                         * one-shot guard has been spent.  Keep compensating until the
                         * content actually reaches the requested size, then latch
                         * `csd_settled` so a later deliberate user resize is honoured
                         * rather than fought. */
                        if(!dsc->ignore_size_chg && !csd_settled && csd_target_w > 0) {
                            if(hres >= csd_target_w && vres >= csd_target_h) {
                                /* Target reached -- stop compensating from here on. */
                                csd_settled = true;
                            }
                            else if(csd_attempts < CSD_MAX_ATTEMPTS) {
                                csd_attempts++;
                                /* SDL_CreateWindow() and SDL_SetWindowSize() do not agree
                                 * about decorations under Wayland/libdecor: the creation
                                 * size behaves as the OUTER size (the compositor carves the
                                 * title bar out of it, so 800x480 yields 800x444 of
                                 * content), while SDL_SetWindowSize() sets the CONTENT size
                                 * and the title bar is added on top.
                                 *
                                 * So re-assert the target verbatim.  Adding the observed
                                 * shortfall here would over-grow the window and leave an
                                 * unpainted band below LVGL's canvas; using
                                 * SDL_GetWindowSize() + shortfall reproduces the original
                                 * request and never converges. */
                                SDL_SetWindowSize(dsc->window,
                                                  (int)csd_target_w, (int)csd_target_h);
                                LV_LOG_USER("CSD compensation (attempt %d/%d): content %dx%d < "
                                            "requested %dx%d, re-asserting content size",
                                            (int)csd_attempts, (int)CSD_MAX_ATTEMPTS,
                                            (int)hres, (int)vres,
                                            (int)csd_target_w, (int)csd_target_h);
                                break;
                            }
                            else {
                                /* Give up, but say so loudly: the running resolution now
                                 * differs from what was requested, which can select a
                                 * different responsive breakpoint than intended. */
                                csd_settled = true;
                                LV_LOG_WARN("CSD compensation failed after %d attempts: running at "
                                            "%dx%d instead of requested %dx%d -- responsive "
                                            "breakpoints will reflect the smaller size",
                                            (int)CSD_MAX_ATTEMPTS, (int)hres, (int)vres,
                                            (int)csd_target_w, (int)csd_target_h);
                            }
                        }

                        dsc->ignore_size_chg = 1;
                        lv_display_set_resolution(disp, hres, vres);
                        dsc->ignore_size_chg = 0;
                        lv_refr_now(disp);
                    }
#endif
                    break;
                case SDL_WINDOWEVENT_CLOSE:
                    lv_display_delete(disp);
                    break;
                default:
                    break;
            }
        }
        if(event.type == SDL_QUIT) {
            /*Don't handle SDL_QUIT here - the application layer handles
             *Cmd+Q via keyboard state polling and window close via
             *SDL_WINDOWEVENT_CLOSE. Calling SDL_Quit() from within the
             *event handler causes a crash on macOS.*/
#if LV_SDL_DIRECT_EXIT
            exit(0);
#endif
        }
        if(event.type == SDL_APP_WILLENTERBACKGROUND) {
            extern void helix_notify_app_backgrounded(void);
            helix_notify_app_backgrounded();
        }
        if(event.type == SDL_APP_DIDENTERFOREGROUND) {
            extern void helix_notify_app_foregrounded(void);
            helix_notify_app_foregrounded();
        }
    }
}

#ifdef __ANDROID__
/* Maximum logical width before we bump to the next integer scale.  Anything
 * at or above this is considered "too dense" for the LVGL canvas — most
 * phones land at 2x; the Fold/Flip inner display lands at 2x or 3x depending
 * on aspect. */
#define HELIX_ANDROID_LOGICAL_W_MAX 1920
#define HELIX_ANDROID_MIN_SCALE     2
#define HELIX_ANDROID_MAX_SCALE     4
/* A panel at or below this density (Android's densityDpi) renders 1:1 instead
 * of starting at HELIX_ANDROID_MIN_SCALE. HelixScreen's layouts are sized for
 * printer-class panels, which all sit at or below 225 DPI (kScaleDeadbandDpi in
 * include/display_metrics.h), so halving one throws away resolution it needs:
 * the Echo Show 5 (2nd gen) is 960x480 at 195, and 2x gave it a 480x240 canvas,
 * below the smallest layout. Phones report 280 and up and keep the 2x floor. */
#define HELIX_ANDROID_NATIVE_SCALE_MAX_DPI 225.0f
#define HELIX_ANDROID_LOG_TAG       "HelixSDL"

#define HELIX_ANDROID_LOG(prio, fmt, ...) \
    __android_log_print((prio), HELIX_ANDROID_LOG_TAG, fmt, ##__VA_ARGS__)

/* Compute an integer-scale logical resolution that divides evenly into the
 * native surface size and push it into LVGL + SDL.  Called at startup from
 * window_create() (after init_display) AND from SDL_WINDOWEVENT_RESIZED so the
 * LVGL canvas tracks fold/unfold transitions on foldable devices (#941). */
static void apply_android_logical_resolution(lv_display_t * disp)
{
    SDL_Renderer * renderer = lv_sdl_backend_ops.get_renderer(disp);
    if(renderer == NULL) {
        HELIX_ANDROID_LOG(ANDROID_LOG_WARN, "apply_android_logical_resolution: renderer null");
        return;
    }

    int native_w = 0, native_h = 0;
    SDL_GetRendererOutputSize(renderer, &native_w, &native_h);

    /* SDL_GetRendererOutputSize sometimes lags during a surface change;
     * fall back to the current display mode. */
    if(native_w <= 0 || native_h <= 0) {
        SDL_DisplayMode mode;
        if(SDL_GetCurrentDisplayMode(0, &mode) == 0) {
            native_w = mode.w;
            native_h = mode.h;
        }
    }

    if(native_w <= 0 || native_h <= 0) {
        HELIX_ANDROID_LOG(ANDROID_LOG_WARN,
            "apply_android_logical_resolution: native size unavailable (%dx%d)",
            native_w, native_h);
        return;
    }

    int min_scale = HELIX_ANDROID_MIN_SCALE;
    float ddpi = 0.0f;
    if(SDL_GetDisplayDPI(0, &ddpi, NULL, NULL) == 0 && ddpi > 0.0f &&
       ddpi <= HELIX_ANDROID_NATIVE_SCALE_MAX_DPI) {
        min_scale = 1;
    }

    /* Smallest integer scale whose logical width is < HELIX_ANDROID_LOGICAL_W_MAX.
     * Gives the highest usable resolution with pixel-perfect upscaling. */
    int scale = 1;
    for(int s = min_scale; s <= HELIX_ANDROID_MAX_SCALE; s++) {
        if(native_w / s < HELIX_ANDROID_LOGICAL_W_MAX) {
            scale = s;
            break;
        }
    }

    int32_t logical_w = native_w / scale;
    int32_t logical_h = native_h / scale;
    int32_t prev_w = lv_display_get_horizontal_resolution(disp);
    int32_t prev_h = lv_display_get_vertical_resolution(disp);

    SDL_RenderSetLogicalSize(renderer, logical_w, logical_h);

    if(logical_w == prev_w && logical_h == prev_h) {
        HELIX_ANDROID_LOG(ANDROID_LOG_DEBUG,
            "logical resolution unchanged: %dx%d (native %dx%d, %dx)",
            logical_w, logical_h, native_w, native_h, scale);
        return;
    }

    lv_sdl_window_t * dsc = lv_display_get_driver_data(disp);
    dsc->ignore_size_chg = 1;
    /* lv_display_set_resolution fires LV_EVENT_RESOLUTION_CHANGED, which
     * res_chg_event_cb routes into lv_sdl_backend_ops.resize_display to
     * resize the SW framebuffers and SDL texture.  No need to call
     * resize_display() again here. */
    lv_display_set_resolution(disp, logical_w, logical_h);
    dsc->ignore_size_chg = 0;

    HELIX_ANDROID_LOG(ANDROID_LOG_INFO,
        "logical %dx%d -> %dx%d (native %dx%d, %.0f dpi, %dx integer)",
        prev_w, prev_h, logical_w, logical_h, native_w, native_h, (double)ddpi, scale);
}
#endif

static lv_result_t window_create(lv_display_t * disp)
{
    LV_ASSERT(disp != NULL);
    lv_sdl_window_t * dsc = lv_display_get_driver_data(disp);
    LV_ASSERT(dsc != NULL);
    dsc->zoom = 1.0;

    int flag = 0;
#if LV_SDL_USE_EGL
    flag |= SDL_WINDOW_OPENGL;
#endif

#if LV_SDL_FULLSCREEN
    flag |= SDL_WINDOW_FULLSCREEN;
#endif
#ifdef __ANDROID__
    /* On Android, always use fullscreen desktop mode so the SDL window
     * matches the native surface size. Without this, the EGL surface
     * gets invalidated when Android resizes it, causing EGL_BAD_SURFACE. */
    flag |= SDL_WINDOW_FULLSCREEN_DESKTOP;
#else
    /* Decorations are kept so the window stays draggable by its title bar.  A
     * compositor drawing client-side decorations carves them out of the content
     * area (800x480 -> 800x444 under Wayland/libdecor), which would leave LVGL a
     * canvas smaller than requested and silently select the wrong responsive
     * breakpoint -- the SDL_WINDOWEVENT_RESIZED handler measures that shortfall
     * and grows the window to cancel it out.
     *
     * HELIX_SDL_BORDERLESS=1 drops decorations instead, for a compositor where
     * that compensation can't converge. */
    {
        const char * borderless_env = getenv("HELIX_SDL_BORDERLESS");
        if(borderless_env != NULL && borderless_env[0] != '0') {
            flag |= SDL_WINDOW_BORDERLESS;
        }
    }
#endif

    int32_t hor_res = (int32_t)((float)(disp->hor_res) * dsc->zoom);
    int32_t ver_res = (int32_t)((float)(disp->ver_res) * dsc->zoom);

    /* Check environment variables for window position */
    int x_pos = SDL_WINDOWPOS_UNDEFINED;
    int y_pos = SDL_WINDOWPOS_UNDEFINED;

    const char* display_env = getenv("HELIX_SDL_DISPLAY");
    const char* xpos_env = getenv("HELIX_SDL_XPOS");
    const char* ypos_env = getenv("HELIX_SDL_YPOS");

    if (display_env != NULL) {
        /* Center window on specific display by calculating actual center position */
        int display_num = atoi(display_env);
        int num_displays = SDL_GetNumVideoDisplays();

        if (display_num >= 0 && display_num < num_displays) {
            SDL_Rect display_bounds;
            if (SDL_GetDisplayBounds(display_num, &display_bounds) == 0) {
                /* Calculate center position on the display */
                x_pos = display_bounds.x + (display_bounds.w - hor_res) / 2;
                y_pos = display_bounds.y + (display_bounds.h - ver_res) / 2;
                LV_LOG_USER("Window centered on display %d at (%d, %d) [display bounds: %dx%d at %d,%d]",
                            display_num, x_pos, y_pos,
                            display_bounds.w, display_bounds.h,
                            display_bounds.x, display_bounds.y);
            } else {
                LV_LOG_WARN("Failed to get bounds for display %d, using SDL_WINDOWPOS_CENTERED", display_num);
                x_pos = SDL_WINDOWPOS_CENTERED;
                y_pos = SDL_WINDOWPOS_CENTERED;
            }
        } else {
            LV_LOG_WARN("Invalid display number %d (available: 0-%d), using SDL_WINDOWPOS_CENTERED",
                        display_num, num_displays - 1);
            x_pos = SDL_WINDOWPOS_CENTERED;
            y_pos = SDL_WINDOWPOS_CENTERED;
        }
    }
    else if (xpos_env != NULL && ypos_env != NULL) {
        /* Use exact coordinates if both X and Y are specified */
        x_pos = atoi(xpos_env);
        y_pos = atoi(ypos_env);
        LV_LOG_USER("Window position set to exact coordinates (%d, %d)", x_pos, y_pos);
    }

    /* Remember what was actually asked for.  The live display resolution can't be
     * used as the compensation target: once a shrunken size is accepted it becomes
     * the resolution, and the original request is lost. */
    csd_target_w = hor_res;
    csd_target_h = ver_res;
    csd_attempts = 0;
    csd_settled  = false;

    dsc->window = SDL_CreateWindow("HelixScreen",
                                   x_pos, y_pos,
                                   hor_res, ver_res, flag);       /*last param. SDL_WINDOW_BORDERLESS to hide borders*/

    /* If we calculated an explicit position, set it again after window creation
     * to ensure it's honored (some window managers adjust initial position) */
    if (display_env != NULL && x_pos != SDL_WINDOWPOS_UNDEFINED && x_pos != SDL_WINDOWPOS_CENTERED) {
        SDL_SetWindowPosition(dsc->window, x_pos, y_pos);
        LV_LOG_USER("Explicitly set window position to (%d, %d) after creation", x_pos, y_pos);
    }

    if(!dsc->window) {
        LV_LOG_ERROR("Failed to create SDL window: '%s'", SDL_GetError());
        return LV_RESULT_INVALID;
    }

    if(lv_sdl_backend_ops.init_display(disp) != LV_RESULT_OK) {
        LV_LOG_ERROR("Failed to initialize SDL backend");
        SDL_DestroyWindow(dsc->window);
        /*Clear the field so the LV_EVENT_DELETE handler that runs on the way
         *out of lv_sdl_window_create() doesn't destroy the same window twice.*/
        dsc->window = NULL;
        return LV_RESULT_INVALID;
    }

#ifdef __ANDROID__
    /* On Android with FULLSCREEN_DESKTOP, the window is at the native surface
     * size (e.g. 2272x954). Pick an integer-scale logical resolution so the
     * canvas divides evenly into the native surface (no fractional aliasing)
     * and let SDL_RenderSetLogicalSize handle upscaling + touch coordinate
     * mapping.  The same helper runs on SDL_WINDOWEVENT_RESIZED so fold/
     * unfold transitions on foldable devices keep the canvas in sync (#941). */
    apply_android_logical_resolution(disp);
#endif

#ifndef __ANDROID__
    /*Some platforms (e.g. Emscripten) seem to require setting the size again */
    SDL_SetWindowSize(dsc->window, hor_res, ver_res);
#endif
    return LV_RESULT_OK;
}

static void res_chg_event_cb(lv_event_t * e)
{
    LV_ASSERT(e != NULL);
    lv_display_t * disp = lv_event_get_current_target(e);
    LV_ASSERT(disp != NULL);
    if(lv_sdl_backend_ops.resize_display(disp) != LV_RESULT_OK) {
        LV_LOG_WARN("Failed to resize display");
        return;
    }

    lv_sdl_window_t * dsc = lv_display_get_driver_data(disp);
    if(!dsc->ignore_size_chg) {
        SDL_SetWindowSize(dsc->window,
                          lv_sdl_window_get_horizontal_resolution(disp),
                          lv_sdl_window_get_vertical_resolution(disp));
    }
}


static void release_disp_cb(lv_event_t * e)
{
    LV_ASSERT(e != NULL);
    lv_display_t * disp = (lv_display_t *) lv_event_get_user_data(e);
    LV_ASSERT(disp != NULL);
    lv_sdl_window_t * dsc = lv_display_get_driver_data(disp);

    if(dsc != NULL) {
        lv_sdl_backend_ops.deinit_display(disp);
        if(dsc->window != NULL) SDL_DestroyWindow(dsc->window);
        lv_free(dsc);
        lv_display_set_driver_data(disp, NULL);
    }

    /*Quit SDL only AFTER this display's texture, renderer and window are gone.
     *SDL_Quit() tears down the video subsystem that owns them, so quitting
     *first would leave deinit_display() destroying dangling handles.*/
    if(lv_deinit_in_progress) {
        lv_sdl_quit();
    }
}

#endif /*LV_USE_SDL*/
