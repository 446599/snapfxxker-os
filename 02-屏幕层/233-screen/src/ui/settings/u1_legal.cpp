// SPDX-License-Identifier: GPL-3.0-or-later
#include "u1_legal.h"
#include "board_identity.h"
#include "platform_table.h"
#include "ui_info_qr_modal.h"
#include "ui_modal.h"

#include <cstdlib>
#include <string_view>

namespace helix::ui {
bool is_u1_legal_device() {
    const char* mock = std::getenv("HELIX_MOCK_PRINTER");
    return helix::board::current() == helix::board::Board::SnapmakerU1 ||
           helix::platform::current_key() == "snapmaker-u1" || (mock && std::string_view(mock) == "snapmaker_u1");
}

void show_u1_terms() {
    // The stock U1 Guide Terms controller chooses the QR payload by sales region.
    auto qr = [](bool china) {
        const char* url = china ? "https://www.snapmaker.cn/device/qr-code" : "http://www.snapmaker.com/device/qr-code";
        InfoQrModal::show_owned({"qrcode", "使用条款与隐私政策", "请用手机扫描原厂二维码查看完整条款。", url, url});
    };
    ConfirmOptions options;
    options.cancel_text = "国际服务区";
    options.on_cancel = [qr] { qr(false); };
    modal_confirm("使用条款与隐私政策", "请选择设备所属的服务地区。", ModalSeverity::Info,
                  "中国大陆", [qr] { qr(true); }, options);
}
} // namespace helix::ui
