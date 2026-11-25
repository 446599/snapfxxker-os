/**
 * @file lv_linux_drm.h
 *
 */

#ifndef LV_LINUX_DRM_H
#define LV_LINUX_DRM_H

#ifdef __cplusplus
extern "C" {
#endif

/*********************
 *      INCLUDES
 *********************/

#include "../../display/lv_display.h"

#if LV_USE_LINUX_DRM

/*********************
 *      DEFINES
 *********************/

/**********************
 *      TYPEDEFS
 **********************/

/**
 * DRM display mode information.
 * Contains the essential display parameters for mode selection.
 */
typedef struct _lv_linux_drm_mode_t lv_linux_drm_mode_t;

/**
 * Callback function type for selecting a DRM display mode
 * @param disp pointer to the display object
 * @param modes array of available DRM modes
 * @param mode_count number of modes in the array
 * @return index of the selected mode from the modes array
 */
typedef size_t (*lv_linux_drm_select_mode_cb_t)(lv_display_t * disp,
                                                const lv_linux_drm_mode_t * modes,
                                                size_t mode_count);

/**********************
 * GLOBAL PROTOTYPES
 **********************/

/**
 * @brief Create a new Linux DRM display
 *
 * Creates and initializes a new LVGL display using the Linux DRM (Direct Rendering Manager)
 * subsystem for hardware-accelerated graphics output.
 *
 * @return Pointer to the created display object, or NULL on failure
 */
lv_display_t * lv_linux_drm_create(void);

/**
 * @brief Configure the DRM device file and connector for a display
 *
 * Sets the DRM device file path and connector ID to use for the specified display.
 * The DRM device file is typically located at /dev/dri/cardN where N is the card number.
 * The connector ID specifies which physical output (HDMI, VGA, etc.) to use.
 *
 * @param disp         Pointer to the display object created with lv_linux_drm_create()
 * @param file         Path to the DRM device file (e.g., "/dev/dri/card0")
 * @param connector_id ID of the DRM connector to use, or -1 to auto-select the first available
 * @return LV_RESULT_OK if the initialization succeeded or LV_RESULT_INVALID if it failed
 */
lv_result_t lv_linux_drm_set_file(lv_display_t * disp, const char * file, int64_t connector_id);

/**
 * @brief Set a preferred mode (by hdisplay/vdisplay) before lv_linux_drm_set_file()
 *
 * When set to non-zero values, the DRM backend will search the connector's mode
 * list for a mode whose horizontal and vertical resolution exactly match the
 * requested values, and use that instead of the default (first / preferred)
 * mode. If no matching mode is found, it falls back to the default behavior.
 *
 * Pass hdisplay=0 and vdisplay=0 to clear the override.
 *
 * Must be called before lv_linux_drm_set_file() to take effect.
 *
 * Added for HelixScreen -s resolution support (prestonbrown/helixscreen#766).
 *
 * @param disp      Pointer to the display object created with lv_linux_drm_create()
 * @param hdisplay  Requested horizontal resolution in pixels, or 0 to clear
 * @param vdisplay  Requested vertical resolution in pixels, or 0 to clear
 */
void lv_linux_drm_set_preferred_mode(lv_display_t * disp, uint32_t hdisplay, uint32_t vdisplay);

/**
 * @brief Automatically find a suitable DRM device path
 *
 * Scans the system for available DRM devices and returns the path to a suitable
 * device file that can be used with lv_linux_drm_set_file().
 *
 * @return Dynamically allocated string containing the device path (must be freed with lv_free()),
 *         or NULL if no suitable device is found
 */
char * lv_linux_drm_find_device_path(void);

#if LV_LINUX_DRM_USE_EGL
/**
 * @brief Make an EGL DRM display wait for each page flip before queueing the next frame
 *
 * Off (the default), a frame finished while the previous flip is still in flight waits
 * as pending, and a newer frame replaces it before it is ever shown. On, the flush
 * callback blocks until the in-flight flip completes, so every rendered frame reaches
 * the screen, at the cost of holding the LVGL thread for up to one refresh interval.
 * Flips are latched to vblank either way.
 *
 * @param disp   display created with lv_linux_drm_create() and set up by lv_linux_drm_set_file()
 * @param vsync  true to wait for each flip
 */
void lv_linux_drm_egl_set_vsync(lv_display_t * disp, bool vsync);

/**
 * @brief Send the display texture only the areas LVGL flushed, instead of the whole frame
 *
 * The first frame, a frame into a new texture or at a new size, and the frame after
 * lv_linux_drm_egl_request_full_upload() still upload the whole buffer. Sending a
 * sub-rectangle needs GL_UNPACK_ROW_LENGTH (OpenGL ES 3, or GL_EXT_unpack_subimage on
 * OpenGL ES 2); a context without it keeps uploading whole frames.
 *
 * @param disp    display created with lv_linux_drm_create() and set up by lv_linux_drm_set_file()
 * @param enable  true to upload only the flushed areas
 * @return        whether the display now uploads only the flushed areas
 */
bool lv_linux_drm_egl_set_partial_upload(lv_display_t * disp, bool enable);

/**
 * @brief Upload the whole buffer on the next frame
 *
 * For pixels that changed without being flushed to this driver: frames rendered while
 * another flush callback stood in for it, or pixels rewritten on the way to it.
 *
 * @param disp    display created with lv_linux_drm_create()
 */
void lv_linux_drm_egl_request_full_upload(lv_display_t * disp);
#endif

/**
 * Set a callback function for custom DRM mode selection to override the default mode selection behavior
 *
 * The default mode selection behavior is selecting the native mode
 *
 * @param disp pointer to the display object
 * @param callback function to be called when a display mode needs to be selected,
 *                 or NULL to use the default mode selection behavior
 */
void lv_linux_drm_set_mode_cb(lv_display_t * disp, lv_linux_drm_select_mode_cb_t callback);

/**
 * @brief Query the DRM plane's supported rotation bitmask
 *
 * Iterates the plane properties to find the "rotation" property and builds
 * a bitmask from its enum values. Returns 0 if no rotation property exists.
 *
 * @param disp      Pointer to the display object created with lv_linux_drm_create()
 * @return Bitmask of supported DRM_MODE_ROTATE_* values, or 0
 */
uint64_t lv_linux_drm_get_plane_rotation_mask(lv_display_t * disp);

/**
 * @brief Set the DRM plane rotation property
 *
 * Configures the hardware rotation via the DRM plane "rotation" property.
 * This is necessary because LVGL's software rotation (lv_display_set_rotation)
 * does not work with LV_DISPLAY_RENDER_MODE_DIRECT used by the DRM backend.
 *
 * @param disp      Pointer to the display object created with lv_linux_drm_create()
 * @param rotation  DRM rotation bitmask (DRM_MODE_ROTATE_0, DRM_MODE_ROTATE_90,
 *                  DRM_MODE_ROTATE_180, DRM_MODE_ROTATE_270)
 */
void lv_linux_drm_set_rotation(lv_display_t * disp, uint64_t rotation);

/**
 * Get the horizontal resolution of a DRM mode
 * @param mode pointer to the DRM mode object
 * @return horizontal resolution in pixels, or 0 if mode is invalid
 */
int32_t lv_linux_drm_mode_get_horizontal_resolution(const lv_linux_drm_mode_t * mode);

/**
 * Get the vertical resolution of a DRM mode
 * @param mode pointer to the DRM mode object
 * @return vertical resolution in pixels, or 0 if mode is invalid
 */
int32_t lv_linux_drm_mode_get_vertical_resolution(const lv_linux_drm_mode_t * mode);

/**
 * Get the refresh rate of a DRM mode
 * @param mode pointer to the DRM mode object
 * @return refresh rate in Hz, or 0 if mode is invalid
 */
int32_t lv_linux_drm_mode_get_refresh_rate(const lv_linux_drm_mode_t * mode);

/**
 * Check if a DRM mode is the preferred mode for the display
 * @param mode pointer to the DRM mode object
 * @return true if this is the preferred/native mode, false otherwise
 */
bool lv_linux_drm_mode_is_preferred(const lv_linux_drm_mode_t * mode);

/**
 * Get the open DRM master file descriptor backing a DRM display.
 *
 * HelixScreen uses this to issue connector DPMS (drmModeConnectorSetProperty)
 * on the same master fd LVGL holds — required because DPMS modesetting needs
 * DRM master, which LVGL already acquired via drmSetMaster() (#1049). Valid for
 * both the non-EGL (drm_dev_t) and EGL (lv_drm_ctx_t) paths.
 *
 * @param disp pointer to the display object
 * @return the DRM fd (>= 0), or -1 if unavailable
 */
int lv_linux_drm_get_fd(lv_display_t * disp);

/**
 * Get the underlying DRM mode object.
 * @param mode pointer to the DRM mode object
 * @return pointer to the underlying drmModeModeInfo, or NULL if mode is invalid.
 *         Requires including <xf86drmMode.h> to dereference.
 */
void * lv_linux_drm_mode_get_raw(const lv_linux_drm_mode_t * mode);

/**********************
 *      MACROS
 **********************/

#endif /* LV_USE_LINUX_DRM */

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* LV_LINUX_DRM_H */
