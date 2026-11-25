// SPDX-License-Identifier: GPL-3.0-or-later
#include "ui_u1_service_overlay.h"

#include "app_globals.h"
#include "core/update_queue.h"
#include "chamber_air_modes.h"
#include "i_moonraker_api.h"
#include "i_moonraker_client.h"
#include "json_utils.h"
#include "material_workspace.h"
#include "printer_state.h"
#include "src/ui/panel_widgets/maintenance_model.h"
#include "theme_manager.h"
#include "ui_callback_helpers.h"
#include "ui_maintenance_overlay.h"
#include "ui_modal.h"
#include "ui_toast_manager.h"
#include "ui_utils.h"

#include <spdlog/fmt/fmt.h>
#include <algorithm>
#include <array>

namespace helix {
namespace {
constexpr std::array<const char*, 4> kinds{"photos", "videos", "gcodes", "logs"};
constexpr std::array<const char*, 4> labels{"AI 照片", "延时摄影", "打印文件", "运行日志"};

void feedback(const std::string& message) {
    ToastManager::instance().show(ToastSeverity::INFO, message.c_str());
}

std::string size_text(int64_t bytes) {
    if (bytes >= 1024LL * 1024 * 1024) return fmt::format("{:.1f} GiB", bytes / 1073741824.);
    if (bytes >= 1024 * 1024) return fmt::format("{:.1f} MiB", bytes / 1048576.);
    if (bytes >= 1024) return fmt::format("{:.0f} KiB", bytes / 1024.);
    return fmt::format("{} B", bytes);
}
} // namespace

lv_obj_t* U1ServiceOverlay::widget(const char* name) const {
    return overlay_root_ ? lv_obj_find_by_name(overlay_root_, name) : nullptr;
}

void U1ServiceOverlay::label(const char* name, const std::string& text) const {
    if (auto* obj = widget(name)) lv_label_set_text(obj, text.c_str());
}

void U1ServiceOverlay::enable(const char* name, bool enabled) const {
    if (auto* obj = widget(name)) lv_obj_set_state(obj, LV_STATE_DISABLED, !enabled);
}

void U1ServiceOverlay::register_callbacks() {
    register_xml_callbacks({
        {"on_u1_service_action", [](lv_event_t* e) {
             if (auto* name = lv_obj_get_name(lv_event_get_current_target_obj(e)))
                 get_u1_service_overlay().action(name);
         }},
        {"on_u1_service_kind", [](lv_event_t* e) {
             get_u1_service_overlay().select_kind(lv_dropdown_get_selected(lv_event_get_current_target_obj(e)));
         }}
    });
}

void U1ServiceOverlay::before_show() {
    exporting_ = showing_system_ = false;
    lv_obj_set_flag(widget("service_system_view"), LV_OBJ_FLAG_HIDDEN, true);
    kind_ = 0;
    offset_ = 0;
    next_ = -1;
    previous_.clear();
    checked_.clear();
    lv_dropdown_set_selected(widget("service_kind"), 0);
    lv_obj_set_flag(widget("service_home"), LV_OBJ_FLAG_HIDDEN, false);
    lv_obj_set_flag(widget("service_exports"), LV_OBJ_FLAG_HIDDEN, true);
}

void U1ServiceOverlay::on_activate() {
    OverlayBase::on_activate();
    if (showing_system_) system_page(true);
    else if (exporting_) export_view(true);
    else query_heads();
}

void U1ServiceOverlay::on_deactivating(DeactivateReason) {
    timer_.reset();
    ++system_generation_; system_pending_ = false;
    ++list_sequence_;
    ++status_sequence_;
    listing_ = reading_ = posting_ = false;
}

void U1ServiceOverlay::query_heads() {
    const auto duration = get_printer_state().chamber_air().filter_seconds();
    label("service_filter_hours", duration ? fmt::format("累计净化 {:.1f} 小时", *duration / 3600.) :
                                            "设备尚未报告净化时长");
    enable("service_filter_reset", duration && !chamber_air::filter_reset_gcode(get_printer_state().get_discovery()).empty());
    enable("service_lubricated", false);
    label("service_head_counts", "正在读取换头次数…");
    auto* client = get_moonraker_client();
    if (!client) {
        label("service_head_counts", "打印机未连接");
        return;
    }
    const auto token = lifetime_.token();
    json objects;
    for (int head = 0; head < 4; ++head)
        objects[head ? fmt::format("extruder{}", head) : "extruder"] = {"switch_count", "last_maintenance_count"};
    client->send_jsonrpc("printer.objects.query", {{"objects", objects}}, [this, token](const json& reply) {
        const auto* result = json_util::find_member(reply, "result");
        const auto* status = result ? json_util::find_member(*result, "status") : nullptr;
        std::vector<std::string> counts;
        bool reported = false;
        for (int i = 0; i < 4; ++i) {
            const auto name = i ? fmt::format("extruder{}", i) : "extruder";
            const auto* head = status ? json_util::find_member(*status, name.c_str()) : nullptr;
            const auto total = head ? json_util::safe_int64(*head, "switch_count", -1) : -1;
            const auto last = head ? json_util::safe_int64(*head, "last_maintenance_count", -1) : -1;
            const bool known = last >= 0 && total >= last;
            reported |= known;
            counts.push_back(fmt::format("{}号 {}", i + 1, known ? std::to_string(total - last) : "—"));
        }
        const std::string state = reported ?
            fmt::format("上次保养后换头次数\n{}  {}  {}  {}", counts[0], counts[1], counts[2], counts[3]) :
            "设备尚未报告工具头保养计数";
        ui::queue_update([this, token, reported, state]() {
            if (token.expired()) return;
            label("service_head_counts", state);
            enable("service_lubricated", reported);
        });
    }, [this, token](const MoonrakerError& error) {
        ui::queue_update([this, token, detail=error.message]() {
            if (!token.expired()) label("service_head_counts", "读取保养计数失败：" + detail);
        });
    }, 4000, true);
}

void U1ServiceOverlay::confirm_lubrication() {
    auto token = lifetime_.token();
    ui::modal_confirm("确认已完成工具头保养？", "确认后，四个工具头将从当前换头次数重新计算保养周期。",
        ModalSeverity::Warning, "确认已保养", [this, token] {
            if (token.expired()) return;
            auto* api = get_moonraker_api();
            if (!api) { feedback("打印机未连接"); return; }
            enable("service_lubricated", false);
            api->execute_gcode("RESET_EXTRUDER_MAINTENANCE_COUNT", [this, token]() {
                ui::queue_update([this, token]() {
                    if (token.expired()) return;
                    feedback("工具头保养已记录");
                    query_heads();
                });
            }, [this, token](const MoonrakerError& error) {
                ui::queue_update([this, token, detail=error.message]() {
                    if (token.expired()) return;
                    feedback("保存失败：" + detail);
                    query_heads();
                });
            });
        });
}

void U1ServiceOverlay::export_view(bool open) {
    exporting_ = open;
    ++status_sequence_;
    ++list_sequence_;
    listing_ = reading_ = posting_ = false;
    timer_.reset();
    lv_obj_set_flag(widget("service_home"), LV_OBJ_FLAG_HIDDEN, open);
    lv_obj_set_flag(widget("service_exports"), LV_OBJ_FLAG_HIDDEN, !open);
    lv_obj_scroll_to_y(widget("overlay_content"), 0, LV_ANIM_OFF);
    if (!open) { query_heads(); return; }
    mounted_ = active_job_ = false;
    job_id_.clear();
    label("service_usb", "正在检查 U 盘…");
    label("service_progress", "");
    read_service();
    list_files(offset_);
    timer_.reset(lv_timer_create([](lv_timer_t* timer) {
        static_cast<U1ServiceOverlay*>(lv_timer_get_user_data(timer))->read_service();
    }, 1500, this));
}

void U1ServiceOverlay::read_service() {
    if (!exporting_ || reading_ || posting_) return;
    reading_ = true;
    const auto seq = ++status_sequence_;
    auto token = lifetime_.token();
    material_workspace::request("/exports", {}, [this, token, seq](auto data, std::string error) {
        if (token.expired() || seq != status_sequence_) return;
        reading_ = false;
        if (error.empty()) display_job(data);
        else {
            mounted_ = active_job_ = false;
            job_id_.clear();
            label("service_usb", "无法读取导出状态：" + error);
            label("service_progress", "");
            update_actions();
        }
    });
}

void U1ServiceOverlay::display_job(const nlohmann::json& data) {
    const auto* usb = json_util::find_member(data, "usb");
    const auto* job = json_util::find_member(data, "job");
    mounted_ = usb && json_util::safe_bool(*usb, "mounted");
    label("service_usb", mounted_ ? "U 盘可用 · 剩余 " + size_text(json_util::safe_int64(*usb, "free_bytes")) :
                                  "U 盘未连接");
    job_id_ = job ? json_util::safe_string(*job, "id") : "";
    const auto state = job ? json_util::safe_string(*job, "state") : "";
    active_job_ = state == "scanning" || state == "copying" || state == "cancelling";
    std::string text = job ? json_util::safe_string(*job, "message") : "设备未返回任务状态";
    if (job && state == "copying")
        text += fmt::format(" · {}/{} 个文件 · {} / {}", json_util::safe_int(*job, "files_done"),
            json_util::safe_int(*job, "files_total"), size_text(json_util::safe_int64(*job, "done_bytes")),
            size_text(json_util::safe_int64(*job, "total_bytes")));
    if (job && state == "complete") text += "\n" + json_util::safe_string(*job, "output");
    label("service_progress", text);
    update_actions();
    enable("service_cancel", active_job_ && !posting_ && !job_id_.empty() && state != "cancelling");
}

void U1ServiceOverlay::update_actions() {
    const bool ready = mounted_ && !active_job_ && !posting_ && listed_ && !listing_;
    enable("service_copy_all", ready && !files_.empty());
    enable("service_copy_checked", ready && !checked_.empty());
    const bool can_manage = !active_job_ && !posting_ && listed_ && !listing_;
    lv_obj_set_flag(widget("service_local_logs"), LV_OBJ_FLAG_HIDDEN, kind_ != 3);
    lv_obj_set_flag(widget("service_delete"), LV_OBJ_FLAG_HIDDEN, kind_ != 1 && kind_ != 2);
    lv_obj_set_flag(widget("service_clear"), LV_OBJ_FLAG_HIDDEN, kind_ != 1 && kind_ != 2);
    enable("service_clear", can_manage);
    enable("service_select_page", can_manage && !files_.empty());
    enable("service_local_logs", can_manage && !files_.empty());
    enable("service_delete", can_manage && !checked_.empty());
    enable("service_kind", !posting_);
    enable("service_cancel", active_job_ && !posting_ && !job_id_.empty());
    enable("service_prev", !listing_ && !previous_.empty());
    enable("service_next", !listing_ && next_ >= 0);
}

void U1ServiceOverlay::select_kind(int index) {
    if (index < 0 || index >= static_cast<int>(kinds.size())) return;
    kind_ = index;
    previous_.clear();
    checked_.clear();
    list_files(0);
}

void U1ServiceOverlay::check_file(lv_event_t* event) {
    auto* self = static_cast<U1ServiceOverlay*>(lv_event_get_user_data(event));
    auto* box = lv_event_get_current_target_obj(event);
    const auto i = lv_obj_get_index(box);
    if (i < 0 || static_cast<size_t>(i) >= self->files_.size()) return;
    const auto& id = self->files_[i].id;
    if (lv_obj_has_state(box, LV_STATE_CHECKED)) self->checked_.insert(id);
    else self->checked_.erase(id);
    self->label("service_files_status", fmt::format("本页 {} 个 · 已选 {} 个", self->files_.size(), self->checked_.size()));
    self->update_actions();
}

void U1ServiceOverlay::list_files(int offset) {
    const auto sequence = ++list_sequence_;
    const auto token = lifetime_.token();
    listed_ = false; listing_ = true; offset_ = offset; next_ = -1;
    files_.clear();
    helix::ui::safe_clean_children(widget("service_files"));
    label("service_files_status", "正在读取文件…");
    update_actions();
    const std::string path = fmt::format("/exports/files?kind={}&offset={}", kinds[kind_], offset);
    material_workspace::request(path, {}, [this, token, sequence](auto data, std::string error) {
        if (token.expired() || sequence != list_sequence_) return;
        listing_ = false;
        const auto* rows = json_util::find_member(data, "files");
        if (!error.empty() || !rows || !rows->is_array()) {
            label("service_files_status", error.empty() ? "文件清单不可用" : error);
            update_actions();
            return;
        }
        next_ = json_util::safe_int(data, "next_offset", -1);
        if (next_ <= offset_) next_ = -1;
        for (const auto& row : *rows) {
            const auto id = json_util::safe_string(row, "id");
            const auto name = json_util::safe_string(row, "name");
            if (id.empty() || name.empty()) continue;
            files_.push_back({id, name});
            auto* box = lv_checkbox_create(widget("service_files"));
            lv_obj_set_name(box, fmt::format("service_file_{}", files_.size() - 1).c_str());
            lv_obj_set_width(box, lv_pct(100));
            lv_obj_set_style_min_height(box, theme_manager_get_spacing("u1_touch_target"), 0);
            lv_obj_set_style_text_font(box, theme_manager_get_font("u1_aux_font"), 0);
            lv_checkbox_set_text(box, fmt::format("{}  ·  {}", name, size_text(json_util::safe_int64(row, "bytes"))).c_str());
            lv_obj_set_state(box, LV_STATE_CHECKED, checked_.count(id) != 0);
            lv_obj_add_event_cb(box, check_file, LV_EVENT_VALUE_CHANGED, this);
        }
        listed_ = true;
        label("service_files_status", files_.empty() ? "本类没有可导出的文件" :
              fmt::format("本页 {} 个 · 已选 {} 个", files_.size(), checked_.size()));
        update_actions();
    });
}

void U1ServiceOverlay::send_export(const std::string& path, const nlohmann::json& body) {
    if (posting_) return;
    const auto token = lifetime_.token();
    ++status_sequence_;
    reading_ = false;
    posting_ = true;
    const bool deleting = path == "/exports/delete" || path == "/exports/cleanup";
    label("service_progress", deleting ? "正在删除文件…" : path == "/exports/start" ? "正在创建导出任务…" : "正在请求取消…");
    update_actions();
    material_workspace::request(path, body, [this, token, deleting](auto data, std::string error) {
        if (token.expired()) return;
        posting_ = false;
        if (deleting) {
            feedback(error.empty() ? fmt::format("已删除 {} 个文件", json_util::safe_int(data, "deleted")) : error);
            checked_.clear();
            previous_.clear();
            list_files(0);
            read_service();
        } else if (error.empty()) display_job(data);
        else { feedback(error); read_service(); update_actions(); }
    }, true);
}

void U1ServiceOverlay::system_page(bool show) {
    ++system_generation_;
    showing_system_ = show;
    system_pending_ = false;
    timer_.reset();
    lv_obj_set_flag(widget("service_home"), LV_OBJ_FLAG_HIDDEN, show);
    lv_obj_set_flag(widget("service_system_view"), LV_OBJ_FLAG_HIDDEN, !show);
    lv_obj_scroll_to_y(widget("overlay_content"), 0, LV_ANIM_OFF);
    if (!show) { query_heads(); return; }
    label("system_status", "正在读取设备状态…");
    system_request(firmware_started_ ? "/firmware/status" : "/oem/system", {}, false);
    timer_.reset(lv_timer_create([](lv_timer_t* t) {
        auto* self = static_cast<U1ServiceOverlay*>(lv_timer_get_user_data(t));
        self->system_request(self->firmware_started_ ? "/firmware/status" : "/oem/system", {}, false);
    }, 1800, this));
}

void U1ServiceOverlay::system_request(const std::string& path, const nlohmann::json& body, bool post) {
    if (!showing_system_ || system_pending_) return;
    system_pending_ = true;
    const auto token = lifetime_.token();
    const auto seq = system_generation_;
    if (post) {
        label("system_status", "正在请求设备服务…");
        for (auto name : {"system_check", "system_download", "system_verify", "system_install", "system_reset"}) enable(name, false);
    }
    material_workspace::request(path, body, [this, token, seq, path](auto data, std::string error) {
        if (token.expired() || seq != system_generation_) return;
        system_pending_ = false;
        if (path == "/firmware/start" && error.empty()) {
            firmware_started_ = true;
            system_request("/firmware/status", {}, false);
            return;
        }
        const bool flashing = path == "/firmware/status" && error.empty();
        const auto phase = json_util::safe_string(data, "phase");
        firmware_token_ = json_util::safe_string(data, "token");
        firmware_hash_ = json_util::safe_string(data, "sha256");
        system_version_ = json_util::safe_string(data, "version");
        std::string message = error.empty() ? json_util::safe_string(data, flashing ? "msg" : "message") : error;
        if (flashing) {
            auto state = json_util::safe_string(data, "state");
            message += fmt::format(" · {}%", json_util::safe_int(data, "pct"));
            if (auto err = json_util::safe_string(data, "error"); !err.empty()) message += "\n" + err;
            if (state == "idle" || state == "error" || state == "complete") firmware_started_ = false;
        }
        label("system_status", message.empty() ? "检查新版本，或选择整机恢复。" : message);
        const bool ready = !firmware_started_ && phase != "working";
        enable("system_check", ready);
        enable("system_reset", ready && phase != "downloading");
        enable("system_download", ready && phase == "available");
        enable("system_verify", ready && phase == "downloaded");
        enable("system_install", ready && phase == "verified" && !firmware_token_.empty());
    }, post);
}

void U1ServiceOverlay::action(const std::string& name) {
    if (name == "service_system" || name == "system_return") { system_page(name == "service_system"); return; }
    if (name.rfind("system_", 0) == 0 && !system_pending_ && !firmware_started_) {
        if (name == "system_check") { system_request("/oem/system", {{"action","check"}}, true); return; }
        if (name == "system_verify") { system_request("/oem/system", {{"action","verify"},{"version",system_version_}}, true); return; }
        const bool install = name == "system_install", reset = name == "system_reset";
        if (!install && !reset && name != "system_download") return;
        const auto version = system_version_, receipt = firmware_token_, hash = firmware_hash_;
        const auto seq = system_generation_;
        const auto token = lifetime_.token();
        auto title = reset ? "恢复整机出厂设置？" : install ? "安装已校验的固件？" : "下载原厂固件？";
        auto message = reset ? std::string("将清除机身配置、网络、账号连接及设备日志等资料。完成后须重启并重新配置打印机。") :
            install ? "即将更新 U1 主机、主控及四个工具头。请保持供电。\n" + version + "\nSHA-256 " + hash :
            "将使用设备的云端连接下载 " + version;
        ui::modal_confirm(title, message.c_str(), ModalSeverity::Warning, reset ? "确认整机恢复" : install ? "确认安装" : "下载",
            [this, token, seq, reset, install, version, receipt] {
                if (token.expired() || seq != system_generation_ || !showing_system_) return;
                if (install) {
                    if (receipt == firmware_token_ && !receipt.empty()) system_request("/firmware/start", {{"token",receipt}}, true);
                } else if (reset || version == system_version_) {
                    system_request("/oem/system", {{"action",reset ? "reset" : "download"},{"version",version},{"confirmed",true}}, true);
                }
            });
        return;
    }
    if (name == "service_export") { export_view(true); return; }
    if (name == "service_return") { export_view(false); return; }
    if (name == "service_refresh") { query_heads(); return; }
    if (name == "service_lubricated") { confirm_lubrication(); return; }
    if (name == "service_filter_reset" || name == "service_intervals") {
        maintenance_model::init_subjects();
        if (name == "service_filter_reset") maintenance_model::confirm_reset(static_cast<int>(maintenance::MeterId::Filter));
        else get_maintenance_overlay().show(parent_screen_);
        return;
    }
    if (name == "service_next" && next_ >= 0) { previous_.push_back(offset_); list_files(next_); return; }
    if (name == "service_prev" && !previous_.empty()) {
        const int pos = previous_.back(); previous_.pop_back(); list_files(pos); return;
    }
    if (name == "service_cancel" && active_job_ && !job_id_.empty()) {
        send_export("/exports/cancel", {{"id", job_id_}}); return;
    }
    if (name == "service_select_page" && listed_ && !listing_) {
        const bool all = std::all_of(files_.begin(), files_.end(), [this](const File& f) { return checked_.count(f.id) != 0; });
        for (size_t i = 0; i < files_.size(); ++i) {
            const auto& id = files_[i].id;
            if (all) checked_.erase(id); else checked_.insert(id);
            lv_obj_set_state(lv_obj_get_child(widget("service_files"), static_cast<int32_t>(i)), LV_STATE_CHECKED, !all);
        }
        label("service_files_status", fmt::format("本页 {} 个 · 已选 {} 个", files_.size(), checked_.size()));
        update_actions();
        return;
    }
    if (name == "service_clear" && !posting_ && !active_job_) {
        const auto token = lifetime_.token();
        const auto seq = list_sequence_;
        material_workspace::request("/exports/cleanup", {}, [this, token, seq](auto data, std::string error) {
            if (token.expired() || !exporting_ || seq != list_sequence_) return;
            const auto* counts = json_util::find_member(data, "counts");
            const auto hash = json_util::safe_string(data, "snapshot");
            if (!error.empty() || !counts || hash.empty()) {
                feedback(error.empty() ? "无法读取清理清单" : error); return;
            }
            const int jobs = json_util::safe_int(*counts, "gcodes"), videos = json_util::safe_int(*counts, "videos");
            if (!jobs && !videos) { feedback("当前没有可清理的文件"); return; }
            const auto msg = fmt::format("永久删除本机的 {} 个打印文件和 {} 段延时视频？", jobs, videos);
            ui::modal_confirm("清理本机存储", msg.c_str(), ModalSeverity::Warning, "确认清理", [this, token, seq, hash] {
                if (!token.expired() && exporting_ && seq == list_sequence_ && !active_job_)
                    send_export("/exports/cleanup", {{"snapshot", hash}});
            });
        });
        return;
    }
    if (name == "service_local_logs" || name == "service_delete") {
        if (posting_ || active_job_ || !listed_ || listing_) return;
        const bool deleting = name == "service_delete";
        if ((deleting && ((kind_ != 1 && kind_ != 2) || checked_.empty())) || (!deleting && kind_ != 3)) return;
        nlohmann::json body = {{"kind", kinds[kind_]}};
        if (deleting) body["files"] = checked_;
        else body["destination"] = "local";
        const auto token = lifetime_.token();
        const std::string message = deleting ? fmt::format("所选 {} 个{}将永久删除。", checked_.size(), labels[kind_]) :
            "将全部运行日志打包到本机打印文件目录下的 logs 文件夹。";
        ui::modal_confirm(deleting ? "删除所选文件？" : "保存日志包到本机？", message.c_str(),
            deleting ? ModalSeverity::Warning : ModalSeverity::Info, deleting ? "删除" : "保存",
            [this, token, deleting, body] {
                if (token.expired() || !exporting_ || active_job_) return;
                send_export(deleting ? "/exports/delete" : "/exports/start", body);
            });
        return;
    }
    if ((name != "service_copy_checked" && name != "service_copy_all") || !mounted_ ||
        posting_ || active_job_ || !listed_ || listing_) return;
    nlohmann::json body = {{"kind", kinds[kind_]}};
    if (name == "service_copy_checked") {
        if (checked_.empty()) return;
        body["files"] = checked_;
    }
    auto token = lifetime_.token();
    const auto summary = name == "service_copy_checked" ? fmt::format("所选 {} 个文件", checked_.size()) :
                         fmt::format("全部{}", labels[kind_]);
    ui::modal_confirm("导出到 U 盘", ("将" + summary + "复制到当前 U 盘的新目录。").c_str(),
        ModalSeverity::Info, "导出", [this, token, body] {
            if (!token.expired() && exporting_ && mounted_ && !active_job_) send_export("/exports/start", body);
        });
}
} // namespace helix
