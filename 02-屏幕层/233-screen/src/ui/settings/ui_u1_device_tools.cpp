// SPDX-License-Identifier: GPL-3.0-or-later
#include "ui_u1_device_tools.h"
#include "ui_widget_ref.h"
#include "ui_ams_device_section_detail_overlay.h"
#include "ams_backend.h"
#include "ams_state.h"
#include "core/data_root_resolver.h"
#include "core/static_panel_registry.h"
#include "json_utils.h"
#include "material_workspace.h"
#include "overlay_base.h"
#include "ui_nav.h"
#include <algorithm>
#include <spdlog/fmt/fmt.h>
#include <string>

namespace helix::ui {
namespace {
using json = nlohmann::json;
class U1Tools : public OverlayBase {
  public:
    HELIX_KIND_TAG(U1Tools)
    ~U1Tools() override { deinit_subjects_base(subjects_); }
    const char* get_name() const override { return "U1 device tools"; }
    const char* xml_component() const override { return "u1_device_tools"; }
    void init_subjects() override {
        init_subjects_guarded([&] {
            UI_MANAGED_SUBJECT_STRING(status_, status_buf_, "", "u1_tools_status", subjects_);
            UI_MANAGED_SUBJECT_STRING(ai_, ai_buf_, "", "u1_tools_ai", subjects_);
            UI_MANAGED_SUBJECT_STRING(consent_, consent_buf_, "", "u1_tools_consent", subjects_);
            UI_MANAGED_SUBJECT_INT(can_submit_, 0, "u1_tools_can_submit", subjects_);
            UI_MANAGED_SUBJECT_INT(pending_, 0, "u1_tools_pending", subjects_);
        });
    }
    lv_obj_t* create(lv_obj_t* parent) override {
        lv_xml_register_component_from_file(asset_component_uri("ui_xml/u1_device_tools.xml").c_str());
        return OverlayBase::create(parent);
    }
    void open() {
        init_subjects();
        ++generation_;
        set_pending(false);
        config_ = json::object();
        types_ = json::array();
        consent_text();
        show(lv_screen_active());
        act("read_ai");
    }
    void act(const std::string& op) {
        if (op == "back") { ++generation_; helix::nav::go_back(); return; }
        if (busy_) return;
        if (op == "heads") {
            auto* backend = AmsState::instance().get_backend();
            const auto sections = backend ? backend->get_device_sections() : std::vector<printer::DeviceSection>{};
            if (std::any_of(sections.begin(), sections.end(), [](const auto& s){return s.id=="toolhead";})) {
                get_ams_device_section_detail_overlay().show(lv_screen_active(),"toolhead","四喷头直径与流量");
            } else status("当前设备尚未报告喷嘴直径或流量属性");
            return;
        }
        if (op == "pick_noodle" || op == "pick_item") {
            const std::string key = op.substr(5);
            const auto it = std::find(types_.begin(),types_.end(),json(key));
            if (it == types_.end()) types_.push_back(key); else types_.erase(it);
            consent_text(); return;
        }
        std::string path = "/oem/test";
        json data = {{"kind",op}};
        bool post = true;
        if (op == "read_ai") { path = "/oem/ai"; post = false; }
        else if (op == "read_report") { path = "/oem/report"; post = false; }
        else if (op == "report") {
            if (types_.empty()) return;
            path = "/oem/report"; data = {{"types",types_},{"consent",true}};
            types_ = json::array(); consent_text();
        } else if (op.rfind("ai:",0) == 0) {
            const auto key = op.substr(3);
            if (!config_.contains(key)) { status("该项尚未从设备读回"); return; }
            const auto& val = config_.at(key);
            path = "/oem/ai";
            if (val.is_boolean()) data = {{key,!val.get<bool>()}};
            else if (val == "high" || val == "low") data = {{key,val == "high" ? "low" : "high"}};
            else { status("设置值未知"); return; }
        } else if (op == "wifi_enable" || op == "wifi_disable") {
            data["confirmed"] = true;
        } else if (op.rfind("rfid",0) == 0 && op.size() == 5) {
            data = {{"kind","rfid"},{"channel",op.back()-'0'}};
        }
        const auto stamp = generation_;
        const auto lifetime = object_lifetime_.token();
        set_pending(true); status("正在等待设备回复…");
        material_workspace::request(path,data,[this,lifetime,stamp,path,post](json result,std::string error) {
            if (lifetime.expired() || stamp != generation_) return;
            set_pending(false);
            if (!error.empty()) { status(error); return; }
            if (path == "/oem/ai") {
                config_ = result.value("config",json::object());
                std::string summary;
                for (const auto& item : {std::pair{"main_enable","总开关"}, {"clean_bed_enable","异物"},
                                          {"noodle_enable","意面"},{"residue_enable","喷嘴积料"}}) {
                    if (!summary.empty()) summary += " · ";
                    summary += std::string(item.second) + " " + (!config_.contains(item.first) ? "—" :
                        config_[item.first] == true ? "开" : "关");
                    const auto key = std::string(item.first).substr(0,std::string(item.first).find("_enable"))+"_sensitivity";
                    if (config_.contains(key)) summary += config_[key]=="high" ? " / 高" : " / 低";
                }
                lv_subject_copy_string(&ai_,summary.c_str());
                status(config_.empty() ? "未收到 AI 状态" : post ? "设备已读回新设置" : "已读取 AI 配置");
            } else status(json_util::safe_string(result,"message","设备未返回本次结果"));
        },post);
    }
  private:
    void set_pending(bool value) { busy_=value; lv_subject_set_int(&pending_,value); }
    void status(const std::string& text) { lv_subject_copy_string(&status_,text.c_str()); }
    void consent_text() {
        std::string text;
        for (const auto& entry : types_) {
            if (!text.empty()) text += "、";
            text += entry == "noodle" ? "意面检测照片" : "异物检测照片";
        }
        lv_subject_copy_string(&consent_,text.empty() ? "选择本次反馈照片类型" :
            ("将发送至 Snapmaker：设备基本信息、"+text).c_str());
        lv_subject_set_int(&can_submit_,!text.empty());
    }
    SubjectManager subjects_;
    lv_subject_t ai_{},status_{},pending_{},can_submit_{},consent_{};
    char ai_buf_[384]{},status_buf_[512]{},consent_buf_[256]{};
    unsigned generation_ = 0;
    bool busy_ = false;
    json config_ = json::object(), types_ = json::array();
};
U1Tools& device_tools() { return helix::lazy_global<U1Tools>("U1Tools"); }
} // namespace
} // namespace helix::ui
void register_u1_device_tools() {
    lv_xml_register_event_cb(nullptr,"on_u1_print_prefs",[](lv_event_t* event){
        auto* origin = static_cast<lv_obj_t*>(lv_event_get_current_target(event));
        const auto call = helix::ui::WeakWidget(origin);
        helix::material_workspace::request("/oem/storage",{},[call](auto data,std::string error){
            if (!call.get() || !lv_obj_is_visible(call.get())) return;
            std::string title = "逐头设置 · 本机空间未读到";
            if (error.empty() && data.contains("free_bytes") && data["free_bytes"].is_number_unsigned()) {
                const auto bytes = data["free_bytes"].template get<uint64_t>();
                const auto mib = bytes / (1024 * 1024);
                title = mib >= 1024 ? fmt::format("逐头设置 · 本机剩余 {:.1f} GiB", mib / 1024.0) :
                        mib ? fmt::format("逐头设置 · 本机剩余 {} MiB",mib) :
                              fmt::format("逐头设置 · 本机剩余 {} KiB",bytes / 1024);
            }
            helix::ui::get_ams_device_section_detail_overlay().show(lv_screen_active(),"print_prefs",title);
        });
    });
    lv_xml_register_event_cb(nullptr,"on_u1_device_tools",[](lv_event_t*){helix::ui::device_tools().open();});
    lv_xml_register_event_cb(nullptr,"u1_device_action",[](lv_event_t* e){
        const auto* op = static_cast<const char*>(lv_event_get_user_data(e));
        if (!op) return;
        const std::string action(op);
        if (action != "wifi_enable" && action != "wifi_disable") { helix::ui::device_tools().act(action); return; }
        auto* box = lv_msgbox_create(nullptr);
        lv_msgbox_add_title(box,"Wi-Fi 兼容模式");
        lv_msgbox_add_text(box,"切换模式会暂时断开 Wi-Fi，确认继续？");
        auto* no = lv_msgbox_add_footer_button(box,"取消");
        auto* yes = lv_msgbox_add_footer_button(box,"确认");
        lv_obj_set_name(no,"u1_wifi_cancel");
        lv_obj_set_name(yes,"u1_wifi_confirm");
        lv_obj_add_event_cb(no,[](lv_event_t* ev){lv_msgbox_close(static_cast<lv_obj_t*>(lv_event_get_user_data(ev)));},LV_EVENT_CLICKED,box);
        lv_obj_set_user_data(yes,const_cast<char*>(action == "wifi_enable" ? "wifi_enable" : "wifi_disable"));
        lv_obj_add_event_cb(yes,[](lv_event_t* ev){
            auto* btn = static_cast<lv_obj_t*>(lv_event_get_current_target(ev));
            helix::ui::device_tools().act(static_cast<const char*>(lv_obj_get_user_data(btn)));
            lv_msgbox_close(static_cast<lv_obj_t*>(lv_event_get_user_data(ev)));
        },LV_EVENT_CLICKED,box);
    });
}
