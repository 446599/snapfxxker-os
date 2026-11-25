// SPDX-License-Identifier: GPL-3.0-or-later
#include "ui_u1_factory_calibration.h"

#include "ui_callback_helpers.h"
#include "ui_modal.h"
#include "ui_toast_manager.h"

#include "app_globals.h"
#include "connection_state.h"
#include "core/data_root_resolver.h"
#include "i_moonraker_client.h"
#include "json_utils.h"
#include "moonraker_error.h"

#include <spdlog/fmt/fmt.h>

#include <array>
#include <cmath>

namespace helix::ui {
namespace {
using json = nlohmann::json;
const json& obj(const json& value, const char* key) {
    static const json missing = json::object();
    auto i = value.find(key);
    return i != value.end() && i->is_object() ? *i : missing;
}
std::string extruder(int n) {
    return n ? "extruder" + std::to_string(n) : "extruder";
}
const std::array<const char*, 14> actions = {"enter", "motors", "next",  "home",    "tool",
                                             "move",  "left",   "right", "capture", "verify",
                                             "save",  "noise",  "exit",  "refresh"};
int machine_state(const json& object) {
    auto it = object.find("main_state");
    if (it != object.end() && it->is_number_integer())
        return it->get<int>();
    const auto state = json_util::safe_string(object, "main_state");
    if (state == "IDLE")
        return 0;
    if (state == "PARK_POINT_MANUAL_CALIBRATION")
        return 12;
    if (state == "HOMING_ORIGIN_CALIBRATION")
        return 13;
    if (state == "STEPPER_RESONANCE_CALIBRATE")
        return 14;
    return -1;
}
} // namespace

lv_obj_t* U1FactoryCalibration::create(lv_obj_t* parent) {
    lv_xml_register_component_from_file(
        asset_component_uri("ui_xml/u1_factory_calibration.xml").c_str());
    return OverlayBase::create(parent);
}
void U1FactoryCalibration::register_callbacks() {
    for (auto key : actions) {
        static std::array<std::string, 14> names;
        size_t pos = std::find(actions.begin(), actions.end(), key) - actions.begin();
        names[pos] = "u1_factory_" + std::string(key);
        lv_xml_register_event_cb(nullptr, names[pos].c_str(), [](lv_event_t* e) {
            auto* widget = lv_event_get_current_target_obj(e);
            if (const char* name = lv_obj_get_name(widget);
                name && std::string(name).rfind("factory_", 0) == 0)
                get_u1_factory_calibration().choose(name + 8);
        });
    }
}
lv_obj_t* U1FactoryCalibration::widget(const char* name) const {
    return overlay_root_ ? lv_obj_find_by_name(overlay_root_, name) : nullptr;
}
void U1FactoryCalibration::open(const std::string& kind) {
    kind_ = kind;
    show(lv_screen_active());
}
void U1FactoryCalibration::reset_position() {
    positioned_ = verified_ = awaiting_move_position_ = false;
    candidate_.reset();
    starting_x_.reset();
}
void U1FactoryCalibration::on_activate() {
    OverlayBase::on_activate();
    ++epoch_;
    client_ = get_moonraker_client();
    machine_ = -1;
    eligible_ = parked_ = read_busy_ = motion_ = reference_ = false;
    head_ = step_ = 0;
    entered_ = false;
    queued_action_.clear();
    awaiting_origin_ = watch_save_ = save_warning_ = false;
    if (client_) {
        const auto epoch = epoch_;
        client_->register_method_callback(
            "notify_gcode_response", "u1_factory_save",
            lifetime_.bg_cb("U1Calibration::save_warning", [this, epoch](const json& msg) {
                if (!live(epoch) || !watch_save_)
                    return;
                const auto p = msg.find("params");
                if (p == msg.end() || !p->is_array() || p->empty() || !(*p)[0].is_string())
                    return;
                if ((*p)[0].get_ref<const std::string&>().find(
                        "Failed to save park position to config file") != std::string::npos) {
                    save_warning_ = true;
                    notice_ = "固件报告泊位写入失败，请检查设备存储后重试";
                    render();
                }
            }));
    }
    reset_position();
    noise_.clear();
    image_.clear();
    notice_ = "正在核对设备状态";
    render();
    query();
    timer_.reset(lv_timer_create(
        [](lv_timer_t* timer) {
            auto* self = static_cast<U1FactoryCalibration*>(lv_timer_get_user_data(timer));
            self->query();
        },
        1500, this));
}
bool U1FactoryCalibration::live(uint64_t epoch) const {
    return epoch == epoch_ && client_ && client_ == get_moonraker_client() && is_visible() &&
           client_->get_connection_state() == ConnectionState::CONNECTED;
}
void U1FactoryCalibration::on_deactivating(DeactivateReason reason) {
    if (reason != DeactivateReason::Suspended && entered_ && live(epoch_) && kind_ != "noise") {
        // Stock EXIT is guarded by the exact machine state and restores its
        // idle timeout. Queue behind a move even if the user closes mid-step.
        client_->send_jsonrpc(
            "printer.gcode.script", {{"script", script("exit")}}, [](const json&) {},
            object_lifetime_.bg_cb("U1Calibration::exit_error",
                                   [](const MoonrakerError&) {
                                       ToastManager::instance().show(
                                           ToastSeverity::ERROR,
                                           "退出未确认，请重新打开原厂校准检查设备");
                                   }),
            180000, true);
        entered_ = false;
    }
    if (client_ && client_ == get_moonraker_client())
        client_->unregister_method_callback("notify_gcode_response", "u1_factory_save");
    ++epoch_;
    timer_.reset();
    motion_ = read_busy_ = false;
}
void U1FactoryCalibration::query(const std::string& action) {
    if (!live(epoch_)) {
        if (client_ != get_moonraker_client() || !client_ ||
            client_->get_connection_state() != ConnectionState::CONNECTED) {
            ++epoch_;
            client_ = get_moonraker_client();
            machine_ = -1;
            read_busy_ = eligible_ = entered_ = motion_ = reference_ = false;
            reset_position();
            step_ = 0;
            notice_ = "设备连接已改变，请核对原机的校准状态";
            render();
        }
        return;
    }
    if (read_busy_) {
        if (!action.empty() && queued_action_.empty())
            queued_action_ = action;
        return;
    }
    if (motion_ && kind_ != "noise")
        return;
    read_busy_ = true;
    const auto epoch = epoch_;
    json objects = {{"print_stats", {"state"}},
                    {"webhooks", {"state"}},
                    {"machine_state_manager", {"main_state", "action_code"}},
                    {"toolhead", {"homed_axes", "position"}}};
    if (kind_ == "noise")
        objects["stepper_resonance_calibrate"] = {"state"};
    else {
        objects["homing_precise_corexy"] = {"use_calibration_origin", "calibrated_origin"};
        objects["extruder"] = {"real_extruder_stats"};
    }
    auto token = client_->send_jsonrpc(
        "printer.objects.query", {{"objects", objects}},
        lifetime_.bg_cb(
            "U1Calibration::status",
            [this, epoch, action](const json& reply) {
                if (!live(epoch))
                    return;
                read_busy_ = false;
                const auto& values = obj(obj(reply, "result"), "status");
                const auto& head = obj(values, "toolhead");
                const auto& ext = obj(values, "extruder");
                const auto& source = obj(values, "homing_precise_corexy");
                const auto& noise = obj(values, "stepper_resonance_calibrate");
                const auto print = json_util::safe_string(obj(values, "print_stats"), "state");
                const auto web = json_util::safe_string(obj(values, "webhooks"), "state");
                const auto prev = machine_;
                machine_ = machine_state(obj(values, "machine_state_manager"));
                noise_ = json_util::safe_string(noise, "state");
                if (prev != machine_ && machine_ == 0) {
                    entered_ = false;
                    step_ = 0;
                    reset_position();
                }
                eligible_ =
                    web == "ready" &&
                    (print == "standby" || print == "complete" || print == "cancelled") &&
                    machine_ >= 0 &&
                    (kind_ == "noise" ? !noise_.empty()
                                      : !ext.empty() && (kind_ == "park" || !source.empty()));
                homed_ = json_util::safe_string(head, "homed_axes");
                const auto pos = head.find("position");
                xpos_.reset();
                if (pos != head.end() && pos->is_array() && !pos->empty() &&
                    (*pos)[0].is_number()) {
                    double x = (*pos)[0].get<double>();
                    if (std::isfinite(x))
                        xpos_ = x;
                }
                if (awaiting_move_position_ && xpos_) {
                    starting_x_ = xpos_;
                    positioned_ = true;
                    awaiting_move_position_ = false;
                }
                if (awaiting_origin_ && machine_ == 13) {
                    awaiting_origin_ = false;
                    const auto origin = source.find("calibrated_origin");
                    reference_ = (source.contains("use_calibration_origin") &&
                                  source["use_calibration_origin"] == true) &&
                                 origin != source.end() && origin->is_array() &&
                                 origin->size() == 2 && (*origin)[0].is_number() &&
                                 (*origin)[1].is_number();
                    step_ = reference_ ? 5 : 4;
                    notice_ = reference_ ? "设备已启用测得的原点，可继续校验泊位"
                                         : "设备未回报有效的新原点，可重新测量";
                }
                const auto e = ext.find("real_extruder_stats");
                parked_ = e != ext.end() && e->is_array() && e->size() == 2 &&
                          (*e)[1].is_number_integer() && (*e)[1] == 1;
                if (notice_ == "正在核对设备状态")
                    notice_.clear();
                if (!action.empty()) {
                    if (allowed(action))
                        run(action);
                    else
                        notice_ = "设备状态发生变化，操作未执行";
                }
                render();
                if (!queued_action_.empty()) {
                    auto pending = std::move(queued_action_);
                    queued_action_.clear();
                    if (allowed(pending))
                        query(pending);
                }
            }),
        lifetime_.bg_cb("U1Calibration::offline",
                        [this, epoch](const MoonrakerError&) {
                            if (!live(epoch))
                                return;
                            read_busy_ = eligible_ = false;
                            machine_ = -1;
                            queued_action_.clear();
                            reset_position();
                            notice_ = "未收到原厂校准状态，请检查设备连接";
                            render();
                        }),
        4500, true);
    if (!token) {
        read_busy_ = eligible_ = false;
        machine_ = -1;
        notice_ = "状态请求未发送";
        render();
    }
}
bool U1FactoryCalibration::allowed(const std::string& key) const {
    if (key == "refresh")
        return !read_busy_;
    if (!eligible_ || motion_)
        return false;
    if (kind_ == "noise")
        return key == "noise" && machine_ == 0 && noise_ != "calibrating" && noise_ != "homing";
    if (key == "enter")
        return machine_ == 0;
    if (machine_ != (kind_ == "origin" ? 13 : 12))
        return false;
    if (key == "exit")
        return true;
    if (key == "motors")
        return kind_ == "origin" && step_ == 0 && parked_;
    if (key == "next")
        return kind_ == "origin" && step_ >= 1 && step_ <= 3;
    if (key == "home")
        return parked_ && (kind_ == "park" || step_ == 4);
    if (!parked_ || homed_.find('x') == std::string::npos ||
        homed_.find('y') == std::string::npos || (kind_ == "origin" && !reference_))
        return false;
    if (key == "tool" || key == "move")
        return true;
    if (key == "left" || key == "right")
        return positioned_ && xpos_ && starting_x_ &&
               std::abs(*xpos_ + (key == "left" ? -0.1 : 0.1) - *starting_x_) <= 3.00001;
    if (key == "capture")
        return positioned_ && xpos_ && starting_x_ && std::abs(*xpos_ - *starting_x_) <= 3.00001;
    if (key == "verify")
        return candidate_.has_value();
    if (key == "save")
        return verified_ && candidate_.has_value();
    return false;
}
std::string U1FactoryCalibration::script(const std::string& key) const {
    const auto mode =
        kind_ == "origin" ? "HOMING_ORIGIN_CALIBRATION" : "PARK_POINT_MANUAL_CALIBRATION";
    if (key == "enter")
        return std::string("ENTER_") + mode;
    if (key == "exit")
        return std::string("EXIT_") + mode;
    if (key == "motors")
        return "M84";
    if (key == "home")
        return kind_ == "origin" ? "G28 X Y FORCE_CALIBRATION 1\nM400" : "G28 X Y\nM400";
    if (key == "noise")
        return "STEPPER_RESONANCE_FACTORY_CALIBRATE";
    const auto name = extruder(head_);
    if (key == "move")
        return "MOVE_TO_PARK_CALIBRATION_POINT EXTRUDER=" + name + "\nM400";
    if (key == "left" || key == "right")
        return std::string("G91\nG1 X") + (key == "left" ? "-0.1" : "0.1") + " F7800\nG90\nM400";
    if (key == "verify" && candidate_)
        return fmt::format("VERIFY_PARK_POSITION EXTRUDER={} X={:.3f} SKIP_HOME=0", name,
                           *candidate_);
    if (key == "save" && candidate_)
        return fmt::format("SET_EXTRUDER_PARK_POSITION EXTRUDER={} X={:.3f}", name, *candidate_);
    return {};
}
void U1FactoryCalibration::choose(const std::string& action) {
    if (action == "refresh") {
        query();
        return;
    }
    if (action == "tool" && allowed(action)) {
        head_ = (head_ + 1) % 4;
        reset_position();
        render();
        return;
    }
    if (action == "next" && allowed(action)) {
        guide(step_ + 1);
        return;
    }
    if (!allowed(action))
        return;
    if (action == "left" || action == "right" || action == "capture") {
        query(action);
        return;
    }
    const auto epoch = epoch_;
    std::string msg;
    if (action == "enter")
        msg = "进入原厂校准模式？操作前请清空平台，确认四个打印头均已停靠。";
    else if (action == "home")
        msg = kind_ == "origin" ? "确认两颗螺丝均已锁紧？设备将探测并保存新的回零原点。"
                                : "确认四个打印头已停靠，并执行 XY 回零？";
    else if (action == "motors")
        msg = "释放电机以便手动张紧同步带？释放后须重新回零。";
    else if (action == "noise")
        msg = "设备将回零、切换至 T0、测量两轴并自动保存降噪参数。开始前请清空移动路径。";
    else if (action == "move")
        msg = fmt::format("将转换器移动至 T{} 的原厂校准位？", head_);
    else if (action == "verify")
        msg = fmt::format("设备将回零并在 T{} 的候选 X {:.3f} mm 处实际取放打印头。", head_,
                          *candidate_);
    else if (action == "save")
        msg = fmt::format("将已验证的 X {:.3f} mm 保存为 T{} 的停靠位置？", *candidate_, head_);
    else if (action == "exit")
        msg = "结束本次原厂校准并恢复待机？";
    const auto head = head_;
    const auto coordinate = candidate_;
    modal_confirm("确认校准操作", msg.c_str(), ModalSeverity::Warning, "确认",
                  [this, epoch, head, coordinate, action] {
                      if (!live(epoch) || head != head_ || coordinate != candidate_ ||
                          !allowed(action))
                          return;
                      query(action);
                  });
}
void U1FactoryCalibration::run(const std::string& action) {
    if (action == "capture") {
        candidate_ = xpos_;
        verified_ = false;
        notice_ = "已记录候选 X，请验证取放";
        return;
    }
    watch_save_ = action == "save";
    save_warning_ = false;
    const auto gcode = script(action);
    if (gcode.empty())
        return;
    const auto epoch = epoch_;
    motion_ = true;
    notice_ = "指令执行中，请等待设备回报";
    if (action == "enter")
        entered_ = true;
    if (action == "move" || action == "home")
        reset_position();
    if (action == "left" || action == "right") {
        candidate_.reset();
        verified_ = false;
    }
    auto success = lifetime_.bg_cb("U1Calibration::gcode_ok", [this, epoch, action](const json&) {
        if (!live(epoch))
            return;
        motion_ = false;
        notice_ = "设备已确认操作";
        if (action == "motors")
            guide(1);
        if (action == "home" && kind_ == "origin")
            awaiting_origin_ = true;
        if (action == "verify")
            verified_ = true;
        if (action == "save") {
            notice_ = save_warning_ ? "固件报告泊位写入失败，请检查设备存储后重试"
                                    : "固件已处理泊位保存请求";
            if (!save_warning_)
                reset_position();
        }
        if (action == "move")
            awaiting_move_position_ = true;
        if (action == "exit")
            entered_ = false;
        query();
        render();
    });
    auto failure =
        lifetime_.bg_cb("U1Calibration::gcode_error", [this, epoch](const MoonrakerError& err) {
            if (!live(epoch))
                return;
            motion_ = false;
            reset_position();
            notice_ = "固件未确认操作：" + err.message;
            query();
            render();
        });
    auto id = client_->send_jsonrpc("printer.gcode.script", {{"script", gcode}}, success, failure,
                                    600000, true);
    if (!id) {
        motion_ = false;
        notice_ = "指令未发送";
        render();
    }
}
void U1FactoryCalibration::guide(int step) {
    step_ = step;
    render();
    if (auto* scroll = widget("factory_scroll"))
        lv_obj_scroll_to_y(scroll, 0, LV_ANIM_OFF);
}
void U1FactoryCalibration::render() {
    auto set = [this](const char* key, const std::string& text) {
        if (auto* child = widget(key))
            lv_label_set_text(child, text.c_str());
    };
    const bool origin = kind_ == "origin", noise = kind_ == "noise";
    set("factory_heading", origin ? "回零原点校准" : noise ? "电机降噪校准" : "取放头泊位校准");

    std::string state = machine_ < 0     ? "状态待确认"
                        : machine_ == 0  ? "待机"
                        : machine_ == 12 ? "取放头坐标校准"
                        : machine_ == 13 ? "回零原点校准"
                        : machine_ == 14 ? "电机降噪校准"
                                         : "设备正忙";
    std::string note, filename;
    if (noise) {
        if (noise_ == "completed")
            note = "固件已完成测量并保存结果。";
        else if (noise_ == "failed")
            note = "固件报告测量失败。检查机械状态后可重试。";
        else if (noise_ == "homing")
            note = "正在回零并准备打印头。";
        else if (noise_ == "calibrating")
            note = "正在扫描 XY 电机共振。";
        else
            note = "使用 U1 自带的双轴电机降噪测量，参数由设备保存。";
    } else if (machine_ == 0)
        note = "请在技术支持指导下操作，确认平台与转换器运动路径无障碍。";
    else if (origin && step_ >= 1 && step_ <= 3) {
        constexpr const char* instructions[] = {"",
                                                "逆时针转动约两圈，拧松图中两颗螺丝，无需拆下。",
                                                "手持打印头转换器，沿图示路径缓慢移动约两周。",
                                                "重新锁紧两颗螺丝，再执行回零原点测量。"};
        constexpr const char* images[] = {"", "belt_loosen.png", "belt_move.png",
                                          "loosen_tighten.png"};
        state = fmt::format("张紧同步带  ·  步骤 {}/3", step_);
        note = instructions[step_];
        filename = images[step_];
    } else if (origin && !reference_)
        note = step_ == 4 ? "确认螺丝已锁紧，可开始测量新的回零原点。"
                          : "停靠四个打印头后释放电机，依次完成同步带张紧。";
    else {
        note = fmt::format(
            "T{} · "
            "{}"
            "。选择打印头并移动至校准位，用左右微调让销钉对准打印头孔位中心，再记录、验证并保存。",
            head_, parked_ ? "四个打印头已停靠" : "等待全部打印头停靠");
        if (positioned_)
            filename = "toolhead_coordinate_adjust_align.png";
        if (xpos_)
            state += fmt::format("  ·  当前 X {:.3f} mm", *xpos_);
        if (candidate_)
            state += fmt::format("  ·  候选 {:.3f} mm", *candidate_);
    }
    if (auto* home = widget("factory_home")) {
        if (auto* label = lv_obj_find_by_name(home, "label"))
            lv_label_set_text(label, origin ? "测量回零原点" : "XY 回零");
    }
    set("factory_status", state);
    set("factory_instruction", note);
    set("factory_feedback", notice_);
    auto* img = widget("factory_illustration");
    if (img) {
        lv_obj_set_flag(img, LV_OBJ_FLAG_HIDDEN, filename.empty());
        if (!filename.empty() && filename != image_) {
            const auto path = asset_component_uri("assets/images/u1-calibration/" + filename);
            lv_image_set_src(img, path.c_str());
            // Fit the entire factory illustration in a single 480x320 viewport.
            const int w = filename == "toolhead_coordinate_adjust_align.png" ? 184 : 436;
            const int h = filename == "toolhead_coordinate_adjust_align.png" ? 256
                          : filename == "belt_move.png"                      ? 453
                                                                             : 222;
            const int factor = std::min(380 * 256 / w, 150 * 256 / h);
            lv_obj_set_size(img, w * factor / 256, h * factor / 256);
            lv_image_set_inner_align(img, LV_IMAGE_ALIGN_STRETCH);
        }
        image_ = filename;
    }
    for (auto key : actions) {
        auto* row = widget(("factory_" + std::string(key)).c_str());
        if (!row)
            continue;
        lv_obj_set_flag(row, LV_OBJ_FLAG_HIDDEN, !allowed(key));
    }
}
} // namespace helix::ui
