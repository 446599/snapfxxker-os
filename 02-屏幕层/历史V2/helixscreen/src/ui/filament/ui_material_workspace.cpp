// SPDX-License-Identifier: GPL-3.0-or-later
#include "ui_material_workspace.h"

#include "ui_ams_edit_overlay.h"
#include "ui_ams_environment_overlay.h"
#include "ui_callback_helpers.h"
#include "ui_nav.h"

#include "ams_backend.h"
#include "ams_state.h"
#include "app_globals.h"
#include "core/data_root_resolver.h"
#include "core/observer_factory.h"
#include "core/static_panel_registry.h"
#include "json_utils.h"
#include "material_workspace.h"
#include "printer_state.h"

#include <spdlog/fmt/fmt.h>

#include <algorithm>
#include <cmath>
#include <cstdlib>

namespace helix::ui {
using json_util::safe_bool;
using json_util::safe_int;
using json_util::safe_string;
MaterialWorkspace::~MaterialWorkspace() {
    poll_.reset();
    revision_observer_.reset();
    subjects_.deinit_all();
}
void MaterialWorkspace::init_subjects() {
    init_subjects_guarded([this] {
        UI_MANAGED_SUBJECT_INT(page_, 0, "workspace_page", subjects_);
        UI_MANAGED_SUBJECT_INT(busy_, 0, "workspace_busy", subjects_);
        UI_MANAGED_SUBJECT_INT(ready_, 0, "workspace_ready", subjects_);
        UI_MANAGED_SUBJECT_INT(list_count_, 0, "workspace_spool_count", subjects_);
        UI_MANAGED_SUBJECT_INT(file_count_, 0, "workspace_file_count", subjects_);
        UI_MANAGED_SUBJECT_INT(confirmed_, 0, "workspace_prepared", subjects_);
        UI_MANAGED_SUBJECT_INT(settings_ready_, 0, "workspace_settings_ready", subjects_);
        settings_labels_.ensure_size(5);
        UI_MANAGED_SUBJECT_INT(auto_sensor_, 1, "workspace_auto_sensor", subjects_);
        auto_values_.ensure_size(5);
        UI_MANAGED_SUBJECT_INT(progress_, 0, "workspace_progress", subjects_);
        UI_MANAGED_SUBJECT_STRING(title_, title_buf_, "耗材操作", "workspace_title", subjects_);
        UI_MANAGED_SUBJECT_STRING(status_, status_buf_, "请选择需要的操作", "workspace_status",
                                  subjects_);
        UI_MANAGED_SUBJECT_STRING(detail_, detail_buf_, "", "workspace_detail", subjects_);
        UI_MANAGED_SUBJECT_STRING(form_material_, form_material_buf_, "PLA", "workspace_material",
                                  subjects_);
        UI_MANAGED_SUBJECT_STRING(form_color_, form_color_buf_, "D68459", "workspace_color",
                                  subjects_);
        UI_MANAGED_SUBJECT_STRING(form_weight_, form_weight_buf_, "", "workspace_weight",
                                  subjects_);
        UI_MANAGED_SUBJECT_STRING(form_vendor_, form_vendor_buf_, "Generic", "workspace_vendor",
                                  subjects_);
        UI_MANAGED_SUBJECT_STRING(file_info_, file_info_buf_, "插入 U 盘后刷新文件列表",
                                  "workspace_file_info", subjects_);
        UI_MANAGED_SUBJECT_STRING(version_, version_buf_, "", "workspace_version", subjects_);
    });
}
void MaterialWorkspace::open(int head, int unit, bool library) {
    firmware_entry_ = false;
    head_ = head;
    unit_ = std::max(0, unit);
    init_subjects();
    show(lv_screen_active());
    lv_subject_copy_string(&status_, "请选择需要的操作");
    page(library ? 1 : 0);
    refresh();
    if (library)
        load_library();
}
void MaterialWorkspace::open_firmware() {
    firmware_entry_ = true;
    init_subjects();
    show(lv_screen_active());
    page(3);
    load_files();
}
lv_obj_t* MaterialWorkspace::create(lv_obj_t* parent) {
    lv_xml_register_component_from_file(
        asset_component_uri("ui_xml/material_workspace.xml").c_str());
    return OverlayBase::create(parent);
}
void MaterialWorkspace::register_callbacks() {
    register_xml_callbacks({
        {"workspace_back",
         [](lv_event_t* e) {
             if (auto* self = from_event<MaterialWorkspace>(e))
                 self->act("back");
         }},
        {"workspace_action",
         [](lv_event_t* e) {
             if (auto* self = from_event<MaterialWorkspace>(e))
                 if (auto* value = static_cast<const char*>(lv_event_get_user_data(e)))
                     self->act(value);
         }},
        {"workspace_spool_select",
         [](lv_event_t* e) {
             if (auto* self = from_event<MaterialWorkspace>(e))
                 if (auto* value = static_cast<const char*>(lv_event_get_user_data(e)))
                     self->choose_spool(std::atoi(value));
         }},
        {"workspace_file_select",
         [](lv_event_t* e) {
             if (auto* self = from_event<MaterialWorkspace>(e))
                 if (auto* value = static_cast<const char*>(lv_event_get_user_data(e)))
                     self->choose_file(std::atoi(value));
         }},

    });
}
void MaterialWorkspace::on_activate() {
    OverlayBase::on_activate();
    visible_ = true;
    revision_observer_ = observe<int>(
        AmsState::instance().get_ams_data_revision_subject(), this,
        [](MaterialWorkspace* self, int) { self->refresh(); },
        AmsState::instance().get_subjects_lifetime());
    refresh();
    if (lv_subject_get_int(&page_) == 5)
        show_progress();
}
void MaterialWorkspace::on_deactivating(DeactivateReason) {
    visible_ = false;
    revision_observer_.reset();
    poll_.reset();
}
void MaterialWorkspace::page(int value) {
    lv_subject_set_int(&page_, value);
    const char* titles[] = {"耗材操作", "本地耗材库", "编辑线盘", "本地固件升级", "确认固件升级",
                            "升级进度", "确认操作",   "自动烘干", "耗材设置"};
    lv_subject_copy_string(&title_, titles[value]);
    if (value != 5)
        poll_.reset();
    if (value == 0)
        refresh();
}
std::string MaterialWorkspace::input(const char* name) const {
    auto* field = overlay_root_ ? lv_obj_find_by_name(overlay_root_, name) : nullptr;
    return field ? lv_textarea_get_text(field) : "";
}
void MaterialWorkspace::refresh() {
    auto* backend = AmsState::instance().get_backend();
    auto info = backend ? backend->get_system_info() : AmsSystemInfo{};
    slot_ = -1;
    bool connected = false;
    if (static_cast<size_t>(unit_ + 1) < info.units.size()) {
        const auto& unit = info.units[static_cast<size_t>(unit_ + 1)];
        connected = unit.connected;
        for (const auto& slot : unit.slots) {
            if (slot.slot_index != head_ || slot.mapped_tool != head_)
                continue;
            slot_ = slot.global_index;
            const auto upstream = backend->upstream_slot(head_);
            const bool current = upstream && *upstream == slot_;
            std::string quantity = slot.remaining_weight_g >= 0
                                       ? fmt::format("约 {:.0f} g", slot.remaining_weight_g)
                                       : "余量未知";
            if (lv_subject_get_int(&page_) == 0)
                lv_subject_copy_string(
                    &detail_,
                    fmt::format("{} · {} 槽 连接 喷头 {}\n{} · {}\n{}", unit.name, head_ + 1,
                                head_ + 1, slot.material.empty() ? "待识别" : slot.material,
                                quantity, current ? "当前供料已确认" : "正在查看候选耗材")
                        .c_str());
        }
    }
    const bool online =
        lv_subject_get_int(get_printer_state().network_state().get_nav_buttons_enabled_subject()) ==
        1;
    const bool job =
        lv_subject_get_int(get_printer_state().print_state().get_job_holds_machine_subject()) != 0;
    lv_subject_set_int(&ready_, online && connected && slot_ >= 0 &&
                                    info.path_hold_reason.empty() &&
                                    info.action == AmsAction::IDLE && !job);
}
void MaterialWorkspace::run(const std::string& action, const nlohmann::json& args) {
    lv_subject_set_int(&busy_, 1);
    const auto token = object_lifetime_.token();
    material_workspace::command(action, args, [this, token](auto, std::string error) {
        if (token.expired())
            return;
        lv_subject_set_int(&busy_, 0);
        lv_subject_copy_string(&status_,
                               error.empty() ? "命令已受理，结果以设备状态为准" : error.c_str());
        refresh();
        if (error.empty() && lv_subject_get_int(&page_) == 8)
            load_settings();
    });
}
void MaterialWorkspace::load_library() {
    lv_subject_copy_string(&status_, "正在读取设备耗材库");
    auto token = lifetime_.token();
    material_workspace::request("/state", {}, [this, token](auto data, std::string error) {
        if (token.expired())
            return;
        if (!error.empty()) {
            lv_subject_copy_string(&status_, error.c_str());
            return;
        }
        spools_ = nlohmann::json::array();
        const auto library = data.contains("spools") && data["spools"].is_object()
                                 ? data["spools"]
                                 : nlohmann::json::object();
        if (library.is_object())
            for (auto it = library.begin(); it != library.end(); ++it) {
                if (!it.value().is_object())
                    continue;
                auto spool = it.value();
                spool["id"] = it.key();
                spools_.push_back(spool);
            }
        labels_.ensure_size(spools_.size());
        for (size_t i = 0; i < spools_.size(); ++i) {
            const auto& spool = spools_[i];
            const auto weight = spool.find("weight_g");
            const std::string amount =
                weight != spool.end() && weight->is_number() && weight->get<double>() >= 0
                    ? fmt::format("{:.0f} g", weight->get<double>())
                    : "余量未知";
            labels_.set_string(
                i,
                fmt::format("{}  {}  ·  {}", safe_string(spool, "material", "待识别"),
                            safe_string(spool, "label", safe_string(spool, "vendor", "")), amount));
        }
        lv_subject_set_int(&list_count_, spools_.size());
        lv_subject_copy_string(
            &status_, fmt::format("{} 卷耗材 · 点选编辑或绑定当前槽", spools_.size()).c_str());
    });
}
void MaterialWorkspace::choose_spool(int index) {
    if (index < 0 || static_cast<size_t>(index) >= spools_.size())
        return;
    const auto& spool = spools_[index];
    spool_id_ = safe_string(spool, "id", "");
    lv_subject_copy_string(&form_material_, safe_string(spool, "material", "PLA").c_str());
    lv_subject_copy_string(&form_color_, safe_string(spool, "color", "").c_str());
    lv_subject_copy_string(&form_vendor_, safe_string(spool, "vendor", "").c_str());
    const auto weight = spool.find("weight_g");
    const auto text = weight != spool.end() && weight->is_number() && weight->get<double>() >= 0
                          ? fmt::format("{:.0f}", weight->get<double>())
                          : "";
    lv_subject_copy_string(&form_weight_, text.c_str());
    page(2);
}
void MaterialWorkspace::load_files() {
    auto token = lifetime_.token();
    material_workspace::request("/firmware/files", {}, [this, token](auto data, std::string error) {
        if (token.expired())
            return;
        if (!error.empty()) {
            lv_subject_copy_string(&status_, error.c_str());
            return;
        }
        files_ = data.value("files", nlohmann::json::array());
        file_labels_.ensure_size(files_.size());
        for (size_t i = 0; i < files_.size(); ++i)
            file_labels_.set_string(i, fmt::format("{}  · {:.1f} MB", files_[i].value("name", ""),
                                                   files_[i].value("size", 0.0) / 1048576));
        lv_subject_set_int(&file_count_, files_.size());
        lv_subject_copy_string(&status_, files_.empty()
                                             ? "未找到固件，请插入 U 盘或放入本地 firmware 目录"
                                             : "选择文件后会复制到本机并校验");
    });
}
void MaterialWorkspace::choose_file(int index) {
    if (lv_subject_get_int(&busy_) || index < 0 || static_cast<size_t>(index) >= files_.size())
        return;
    selected_file_ = files_[index].value("name", "");
    firmware_token_.clear();
    lv_subject_set_int(&confirmed_, 0);
    lv_subject_set_int(&busy_, 1);
    lv_subject_copy_string(&status_, "正在复制、校验固件，请稍候");
    auto token = object_lifetime_.token();
    material_workspace::request(
        "/firmware/prepare", {{"path", files_[index]["path"]}, {"target", "u1"}},
        [this, token](auto data, std::string error) {
            if (token.expired())
                return;
            lv_subject_set_int(&busy_, 0);
            if (!error.empty()) {
                lv_subject_copy_string(&status_, error.c_str());
                return;
            }
            firmware_token_ = data.value("token", "");
            lv_subject_set_int(&confirmed_, !firmware_token_.empty());
            lv_subject_copy_string(&version_, data.value("version", "").c_str());
            lv_subject_copy_string(
                &file_info_,
                fmt::format("{}\nSHA256 {}", selected_file_, data.value("sha256", "")).c_str());
            lv_subject_copy_string(&status_, "完整包分区校验通过，请核对目标版本");
        },
        true);
}
void MaterialWorkspace::show_progress() {
    page(5);
    if (!visible_)
        return;
    firmware_status();
    poll_.reset(lv_timer_create(
        [](lv_timer_t* timer) {
            static_cast<MaterialWorkspace*>(lv_timer_get_user_data(timer))->firmware_status();
        },
        1500, this));
}
void MaterialWorkspace::load_settings() {
    lv_subject_set_int(&settings_ready_, 0);
    auto token = lifetime_.token();
    material_workspace::request("/state", {}, [this, token](auto data, std::string error) {
        if (token.expired())
            return;
        if (!error.empty()) {
            lv_subject_copy_string(&status_, error.c_str());
            return;
        }
        const char* keys[] = {"quad_replenish", "pa_sync", "airprint_detection", "pickup_cleaning",
                              "purge_matrix"};
        const char* names[] = {"自动续料", "线盘 PA 同步", "空打检测", "取头清洁", "冲刷矩阵"};
        for (int i = 0; i < 5; ++i) {
            if (!data.contains(keys[i]) || !data[keys[i]].is_boolean()) {
                lv_subject_copy_string(&status_, "当前后端尚未提供完整耗材设置");
                return;
            }
            settings_labels_.set_string(
                i, std::string(names[i]) + (safe_bool(data, keys[i]) ? " · 已启用" : " · 已关闭"));
        }
        lv_subject_copy_string(&status_, "设置保存在设备端，与网页版共用");
        lv_subject_set_int(&settings_ready_, 1);
    });
}
void MaterialWorkspace::firmware_status() {
    auto token = lifetime_.token();
    material_workspace::request(
        "/firmware/status", {}, [this, token](auto data, std::string error) {
            if (token.expired())
                return;
            if (!error.empty()) {
                lv_subject_copy_string(&status_, error.c_str());
                return;
            }
            const std::string state = data.value("state", "unknown");
            const auto pct = data.find("pct");
            lv_subject_set_int(&progress_, pct != data.end() && pct->is_number()
                                               ? std::clamp(pct->template get<int>(), 0, 100)
                                               : 0);
            lv_subject_copy_string(&status_,
                                   fmt::format("{}\n{}\n当前系统 {}", data.value("msg", ""),
                                               data.value("error", ""),
                                               data.value("current_version", "未知"))
                                       .c_str());
            if (state == "verify_version" || state == "done" || state == "error" ||
                state == "idle" || state == "prepared" || state == "reboot_pending")
                poll_.reset();
        });
}
void MaterialWorkspace::act(const std::string& action) {
    if (action == "back") {
        const int current_page = lv_subject_get_int(&page_);
        lv_subject_copy_string(&status_, "请选择需要的操作");
        if (firmware_entry_) {
            if (current_page == 4)
                page(3);
            else
                nav::go_back();
            return;
        }
        if (lv_subject_get_int(&page_) == 0 || lv_subject_get_int(&page_) == 1)
            nav::go_back();
        else
            page(0);
        return;
    }
    if (lv_subject_get_int(&busy_))
        return;
    refresh();
    if (action == "read_tag" && !lv_subject_get_int(&ready_)) {
        lv_subject_copy_string(&status_, "请等待设备空闲、在线并确认料路位置");
        return;
    }
    if (action == "settings") {
        page(8);
        load_settings();
        return;
    }
    if (action.compare(0, 8, "setting_") == 0 && action.size() == 12 &&
        lv_subject_get_int(&settings_ready_)) {
        const int index = action[8] - '0';
        const char* actions[] = {"quad_replenish", "pa_sync", "airprint", "pickup_cleaning",
                                 "purge_matrix"};
        if (index < 0 || index >= 5)
            return;
        const bool on = action.substr(10) == "on";
        run(actions[index], {{index == 4 ? "MATRIX" : "ENABLE", on ? 1 : 0}});
        return;
    }
    if (action == "library") {
        page(1);
        load_library();
        return;
    }
    if (action == "refresh_library") {
        load_library();
        return;
    }
    if (action == "firmware") {
        page(3);
        load_files();
        return;
    }
    if (action == "refresh_files") {
        load_files();
        return;
    }
    if (action == "upgrade_status") {
        show_progress();
        return;
    }
    if (action == "firmware_next") {
        if (firmware_token_.empty())
            return;
        lv_subject_copy_string(&detail_,
                               fmt::format("升级目标  本机 U1 打印机\n文件  {}\n版本  "
                                           "{}\n升级期间请保持供电，完成后机器会重启。",
                                           selected_file_, lv_subject_get_string(&version_))
                                   .c_str());
        page(4);
        return;
    }
    if (action == "firmware_start") {
        lv_subject_set_int(&busy_, 1);
        auto token = object_lifetime_.token();
        material_workspace::request(
            "/firmware/start", {{"token", firmware_token_}},
            [this, token](auto, std::string error) {
                if (token.expired())
                    return;
                lv_subject_set_int(&busy_, 0);
                lv_subject_copy_string(&status_, error.empty() ? "升级任务已提交" : error.c_str());
                if (error.empty()) {
                    firmware_token_.clear();
                    lv_subject_set_int(&confirmed_, 0);
                    show_progress();
                }
            },
            true);
        return;
    }
    if (action == "new_spool") {
        spool_id_.clear();
        lv_subject_copy_string(&form_material_, "PLA");
        lv_subject_copy_string(&form_weight_, "");
        lv_subject_copy_string(&form_color_, "");
        lv_subject_copy_string(&form_vendor_, "Generic");
        page(2);
        return;
    }
    if (action == "save_spool") {
        auto args = nlohmann::json{{"MATERIAL", input("spool_material")},
                                   {"COLOR", input("spool_color")},
                                   {"VENDOR", input("spool_vendor")}};
        std::string weight = input("spool_weight");
        if (!weight.empty()) {
            char* end = nullptr;
            const double value = std::strtod(weight.c_str(), &end);
            if (!end || *end || !std::isfinite(value) || value < 0 || value > 100000) {
                lv_subject_copy_string(&status_, "请输入有效余量，单位 g");
                return;
            }
            args["WEIGHT"] = value;
        }
        if (input("spool_material").empty()) {
            lv_subject_copy_string(&status_, "请输入耗材类型");
            return;
        }
        if (!spool_id_.empty())
            args["ID"] = spool_id_;
        run(spool_id_.empty() ? "spool_add" : "spool_set", args);
        return;
    }
    if (action == "sync") {
        auto token = object_lifetime_.token();
        lv_subject_set_int(&busy_, 1);
        material_workspace::request(
            "/spoolman/sync", {},
            [this, token](auto, std::string error) {
                if (token.expired())
                    return;
                lv_subject_set_int(&busy_, 0);
                lv_subject_copy_string(&status_,
                                       error.empty() ? "Spoolman 同步已受理" : error.c_str());
            },
            true);
        return;
    }
    if (action == "confirm") {
        if (!lv_subject_get_int(&ready_)) {
            lv_subject_copy_string(&status_, "请等待设备空闲、在线并确认料路位置");
            return;
        }
        auto* backend = AmsState::instance().get_backend();
        if (pending_action_ == "load" || pending_action_ == "unload") {
            const auto source = backend->upstream_slot(head_);
            if (source != confirmed_source_) {
                lv_subject_copy_string(&status_, "当前供料已变化，请重新确认操作");
                page(0);
                return;
            }
            if (pending_action_ == "unload" && !source) {
                lv_subject_copy_string(&status_, "当前喷头没有已确认料路");
                return;
            }
            auto result = pending_action_ == "load" ? backend->load_filament(slot_)
                                                    : backend->unload_filament(*source);
            lv_subject_copy_string(&status_, result.success() ? "命令已受理，等待实际料路确认"
                                                              : result.user_msg.c_str());
        } else
            run(pending_action_, pending_args_);
        page(0);
        return;
    }
    if (action == "dry") {
        open_environment_for_unit(unit_ + 1);
        return;
    }
    if (action == "edit") {
        if (auto* backend = AmsState::instance().get_backend(); backend && slot_ >= 0)
            get_ams_edit_overlay().show_for_slot(lv_screen_active(), slot_,
                                                 backend->get_slot_info(slot_), get_moonraker_api(),
                                                 [](const auto&) {});
        return;
    }
    if (action == "auto_dry") {
        page(7);
        lv_subject_set_int(&busy_, 1);
        auto token = lifetime_.token();
        material_workspace::request("/state", {}, [this, token](auto data, std::string error) {
            if (token.expired())
                return;
            lv_subject_set_int(&busy_, 0);
            if (!error.empty()) {
                lv_subject_copy_string(&status_, error.c_str());
                return;
            }
            const auto units = data.contains("aces") && data["aces"].is_array()
                                   ? data["aces"]
                                   : nlohmann::json::array();
            for (const auto& unit : units) {
                if (safe_int(unit, "idx", -1) != unit_)
                    continue;
                const auto cfg = unit.value("auto_dry", nlohmann::json::object());
                if (!cfg.is_object())
                    break;
                const bool sensor =
                    safe_string(unit, "protocol") == "v2" ||
                    (unit.contains("humidity_effective") && unit["humidity_effective"].is_number());
                lv_subject_set_int(&auto_sensor_, sensor);
                const char* keys[] = {"temp", "rh_start", "rh_end", "master", "add_time"};
                for (int i = 0; i < 5; ++i) {
                    const auto found = cfg.find(keys[i]);
                    auto_values_.set_string(
                        i, found != cfg.end() && found->is_number() ? found->dump() : "");
                }
                lv_subject_copy_string(
                    &status_, fmt::format("ACE {} · {} · {}", unit_ + 1,
                                          safe_bool(cfg, "enabled") ? "已启用" : "已关闭",
                                          safe_bool(unit, "auto_dry_running") ? "正在烘干" : "待机")
                                  .c_str());
                return;
            }
            lv_subject_copy_string(&status_, "当前设备尚未提供自动烘干设置");
        });
        return;
    }
    if (action == "auto_dry_save") {
        nlohmann::json args = {{"ACE", unit_}};
        const bool sensor = lv_subject_get_int(&auto_sensor_) != 0;
        const char* keys[] = {"TEMP", "RH_START", "RH_END", "MASTER", "ADD_TIME"};
        double values[5]{};
        for (int i = 0; i < 5; ++i) {
            if ((sensor && i >= 3) || (!sensor && (i == 1 || i == 2)))
                continue;
            const auto text = input(fmt::format("auto_value_{}", i).c_str());
            char* end = nullptr;
            values[i] = std::strtod(text.c_str(), &end);
            if (text.empty() || !end || *end || !std::isfinite(values[i])) {
                lv_subject_copy_string(&status_, "请填写有效的烘干参数");
                return;
            }
            if (i == 0 || i >= 3) {
                if (values[i] != std::floor(values[i])) {
                    lv_subject_copy_string(&status_, "温度、主设备编号和时长需要填写整数");
                    return;
                }
                const double lower = i == 0 ? 35 : i == 3 ? -1 : 0;
                const double upper = i == 0 ? 150 : i == 3 ? 3 : 600;
                if (values[i] < lower || values[i] > upper) {
                    lv_subject_copy_string(&status_, "烘干参数超出可用范围");
                    return;
                }
                args[keys[i]] = static_cast<int>(values[i]);
            } else
                args[keys[i]] = values[i];
        }
        if (sensor && values[2] >= values[1]) {
            lv_subject_copy_string(&status_, "停止湿度需要低于启动湿度");
            return;
        }
        run("auto_dry", args);
        return;
    }
    if (action == "auto_dry_on" || action == "auto_dry_off") {
        run("auto_dry", {{"ACE", unit_}, {"ENABLE", action == "auto_dry_on" ? 1 : 0}});
        return;
    }
    if (action == "read_tag") {
        run("read_tag", {{"ACE", unit_}, {"SLOT", head_}});
        return;
    }
    pending_action_ = action;
    pending_args_ = {{"ACE", unit_}, {"SLOT", head_}};
    std::string prompt;
    std::string target_label =
        fmt::format("ACE {} · {} 槽 连接 喷头 {}", unit_ + 1, head_ + 1, head_ + 1);
    if (action == "load" || action == "unload") {
        auto* backend = AmsState::instance().get_backend();
        confirmed_source_ = backend ? backend->upstream_slot(head_) : std::nullopt;
        if (action == "unload") {
            if (!confirmed_source_) {
                lv_subject_copy_string(&status_, "当前喷头没有已确认料路");
                return;
            }
            const auto info = backend->get_system_info();
            for (const auto& unit : info.units)
                for (const auto& slot : unit.slots) {
                    if (slot.global_index == *confirmed_source_)
                        target_label = fmt::format("当前供料 {} · {} 槽 连接 喷头 {}", unit.name,
                                                   slot.slot_index + 1, head_ + 1);
                }
        }
    }
    if (action == "load")
        prompt = "将所选耗材装入喷头，已有耗材会先退料。";
    else if (action == "unload")
        prompt = "退出当前喷头中已确认的耗材。";
    else if (action == "spool_assign") {
        if (spool_id_.empty()) {
            lv_subject_copy_string(&status_, "请先保存新线盘，再从耗材库选择");
            return;
        }
        pending_args_["ID"] = spool_id_;
        prompt = "把所选线盘绑定到当前 ACE 槽。";
    } else if (action == "unbind") {
        pending_action_ = "spool_assign";
        prompt = "解除当前 ACE 槽的线盘绑定，余量将重新读取设备状态。";
    } else if (action == "calibrate_pa") {
        pending_args_ = {{"HEAD", head_}, {"FORCE", 1}};
        prompt = "执行当前已装载耗材的 PA 标定，设备会加热并运动。";
    } else if (action == "write_tag") {
        auto* backend = AmsState::instance().get_backend();
        if (!backend || slot_ < 0)
            return;
        const auto slot = backend->get_slot_info(slot_);
        if (slot.material.empty()) {
            lv_subject_copy_string(&status_, "请先识别或编辑耗材信息");
            return;
        }
        pending_args_["MATERIAL"] = slot.material;
        pending_args_["COLOR"] = fmt::format("{:06X}", slot.color_rgb);
        pending_args_["FORMAT"] = "openspool";
        prompt = "将当前耗材信息写入 RFID 标签，料槽会转动。需要设备支持 OpenSpool 写卡。";
    } else
        return;
    lv_subject_copy_string(&detail_, fmt::format("{}\n{}", target_label, prompt).c_str());
    page(6);
}
MaterialWorkspace& get_material_workspace() {
    return helix::lazy_global<MaterialWorkspace>("MaterialWorkspace");
}
} // namespace helix::ui
