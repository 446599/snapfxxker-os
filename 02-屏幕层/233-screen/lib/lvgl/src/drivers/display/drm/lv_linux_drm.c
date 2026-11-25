/**
 * @file lv_linux_drm.c
 *
 */

/* DRM hands out dumb-buffer mmap offsets from DRM_FILE_PAGE_OFFSET_START, which
 * is 4 GiB, so every offset returned by DRM_IOCTL_MODE_MAP_DUMB is above 2^32.
 * mmap() takes an off_t, and on 32-bit userspace that is 32 bits wide unless
 * large-file support is requested, which silently truncates the offset and makes
 * the mmap fail. Ask for a 64-bit off_t before any system header is pulled in:
 * glibc then redirects mmap() to mmap64(), while musl and 64-bit targets already
 * have a 64-bit off_t and see this as a no-op. Must stay above the first
 * #include - features.h latches the value on first inclusion. */
#ifndef _FILE_OFFSET_BITS
    #define _FILE_OFFSET_BITS 64
#endif

/*********************
 *      INCLUDES
 *********************/
#include "../../../lvgl_public.h"
#if LV_USE_LINUX_DRM && !LV_LINUX_DRM_USE_EGL

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include LV_STDINT_INCLUDE
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#include <string.h>

#include <xf86drm.h>
#include <xf86drmMode.h>
#include <drm_fourcc.h>

#if LV_USE_LINUX_DRM_GBM_BUFFERS
    #include <gbm.h>
    #include <linux/dma-buf.h>
    #include <sys/ioctl.h>
#endif

/*********************
 *      DEFINES
 *********************/
#if LV_COLOR_DEPTH == 32
    #define DRM_FOURCC DRM_FORMAT_XRGB8888
#elif LV_COLOR_DEPTH == 16
    #define DRM_FOURCC DRM_FORMAT_RGB565
#else
    #error LV_COLOR_DEPTH not supported
#endif

#define BUFFER_CNT 2

/**********************
 *      TYPEDEFS
 **********************/
typedef struct {
    uint32_t handle;
    uint32_t pitch;
    uint64_t offset; /* DRM map offsets start at 4 GiB, so 32 bits is not enough */
    unsigned long int size;
    uint8_t * map;
    uint32_t fb_handle;
} drm_buffer_t;

typedef struct {
    int fd;
    uint32_t conn_id, enc_id, crtc_id, plane_id, crtc_idx;
    uint32_t width, height;
    uint32_t mmWidth, mmHeight;
    uint32_t fourcc;
    drmModeModeInfo mode;
    uint32_t blob_id;
    drmModeCrtc * saved_crtc;
    drmModeAtomicReq * req;
    drmEventContext drm_event_ctx;
    drmModePlane * plane;
    drmModeCrtc * crtc;
    drmModeConnector * conn;
    uint32_t count_plane_props;
    uint32_t count_crtc_props;
    uint32_t count_conn_props;
    drmModePropertyPtr plane_props[128];
    drmModePropertyPtr crtc_props[128];
    drmModePropertyPtr conn_props[128];
    drm_buffer_t drm_bufs[BUFFER_CNT];
    drm_buffer_t * act_buf;
    uint64_t plane_rotation; /* DRM_MODE_ROTATE_* bitmask, 0 = unset */
    uint32_t preferred_hdisplay; /* requested hdisplay for mode override, 0 = unset (#766) */
    uint32_t preferred_vdisplay; /* requested vdisplay for mode override, 0 = unset (#766) */
#if LV_USE_LINUX_DRM_GBM_BUFFERS
    struct gbm_device * gbm_device;
#endif
} drm_dev_t;

/**********************
 *  STATIC PROTOTYPES
 **********************/
static uint32_t get_plane_property_id(drm_dev_t * drm_dev, const char * name);
static uint32_t get_crtc_property_id(drm_dev_t * drm_dev, const char * name);
static uint32_t get_conn_property_id(drm_dev_t * drm_dev, const char * name);
static void page_flip_handler(int fd, unsigned int sequence, unsigned int tv_sec, unsigned int tv_usec,
                              void * user_data);
static int drm_get_plane_props(drm_dev_t * drm_dev);
static int drm_get_crtc_props(drm_dev_t * drm_dev);
static int drm_get_conn_props(drm_dev_t * drm_dev);
static int drm_add_plane_property(drm_dev_t * drm_dev, const char * name, uint64_t value);
static int drm_add_crtc_property(drm_dev_t * drm_dev, const char * name, uint64_t value);
static int drm_add_conn_property(drm_dev_t * drm_dev, const char * name, uint64_t value);
static int find_plane(drm_dev_t * drm_dev, unsigned int fourcc, uint32_t * plane_id, uint32_t crtc_id,
                      uint32_t crtc_idx);
static int drm_find_connector(drm_dev_t * drm_dev, int64_t connector_id);
static int drm_open(const char * path);
static int drm_setup(drm_dev_t * drm_dev, const char * device_path, int64_t connector_id, unsigned int fourcc);

static uint32_t tick_get_cb(void);

#if !LV_USE_LINUX_DRM_GBM_BUFFERS
    static int drm_allocate_dumb(drm_dev_t * drm_dev, drm_buffer_t * buf);
#elif LV_USE_LINUX_DRM_GBM_BUFFERS
    static int create_gbm_buffer(drm_dev_t * drm_dev, drm_buffer_t * buf);
#endif

static int drm_setup_buffers(drm_dev_t * drm_dev);
static int drm_legacy_set_crtc(drm_dev_t * drm_dev, drm_buffer_t * buf);
static int drm_dmabuf_set_plane(drm_dev_t * drm_dev, drm_buffer_t * buf);
static void drm_flush_wait(lv_display_t * drm_dev);
static void drm_flush(lv_display_t * disp, const lv_area_t * area, uint8_t * px_map);
static void drm_dmabuf_set_active_buf(lv_event_t * event);
static void drm_del_event_cb(lv_event_t * event);

/**********************
 *  STATIC VARIABLES
 **********************/
static int use_legacy_flip = 0;        /* 1 = atomic failed, use drmModeSetCrtc */
static int atomic_fail_count = 0;      /* consecutive atomic commit failures */

/**********************
 *      MACROS
 **********************/
#ifndef DIV_ROUND_UP
    #define DIV_ROUND_UP(n, d) (((n) + (d) - 1) / (d))
#endif

/**********************
 *   GLOBAL FUNCTIONS
 **********************/

lv_display_t * lv_linux_drm_create(void)
{
    lv_display_t * disp;

    lv_tick_set_cb(tick_get_cb);

    drm_dev_t * drm_dev = lv_malloc_zeroed(sizeof(drm_dev_t));
    LV_ASSERT_MALLOC(drm_dev);
    if(drm_dev == NULL) return NULL;

    drm_dev->fd = -1;

    disp = lv_display_create(800, 480);
    if(disp == NULL) {
        lv_free(drm_dev);
        return NULL;
    }
    lv_display_set_driver_data(disp, drm_dev);
    lv_display_set_flush_wait_cb(disp, drm_flush_wait);
    lv_display_set_flush_cb(disp, drm_flush);
    lv_display_add_event_cb(disp, drm_del_event_cb, LV_EVENT_DELETE, NULL);

    return disp;
}

/* Called by LVGL when there is something that needs redrawing
 * it sets the active buffer. if GBM buffers are used, it issues a DMA_BUF_SYNC
 * ioctl call to lock the buffer for CPU access, the buffer is unlocked just
 * before the atomic commit */
static void drm_dmabuf_set_active_buf(lv_event_t * event)
{
    drm_dev_t * drm_dev;
    lv_display_t * disp;
    lv_draw_buf_t * act_buf;
    int i;

    disp = (lv_display_t *) lv_event_get_current_target(event);
    drm_dev = (drm_dev_t *) lv_display_get_driver_data(disp);
    act_buf = lv_display_get_buf_active(disp);

    if(drm_dev->act_buf == NULL) {

        for(i = 0; i < BUFFER_CNT; i++) {
            if(act_buf->unaligned_data == drm_dev->drm_bufs[i].map) {
                drm_dev->act_buf = &drm_dev->drm_bufs[i];
                LV_LOG_TRACE("Set active buffer idx: %d", i);
                break;
            }
        }

#if LV_USE_LINUX_DRM_GBM_BUFFERS
        struct dma_buf_sync sync_req;
        sync_req.flags = DMA_BUF_SYNC_START | DMA_BUF_SYNC_RW;
        int res;

        if((res = ioctl(drm_dev->act_buf->handle, DMA_BUF_IOCTL_SYNC, &sync_req)) != 0) {
            LV_LOG_ERROR("Failed to start DMA-BUF R/W SYNC res: %d", res);
        }
#endif

    }
    else {

        LV_LOG_TRACE("active buffer already set");
    }

}

lv_result_t lv_linux_drm_set_file(lv_display_t * disp, const char * file, int64_t connector_id)
{
    int ret;

    LV_CHECK_ARG(disp != NULL, return LV_RESULT_INVALID);
    LV_CHECK_ARG(file != NULL, return LV_RESULT_INVALID);
    drm_dev_t * drm_dev = lv_display_get_driver_data(disp);

    ret = drm_setup(drm_dev, file, connector_id, DRM_FOURCC);
    if(ret) {
        return LV_RESULT_INVALID;
    }

    int32_t hor_res = drm_dev->width;
    int32_t ver_res = drm_dev->height;

    ret = drm_setup_buffers(drm_dev);
    if(ret) {
        LV_LOG_ERROR("DRM buffer allocation failed");
        close(drm_dev->fd);
        drm_dev->fd = -1;
        return LV_RESULT_INVALID;
    }

    LV_LOG_INFO("DRM subsystem and buffer mapped successfully");

    int32_t width = drm_dev->mmWidth;

    size_t buf_size = LV_MIN(drm_dev->drm_bufs[1].size, drm_dev->drm_bufs[0].size);
    uint32_t stride = drm_dev->drm_bufs[0].pitch;
    /* Resolution must be set first because if the screen is smaller than the size passed
     * to lv_display_create then the buffers aren't big enough for LV_DISPLAY_RENDER_MODE_DIRECT.
     */
    lv_display_set_resolution(disp, hor_res, ver_res);
    lv_display_set_buffers_with_stride(disp, drm_dev->drm_bufs[1].map, drm_dev->drm_bufs[0].map, buf_size,
                                       stride, LV_DISPLAY_RENDER_MODE_DIRECT);


    /* Set the handler that is called before a redraw occurs to set the active buffer/plane
     * when GBM buffers are used the DMA_BUF_SYNC_START is issued there */
    lv_display_add_event_cb(disp, drm_dmabuf_set_active_buf, LV_EVENT_REFR_START, drm_dev);

    if(width) {
        lv_display_set_dpi(disp, DIV_ROUND_UP(hor_res * 25400, width * 1000));
    }

    LV_LOG_INFO("Resolution is set to %" LV_PRId32 "x%" LV_PRId32 " at %" LV_PRId32 "dpi",
                hor_res, ver_res, lv_display_get_dpi(disp));
    return LV_RESULT_OK;
}

void lv_linux_drm_set_mode_cb(lv_display_t * disp, lv_linux_drm_select_mode_cb_t callback)
{
    LV_UNUSED(disp);
    LV_UNUSED(callback);
    LV_LOG_WARN("DRM without EGL support doesn't currently support setting a mode selection callback");
}

void lv_linux_drm_set_preferred_mode(lv_display_t * disp, uint32_t hdisplay, uint32_t vdisplay)
{
    drm_dev_t * drm_dev = lv_display_get_driver_data(disp);
    if(drm_dev == NULL) {
        LV_LOG_ERROR("DRM display not initialized");
        return;
    }
    drm_dev->preferred_hdisplay = hdisplay;
    drm_dev->preferred_vdisplay = vdisplay;
    LV_LOG_INFO("DRM preferred mode set to %ux%u", (unsigned)hdisplay, (unsigned)vdisplay);
}

uint64_t lv_linux_drm_get_plane_rotation_mask(lv_display_t * disp)
{
    drm_dev_t * drm_dev = lv_display_get_driver_data(disp);
    if(drm_dev == NULL) {
        return 0;
    }

    uint32_t i;
    for(i = 0; i < drm_dev->count_plane_props; ++i) {
        if(lv_strcmp(drm_dev->plane_props[i]->name, "rotation") == 0) {
            drmModePropertyPtr prop = drm_dev->plane_props[i];
            uint64_t mask = 0;
            int e;
            for(e = 0; e < prop->count_enums; ++e) {
                mask |= (1ULL << prop->enums[e].value);
            }
            LV_LOG_INFO("DRM plane rotation mask: 0x%x (%d enums)", (unsigned)mask, prop->count_enums);
            return mask;
        }
    }

    LV_LOG_INFO("DRM plane has no rotation property");
    return 0;
}

int lv_linux_drm_get_fd(lv_display_t * disp)
{
    drm_dev_t * drm_dev = lv_display_get_driver_data(disp);
    if(drm_dev == NULL) {
        return -1;
    }
    return drm_dev->fd;
}

void lv_linux_drm_set_rotation(lv_display_t * disp, uint64_t rotation)
{
    drm_dev_t * drm_dev = lv_display_get_driver_data(disp);
    if(drm_dev == NULL) {
        LV_LOG_ERROR("DRM display not initialized");
        return;
    }
    drm_dev->plane_rotation = rotation;
    LV_LOG_INFO("DRM plane rotation set to %u", (unsigned)rotation);
}

/**********************
 *   STATIC FUNCTIONS
 **********************/

static uint32_t get_plane_property_id(drm_dev_t * drm_dev, const char * name)
{
    uint32_t i;

    LV_LOG_TRACE("Find plane property: %s", name);

    for(i = 0; i < drm_dev->count_plane_props; ++i)
        if(!lv_strcmp(drm_dev->plane_props[i]->name, name))
            return drm_dev->plane_props[i]->prop_id;

    LV_LOG_TRACE("Unknown plane property: %s", name);

    return 0;
}

static uint32_t get_crtc_property_id(drm_dev_t * drm_dev, const char * name)
{
    uint32_t i;

    LV_LOG_TRACE("Find crtc property: %s", name);

    for(i = 0; i < drm_dev->count_crtc_props; ++i)
        if(!lv_strcmp(drm_dev->crtc_props[i]->name, name))
            return drm_dev->crtc_props[i]->prop_id;

    LV_LOG_TRACE("Unknown crtc property: %s", name);

    return 0;
}

static uint32_t get_conn_property_id(drm_dev_t * drm_dev, const char * name)
{
    uint32_t i;

    LV_LOG_TRACE("Find conn property: %s", name);

    for(i = 0; i < drm_dev->count_conn_props; ++i)
        if(!lv_strcmp(drm_dev->conn_props[i]->name, name))
            return drm_dev->conn_props[i]->prop_id;

    LV_LOG_TRACE("Unknown conn property: %s", name);

    return 0;
}

static void page_flip_handler(int fd, unsigned int sequence, unsigned int tv_sec, unsigned int tv_usec,
                              void * user_data)
{
    LV_UNUSED(fd);
    LV_UNUSED(sequence);
    LV_UNUSED(tv_sec);
    LV_UNUSED(tv_usec);
    LV_LOG_TRACE("flip");
    drm_dev_t * drm_dev = user_data;
    if(drm_dev->req) {
        drmModeAtomicFree(drm_dev->req);
        drm_dev->req = NULL;
    }
}

static int drm_get_plane_props(drm_dev_t * drm_dev)
{
    uint32_t i;

    drmModeObjectPropertiesPtr props = drmModeObjectGetProperties(drm_dev->fd, drm_dev->plane_id,
                                                                  DRM_MODE_OBJECT_PLANE);
    if(!props) {
        LV_LOG_ERROR("drmModeObjectGetProperties failed");
        return -1;
    }
    LV_LOG_TRACE("Found %u plane props", props->count_props);
    drm_dev->count_plane_props = props->count_props;
    for(i = 0; i < props->count_props; i++) {
        drm_dev->plane_props[i] = drmModeGetProperty(drm_dev->fd, props->props[i]);
        LV_LOG_TRACE("Added plane prop %u:%s", drm_dev->plane_props[i]->prop_id, drm_dev->plane_props[i]->name);
    }
    drmModeFreeObjectProperties(props);

    return 0;
}

static int drm_get_crtc_props(drm_dev_t * drm_dev)
{
    uint32_t i;

    drmModeObjectPropertiesPtr props = drmModeObjectGetProperties(drm_dev->fd, drm_dev->crtc_id,
                                                                  DRM_MODE_OBJECT_CRTC);
    if(!props) {
        LV_LOG_ERROR("drmModeObjectGetProperties failed");
        return -1;
    }
    LV_LOG_TRACE("Found %u crtc props", props->count_props);
    drm_dev->count_crtc_props = props->count_props;
    for(i = 0; i < props->count_props; i++) {
        drm_dev->crtc_props[i] = drmModeGetProperty(drm_dev->fd, props->props[i]);
        LV_LOG_TRACE("Added crtc prop %u:%s", drm_dev->crtc_props[i]->prop_id, drm_dev->crtc_props[i]->name);
    }
    drmModeFreeObjectProperties(props);

    return 0;
}

static int drm_get_conn_props(drm_dev_t * drm_dev)
{
    uint32_t i;

    drmModeObjectPropertiesPtr props = drmModeObjectGetProperties(drm_dev->fd, drm_dev->conn_id,
                                                                  DRM_MODE_OBJECT_CONNECTOR);
    if(!props) {
        LV_LOG_ERROR("drmModeObjectGetProperties failed");
        return -1;
    }
    LV_LOG_TRACE("Found %u connector props", props->count_props);
    drm_dev->count_conn_props = props->count_props;
    for(i = 0; i < props->count_props; i++) {
        drm_dev->conn_props[i] = drmModeGetProperty(drm_dev->fd, props->props[i]);
        LV_LOG_TRACE("Added connector prop %u:%s", drm_dev->conn_props[i]->prop_id, drm_dev->conn_props[i]->name);
    }
    drmModeFreeObjectProperties(props);

    return 0;
}

static int drm_add_plane_property(drm_dev_t * drm_dev, const char * name, uint64_t value)
{
    int ret;
    uint32_t prop_id = get_plane_property_id(drm_dev, name);

    if(!prop_id) {
        LV_LOG_ERROR("Couldn't find plane prop %s", name);
        return -1;
    }

    ret = drmModeAtomicAddProperty(drm_dev->req, drm_dev->plane_id, get_plane_property_id(drm_dev, name), value);
    if(ret < 0) {
        LV_LOG_ERROR("drmModeAtomicAddProperty (%s:%" PRIu64 ") failed: %d", name, value, ret);
        return ret;
    }

    return 0;
}

static int drm_add_crtc_property(drm_dev_t * drm_dev, const char * name, uint64_t value)
{
    int ret;
    uint32_t prop_id = get_crtc_property_id(drm_dev, name);

    if(!prop_id) {
        LV_LOG_ERROR("Couldn't find crtc prop %s", name);
        return -1;
    }

    ret = drmModeAtomicAddProperty(drm_dev->req, drm_dev->crtc_id, get_crtc_property_id(drm_dev, name), value);
    if(ret < 0) {
        LV_LOG_ERROR("drmModeAtomicAddProperty (%s:%" PRIu64 ") failed: %d", name, value, ret);
        return ret;
    }

    return 0;
}

static int drm_add_conn_property(drm_dev_t * drm_dev, const char * name, uint64_t value)
{
    int ret;
    uint32_t prop_id = get_conn_property_id(drm_dev, name);

    if(!prop_id) {
        LV_LOG_ERROR("Couldn't find conn prop %s", name);
        return -1;
    }

    ret = drmModeAtomicAddProperty(drm_dev->req, drm_dev->conn_id, get_conn_property_id(drm_dev, name), value);
    if(ret < 0) {
        LV_LOG_ERROR("drmModeAtomicAddProperty (%s:%" PRIu64 ") failed: %d", name, value, ret);
        return ret;
    }

    return 0;
}

/**
 * Legacy page flip via drmModeSetCrtc - fallback when atomic modesetting
 * fails (e.g. EACCES on some Pi 4 configurations).
 */
static int drm_legacy_set_crtc(drm_dev_t * drm_dev, drm_buffer_t * buf)
{
    int ret = drmModeSetCrtc(drm_dev->fd, drm_dev->crtc_id, buf->fb_handle,
                              0, 0, &drm_dev->conn_id, 1, &drm_dev->mode);
    if(ret) {
        LV_LOG_ERROR("drmModeSetCrtc fallback failed: %s (%d)", strerror(errno), errno);
    }
    return ret;
}

static int drm_dmabuf_set_plane(drm_dev_t * drm_dev, drm_buffer_t * buf)
{
    int ret;
    static int first = 1;
    uint32_t flags = DRM_MODE_PAGE_FLIP_EVENT | DRM_MODE_ATOMIC_NONBLOCK;

#if LV_USE_LINUX_DRM_GBM_BUFFERS

    struct dma_buf_sync sync_req;

    sync_req.flags = DMA_BUF_SYNC_END | DMA_BUF_SYNC_RW;
    if(ioctl(buf->handle, DMA_BUF_IOCTL_SYNC, &sync_req) != 0) {
        LV_LOG_ERROR("Failed to end DMA-BUF R/W SYNC");
    }

#endif

    drm_dev->req = drmModeAtomicAlloc();

    /* On first Atomic commit, do a modeset */
    if(first) {
        drm_add_conn_property(drm_dev, "CRTC_ID", drm_dev->crtc_id);

        drm_add_crtc_property(drm_dev, "MODE_ID", drm_dev->blob_id);
        drm_add_crtc_property(drm_dev, "ACTIVE", 1);

        flags |= DRM_MODE_ATOMIC_ALLOW_MODESET;

        first = 0;
    }

    drm_add_plane_property(drm_dev, "FB_ID", buf->fb_handle);
    drm_add_plane_property(drm_dev, "CRTC_ID", drm_dev->crtc_id);
    drm_add_plane_property(drm_dev, "SRC_X", 0);
    drm_add_plane_property(drm_dev, "SRC_Y", 0);
    drm_add_plane_property(drm_dev, "SRC_W", drm_dev->width << 16);
    drm_add_plane_property(drm_dev, "SRC_H", drm_dev->height << 16);
    drm_add_plane_property(drm_dev, "CRTC_X", 0);
    drm_add_plane_property(drm_dev, "CRTC_Y", 0);
    drm_add_plane_property(drm_dev, "CRTC_W", drm_dev->width);
    drm_add_plane_property(drm_dev, "CRTC_H", drm_dev->height);

    if(drm_dev->plane_rotation) {
        drm_add_plane_property(drm_dev, "rotation", drm_dev->plane_rotation);
    }

    /* If atomic modesetting previously failed, skip to legacy path.
     * Only after the first frame (initial modeset must go through atomic). */
    if(use_legacy_flip && !first) {
        if(drm_dev->req) {
            drmModeAtomicFree(drm_dev->req);
            drm_dev->req = NULL;
        }
        return drm_legacy_set_crtc(drm_dev, buf);
    }

    ret = drmModeAtomicCommit(drm_dev->fd, drm_dev->req, flags, drm_dev);
    if(ret) {
        int err = errno;
        atomic_fail_count++;

        if(err == EACCES || err == EPERM) {
            if(atomic_fail_count == 1) {
                LV_LOG_ERROR("drmModeAtomicCommit: Permission denied. "
                             "Trying legacy drmModeSetCrtc fallback. "
                             "If issues persist, try HELIX_DISPLAY_BACKEND=fbdev");
            }
        }
        else {
            LV_LOG_ERROR("drmModeAtomicCommit failed: %s (%d)", strerror(err), err);
        }

        /* Try legacy fallback */
        if(drm_dev->req) {
            drmModeAtomicFree(drm_dev->req);
            drm_dev->req = NULL;
        }
        ret = drm_legacy_set_crtc(drm_dev, buf);
        if(ret == 0) {
            LV_LOG_INFO("Legacy drmModeSetCrtc succeeded, using legacy path for future frames");
            use_legacy_flip = 1;
        }
        return ret;
    }

    return 0;
}

/**
 * @brief Get the DRM plane type (PRIMARY, OVERLAY, or CURSOR) for a plane.
 * @return DRM_PLANE_TYPE_PRIMARY (1), DRM_PLANE_TYPE_OVERLAY (0),
 *         DRM_PLANE_TYPE_CURSOR (2), or -1 on error.
 */
static int get_plane_type(int fd, uint32_t plane_id)
{
    drmModeObjectPropertiesPtr props;
    int type = -1;

    props = drmModeObjectGetProperties(fd, plane_id, DRM_MODE_OBJECT_PLANE);
    if(!props) return -1;

    for(uint32_t i = 0; i < props->count_props; i++) {
        drmModePropertyPtr prop = drmModeGetProperty(fd, props->props[i]);
        if(!prop) continue;
        if(!strcmp(prop->name, "type")) {
            type = (int)props->prop_values[i];
            drmModeFreeProperty(prop);
            break;
        }
        drmModeFreeProperty(prop);
    }

    drmModeFreeObjectProperties(props);
    return type;
}

static int find_plane(drm_dev_t * drm_dev, unsigned int fourcc, uint32_t * plane_id, uint32_t crtc_id,
                      uint32_t crtc_idx)
{
    LV_UNUSED(crtc_id);
    drmModePlaneResPtr planes;
    drmModePlanePtr plane;
    unsigned int i;
    unsigned int j;
    int ret = 0;
    uint32_t fallback_plane_id = 0;
    int found_primary = 0;

    planes = drmModeGetPlaneResources(drm_dev->fd);
    if(!planes) {
        LV_LOG_ERROR("drmModeGetPlaneResources failed");
        return -1;
    }

    LV_LOG_INFO("drm: found %u planes, looking for fourcc %c%c%c%c on crtc_idx %u",
                planes->count_planes,
                (fourcc >> 0) & 0xff, (fourcc >> 8) & 0xff,
                (fourcc >> 16) & 0xff, (fourcc >> 24) & 0xff, crtc_idx);

    for(i = 0; i < planes->count_planes; ++i) {
        plane = drmModeGetPlane(drm_dev->fd, planes->planes[i]);
        if(!plane) {
            LV_LOG_ERROR("drmModeGetPlane failed: %s", strerror(errno));
            ret = -1;
            break;
        }

        if(!(plane->possible_crtcs & (1 << crtc_idx))) {
            LV_LOG_INFO("drm: plane %u: crtc_mask=0x%x, skipping (need bit %u)",
                        plane->plane_id, plane->possible_crtcs, crtc_idx);
            drmModeFreePlane(plane);
            continue;
        }

        for(j = 0; j < plane->count_formats; ++j) {
            if(plane->formats[j] == fourcc)
                break;
        }

        if(j == plane->count_formats) {
            LV_LOG_INFO("drm: plane %u: no matching fourcc among %u formats",
                        plane->plane_id, plane->count_formats);
            drmModeFreePlane(plane);
            continue;
        }

        int ptype = get_plane_type(drm_dev->fd, plane->plane_id);
        LV_LOG_INFO("drm: plane %u: format match, type=%d (%s)",
                    plane->plane_id, ptype,
                    ptype == DRM_PLANE_TYPE_PRIMARY ? "PRIMARY" :
                    ptype == DRM_PLANE_TYPE_OVERLAY ? "OVERLAY" :
                    ptype == DRM_PLANE_TYPE_CURSOR  ? "CURSOR"  : "UNKNOWN");

        if(ptype == DRM_PLANE_TYPE_PRIMARY) {
            *plane_id = plane->plane_id;
            drmModeFreePlane(plane);
            found_primary = 1;
            LV_LOG_INFO("drm: selected PRIMARY plane %d", *plane_id);
            goto out;
        }

        /* Remember first suitable non-primary plane as fallback */
        if(!fallback_plane_id && ptype != DRM_PLANE_TYPE_CURSOR) {
            fallback_plane_id = plane->plane_id;
        }

        drmModeFreePlane(plane);
    }

    /* No primary plane found — use fallback overlay if available */
    if(fallback_plane_id) {
        *plane_id = fallback_plane_id;
        LV_LOG_WARN("drm: no PRIMARY plane found, using OVERLAY plane %d", *plane_id);
        goto out;
    }

    ret = -1;
out:
    drmModeFreePlaneResources(planes);
    return ret;
}

static int drm_find_connector(drm_dev_t * drm_dev, int64_t connector_id)
{
    drmModeConnector * conn = NULL;
    drmModeEncoder * enc = NULL;
    drmModeRes * res;
    int i;
    int ret = -1;

    if((res = drmModeGetResources(drm_dev->fd)) == NULL) {
        LV_LOG_ERROR("drmModeGetResources() failed");
        goto free_res;
    }

    if(res->count_crtcs <= 0) {
        LV_LOG_ERROR("no Crtcs");
        goto free_res;
    }

    /* find all available connectors */
    for(i = 0; i < res->count_connectors; i++) {
        conn = drmModeGetConnector(drm_dev->fd, res->connectors[i]);
        if(!conn)
            continue;

        if(connector_id >= 0 && conn->connector_id != connector_id) {
            drmModeFreeConnector(conn);
            continue;
        }

        if(conn->connection == DRM_MODE_CONNECTED) {
            LV_LOG_INFO("drm: connector %d: connected (%d modes)", conn->connector_id, conn->count_modes);
        }
        else if(conn->connection == DRM_MODE_DISCONNECTED) {
            LV_LOG_INFO("drm: connector %d: disconnected", conn->connector_id);
        }
        else if(conn->connection == DRM_MODE_UNKNOWNCONNECTION) {
            LV_LOG_INFO("drm: connector %d: unknownconnection", conn->connector_id);
        }
        else {
            LV_LOG_INFO("drm: connector %d: unknown", conn->connector_id);
        }

        if(conn->connection == DRM_MODE_CONNECTED && conn->count_modes > 0)
            break;

        drmModeFreeConnector(conn);
        conn = NULL;
    };

    if(!conn) {
        LV_LOG_ERROR("suitable connector not found");
        goto free_res;
    }

    drm_dev->conn_id = conn->connector_id;
    LV_LOG_INFO("drm: using connector %d", drm_dev->conn_id);
    drm_dev->mmWidth = conn->mmWidth;
    drm_dev->mmHeight = conn->mmHeight;

    /* Default to the first mode; honor explicit preferred-mode override
     * from lv_linux_drm_set_preferred_mode if one matches (HelixScreen #766). */
    int selected_mode_idx = 0;
    if(drm_dev->preferred_hdisplay > 0 && drm_dev->preferred_vdisplay > 0) {
        int m;
        for(m = 0; m < conn->count_modes; m++) {
            if(conn->modes[m].hdisplay == drm_dev->preferred_hdisplay &&
               conn->modes[m].vdisplay == drm_dev->preferred_vdisplay) {
                selected_mode_idx = m;
                LV_LOG_INFO("drm: using preferred mode %dx%d (idx %d)",
                            conn->modes[m].hdisplay, conn->modes[m].vdisplay, m);
                break;
            }
        }
        if(selected_mode_idx == 0 &&
           (conn->modes[0].hdisplay != drm_dev->preferred_hdisplay ||
            conn->modes[0].vdisplay != drm_dev->preferred_vdisplay)) {
            LV_LOG_WARN("drm: requested mode %ux%u not found, falling back to %dx%d",
                        (unsigned)drm_dev->preferred_hdisplay,
                        (unsigned)drm_dev->preferred_vdisplay,
                        conn->modes[0].hdisplay, conn->modes[0].vdisplay);
        }
    }

    lv_memcpy(&drm_dev->mode, &conn->modes[selected_mode_idx], sizeof(drmModeModeInfo));

    if(drmModeCreatePropertyBlob(drm_dev->fd, &drm_dev->mode, sizeof(drm_dev->mode),
                                 &drm_dev->blob_id)) {
        LV_LOG_ERROR("error creating mode blob");
        goto free_res;
    }

    drm_dev->width = conn->modes[selected_mode_idx].hdisplay;
    drm_dev->height = conn->modes[selected_mode_idx].vdisplay;

    for(i = 0 ; i < res->count_encoders; i++) {
        enc = drmModeGetEncoder(drm_dev->fd, res->encoders[i]);
        if(!enc)
            continue;

        LV_LOG_TRACE("enc%d enc_id %d conn enc_id %d", i, enc->encoder_id, conn->encoder_id);

        if(enc->encoder_id == conn->encoder_id)
            break;

        drmModeFreeEncoder(enc);
        enc = NULL;
    }

    if(enc) {
        drm_dev->enc_id = enc->encoder_id;
        drm_dev->crtc_id = enc->crtc_id;
        LV_LOG_INFO("drm: encoder %d, crtc %d", drm_dev->enc_id, drm_dev->crtc_id);
        drmModeFreeEncoder(enc);
        enc = NULL;
    }
    else {
        /* Encoder hasn't been associated yet, look it up */
        bool found = false;
        for(i = 0; i < conn->count_encoders; i++) {
            int crtc, crtc_id = -1;

            enc = drmModeGetEncoder(drm_dev->fd, conn->encoders[i]);
            if(!enc)
                continue;

            for(crtc = 0 ; crtc < res->count_crtcs; crtc++) {
                uint32_t crtc_mask = 1 << crtc;

                crtc_id = res->crtcs[crtc];

                LV_LOG_TRACE("enc_id %d crtc%d id %d mask %x possible %x", enc->encoder_id, crtc, crtc_id, crtc_mask,
                             enc->possible_crtcs);

                if(enc->possible_crtcs & crtc_mask)
                    break;
            }

            if(crtc_id > 0) {
                drm_dev->enc_id = enc->encoder_id;
                LV_LOG_TRACE("enc_id: %d", drm_dev->enc_id);
                drm_dev->crtc_id = crtc_id;
                LV_LOG_TRACE("crtc_id: %d", drm_dev->crtc_id);
                drmModeFreeEncoder(enc);
                enc = NULL;
                found = true;
                break;
            }

            drmModeFreeEncoder(enc);
            enc = NULL;
        }

        if(!found) {
            LV_LOG_ERROR("suitable encoder not found");
            goto free_res;
        }
    }

    drm_dev->crtc_idx = UINT32_MAX;

    for(i = 0; i < res->count_crtcs; ++i) {
        if(drm_dev->crtc_id == res->crtcs[i]) {
            drm_dev->crtc_idx = i;
            break;
        }
    }

    if(drm_dev->crtc_idx == UINT32_MAX) {
        LV_LOG_ERROR("drm: CRTC not found");
        goto free_res;
    }

    LV_LOG_INFO("drm: crtc_idx: %d", drm_dev->crtc_idx);
    ret = 0;

free_res:
    if(enc) {
        drmModeFreeEncoder(enc);
        enc = NULL;
    }
    if(conn) {
        drmModeFreeConnector(conn);
        conn = NULL;
    }
    if(res) {
        drmModeFreeResources(res);
        res = NULL;
    }
    return ret;
}

static int drm_open(const char * path)
{
    int fd, flags;
    uint64_t has_dumb;
    int ret;

    fd = open(path, O_RDWR);
    if(fd < 0) {
        LV_LOG_ERROR("cannot open \"%s\"", path);
        return -1;
    }

    /* set FD_CLOEXEC flag */
    if((flags = fcntl(fd, F_GETFD)) < 0 ||
       fcntl(fd, F_SETFD, flags | FD_CLOEXEC) < 0) {
        LV_LOG_ERROR("fcntl FD_CLOEXEC failed");
        goto err;
    }

    /* check capability */
    ret = drmGetCap(fd, DRM_CAP_DUMB_BUFFER, &has_dumb);
    if(ret < 0 || has_dumb == 0) {
        LV_LOG_ERROR("drmGetCap DRM_CAP_DUMB_BUFFER failed or \"%s\" doesn't have dumb "
                     "buffer", path);
        goto err;
    }

    return fd;
err:
    close(fd);
    return -1;
}

static int drm_setup(drm_dev_t * drm_dev, const char * device_path, int64_t connector_id, unsigned int fourcc)
{
    int ret;

    drm_dev->fd = drm_open(device_path);
    if(drm_dev->fd < 0)
        return -1;

    ret = drmSetClientCap(drm_dev->fd, DRM_CLIENT_CAP_ATOMIC, 1);
    if(ret) {
        LV_LOG_ERROR("No atomic modesetting support: %s", strerror(errno));
        goto err;
    }

    /*
     * Acquire DRM master before any KMS modesetting. The kernel grants master
     * implicitly only to the FIRST client to open the device; during boot a
     * compositor (cage/weston/KlipperScreen) or console may still hold it for a
     * moment, in which case drmSetMaster() returns EACCES/EBUSY. Retry briefly
     * to win that hand-off race. This is NON-FATAL and never steals an active
     * master: if another process keeps it, drmSetMaster() keeps failing and we
     * proceed exactly as before (drmModeSetCrtc would fail with EACCES anyway) -
     * the only difference is a clear logged cause. On a working single-client
     * setup we are already the implicit master and the first call is a no-op.
     */
    {
        int master_attempts;
        int got_master = 0;
        for(master_attempts = 0; master_attempts < 10; master_attempts++) {
            if(drmSetMaster(drm_dev->fd) == 0) {
                got_master = 1;
                break;
            }
            if(errno != EACCES && errno != EBUSY)
                break; /* not a contention error - retrying won't help */
            if(master_attempts == 0)
                LV_LOG_WARN("DRM master held by another process; retrying...");
            usleep(100000); /* 100 ms; up to ~1 s total */
        }
        if(!got_master)
            LV_LOG_WARN("Could not acquire DRM master: %s - another process "
                        "(a compositor such as cage/weston, KlipperScreen, or a "
                        "console/getty) likely holds /dev/dri/card0; modesetting "
                        "will fail until it is stopped", strerror(errno));
    }

    ret = drm_find_connector(drm_dev, connector_id);
    if(ret) {
        LV_LOG_ERROR("available drm devices not found");
        goto err;
    }

    ret = find_plane(drm_dev, fourcc, &drm_dev->plane_id, drm_dev->crtc_id, drm_dev->crtc_idx);
    if(ret) {
        LV_LOG_ERROR("Cannot find plane");
        goto err;
    }

    drm_dev->plane = drmModeGetPlane(drm_dev->fd, drm_dev->plane_id);
    if(!drm_dev->plane) {
        LV_LOG_ERROR("Cannot get plane");
        goto err;
    }

    drm_dev->crtc = drmModeGetCrtc(drm_dev->fd, drm_dev->crtc_id);
    if(!drm_dev->crtc) {
        LV_LOG_ERROR("Cannot get crtc");
        goto err;
    }

    drm_dev->conn = drmModeGetConnector(drm_dev->fd, drm_dev->conn_id);
    if(!drm_dev->conn) {
        LV_LOG_ERROR("Cannot get connector");
        goto err;
    }

    ret = drm_get_plane_props(drm_dev);
    if(ret) {
        LV_LOG_ERROR("Cannot get plane props");
        goto err;
    }

    ret = drm_get_crtc_props(drm_dev);
    if(ret) {
        LV_LOG_ERROR("Cannot get crtc props");
        goto err;
    }

    ret = drm_get_conn_props(drm_dev);
    if(ret) {
        LV_LOG_ERROR("Cannot get connector props");
        goto err;
    }

    drm_dev->drm_event_ctx.version = DRM_EVENT_CONTEXT_VERSION;
    drm_dev->drm_event_ctx.page_flip_handler = page_flip_handler;
    drm_dev->fourcc = fourcc;

    LV_LOG_INFO("drm: Found plane_id: %u connector_id: %d crtc_id: %d",
                drm_dev->plane_id, drm_dev->conn_id, drm_dev->crtc_id);

    LV_LOG_INFO("drm: %dx%d (%dmm X% dmm) pixel format %c%c%c%c",
                drm_dev->width, drm_dev->height, drm_dev->mmWidth, drm_dev->mmHeight,
                (fourcc >> 0) & 0xff, (fourcc >> 8) & 0xff, (fourcc >> 16) & 0xff, (fourcc >> 24) & 0xff);


#if LV_USE_LINUX_DRM_GBM_BUFFERS

    /* Create GBM device and buffer */
    drm_dev->gbm_device = gbm_create_device(drm_dev->fd);

    if(drm_dev->gbm_device == NULL) {
        LV_LOG_ERROR("Failed to create GBM device");
        goto err;
    }

    LV_LOG_INFO("GBM device backend: %s", gbm_device_get_backend_name(drm_dev->gbm_device));
#endif

    return 0;

err:
#if LV_USE_LINUX_DRM_GBM_BUFFERS
    if(drm_dev->gbm_device) {
        gbm_device_destroy(drm_dev->gbm_device);
        drm_dev->gbm_device = NULL;
    }
#endif

    if(drm_dev->plane) {
        drmModeFreePlane(drm_dev->plane);
        drm_dev->plane = NULL;
    }
    if(drm_dev->crtc) {
        drmModeFreeCrtc(drm_dev->crtc);
        drm_dev->crtc = NULL;
    }
    if(drm_dev->conn) {
        drmModeFreeConnector(drm_dev->conn);
        drm_dev->conn = NULL;
    }
    if(drm_dev->fd >= 0) {
        close(drm_dev->fd);
        drm_dev->fd = -1;
    }
    return -1;
}

#if !LV_USE_LINUX_DRM_GBM_BUFFERS
static int drm_allocate_dumb(drm_dev_t * drm_dev, drm_buffer_t * buf)
{
    struct drm_mode_create_dumb creq;
    struct drm_mode_map_dumb mreq;
    uint32_t handles[4] = {0}, pitches[4] = {0}, offsets[4] = {0};
    int ret;

    /* create dumb buffer */
    lv_memzero(&creq, sizeof(creq));
    creq.width = drm_dev->width;
    creq.height = drm_dev->height;
    creq.bpp = LV_COLOR_DEPTH;
    ret = drmIoctl(drm_dev->fd, DRM_IOCTL_MODE_CREATE_DUMB, &creq);
    if(ret < 0) {
        LV_LOG_ERROR("DRM_IOCTL_MODE_CREATE_DUMB fail");
        return -1;
    }

    buf->handle = creq.handle;
    buf->pitch = creq.pitch;
    buf->size = creq.size;

    /* prepare buffer for memory mapping */
    lv_memzero(&mreq, sizeof(mreq));
    mreq.handle = creq.handle;
    ret = drmIoctl(drm_dev->fd, DRM_IOCTL_MODE_MAP_DUMB, &mreq);
    if(ret) {
        LV_LOG_ERROR("DRM_IOCTL_MODE_MAP_DUMB fail");
        return -1;
    }

    buf->offset = mreq.offset;
    LV_LOG_INFO("size %lu pitch %u offset 0x%" PRIx64, buf->size, buf->pitch, buf->offset);

    /* perform actual memory mapping. mreq.offset is a __u64 above 4 GiB; off_t must
     * be wide enough to carry it or the mapping fails with EINVAL. */
    _Static_assert(sizeof(off_t) == 8, "off_t must be 64-bit: DRM mmap offsets exceed 2^32");
    buf->map = mmap(0, creq.size, PROT_READ | PROT_WRITE, MAP_SHARED, drm_dev->fd, mreq.offset);
    if(buf->map == MAP_FAILED) {
        LV_LOG_ERROR("mmap fail");
        return -1;
    }

    /* clear the framebuffer to 0 (= full transparency in ARGB8888) */
    lv_memzero(buf->map, creq.size);

    /* create framebuffer object for the dumb-buffer */
    handles[0] = creq.handle;
    pitches[0] = creq.pitch;
    offsets[0] = 0;
    ret = drmModeAddFB2(drm_dev->fd, drm_dev->width, drm_dev->height, drm_dev->fourcc,
                        handles, pitches, offsets, &buf->fb_handle, 0);
    if(ret) {
        LV_LOG_ERROR("drmModeAddFB fail");
        return -1;
    }

    return 0;
}
#endif /*!LV_USE_LINUX_DRM_GBM_BUFFERS*/

#if LV_USE_LINUX_DRM_GBM_BUFFERS

static int create_gbm_buffer(drm_dev_t * drm_dev, drm_buffer_t * buf)
{
    struct gbm_bo * gbm_bo;
    int prime_fd;
    uint32_t handles[4] = {0}, pitches[4] = {0}, offsets[4] = {0};
    uint32_t n_planes;
    int res;

    /* gbm_bo_format does not define anything other than ARGB8888 or XRGB8888 */
    if(LV_COLOR_DEPTH != 32) {
        LV_LOG_ERROR("Unsupported color format");
        return -1;
    }

    /* Create a linear GBM buffer object - best practice when modifiers are not used */
    if(!(gbm_bo = gbm_bo_create(drm_dev->gbm_device,
                                drm_dev->width, drm_dev->height, GBM_BO_FORMAT_XRGB8888,
                                GBM_BO_USE_SCANOUT | GBM_BO_USE_LINEAR))) {

        LV_LOG_ERROR("Unable to create gbm buffer object");
        return -1;
    }

    /* Currently only, one plane per dma-buf/prime fd is supported - but some GPUs feature
     * several planes (multiple fds or sometimes a single fd for multiple planes).
     * current implementation is kept simple for now */

    n_planes = gbm_bo_get_plane_count(gbm_bo);

    if(n_planes != 1) {
        LV_LOG_ERROR("The current implementation only supports a single plane per fd");
        return -1;
    }

    uint32_t h = gbm_bo_get_height(gbm_bo);
    pitches[0] = buf->pitch = gbm_bo_get_stride_for_plane(gbm_bo, 0);
    offsets[0] = buf->offset = gbm_bo_get_offset(gbm_bo, 0);
    buf->size = h * buf->pitch;

    LV_LOG_INFO("Created GBM BO of size: %lu pitch: %u offset: %" PRIu64,
                buf->size, buf->pitch, buf->offset);

    prime_fd = gbm_bo_get_fd_for_plane(gbm_bo, 0);

    if(prime_fd < 0) {
        LV_LOG_ERROR("Failed to get prime fd for plane 0");
        return -1;

    }

    buf->map = mmap(NULL, buf->size, PROT_READ | PROT_WRITE, MAP_SHARED, prime_fd, 0);

    if(buf->map == MAP_FAILED) {
        LV_LOG_ERROR("Failed to mmap dma-buf fd.");
        return -1;
    }

    /* Used to perform DMA_BUF_SYNC ioctl calls during the rendering cycle */
    buf->handle = prime_fd;

    /* Convert prime fd to a libdrm buffer handle */
    drmPrimeFDToHandle(drm_dev->fd, buf->handle, &handles[0]);

    /* create libdrm framebuffer */
    res = drmModeAddFB2(drm_dev->fd, drm_dev->width, drm_dev->height, drm_dev->fourcc,
                        handles, pitches, offsets, &buf->fb_handle, 0);

    if(res) {
        LV_LOG_ERROR("drmModeAddFB2 failed");
        return -1;
    }

    if(drmCloseBufferHandle(drm_dev->fd, handles[0]) != 0) {
        LV_LOG_ERROR("drmCloseBufferHandle failed");
        return -1;
    }

    return 0;

}

#endif /* LV_USE_LINUX_DRM_GBM_BUFFERS */

static int drm_setup_buffers(drm_dev_t * drm_dev)
{
    int ret;

#if LV_USE_LINUX_DRM_GBM_BUFFERS
    ret = create_gbm_buffer(drm_dev, &drm_dev->drm_bufs[0]);
    if(ret < 0) {
        return ret;
    }

    ret = create_gbm_buffer(drm_dev, &drm_dev->drm_bufs[1]);
    if(ret < 0) {
        return ret;
    }

#else
    /* Use dumb buffers */
    ret = drm_allocate_dumb(drm_dev, &drm_dev->drm_bufs[0]);
    if(ret)
        return ret;

    ret = drm_allocate_dumb(drm_dev, &drm_dev->drm_bufs[1]);
    if(ret)
        return ret;

#endif

    return 0;
}

static void drm_flush_wait(lv_display_t * disp)
{
    drm_dev_t * drm_dev = lv_display_get_driver_data(disp);

    struct pollfd pfd;
    pfd.fd = drm_dev->fd;
    pfd.events = POLLIN;

    while(drm_dev->req) {
        int ret;
        do {
            ret = poll(&pfd, 1, 2000);
        } while(ret == -1 && errno == EINTR);

        if(ret > 0) {
            drmHandleEvent(drm_dev->fd, &drm_dev->drm_event_ctx);
        }
        else if(ret == 0) {
            LV_LOG_WARN("drm_flush_wait: timed out waiting for page flip event, freeing request");
            drmModeAtomicFree(drm_dev->req);
            drm_dev->req = NULL;
            return;
        }
        else {
            LV_LOG_ERROR("poll failed: %s", strerror(errno));
            return;
        }
    }
}

static void drm_flush(lv_display_t * disp, const lv_area_t * area, uint8_t * px_map)
{
    if(!lv_display_flush_is_last(disp)) return;

    LV_UNUSED(area);
    LV_UNUSED(px_map);
    drm_dev_t * drm_dev = lv_display_get_driver_data(disp);

    if(drm_dev == NULL || drm_dev->act_buf == NULL) {
        LV_LOG_ERROR("drm_flush: NULL driver data or active buffer, skipping frame");
        lv_display_flush_ready(disp);
        return;
    }

    /* Software 180° rotation via shadow buffer.
     * Copy pixels in reverse order to a temp buffer, then bulk-copy back.
     * The single memcpy back prevents tearing (in-place swap is visible
     * to the DRM scanout as it progresses through the buffer).
     * When rotation is inactive, free the shadow buffer to save ~1.5 MB. */
    static uint32_t * rot_buf = NULL;
    static uint32_t rot_buf_size = 0;
    if(lv_display_get_rotation(disp) == LV_DISPLAY_ROTATION_180) {
        uint32_t total = drm_dev->width * drm_dev->height;
        uint32_t needed = total * sizeof(uint32_t);
        if(rot_buf == NULL || rot_buf_size < needed) {
            lv_free(rot_buf);
            rot_buf = lv_malloc(needed);
            rot_buf_size = needed;
        }
        if(rot_buf) {
            uint32_t * src = (uint32_t *)drm_dev->act_buf->map;
            uint32_t i;
            for(i = 0; i < total; i++) {
                rot_buf[i] = src[total - 1 - i];
            }
            lv_memcpy(drm_dev->act_buf->map, rot_buf, needed);
        }
    }
    else if(rot_buf != NULL) {
        lv_free(rot_buf);
        rot_buf = NULL;
        rot_buf_size = 0;
    }

    if(drm_dmabuf_set_plane(drm_dev, drm_dev->act_buf)) {
        LV_LOG_ERROR("Flush fail");
        return;
    }

    drm_dev->act_buf = NULL;

}

static void drm_del_event_cb(lv_event_t * e)
{
    if(LV_EVENT_DELETE != lv_event_get_code(e))
        return;

    lv_display_t * disp = lv_event_get_current_target(e);
    drm_dev_t * drm_dev = lv_display_get_driver_data(disp);
    if(!drm_dev) return;

    /* Restore original CRTC if saved */
    if(drm_dev->fd >= 0 && drm_dev->saved_crtc) {
        drmModeCrtc * s_crtc = drm_dev->saved_crtc;
        drmModeSetCrtc(drm_dev->fd, s_crtc->crtc_id, s_crtc->buffer_id, 0, 0,
                       NULL, 0, &s_crtc->mode);
        drmModeFreeCrtc(s_crtc);
        drm_dev->saved_crtc = NULL;
    }

    /* Prevent further flushes & free any pending atomic request */
    lv_display_set_flush_cb(disp, NULL);
    if(drm_dev->req) {
        drmModeAtomicFree(drm_dev->req);
        drm_dev->req = NULL;
    }

#if LV_USE_LINUX_DRM_GBM_BUFFERS
    for(int i = 0; i < BUFFER_CNT; ++i) {
        drm_buffer_t * b = &drm_dev->drm_bufs[i];

        if(b->fb_handle) {
            drmModeRmFB(drm_dev->fd, b->fb_handle);
            b->fb_handle = 0;
        }

        if(MAP_FAILED != b->map) {
            munmap(b->map, b->size);
            b->map = MAP_FAILED;
        }

        if((int)b->handle >= 0) {
            close((int)b->handle);
            b->handle = 0;
        }
    }

    if(drm_dev->gbm_device) {
        gbm_device_destroy(drm_dev->gbm_device);
        drm_dev->gbm_device = NULL;
    }

#else /* dumb buffers */
    for(int i = 0; i < BUFFER_CNT; ++i) {
        drm_buffer_t * b = &drm_dev->drm_bufs[i];

        if(b->fb_handle) {
            drmModeRmFB(drm_dev->fd, b->fb_handle);
            b->fb_handle = 0;
        }
        if(MAP_FAILED != b->map) {
            munmap(b->map, b->size);
            b->map = MAP_FAILED;
        }
        if(b->handle) {
            struct drm_mode_destroy_dumb d = { .handle = b->handle };
            /* Dumb buffers should be destroyed if they are closed, but might as well use the DRM_IOCTL_MODE_DESTROY_DUMB */
            drmIoctl(drm_dev->fd, DRM_IOCTL_MODE_DESTROY_DUMB, &d);
            b->handle = 0;
        }
    }
#endif

    /* Free DRM properties */
    for(uint32_t i = 0; i < drm_dev->count_plane_props; ++i) {
        if(drm_dev->plane_props[i]) {
            drmModeFreeProperty(drm_dev->plane_props[i]);
            drm_dev->plane_props[i] = NULL;
        }
    }
    drm_dev->count_plane_props = 0;

    for(uint32_t i = 0; i < drm_dev->count_crtc_props; ++i) {
        if(drm_dev->crtc_props[i]) {
            drmModeFreeProperty(drm_dev->crtc_props[i]);
            drm_dev->crtc_props[i] = NULL;
        }
    }
    drm_dev->count_crtc_props = 0;

    for(uint32_t i = 0; i < drm_dev->count_conn_props; ++i) {
        if(drm_dev->conn_props[i]) {
            drmModeFreeProperty(drm_dev->conn_props[i]);
            drm_dev->conn_props[i] = NULL;
        }
    }
    drm_dev->count_conn_props = 0;

    if(drm_dev->blob_id) {
        drmModeDestroyPropertyBlob(drm_dev->fd, drm_dev->blob_id);
        drm_dev->blob_id = 0;
    }

    if(drm_dev->conn) {
        drmModeFreeConnector(drm_dev->conn);
        drm_dev->conn = NULL;
    }

    if(drm_dev->crtc) {
        drmModeFreeCrtc(drm_dev->crtc);
        drm_dev->crtc = NULL;
    }

    if(drm_dev->plane) {
        drmModeFreePlane(drm_dev->plane);
        drm_dev->plane = NULL;
    }

    if(drm_dev->fd >= 0) {
        close(drm_dev->fd);
        drm_dev->fd = -1;
    }

    lv_display_set_driver_data(disp, NULL);
    lv_free(drm_dev);
}

static uint32_t tick_get_cb(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    uint64_t time_ms = t.tv_sec * 1000 + (t.tv_nsec / 1000000);
    return time_ms;
}

#endif /*LV_USE_LINUX_DRM && !LV_LINUX_DRM_USE_EGL*/
