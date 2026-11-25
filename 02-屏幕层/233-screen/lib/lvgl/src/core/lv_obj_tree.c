/**
 * @file lv_obj_tree.c
 *
 */

/*********************
 *      INCLUDES
 *********************/
#include "lv_obj_private.h"
#include "../lvgl_public.h"
#include "lv_obj_class_private.h"
#include "../indev/lv_indev_private.h"
#include "../display/lv_display_private.h"
#include "../misc/lv_anim_private.h"
#include "../core/lv_global.h"
#include "../misc/lv_helix_thread_check.h"

/* Telemetry hook provided by helix_lvgl_anomaly.cpp — reports LVGL anomalies
 * (double-schedule, UAF near-miss) via TelemetryManager::record_error. */
extern void helix_lvgl_anomaly(const char * code, const char * context);

/* Weak-extern hooks into crash_handler.cpp's breadcrumb ring. Record the
 * root of a destruction subtree (sync or async) so #840/L081-class SIGBUS
 * crashes inside lv_event_mark_deleted carry a `sync_d`/`async_d <class>
 * <ptr>` crumb naming the doomed root. Weak so LVGL demos/tests link
 * without the helix-side symbols. */
__attribute__((weak))
extern void helix_crash_note_async_del(const void * obj, const char * class_name);
__attribute__((weak))
extern void helix_crash_note_sync_del(const void * obj, const char * class_name);

/* Set across the inner lv_obj_delete() inside lv_obj_delete_async_cb so the
 * sync crumb is suppressed on that path (async_d already named the root).
 * LVGL is single-threaded — a plain int suffices for visibility. The
 * __cleanup__-tagged guard below resets it on every scope exit including
 * C++ exception unwind through this C frame; mk/rules.mk builds LVGL with
 * -fexceptions and lvgl_event_pop_unwind_safe.patch relies on it too.
 * Without the cleanup, a throwing event_cb inside the inner lv_obj_delete()
 * would leave the flag stuck at 1, silently suppressing every subsequent
 * sync_d crumb. */
static int helix_in_async_del = 0;
static void helix_async_del_guard_pop(int * unused)
{
    (void)unused;
    helix_in_async_del = 0;
}

/*********************
 *      DEFINES
 *********************/
#define MY_CLASS (&lv_obj_class)
#define disp_ll_p &(LV_GLOBAL_DEFAULT()->disp_ll)

#define OBJ_DUMP_STRING_LEN 128
#define LV_OBJ_NAME_MAX_LEN 128

/**********************
 *      TYPEDEFS
 **********************/

/**********************
 *  STATIC PROTOTYPES
 **********************/
static void lv_obj_delete_async_cb(void * obj);
static void obj_delete_core(lv_obj_t * obj);
static lv_obj_tree_walk_res_t walk_core(lv_obj_t * obj, lv_obj_tree_walk_cb_t cb, void * user_data);
static void dump_tree_core(lv_obj_t * obj, int32_t depth);
static lv_obj_t * lv_obj_get_first_not_deleting_child(lv_obj_t * obj);
#if LV_USE_OBJ_NAME
    static lv_obj_t * find_by_name_direct(const lv_obj_t * parent, const char * name, size_t len);
#endif /*LV_USE_OBJ_NAME*/

/**********************
 *  STATIC VARIABLES
 **********************/

/**********************
 *      MACROS
 **********************/

/**********************
 *   GLOBAL FUNCTIONS
 **********************/

void lv_obj_delete(lv_obj_t * obj)
{
    LV_HELIX_ASSERT_LVGL_THREAD();
    if(obj == NULL) return;
    if(obj->is_deleting) return;

    /* Crumb the sync-delete root (suppressed when re-entered from async_cb). */
    if(!helix_in_async_del && helix_crash_note_sync_del) {
        const char * cls_name = (obj->class_p && obj->class_p->name) ? obj->class_p->name : "?";
        helix_crash_note_sync_del(obj, cls_name);
    }

    LV_LOG_TRACE("begin (delete %p)", (void *)obj);
    lv_obj_invalidate(obj);

    lv_obj_t * par = lv_obj_get_parent(obj);

    lv_display_t * disp = NULL;
    bool act_screen_del = false;
    if(par == NULL) {
        disp = lv_obj_get_display(obj);
        if(!disp) return;   /*Shouldn't happen*/
        if(disp->act_scr == obj) act_screen_del = true;
    }

    obj_delete_core(obj);

    /*Call the ancestor's event handler to the parent to notify it about the child delete*/
    if(par && !par->is_deleting) {
        lv_obj_scrollbar_invalidate(par);
        lv_obj_send_event(par, LV_EVENT_CHILD_CHANGED, NULL);
        lv_obj_send_event(par, LV_EVENT_CHILD_DELETED, NULL);
    }

    /*Handle if the active screen was deleted*/
    if(act_screen_del) {
        LV_LOG_WARN("the active screen was deleted");
        disp->act_scr = NULL;
    }

    LV_ASSERT_MEM_INTEGRITY();
    LV_LOG_TRACE("finished (delete %p)", (void *)obj);
}

void lv_obj_clean(lv_obj_t * obj)
{
    LV_HELIX_ASSERT_LVGL_THREAD();
    LV_LOG_TRACE("begin (clean %p)", (void *)obj);
    LV_CHECK_OBJ(obj, MY_CLASS, return);

    lv_obj_invalidate(obj);

    uint32_t cnt = lv_obj_get_child_count(obj);
    lv_obj_t * child = lv_obj_get_first_not_deleting_child(obj);
    while(child) {
        obj_delete_core(child);
        child = lv_obj_get_first_not_deleting_child(obj);
    }
    /*Just to remove scroll animations if any*/
    lv_obj_scroll_to(obj, 0, 0, LV_ANIM_OFF);
    if(obj->spec_attr) {
        obj->spec_attr->scroll.x = 0;
        obj->spec_attr->scroll.y = 0;
    }

    if(lv_obj_get_child_count(obj) < cnt) {
        lv_obj_send_event(obj, LV_EVENT_CHILD_CHANGED, NULL);
        lv_obj_send_event(obj, LV_EVENT_CHILD_DELETED, NULL);
    }

    LV_ASSERT_MEM_INTEGRITY();

    LV_LOG_TRACE("finished (clean %p)", (void *)obj);
}

void lv_obj_delete_delayed(lv_obj_t * obj, uint32_t delay_ms)
{
    if(obj == NULL) return;

    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, obj);
    lv_anim_set_exec_cb(&a, NULL);
    lv_anim_set_duration(&a, 1);
    lv_anim_set_delay(&a, delay_ms);
    lv_anim_set_completed_cb(&a, lv_obj_delete_anim_completed_cb);
    lv_anim_start(&a);
}

void lv_obj_delete_anim_completed_cb(lv_anim_t * a)
{
    LV_CHECK_ARG(a != NULL, return);

    lv_obj_delete(a->var);
}

void lv_obj_delete_async(lv_obj_t * obj)
{
    if(obj == NULL) return;

    /* A second schedule for one object is a caller bug worth naming: report it and keep
     * one pending delete. A match means obj is live, so reading it here is safe:
     * obj_delete_core() cancels an object's pending async deletes before freeing it. */
    if(lv_async_call_cancel(lv_obj_delete_async_cb, obj) == LV_RESULT_OK) {
        const char * cls_name = (obj->class_p && obj->class_p->name) ? obj->class_p->name : "?";
        LV_LOG_WARN("lv_obj_delete_async: duplicate schedule for %p (class=%s)",
                    (void *)obj, cls_name);
        char ctx[96];
        lv_snprintf(ctx, sizeof(ctx), "class=%s", cls_name);
        helix_lvgl_anomaly("obj_delete_async_dup", ctx);
    }
    lv_async_call(lv_obj_delete_async_cb, obj);
}

void lv_obj_set_parent(lv_obj_t * obj, lv_obj_t * parent)
{
    LV_CHECK_OBJ(obj, MY_CLASS, return);
    LV_CHECK_OBJ(parent, MY_CLASS, return);

    if(obj->parent == NULL) {
        LV_LOG_WARN("Can't set the parent of a screen");
        return;
    }

    if(parent == obj->parent) {
        return;
    }

    if(!lv_obj_allocate_spec_attr(parent)) {
        return;
    }

    /*Add the object to the new parents list first*/
    if(lv_obj_add_child(parent, obj) != LV_RESULT_OK) {
        LV_LOG_WARN("Failed to attach child to parent");
        return;
    }

    /*Remove the object from the old parent's child list
     * This should never fail, thus we do only do it after adding it to the new parent's list*/
    lv_obj_remove_child(obj->parent, obj);

    /*Notify the original parent because one of its children is lost*/
    lv_obj_scrollbar_invalidate(obj->parent);
    lv_obj_send_event(obj->parent, LV_EVENT_CHILD_CHANGED, obj);
    lv_obj_send_event(obj->parent, LV_EVENT_CHILD_DELETED, NULL);
    obj->parent = parent;

    /*Notify the new parent about the child*/
    lv_obj_send_event(parent, LV_EVENT_CHILD_CHANGED, obj);
    lv_obj_send_event(parent, LV_EVENT_CHILD_CREATED, NULL);

    lv_obj_mark_layout_as_dirty(obj);

    lv_obj_invalidate(obj);
}

void lv_obj_move_to_index(lv_obj_t * obj, int32_t index)
{
    LV_CHECK_OBJ(obj, MY_CLASS, return);

    /* Check parent validity */
    lv_obj_t * parent = lv_obj_get_parent(obj);
    if(!parent) {
        LV_LOG_WARN("parent is NULL");
        return;
    }

    const uint32_t parent_child_count = lv_obj_get_child_count(parent);
    /* old_index only can be 0 or greater, this point cannot be reached if the parent is not null */
    const int32_t old_index = lv_obj_get_index(obj);
    LV_ASSERT(0 <= old_index);

    if(index < 0) {
        index += parent_child_count;
    }

    /* Index was negative and the absolute value is greater than parent child count */
    if((index < 0)
       /* Index is same or bigger than parent child count */
       || (index >= (int32_t) parent_child_count)
       /* If both previous and new index are the same */
       || (index == old_index)) {

        return;
    }

    int32_t i = old_index;
    if(index < old_index) {
        while(i > index)  {
            parent->spec_attr->children[i] = parent->spec_attr->children[i - 1];
            i--;
        }
    }
    else {
        while(i < index) {
            parent->spec_attr->children[i] = parent->spec_attr->children[i + 1];
            i++;
        }
    }

    parent->spec_attr->children[index] = obj;
    lv_obj_send_event(parent, LV_EVENT_CHILD_CHANGED, NULL);
    lv_obj_invalidate(parent);
}

void lv_obj_swap(lv_obj_t * obj1, lv_obj_t * obj2)
{
    LV_CHECK_OBJ(obj1, MY_CLASS, return);
    LV_CHECK_OBJ(obj2, MY_CLASS, return);

    lv_obj_t * parent = lv_obj_get_parent(obj1);
    lv_obj_t * parent2 = lv_obj_get_parent(obj2);

    uint_fast32_t index1 = lv_obj_get_index(obj1);
    uint_fast32_t index2 = lv_obj_get_index(obj2);

    lv_obj_send_event(parent2, LV_EVENT_CHILD_DELETED, obj2);
    lv_obj_send_event(parent, LV_EVENT_CHILD_DELETED, obj1);

    parent->spec_attr->children[index1] = obj2;
    obj2->parent = parent;

    parent2->spec_attr->children[index2] = obj1;
    obj1->parent = parent2;

    lv_obj_send_event(parent, LV_EVENT_CHILD_CHANGED, obj2);
    lv_obj_send_event(parent, LV_EVENT_CHILD_CREATED, obj2);
    lv_obj_send_event(parent2, LV_EVENT_CHILD_CHANGED, obj1);
    lv_obj_send_event(parent2, LV_EVENT_CHILD_CREATED, obj1);

    lv_obj_invalidate(parent);

    if(parent != parent2) {
        lv_obj_invalidate(parent2);
    }
    lv_group_swap_obj(obj1, obj2);
}

lv_obj_t * lv_obj_get_screen(const lv_obj_t * obj)
{
    LV_CHECK_OBJ(obj, MY_CLASS, return NULL);

    const lv_obj_t * par = obj;
    const lv_obj_t * act_par;
    /* Cap iterations so a cyclic parent chain (UAF / double-reparent / memory
     * reuse after lv_obj_delete) becomes a recoverable error instead of an
     * infinite spin that hangs the UI thread: lv_obj_set_parent() on a freed
     * object whose memory was reused can produce such a cycle.
     * No real UI has a parent chain deeper than 128. */
    uint32_t depth = 0;

    do {
        act_par = par;
        par = lv_obj_get_parent(act_par);
        if(++depth > 128) {
            LV_LOG_ERROR("lv_obj_get_screen: parent chain cycle detected (obj=%p depth>128) — "
                         "breaking walk and returning last valid ancestor",
                         (const void *)obj);
            helix_lvgl_anomaly("obj_get_screen_cycle", "depth>128");
            break;
        }
    } while(par != NULL);

    return (lv_obj_t *)act_par;
}

lv_display_t * lv_obj_get_display(const lv_obj_t * obj)
{
    LV_CHECK_OBJ(obj, MY_CLASS, return NULL);

    const lv_obj_t * scr;

    if(obj->parent == NULL) scr = obj;  /*`obj` is a screen*/
    else scr = lv_obj_get_screen(obj);  /*get the screen of `obj`*/

    lv_display_t * d;
    lv_ll_t * disp_head = disp_ll_p;
    LV_LL_READ(disp_head, d) {
        uint32_t i;
        for(i = 0; i < d->screen_cnt; i++) {
            if(d->screens[i] == scr) return d;
        }
    }

    LV_LOG_WARN("No screen found");
    return NULL;
}

lv_obj_t * lv_obj_get_parent(const lv_obj_t * obj)
{
    LV_CHECK_OBJ(obj, MY_CLASS, return NULL);

    return obj->parent;
}

lv_obj_t * lv_obj_get_child(const lv_obj_t * obj, int32_t idx)
{
    LV_CHECK_OBJ(obj, MY_CLASS, return NULL);

    if(obj->spec_attr == NULL) return NULL;

    if(idx < 0) {
        idx = obj->spec_attr->child_cnt + idx;
        if(idx < 0) return NULL;
    }

    uint32_t idu = (uint32_t) idx;

    if(idu < obj->spec_attr->child_cnt) return obj->spec_attr->children[idu];
    else return NULL;
}

lv_obj_t * lv_obj_get_child_by_type(const lv_obj_t * obj, int32_t idx, const lv_obj_class_t * class_p)
{
    LV_CHECK_OBJ(obj, MY_CLASS, return NULL);
    LV_CHECK_ARG(class_p != NULL, return NULL);

    if(obj->spec_attr == NULL) return NULL;

    int32_t i;
    int32_t cnt = (int32_t)obj->spec_attr->child_cnt;
    if(idx >= 0) {
        for(i = 0; i < cnt; i++) {
            if(obj->spec_attr->children[i]->class_p == class_p) {
                if(idx == 0) return obj->spec_attr->children[i];
                else idx--;
            }
        }
    }
    else {
        idx++;   /*-1 means the first child*/
        for(i = cnt - 1; i >= 0; i--) {
            if(obj->spec_attr->children[i]->class_p == class_p) {
                if(idx == 0) return obj->spec_attr->children[i];
                else idx++;
            }
        }
    }
    return NULL;
}

lv_obj_t * lv_obj_get_sibling(const lv_obj_t * obj, int32_t idx)
{
    LV_CHECK_OBJ(obj, MY_CLASS, return NULL);
    LV_CHECK_ARG(obj->parent != NULL, return NULL);

    lv_obj_t * parent = lv_obj_get_parent(obj);
    int32_t sibling_idx = (int32_t)lv_obj_get_index(obj) + idx;
    if(sibling_idx < 0) return NULL;

    return lv_obj_get_child(parent, sibling_idx);
}

lv_obj_t * lv_obj_get_sibling_by_type(const lv_obj_t * obj, int32_t idx, const lv_obj_class_t * class_p)
{
    LV_CHECK_OBJ(obj, MY_CLASS, return NULL);
    LV_CHECK_ARG(obj->parent != NULL, return NULL);
    LV_CHECK_ARG(class_p != NULL, return NULL);

    lv_obj_t * parent = lv_obj_get_parent(obj);
    int32_t sibling_idx = (int32_t)lv_obj_get_index_by_type(obj, class_p) + idx;
    if(sibling_idx < 0) return NULL;

    return lv_obj_get_child_by_type(parent, sibling_idx, class_p);
}

uint32_t lv_obj_get_child_count(const lv_obj_t * obj)
{
    LV_CHECK_OBJ(obj, MY_CLASS, return 0);
    if(obj->spec_attr == NULL) return 0;
    return obj->spec_attr->child_cnt;
}

uint32_t lv_obj_get_child_count_by_type(const lv_obj_t * obj, const lv_obj_class_t * class_p)
{
    LV_CHECK_OBJ(obj, MY_CLASS, return 0);
    LV_CHECK_ARG(class_p != NULL, return 0);

    if(obj->spec_attr == NULL) return 0;

    uint32_t i;
    uint32_t cnt = 0;
    for(i = 0; i < obj->spec_attr->child_cnt; i++) {
        if(obj->spec_attr->children[i]->class_p == class_p) cnt++;
    }
    return cnt;
}

#if LV_USE_OBJ_NAME

void lv_obj_set_name(lv_obj_t * obj, const char * name)
{
    LV_CHECK_OBJ(obj, MY_CLASS, return);

    if(!lv_obj_allocate_spec_attr(obj)) {
        return;
    }

    if(!obj->spec_attr->name_static && obj->spec_attr->name) lv_free((void *)obj->spec_attr->name);

    if(name == NULL) {
        obj->spec_attr->name = NULL;
        obj->spec_attr->name_static = 1;
    }
    else {
        obj->spec_attr->name = lv_strdup(name);
        obj->spec_attr->name_static = 0;
    }
}

void lv_obj_set_name_static(lv_obj_t * obj, const char * name)
{
    LV_CHECK_OBJ(obj, MY_CLASS, return);
    LV_CHECK_ARG(name != NULL, return);

    if(!lv_obj_allocate_spec_attr(obj)) {
        return;
    }
    if(!obj->spec_attr->name_static && obj->spec_attr->name) lv_free((void *)obj->spec_attr->name);

    obj->spec_attr->name = name;
    obj->spec_attr->name_static = 1;
}

const char * lv_obj_get_name(const lv_obj_t * obj)
{
    LV_CHECK_OBJ(obj, MY_CLASS, return NULL);

    if(obj->spec_attr == NULL) return NULL;
    else return obj->spec_attr->name;
}

void lv_obj_get_name_resolved(const lv_obj_t * obj, char buf[], size_t buf_size)
{
    LV_CHECK_OBJ(obj, MY_CLASS, return);
    LV_CHECK_ARG(buf != NULL && buf_size > 0, return);

    const char * name = lv_obj_get_name(obj);
    /*Use a default name which auto-indexing*/
    char name_buf[LV_OBJ_NAME_MAX_LEN];
    if(name == NULL) {
        lv_snprintf(name_buf, sizeof(name_buf), "%s_#", obj->class_p->name);
        name = name_buf;
    }

    size_t name_len = lv_strlen(name);
    lv_obj_t * parent = lv_obj_get_parent(obj);

    /*If the last character is # automatically index the children with the same name start*/
    if(parent && name_len > 0 && name[name_len - 1] == '#') {
        uint32_t child_cnt = lv_obj_get_child_count(parent);
        uint32_t cnt = 0;
        uint32_t i;
        for(i = 0; i < child_cnt; i++) {
            lv_obj_t * child = lv_obj_get_child(parent, i);
            /*All siblings older siblings are checked, craft the name of this widget*/
            if(child == obj) {
                char num_buf[8];
                size_t num_len;
                num_len = lv_snprintf(num_buf, sizeof(num_buf), "%d", cnt);
                /*Is there enough space for the name and the index?*/
                if(buf_size > name_len + num_len) {
                    /*E.g. buf = "some_name_", so trim the # from the end*/
                    lv_strncpy(buf, name, name_len - 1);
                    lv_strcpy(&buf[name_len - 1], num_buf);
                }
                else {
                    /*Use the name as it is as a fallback*/
                    lv_strlcpy(buf, obj->spec_attr->name, buf_size);
                }
                break;
            }
            /*Check the older siblings. IF they start with the same name count them*/
            else {
                const char * child_name = lv_obj_get_name(child);
                if(child_name == NULL) {
                    /*If the name we are looking for start with the child's class name
                     *increment the index. E.g. <class_name>_#*/
                    size_t class_name_len = lv_strlen(child->class_p->name);
                    if(name_len > 3 && class_name_len == name_len - 2 &&
                       lv_strncmp(child->class_p->name, name, class_name_len) == 0) {
                        cnt++;
                    }
                }
                /*The name is set, check if it's e.g. <some_name>#*/
                else {
                    if(lv_strcmp(child->spec_attr->name, name) == 0) {
                        cnt++;
                    }
                }
            }
        }
    }
    else {
        /*Just use the set name*/
        lv_strlcpy(buf, obj->spec_attr->name, buf_size);
    }
}

lv_obj_t * lv_obj_get_child_by_name(const lv_obj_t * parent, const char * path)
{
    LV_CHECK_OBJ(parent, MY_CLASS, return NULL);
    LV_CHECK_ARG(path != NULL, return NULL);
    LV_CHECK_ARG(parent->spec_attr != NULL, return NULL);

    while(*path) {
        const char * segment = path;
        uint32_t len = 0;

        /* Calculate the length of the current segment */
        while(path[len] && path[len] != '/')
            len++;

        /* Look for a child whose resolved name exactly matches the segment */
        lv_obj_t * child = find_by_name_direct(parent, segment, len);
        if(!child) return NULL; /*Segment not found*/

        /* Advance to the next segment */
        path += len;
        if(*path == '/') path++; /* Skip the '/' */

        /* If there is no further segment, we've found the target child */
        if(*path == '\0') return child;

        parent = child;
    }

    return NULL;

}

lv_obj_t * lv_obj_find_by_name(const lv_obj_t * parent, const char * name)
{
    LV_CHECK_ARG(name != NULL, return NULL);

    if(parent == NULL) parent = lv_display_get_screen_active(lv_display_get_default());
    if(parent == NULL) return NULL;

    lv_obj_t * child = find_by_name_direct(parent, name, UINT16_MAX);
    if(child) return child;

    /*Search children recursively*/
    uint32_t child_cnt = lv_obj_get_child_count(parent);
    uint32_t i;
    for(i = 0; i < child_cnt; i++) {
        child = parent->spec_attr->children[i];
        lv_obj_t * found = lv_obj_find_by_name(child, name);
        if(found != NULL) return found;
    }

    return NULL;
}

#endif /*LV_USE_OBJ_NAME*/

int32_t lv_obj_get_index(const lv_obj_t * obj)
{
    LV_CHECK_OBJ(obj, MY_CLASS, return -1);

    lv_obj_t * parent = lv_obj_get_parent(obj);
    if(parent == NULL) return -1;

    uint32_t i = 0;
    for(i = 0; i < parent->spec_attr->child_cnt; i++) {
        if(parent->spec_attr->children[i] == obj) return (int32_t)i;
    }

    /*Shouldn't reach this point*/
    LV_ASSERT(0);
    return -1;
}

int32_t lv_obj_get_index_by_type(const lv_obj_t * obj, const lv_obj_class_t * class_p)
{
    LV_CHECK_OBJ(obj, MY_CLASS, return -1);
    LV_CHECK_ARG(class_p != NULL, return -1);

    lv_obj_t * parent = lv_obj_get_parent(obj);
    if(parent == NULL) return 0xFFFFFFFF;

    uint32_t i = 0;
    uint32_t idx = 0;
    for(i = 0; i < parent->spec_attr->child_cnt; i++) {
        lv_obj_t * child = parent->spec_attr->children[i];
        if(child->class_p == class_p) {
            if(child == obj) return idx;
            idx++;
        }
    }

    /*Can happen if there was no children with the given type*/
    return -1;
}

void lv_obj_tree_walk(lv_obj_t * start_obj, lv_obj_tree_walk_cb_t cb, void * user_data)
{
    LV_CHECK_ARG(cb != NULL, return);

    walk_core(start_obj, cb, user_data);
}

void lv_obj_dump_tree(lv_obj_t * start_obj)
{
    if(start_obj == NULL) {
        lv_display_t * disp = lv_display_get_next(NULL);
        while(disp) {
            uint32_t i;
            for(i = 0; i < disp->screen_cnt; i++) {
                dump_tree_core(disp->screens[i], 0);
            }
            disp = lv_display_get_next(disp);
        }
    }
    else {
        dump_tree_core(start_obj, 0);
    }
}

/**********************
 *   STATIC FUNCTIONS
 **********************/

static void lv_obj_delete_async_cb(void * obj)
{
    /* Crumb the async-delete root before recursing into children. */
    if(helix_crash_note_async_del) {
        lv_obj_t * lvobj = (lv_obj_t *)obj;
        const char * cls_name = (lvobj->class_p && lvobj->class_p->name) ? lvobj->class_p->name : "?";
        helix_crash_note_async_del(obj, cls_name);
    }

    /* __cleanup__ pops the suppression flag on normal return AND on C++
     * exception unwind — see helix_async_del_guard_pop above. */
    int async_del_guard __attribute__((__cleanup__(helix_async_del_guard_pop))) = 0;
    (void)async_del_guard;
    helix_in_async_del = 1;
    lv_obj_delete(obj);
}

static void obj_indev_reset(lv_indev_t * indev, lv_obj_t * obj)
{
    /* If the input device is already in the release state,
     * there is no need to wait for the input device to be released
     */
    if(lv_indev_get_state(indev) != LV_INDEV_STATE_RELEASED) {
        /*Wait for release to avoid accidentally triggering other obj to be clicked*/
        lv_indev_wait_release(indev);
    }

    /*Reset the input device*/
    lv_indev_reset(indev, obj);
}

static void obj_delete_core(lv_obj_t * obj)
{
    LV_ASSERT(obj != NULL);
    if(obj == NULL || obj->is_deleting)
        return;

    obj->is_deleting = true;

    /*Let the user free the resources used in `LV_EVENT_DELETE`*/
    lv_result_t res = lv_obj_send_event(obj, LV_EVENT_DELETE, NULL);
    if(res == LV_RESULT_INVALID) {
        obj->is_deleting = false;
        return;
    }

    /*Release the blur bookkeeping now: the style teardown below doesn't go
     *through lv_obj_update_blur_status()*/
    if(obj->has_blur) {
        obj->has_blur = 0;
        LV_ASSERT(LV_GLOBAL_DEFAULT()->blur_obj_cnt > 0);
        LV_GLOBAL_DEFAULT()->blur_obj_cnt--;
    }

    /*Clean registered event_cb*/
    if(obj->spec_attr) lv_event_remove_all(&(obj->spec_attr->event_list));

    /*Recursively delete the children*/
    lv_obj_t * child = lv_obj_get_child(obj, 0);
    while(child) {
        obj_delete_core(child);
        child = lv_obj_get_child(obj, 0);
    }

    lv_group_t * group = lv_obj_get_group(obj);

    /*Reset all input devices if the object to delete is used*/
    lv_indev_t * indev = lv_indev_get_next(NULL);
    while(indev) {
        lv_indev_type_t indev_type = lv_indev_get_type(indev);
        if(indev_type == LV_INDEV_TYPE_POINTER || indev_type == LV_INDEV_TYPE_BUTTON) {
            if(indev->pointer.act_obj == obj || indev->pointer.scroll_obj == obj) {
                obj_indev_reset(indev, obj);
            }
            if(indev->pointer.last_pressed == obj) {
                indev->pointer.last_pressed = NULL;
            }
            if(indev->pointer.last_hovered == obj) {
                indev->pointer.last_hovered = NULL;
            }
        }

        if(indev->group == group && obj == lv_indev_get_active_obj()) {
            obj_indev_reset(indev, obj);
        }
        indev = lv_indev_get_next(indev);
    }

    /*Delete all pending async del-s*/
    lv_result_t async_cancel_res = LV_RESULT_OK;
    while(async_cancel_res == LV_RESULT_OK) {
        async_cancel_res = lv_async_call_cancel(lv_obj_delete_async_cb, obj);
    }

    /*All children deleted. Now clean up the object specific data*/
    lv_obj_destruct(obj);

    /*Remove the screen for the screen list*/
    if(obj->parent == NULL) {
        lv_display_t * disp = lv_obj_get_display(obj);
        uint32_t i;
        /*Find the screen in the list*/
        for(i = 0; i < disp->screen_cnt; i++) {
            if(disp->screens[i] == obj) break;
        }

        uint32_t id = i;
        for(i = id; i < disp->screen_cnt - 1; i++) {
            disp->screens[i] = disp->screens[i + 1];
        }
        disp->screen_cnt--;
        disp->screens = lv_realloc(disp->screens, disp->screen_cnt * sizeof(lv_obj_t *));
    }
    /*Remove the object from the child list of its parent*/
    else {
        lv_obj_remove_child(obj->parent, obj);
    }

    obj->parent = NULL;

    /*Free the object itself*/
    lv_free(obj);
}

static lv_obj_tree_walk_res_t walk_core(lv_obj_t * obj, lv_obj_tree_walk_cb_t cb, void * user_data)
{
    lv_obj_tree_walk_res_t res = LV_OBJ_TREE_WALK_NEXT;

    if(obj == NULL) {
        lv_display_t * disp = lv_display_get_next(NULL);
        while(disp) {
            uint32_t i;
            for(i = 0; i < disp->screen_cnt; i++) {
                walk_core(disp->screens[i], cb, user_data);
            }
            disp = lv_display_get_next(disp);
        }
        return LV_OBJ_TREE_WALK_END;    /*The value doesn't matter as it wasn't called recursively*/
    }

    res = cb(obj, user_data);

    if(res == LV_OBJ_TREE_WALK_END) return LV_OBJ_TREE_WALK_END;

    if(res != LV_OBJ_TREE_WALK_SKIP_CHILDREN) {
        uint32_t i;
        for(i = 0; i < lv_obj_get_child_count(obj); i++) {
            res = walk_core(lv_obj_get_child(obj, i), cb, user_data);
            if(res == LV_OBJ_TREE_WALK_END) return LV_OBJ_TREE_WALK_END;
        }
    }
    return LV_OBJ_TREE_WALK_NEXT;
}

static void dump_tree_core(lv_obj_t * obj, int32_t depth)
{
    LV_ASSERT(obj != NULL);
#if LV_USE_LOG
    const char * id;

#if LV_USE_OBJ_ID
    char buf[OBJ_DUMP_STRING_LEN];
    id = lv_obj_stringify_id(obj, buf, sizeof(buf));
    if(id == NULL) id = "obj0";
#else
    id = "obj0";
#endif

    /*id of `obj0` is an invalid id for builtin id*/
    LV_LOG_USER("%*sobj:%p, id:%s;", (int)(2 * depth), "", (void *)obj, id);
#endif /*LV_USE_LOG*/

    if(obj->spec_attr && obj->spec_attr->child_cnt) {
        for(uint32_t i = 0; i < obj->spec_attr->child_cnt; i++) {
            dump_tree_core(lv_obj_get_child(obj, i), depth + 1);
        }
    }
}

static lv_obj_t * lv_obj_get_first_not_deleting_child(lv_obj_t * obj)
{
    LV_ASSERT(obj != NULL);

    if(obj->spec_attr == NULL) return NULL;

    int32_t i;
    int32_t cnt = (int32_t)obj->spec_attr->child_cnt;
    for(i = 0; i < cnt; i++) {
        if(!obj->spec_attr->children[i]->is_deleting) {
            return obj->spec_attr->children[i];
        }
    }

    return NULL;
}

#if LV_USE_OBJ_NAME

static lv_obj_t * find_by_name_direct(const lv_obj_t * parent, const char * name, size_t len)
{
    uint32_t i;
    uint32_t child_cnt = lv_obj_get_child_count(parent);
    for(i = 0; i < child_cnt; i++) {
        lv_obj_t * child = parent->spec_attr->children[i];

        char child_name_resolved[LV_OBJ_NAME_MAX_LEN];
        lv_obj_get_name_resolved(child, child_name_resolved, sizeof(child_name_resolved));

        if(len == UINT16_MAX) {
            if(lv_strcmp(child_name_resolved, name) == 0) return child;
        }
        else {
            if(lv_strncmp(child_name_resolved, name, len) == 0 &&
               child_name_resolved[len] == '\0') {
                return child;
            }
        }
    }

    return NULL;
}

#endif /*LV_USE_OBJ_NAME*/
