// SPDX-License-Identifier: GPL-3.0-or-later
#include "ui_settings_u1_account.h"

#include "app_globals.h"
#include "connection_state.h"
#include "core/data_root_resolver.h"
#include "i_moonraker_client.h"
#include "json_utils.h"
#include "moonraker_error.h"
#include "ui_callback_helpers.h"
#include "ui_modal.h"

namespace helix::settings {
namespace {
using json = nlohmann::json;
std::string member(const json& data, const char* key) {
    return json_util::safe_string(data, key, "", true);
}
void visibility(lv_obj_t* obj, bool show) {
    if (obj) lv_obj_set_flag(obj, LV_OBJ_FLAG_HIDDEN, !show);
}
}

lv_obj_t* U1AccountOverlay::create(lv_obj_t* parent) {
    lv_xml_register_component_from_file(asset_component_uri("ui_xml/u1_account_overlay.xml").c_str());
    return OverlayBase::create(parent);
}

void U1AccountOverlay::register_callbacks() {
    register_xml_callbacks({
        {"u1_account_refresh", [](lv_event_t*) { get_u1_account_overlay().action("refresh"); }},
        {"u1_account_pin", [](lv_event_t*) { get_u1_account_overlay().action("pin"); }},
        {"u1_account_access", [](lv_event_t*) { get_u1_account_overlay().action("access"); }},
        {"u1_account_mode", [](lv_event_t*) { get_u1_account_overlay().action("mode"); }},
        {"u1_account_cn", [](lv_event_t*) { get_u1_account_overlay().action("cn"); }},
        {"u1_account_int", [](lv_event_t*) { get_u1_account_overlay().action("int"); }},
        {"u1_account_disconnect", [](lv_event_t*) { get_u1_account_overlay().action("disconnect"); }},
        {"u1_account_region", [](lv_event_t*) { get_u1_account_overlay().action("region"); }},
        {"u1_account_logout", [](lv_event_t*) { get_u1_account_overlay().action("logout"); }},
    });
}

lv_obj_t* U1AccountOverlay::widget(const char* name) const {
    return overlay_root_ ? lv_obj_find_by_name(overlay_root_, name) : nullptr;
}

void U1AccountOverlay::status(const std::string& text) {
    message_ = text;
    if (auto* obj = widget("account_message")) lv_label_set_text(obj, message_.c_str());
}

void U1AccountOverlay::clear_codes() {
    pin_.clear();
    access_.clear();
    if (auto* qr = widget("account_qr")) lv_obj_clean(qr);
    if (auto* code = widget("account_code")) lv_label_set_text_static(code, "");
    visibility(widget("account_pairing"), false);
}

bool U1AccountOverlay::current(uint64_t epoch) const {
    return epoch == generation_ && is_visible() && client_ && client_ == get_moonraker_client()
           && client_->get_connection_state() == ConnectionState::CONNECTED;
}

void U1AccountOverlay::on_activate() {
    OverlayBase::on_activate();
    client_ = get_moonraker_client();
    ++generation_;
    busy_ = false;
    mode_ = clients_ = -1;
    device_checked_ = false;
    device_name_.clear();
    region_.clear();
    username_.clear();
    userid_.clear();
    clear_codes();
    status("正在读取设备状态");
    render();
    poll();
    timer_.reset(lv_timer_create([](lv_timer_t* t) {
        static_cast<U1AccountOverlay*>(lv_timer_get_user_data(t))->poll();
    }, 5000, this));
}

void U1AccountOverlay::on_deactivating(DeactivateReason) {
    ++generation_;
    timer_.reset();
    clear_codes();
    username_.clear();
    userid_.clear();
    busy_ = false;
    client_ = nullptr;
}

void U1AccountOverlay::poll() {
    if (!current(generation_)) {
        ++generation_;
        client_ = get_moonraker_client();
        failure();
        device_name_.clear();
        device_checked_ = false;
        if (!current(generation_)) return;
    }
    // Do not keep a scannable secret visible indefinitely when the operator
    // leaves the screen untouched. The cloud determines the PIN's validity.
    if ((!pin_.empty() || !access_.empty()) && lv_tick_elaps(pin_time_) >= 60000) {
        clear_codes();
        status("配对码已隐藏，可重新获取");
    }
    if (!busy_) request("info");
}

void U1AccountOverlay::request(const std::string& method, const json& params) {
    if (busy_ || !current(generation_)) {
        if (!busy_) failure();
        return;
    }
    busy_ = true;
    const auto epoch = generation_;
    const auto rpc = method == "machine" ? "machine.system_info" : "server.client_manager." + method;
    const auto id = client_->send_jsonrpc(rpc, params,
        lifetime_.bg_cb("U1Account::response", [this, method, epoch](const json& response) {
            if (!current(epoch)) return;
            busy_ = false;
            auto result = response.find("result");
            if (result == response.end() || !result->is_object()) { failure(); return; }
            received(method, *result);
        }), lifetime_.bg_cb("U1Account::error", [this, method, epoch](const MoonrakerError&) {
            if (!current(epoch)) return;
            if (method == "machine") {
                busy_ = false;
                device_checked_ = false;
                return;
            }
            failure();
        }), method == "refresh_pin_code" ? 30000 : 18000, true);
    if (!id && current(epoch)) failure();
}

void U1AccountOverlay::failure() {
    busy_ = false;
    mode_ = clients_ = -1;
    userid_.clear();
    username_.clear();
    region_.clear();
    clear_codes();
    status("服务未确认，请检查连接后刷新");
    render();
}

void U1AccountOverlay::received(const std::string& method, const json& result) {
    if (method == "machine") {
        device_checked_ = true;
        if (auto sys = result.find("system_info"); sys != result.end() && sys->is_object()) {
            if (auto product = sys->find("product_info"); product != sys->end() && product->is_object())
                device_name_ = member(*product, "device_name");
        }
        render();
        return;
    }
    if (member(result, "state") != "success") { failure(); return; }
    if (method == "info") {
        const auto link = member(result, "link_mode");
        const int mode = link == "0" || link == "wan" ? 0 : link == "1" || link == "lan" ? 1 : -1;
        if (mode < 0) { failure(); return; }
        auto user = member(result, "userid");
        if (user == "none") user.clear();
        const auto region = member(result, "region");
        if (mode != mode_ || userid_ != user || region != region_) clear_codes();
        mode_ = mode;
        region_ = region;
        userid_ = user;
        username_ = member(result, "username");
        clients_ = json_util::safe_int(result, "client_count", -1);
        if (message_ == "正在读取设备状态" || message_ == "服务未确认，请检查连接后刷新") status("");
        if (!device_checked_) request("machine");
    } else if (method == "refresh_pin_code") {
        clear_codes();
        pin_ = member(result, "pin_code");
        if (pin_.empty() || pin_.size() > 64 || pin_.find_first_of("\r\n") != std::string::npos) {
            clear_codes();
            status("没有收到有效 PIN，请重新获取");
        } else {
            pin_time_ = lv_tick_get();
            status("");
        }
    } else if (method == "refresh_access_code") {
        clear_codes();
        access_ = member(result, "access_code");
        pin_time_ = lv_tick_get();
        status(access_.empty() ? "设备未返回访问码" : "");
    } else {
        clear_codes();
        status(method == "disconnect_all" ? "设备已确认断开连接" : "设备已接收，正在核对账号状态");
        request("info");
    }
    render();
}

void U1AccountOverlay::render() {
    auto set = [this](const char* name, const std::string& text) {
        if (auto* obj = widget(name)) lv_label_set_text(obj, text.c_str());
    };
    set("account_identity", mode_ < 0 ? "账号状态待确认" : userid_.empty() ? "尚未绑定账号" :
        username_.empty() ? "已绑定账号" : "已绑定 · " + username_);
    std::string summary = mode_ == 0 ? "云端模式" : mode_ == 1 ? "局域网模式" : "";
    if (!summary.empty() && !region_.empty())
        summary += "  ·  " + (region_ == "cn" ? std::string("中国大陆") : region_ == "int" ? std::string("国际服务区") : region_);
    if (clients_ >= 0) summary += "  ·  已授权客户端 " + std::to_string(clients_);
    set("account_summary", summary);
    visibility(widget("account_mode"), mode_ >= 0);
    visibility(widget("account_region"), mode_ >= 0 && !region_.empty());
    visibility(widget("account_cn"), mode_ >= 0 && region_.empty());
    visibility(widget("account_int"), mode_ >= 0 && region_.empty());
    visibility(widget("account_disconnect"), mode_ == 1 && clients_ != 0);
    visibility(widget("account_pin"), mode_ == 0 && userid_.empty());
    visibility(widget("account_access"), mode_ == 1);
    visibility(widget("account_logout"), !userid_.empty());
    const bool qr = !pin_.empty() && !device_name_.empty();
    visibility(widget("account_pairing"), !pin_.empty() || !access_.empty());
    auto* container = widget("account_qr");
    visibility(container, qr);
    if (qr && container && !lv_obj_get_child_count(container)) {
        // Stock account view formats these three exact fields before calling
        // lv_qrcode_update. The PIN and name come from this same live printer.
        auto payload = json{{"deviceModel","U1"}, {"deviceName",device_name_}, {"authCode",pin_}}.dump();
        if (auto* image = lv_qrcode_create(container)) {
            lv_qrcode_set_size(image, 186);
            lv_qrcode_set_quiet_zone(image, true);
            lv_qrcode_set_dark_color(image, lv_color_black());
            lv_qrcode_set_light_color(image, lv_color_white());
            if (lv_qrcode_update(image, payload.data(), payload.size()) != LV_RESULT_OK) {
                lv_obj_delete(image);
                visibility(container, false);
            } else lv_obj_center(image);
        }
    }
    set("account_code", !pin_.empty() ? "PIN  " + pin_ : access_.empty() ? "" : "访问码  " + access_);
    set("account_code_hint", !pin_.empty() ? "在 Snapmaker App 添加设备，然后在本机确认绑定" :
        "在同一局域网内的客户端输入此码");
}

void U1AccountOverlay::action(std::string name) {
    if (name == "refresh") { poll(); return; }
    if (!current(generation_) || busy_ || mode_ < 0) return;
    if (name == "pin" && mode_ == 0 && userid_.empty()) {
        clear_codes(); status("正在向云端请求绑定码"); request("refresh_pin_code"); return;
    }
    if (name == "access" && mode_ == 1) {
        clear_codes(); status("正在读取访问码"); request("refresh_access_code"); return;
    }
    std::string method, prompt, button;
    json params = json::object();
    if (name == "logout" && !userid_.empty()) {
        method = "logout"; prompt = "退出本机绑定的 Snapmaker 账号？"; button = "退出账号";
    } else if (name == "mode") {
        const bool lan = mode_ == 0;
        method = "set_link_mode"; params["link_mode"] = lan ? 1 : 0;
        prompt = lan ? "切换到局域网后，云端连接会断开，可能需要重新绑定账号。" : "启用云端连接并切换至 Snapmaker 云端模式？";
        button = lan ? "切换到局域网" : "切换到云端";
    } else if (name == "disconnect" && mode_ == 1 && clients_ != 0) {
        method = "disconnect_all"; prompt = "断开所有局域网客户端的连接？"; button = "断开连接";
    } else if (name == "region" || ((name == "cn" || name == "int") && region_.empty())) {
        const bool china = name == "cn" || (name == "region" && region_ != "cn");
        method = "set_region"; params["region"] = china ? "cn" : "int";
        prompt = china ? "切换至中国大陆服务区？已绑定的账号可能退出。" : "切换至国际服务区？已绑定的账号可能退出。";
        button = china ? "选择中国大陆" : "选择国际服务区";
    } else return;
    const auto epoch = generation_;
    const auto mode = mode_;
    const auto user = userid_;
    const auto region = region_;
    ui::modal_confirm("账号与配对", prompt.c_str(), ModalSeverity::Warning, button.c_str(),
        [this, epoch, method, params, mode, user, region] {
            if (!current(epoch) || mode != mode_ || user != userid_ || region != region_ || busy_) {
                status("账号状态已变化，请刷新后重试");
                return;
            }
            clear_codes();
            status("等待设备确认");
            request(method, params);
        });
}
}
