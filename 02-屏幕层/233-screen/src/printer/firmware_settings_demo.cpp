// SPDX-License-Identifier: GPL-3.0-or-later
#include "firmware_settings_demo.h"

#include "hv/json.hpp"
#include "json_utils.h"

#include <mutex>
#include <string>
#include <utility>

namespace helix {
namespace {

using nlohmann::json;

json option(const char* value, const char* label, const char* original,
            const char* confirmation = "", json inputs = json::array()) {
    return {{"value", value}, {"label", label}, {"source_label", original},
            {"confirm", confirmation}, {"hidden", false}, {"inputs", std::move(inputs)}};
}

json toggles(const char* confirmation = "", bool bool_values = false) {
    return json::array({
        option(bool_values ? "false" : "disabled", "关闭", "Disabled"),
        option(bool_values ? "true" : "enabled", "启用", "Enabled", confirmation),
    });
}

json setting(const char* id, const char* group, const char* original_group,
             const char* label, const char* original, const char* current,
             const char* description, json options) {
    return {{"id", id}, {"group", group}, {"source_group", original_group},
            {"label", label}, {"source_label", original}, {"current", current},
            {"description", std::string("模拟设备 · ") + description},
            {"help_url", ""}, {"hidden", false}, {"requires_idle", true},
            {"options", std::move(options)}};
}

json make_catalog() {
    json catalog = {
        {"available", true}, {"source", "paxx"}, {"mock", true},
        {"message", "模拟设备 · 这些数值仅供界面演示，不代表真实打印机"},
        {"groups", json::array({
            {{"id", "printing"}, {"label", "打印与调平"}},
            {{"id", "materials"}, {"label", "耗材与识别"}},
            {{"id", "camera"}, {"label", "摄像与录像"}},
            {{"id", "motion"}, {"label", "运动与电机"}},
            {{"id", "remote"}, {"label", "网络与远程"}},
            {{"id", "interface"}, {"label", "界面与扩展"}},
            {{"id", "maintenance"}, {"label", "维护与诊断"}},
            {{"id", "updates"}, {"label", "固件更新"}},
        })},
        {"settings", json::array()},
    };
    auto& items = catalog["settings"];
    items.push_back(setting("bed_leveling_force", "printing", "print_preferences",
        "每次打印前调平", "Force Bed Mesh Calibration", "disabled",
        "选择全床或自适应调平；自适应需要切片对象边界。", json::array({
            option("disabled", "关闭", "Disabled"),
            option("force", "每次全床调平", "Force Full Mesh", "每次打印前执行全床调平？"),
            option("adaptive", "每次自适应调平", "Force Adaptive Mesh",
                   "自适应调平需要对象边界；没有边界时回退全床调平。"),
        })));
    items.push_back(setting("timelapse_force", "printing", "print_preferences",
        "强制延时摄影", "Force Timelapse", "disabled",
        "切片仍需包含延时摄影开始、拍照和结束指令。", toggles("启用强制延时摄影？")));
    items.push_back(setting("object_processing", "printing", "print_preferences",
        "模型对象处理", "Object Processing for Adaptive Mesh", "disabled",
        "优先在切片软件生成对象信息；服务端处理大文件可能较慢。",
        toggles("开启对象处理会重启 Moonraker，并可能延长大文件处理时间。")));

    items.push_back(setting("ace_auto_insert_rfid", "materials", "multiace",
        "插料时自动读取 RFID", "Automatic insertion RFID", "1",
        "关闭后插料 400 mm；手动读取 RFID 仍可用。只对下次插料生效。\nACE 1：已开启  ·  ACE 2：已开启  ·  ACE 3：已开启",
        json::array({option("1", "开启", "Enabled", "下次插料生效，保留手动读取。"),
                     option("0", "关闭 · 插料 400 mm", "Disabled", "下次插料生效，保留手动读取。")})));
    items.push_back(setting("rfid", "materials", "snapmaker_components",
        "耗材标签识别", "Filament Detection", "openrfid",
        "选择标签识别方式；品牌标签支持取决于实际配置。", json::array({
            option("snapmaker", "Snapmaker 原厂", "Snapmaker"),
            option("external", "外部读卡器", "External"),
            option("openrfid", "OpenRFID", "OpenRFID",
                   "请阅读标签支持说明；部分品牌需要额外配置。"),
            option("openrfid-generic", "OpenRFID（通用品牌）", "OpenRFID (force generic vendor)",
                   "将耗材品牌映射为通用品牌，标签兼容性仍取决于配置。"),
        })));
    const json spoolman_input = json::array({
        {{"name", "SPOOLMAN_URL"}, {"label", "Spoolman 服务器地址"},
         {"source_label", "Spoolman URL"}, {"type", "text"}, {"required", true},
         {"placeholder", "http://192.168.1.100:7912"}, {"regex", "^https?://.+"}},
    });
    items.push_back(setting("spoolman", "materials", "snapmaker_components",
        "Spoolman 耗材库", "Spoolman Integration", "disabled",
        "填写耗材库地址；演示模式不连接服务器。", json::array({
            option("disabled", "关闭", "Disabled"),
            option("enabled", "启用", "Enabled", "连接此 Spoolman 耗材库？", spoolman_input),
            option("force_generic", "启用（通用品牌）", "Enabled (Force Generic Vendor)",
                   "启用耗材库，并把非原厂品牌映射为通用品牌？", spoolman_input),
        })));
    items.push_back(setting("filament_feeders", "materials", "snapmaker_components",
        "送料器配置", "Filament Feeders", "default",
        "送料长度应匹配实际安装的送料器和料路。", json::array({
            option("default", "默认送料器", "Default"),
            option("popstation_mini", "BIQU PopStation mini", "BIQU PopStation mini",
                   "仅安装对应硬件时使用，此选项会增加预送料长度。"),
        })));
    items.push_back(setting("afc", "materials", "snapmaker_components",
        "AFC Lite 耗材管理", "AFC Lite via Fluidd/Mainsail", "disabled",
        "为兼容的网页提供 AFC Lite 耗材控制。", toggles("启用 AFC Lite 兼容组件？")));
    items.push_back(setting("ace", "materials", "snapmaker_components",
        "multiACE 集成", "multiACE Integration", "multiace",
        "模拟已安装组件；实际可用性由固件 profile 决定。", json::array({
            option("none", "关闭", "Disabled", "关闭托管组件会在实际设备上重启并保留配置。"),
            option("multiace", "启用", "Enabled", "实际设备需要匹配的固件与 ACE 硬件。"),
        })));

    items.push_back(setting("camera-internal", "camera", "camera",
        "内置摄像头", "Internal Camera", "paxx12", "选择内置摄像头服务。", json::array({
            option("paxx12", "Paxx12", "Paxx12"),
            option("snapmaker", "Snapmaker 原厂", "Snapmaker"),
            option("none", "关闭", "Disabled"),
        })));
    items.push_back(setting("camera-internal-resolution", "camera", "camera",
        "内置摄像头帧率", "Internal Camera Resolution", "1080p25",
        "内置画面固定 1080p，可调整帧率。", json::array({
            option("1080p30", "1080p · 30 fps", "1920x1080 @ 30fps"),
            option("1080p25", "1080p · 25 fps", "1920x1080 @ 25fps"),
            option("1080p15", "1080p · 15 fps", "1920x1080 @ 15fps"),
            option("1080p5", "1080p · 5 fps", "1920x1080 @ 5fps"),
        })));
    items.push_back(setting("camera-usb", "camera", "camera",
        "USB 摄像头", "USB Camera", "none", "使用外接 USB 摄像头。", json::array({
            option("none", "关闭", "Disabled"), option("paxx12", "Paxx12", "Paxx12"),
        })));
    items.push_back(setting("camera-usb-resolution", "camera", "camera",
        "USB 摄像头画质", "USB Camera Resolution", "1080p25",
        "演示 USB 摄像头画质选项。", json::array({
            option("1080p30", "1080p · 30 fps", "1920x1080 @ 30fps"),
            option("1080p25", "1080p · 25 fps", "1920x1080 @ 25fps"),
            option("1080p5", "1080p · 5 fps", "1920x1080 @ 5fps"),
            option("720p30", "720p · 30 fps", "1280x720 @ 30fps"),
            option("720p25", "720p · 25 fps", "1280x720 @ 25fps"),
            option("720p5", "720p · 5 fps", "1280x720 @ 5fps"),
            option("360p30", "360p · 30 fps", "640x360 @ 30fps"),
            option("360p25", "360p · 25 fps", "640x360 @ 25fps"),
            option("360p5", "360p · 5 fps", "640x360 @ 5fps"),
        })));
    items.push_back(setting("camera-rtsp", "camera", "camera",
        "RTSP 视频流", "RTSP Stream (paxx12 only)", "false",
        "将摄像头画面提供给兼容的播放器。", toggles("", true)));

    items.push_back(setting("max_speed", "motion", "tweaks", "运动速度上限",
        "Max Speed", "disabled", "实验设置；不能同时启用降低 XY 电机电流。", json::array({
            option("disabled", "关闭", "Disabled"),
            option("balanced", "均衡（实验）", "Balanced",
                   "此模式提高运动上限并增加磨损，只应在校准正常的机器上试用。"),
            option("aggressive", "激进（实验）", "Aggressive",
                   "此模式明显提高运动上限，可能增加失步、碰撞与磨损风险。"),
        })));
    items.push_back(setting("tmc_autotune", "motion", "tweaks",
        "TMC 驱动调优", "TMC AutoTune", "disabled",
        "实验性驱动参数；启用后需要观察电机温度与打印质量。",
        toggles("启用 TMC AutoTune？首次使用应观察电机与打印状态。")));
    items.push_back(setting("tmc_reduce_current", "motion", "tweaks",
        "降低 XY 电机电流", "TMC Reduced Current", "disabled",
        "降低电流可能减少发热，也可能降低大负载时的稳定性。",
        toggles("降低 XY 电机电流？不能与提高运动速度上限同时启用。")));

    items.push_back(setting("ssh", "remote", "remote_access", "SSH 访问", "SSH Access",
        "true", "允许远程终端访问。", toggles("", true)));
    items.push_back(setting("vpn", "remote", "remote_access", "Tailscale 远程连接",
        "VPN Provider (Experimental)", "none", "真实设备启用后还需要完成账户登录。",
        json::array({option("none", "关闭", "None"), option("tailscale", "Tailscale", "Tailscale")})));
    items.push_back(setting("cloud", "remote", "remote_access", "云端远程访问",
        "Cloud Access (Experimental)", "none", "选择远程访问服务。", json::array({
            option("none", "关闭", "None"), option("octoeverywhere", "OctoEverywhere", "OctoEverywhere"),
        })));
    items.push_back(setting("remote-screen", "remote", "web", "远程屏幕", "Remote Screen",
        "false", "从网页查看和操作触摸屏。", toggles("", true)));
    items.push_back(setting("authorization", "remote", "remote_access", "网页登录验证",
        "Web Authentication (Experimental)", "disabled",
        "实际设备仅支持对应网页前端，生成的登录信息需保存。",
        toggles("实际设备会创建登录凭据；本次仅修改演示值。")));

    items.push_back(setting("web-frontend", "interface", "web", "网页界面", "Frontend",
        "fluidd", "选择设备的网页控制界面。", json::array({
            option("fluidd", "Fluidd", "Fluidd"), option("mainsail", "Mainsail", "Mainsail"),
        })));
    items.push_back(setting("gui", "interface", "snapmaker_components", "触摸屏界面",
        "Touchscreen GUI (Experimental)", "helixscreen", "模拟切换，不会更改当前演示程序。",
        json::array({
            option("snapmaker", "Snapmaker 原厂", "Snapmaker", "实际设备需要重启后切换屏幕界面。"),
            option("helixscreen", "HelixScreen", "HelixScreen", "实际设备需要下载组件并重启。"),
        })));
    items.push_back(setting("firmware-config", "interface", "web", "PAXX 配置服务",
        "Firmware Config", "true", "实际设备关闭后将无法继续访问此配置服务。", json::array({
            option("true", "启用", "Enabled"),
            option("false", "关闭", "Disabled", "真实设备关闭后需编辑配置文件重新启用。"),
        })));
    items.push_back(setting("klipper-metrics", "maintenance", "monitoring_debugging",
        "运行指标导出", "Klipper Metrics Exporter", "false",
        "向兼容的监测服务提供运行指标。", toggles("", true)));
    for (int index = 1; index <= 4; ++index) {
        const std::string id = "toolhead" + std::to_string(index);
        const std::string label = "工具头 " + std::to_string(index) + " 故障处理";
        const std::string original = "Toolhead " + std::to_string(index);
        items.push_back(setting(id.c_str(), "maintenance", "troubleshooting", label.c_str(),
            original.c_str(), "enabled", "维修用途；隔离后不可使用该工具头打印。", json::array({
                option("enabled", "正常", "Normal"),
                option("bypass_thermistor", "隔离故障温度传感器", "Bypass Thermistor",
                       "温度保护行为将改变；禁止加热或使用故障工具头打印。"),
                option("bypass_mcu", "隔离断开的工具头主板", "Bypass MCU (Disconnected)",
                       "仅用于主板故障或断开的工具头，它将不能加热、挤出或运动。"),
            })));
    }
    items.push_back(setting("firmware-upgrade-channel", "updates", "snapmaker_components",
        "固件更新通道", "Firmware Upgrade", "none", "模拟更新通道，不下载或刷写固件。",
        json::array({
            option("none", "关闭", "Disabled", "实际设备切换更新通道需要重启。"),
            option("stable", "稳定版", "Stable (recommended)", "实际设备切换更新通道需要重启。"),
            option("testing", "测试版", "Testing (pre-releases)", "测试版可能包含未稳定的功能。"),
            option("develop", "开发版", "Develop (bleeding edge)", "开发版用于测试；实际设备切换需要重启。"),
        })));
    return catalog;
}

json* find_setting(json& catalog, const std::string& id) {
    for (auto& item : catalog["settings"]) {
        if (json_util::safe_string(item, "id") == id) {
            return &item;
        }
    }
    return nullptr;
}

json fail(const std::string& message) {
    return {{"ok", false}, {"state", "failed"}, {"mock", true},
            {"error", message}, {"message", message},
            {"output", "模拟设备 · " + message + "\n未调用任何网络或设备。"}};
}

} // namespace

nlohmann::json firmware_settings_demo(const nlohmann::json& body, bool post) {
    static std::mutex mutex;
    static json catalog = make_catalog();
    std::lock_guard<std::mutex> lock(mutex);
    if (!post) {
        return catalog;
    }
    const std::string id = json_util::safe_string(body, "id");
    const std::string value = json_util::safe_string(body, "value");
    json* item = find_setting(catalog, id);
    if (!item) {
        return fail("演示设备不支持此设置");
    }
    const json* selected = nullptr;
    for (const auto& entry : (*item)["options"]) {
        if (json_util::safe_string(entry, "value") == value &&
            !json_util::safe_bool(entry, "hidden")) {
            selected = &entry;
            break;
        }
    }
    if (!selected) {
        return fail("请选择当前设置提供的选项");
    }
    if (!json_util::safe_string(*selected, "confirm").empty()) {
        const auto* confirmation = json_util::find_member(body, "confirm");
        if (!confirmation || !confirmation->is_boolean() || !json_util::as_bool(*confirmation)) {
            return fail("请先阅读并确认此选项的说明");
        }
    }
    const auto* inputs = json_util::find_member(body, "inputs");
    if (inputs && !inputs->is_object()) {
        return fail("额外输入应为对象");
    }
    const auto* fields = json_util::find_member(*selected, "inputs");
    if (fields && fields->is_array()) {
        for (const auto& field : *fields) {
            const std::string name = json_util::safe_string(field, "name");
            const std::string text = inputs ? json_util::safe_string(*inputs, name.c_str()) : "";
            if (text.empty()) {
                return fail("请填写：" + json_util::safe_string(field, "label"));
            }
            if (name == "SPOOLMAN_URL" &&
                !((text.rfind("http://", 0) == 0 && text.size() > 7) ||
                  (text.rfind("https://", 0) == 0 && text.size() > 8))) {
                return fail("服务器地址需要以 http:// 或 https:// 开头");
            }
        }
    }
    if (inputs) {
        for (auto it = inputs->begin(); it != inputs->end(); ++it) {
            bool known = false;
            if (fields && fields->is_array()) {
                for (const auto& field : *fields) {
                    known = known || json_util::safe_string(field, "name") == it.key();
                }
            }
            if (!known) {
                return fail("此选项不接受额外字段：" + it.key());
            }
        }
    }
    const json* speed = find_setting(catalog, "max_speed");
    const json* current = find_setting(catalog, "tmc_reduce_current");
    if ((id == "max_speed" && value != "disabled" && current &&
         json_util::safe_string(*current, "current") == "enabled") ||
        (id == "tmc_reduce_current" && value == "enabled" && speed &&
         json_util::safe_string(*speed, "current") != "disabled")) {
        return fail("提高运动速度上限与降低 XY 电机电流不能同时启用，请先关闭另一项");
    }
    (*item)["current"] = value;
    return {{"ok", true}, {"state", "applied"}, {"mock", true}, {"id", id},
            {"current", value}, {"setting", *item},
            {"message", "演示值已更新"},
            {"output", "模拟设备 · 已更新内存中的演示值。\n没有连接设备、重启服务或写入固件。"}};
}

} // namespace helix
