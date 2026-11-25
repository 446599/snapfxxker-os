/**
 * @file lv_linux_drm_egl_upload.h
 *
 * Which pixels the EGL DRM flush sends to the display texture. Holds no GL or DRM
 * state, so it builds on every target and can be tested without a GPU.
 */

#ifndef LV_LINUX_DRM_EGL_UPLOAD_H
#define LV_LINUX_DRM_EGL_UPLOAD_H

#ifdef __cplusplus
extern "C" {
#endif

/*********************
 *      INCLUDES
 *********************/

#include "../../../lvgl_public.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/**********************
 *      TYPEDEFS
 **********************/

typedef enum {
    LV_LINUX_DRM_EGL_UPLOAD_NONE,   /**< send nothing for this flush */
    LV_LINUX_DRM_EGL_UPLOAD_AREA,   /**< send the flushed area, clipped to the texture */
    LV_LINUX_DRM_EGL_UPLOAD_FULL,   /**< send the whole buffer */
} lv_linux_drm_egl_upload_t;

typedef struct {
    bool partial;               /**< flushed areas may be sent on their own */
    bool full_pending;          /**< a full upload was requested and has not happened yet */
    unsigned int texture_id;    /**< texture the last full upload filled; 0, which no GL texture uses, before the first */
    int32_t width;              /**< size of that upload */
    int32_t height;
} lv_linux_drm_egl_upload_state_t;

/**********************
 * GLOBAL PROTOTYPES
 **********************/

/**
 * Whether the draw buffer holds every flushed area at its display coordinates, which is
 * what sending one area on its own needs. Direct render mode draws the whole frame at the
 * display's rotated resolution, the size the texture is reshaped to, and the presentation
 * pass applies the rotation. Matrix rotation draws at the original resolution instead.
 * @param render_mode      the display's render mode
 * @param matrix_rotation  whether LVGL rotates the drawing with its transform matrix
 * @return                 true when a flushed area can be sent on its own
 */
static inline bool lv_linux_drm_egl_upload_in_place(lv_display_render_mode_t render_mode,
                                                    bool matrix_rotation)
{
    return render_mode == LV_DISPLAY_RENDER_MODE_DIRECT && !matrix_rotation;
}

/**
 * Decide what one flush sends to the display texture.
 *
 * A texture holds only what was uploaded into it, so sending one area is correct only
 * while the texture holds the rest of the frame: the last full upload filled this same
 * texture at this same size, no full upload was requested since, and the buffer holds the
 * whole frame at display coordinates. Otherwise the whole buffer is sent once, on the
 * frame's last flush, when every area of the frame has been rendered into it.
 *
 * @param state       upload bookkeeping for the display
 * @param texture_id  texture the frame is presented from
 * @param width       display width in pixels
 * @param height      display height in pixels
 * @param in_place    lv_linux_drm_egl_upload_in_place() for the display
 * @param last        this is the frame's last flush
 * @param area        the flushed area
 * @param clipped     receives `area` clipped to the texture when the result is AREA
 * @return            what to send
 */
static inline lv_linux_drm_egl_upload_t lv_linux_drm_egl_upload_plan(const lv_linux_drm_egl_upload_state_t * state,
                                                                     unsigned int texture_id, int32_t width,
                                                                     int32_t height, bool in_place, bool last,
                                                                     const lv_area_t * area, lv_area_t * clipped)
{
    bool texture_holds_frame = state->partial && in_place && !state->full_pending &&
                               state->texture_id == texture_id && state->width == width &&
                               state->height == height;
    if(!texture_holds_frame) {
        return last ? LV_LINUX_DRM_EGL_UPLOAD_FULL : LV_LINUX_DRM_EGL_UPLOAD_NONE;
    }

    clipped->x1 = LV_MAX(area->x1, 0);
    clipped->y1 = LV_MAX(area->y1, 0);
    clipped->x2 = LV_MIN(area->x2, width - 1);
    clipped->y2 = LV_MIN(area->y2, height - 1);
    if(clipped->x1 > clipped->x2 || clipped->y1 > clipped->y2) {
        return LV_LINUX_DRM_EGL_UPLOAD_NONE;
    }
    return LV_LINUX_DRM_EGL_UPLOAD_AREA;
}

/**
 * Byte offset of an area's first pixel inside the whole-frame buffer. Rows step by the
 * buffer's stride, which can be wider than the width times the pixel size; columns step
 * by the pixel size.
 * @param area     the area, at display coordinates
 * @param stride   bytes per buffer row
 * @param px_size  bytes per pixel
 * @return         offset of the area's top-left pixel
 */
static inline size_t lv_linux_drm_egl_upload_first_byte(const lv_area_t * area, uint32_t stride, uint32_t px_size)
{
    return (size_t)area->y1 * stride + (size_t)area->x1 * px_size;
}

/**
 * Record that the whole buffer was sent into `texture_id` at `width` x `height`.
 * @param state       upload bookkeeping for the display
 * @param texture_id  texture that was filled
 * @param width       width of the upload
 * @param height      height of the upload
 */
static inline void lv_linux_drm_egl_upload_filled(lv_linux_drm_egl_upload_state_t * state, unsigned int texture_id,
                                                  int32_t width, int32_t height)
{
    state->texture_id = texture_id;
    state->width = width;
    state->height = height;
    state->full_pending = false;
}

/**
 * Record what one flush sent. Only a whole-buffer upload fills the texture: a sub-area
 * upload adds to a texture that already held the rest of the frame, and a flush that sent
 * nothing changed nothing.
 * @param state       upload bookkeeping for the display
 * @param sent        what lv_linux_drm_egl_upload_plan() decided and the flush sent
 * @param texture_id  texture the flush sent to
 * @param width       display width in pixels
 * @param height      display height in pixels
 */
static inline void lv_linux_drm_egl_upload_record(lv_linux_drm_egl_upload_state_t * state,
                                                  lv_linux_drm_egl_upload_t sent, unsigned int texture_id,
                                                  int32_t width, int32_t height)
{
    if(sent == LV_LINUX_DRM_EGL_UPLOAD_FULL) {
        lv_linux_drm_egl_upload_filled(state, texture_id, width, height);
    }
}

#ifdef __cplusplus
} /*extern "C"*/
#endif

#endif /*LV_LINUX_DRM_EGL_UPLOAD_H*/
