# U1 原生 UI：UI.4 当前交付文件

当前完整固件版本：`2.1.0-233-dev.3-ui.4`。日期：2026-11-10（北京时间）。源码分支：`codex/u1-native-ui-20261110`。

UI.4 在 UI.3 的升级互斥、字体补丁链、回环代理及系统槽位回退修复之上，完成白黄主题的原生全界面改版。对应实现和截图见 [全界面改版记录](native-ui-full-redesign-20261110.md)。

## 下载

[GitHub 预发布版本及发布状态](https://github.com/446599/snapfxxker-os/releases/tag/v2.1.0-233-dev.3-ui.4)。该仓库为私有仓库，下载需登录有访问权限的 GitHub 账号。

| 文件 | 字节数 | 用途 |
| --- | ---: | --- |
| [U1_233_2.1.0-233-dev.3-ui.4_upgrade.bin](https://github.com/446599/snapfxxker-os/releases/download/v2.1.0-233-dev.3-ui.4/U1_233_2.1.0-233-dev.3-ui.4_upgrade.bin) | 286,833,178 | 完整 U1 整机升级包 |
| [U1-native-ui-20261110.4-runtime.zip](https://github.com/446599/snapfxxker-os/releases/download/v2.1.0-233-dev.3-ui.4/U1-native-ui-20261110.4-runtime.zip) | 33,490,267 | ARM64 运行归档及相邻清单，用于源码构建导入 |
| [U1_233_2.1.0-233-dev.3-ui.4-verification.zip](https://github.com/446599/snapfxxker-os/releases/download/v2.1.0-233-dev.3-ui.4/U1_233_2.1.0-233-dev.3-ui.4-verification.zip) | 3,923,222 | 测试、来源、固件校验和回退探针记录 |
| [U1-native-ui-20261110.4-previews.zip](https://github.com/446599/snapfxxker-os/releases/download/v2.1.0-233-dev.3-ui.4/U1-native-ui-20261110.4-previews.zip) | 173,080 | 六张 480×320 原生模拟预览 |
| [SHA256SUMS-ui.4.txt](https://github.com/446599/snapfxxker-os/releases/download/v2.1.0-233-dev.3-ui.4/SHA256SUMS-ui.4.txt) | 422 | 其余四个附件的 SHA-256 校验和 |

## SHA-256

```text
fbad25547ebe72fb82e1a8dc7876f2466ed161e055a50d88a2b6998fa73f2f00  U1_233_2.1.0-233-dev.3-ui.4_upgrade.bin
11e48d1fcf82445217bbd11af451241731e4fcc701e2cd2ae9e9fdbd26e44371  U1-native-ui-20261110.4-runtime.zip
06dcb84eedb02953cbad0372ef5dfb7c7a7ad993f375ae0077b683c090636e15  U1_233_2.1.0-233-dev.3-ui.4-verification.zip
7fa32bf3705fee1242961918d0f9793dadc59cb688615b2fba62288fdae6b31e  U1-native-ui-20261110.4-previews.zip
549d50bac5d45a8364731d757fd977162240dcf6afcd3a0d6a890d96afaf244f  SHA256SUMS-ui.4.txt
```

## 版本与构建来源

| 项目 | 固定值 |
| --- | --- |
| 固件版本 | `2.1.0-233-dev.3-ui.4` |
| 运行包标识 | `233-native-ui-20261110.4` |
| 运行包安装版本 | `1.2.0-233.20261110.4` |
| HelixScreen 基线 | `cf2e80ab6ccbf44b5ee389ffd0d68b4858ea9fd1` + 6 个顺序源码补丁 |
| multiACE | 保留 `1.27b-233.20261109` managed 包及固定 provider 提交 |
| 整包输入 | 原始 rsyncfix 基线，依次应用 `233-dev.2-perms.1` 与 `233-native-ui` overlay |

构建输入指纹：`25c261bdc873d453ccdda7a3cdec4a2a55e565e96f8dbe2c2cb6dab5fbf95be5`。

第六补丁保留未压缩的原始构建输入；SHA-256 为 `c703289246481379c1ed9a9c7d02188c7275d634f6c0b7ff75b40a92b2ced441`。前五个补丁保持原样。

运行 ZIP 内 `helixscreen-233-native-ui-20261110.4.tar.gz` 为 33,286,246 字节，SHA-256 `115e1ff059d65646b454976d35daa5dbcf592685d92c094f44848f6990880cc6`。其逐文件清单与仓库 `bundles/native-ui/runtime-files.json` 一致；运行归档通过 Release 下载和导入，不作为普通 Git 大文件提交。

## 已完成的离线验证

| 检查 | 结果 |
| --- | --- |
| Python 源码集成及设备服务 | 39 + 39 = 78 项通过 |
| 选定的原生测试 | 108 项、799 个断言通过 |
| 补丁链 | 6 个补丁全部应用；388 个变更文件与冻结源码一致 |
| 布局 | 485 个 XML 文件检查通过 |
| 字体 | 35 个字体文件重建；所需 1,368 个码点没有缺失 |
| ARM64 运行包 | 架构、逐文件哈希、权限和构建指纹通过 |
| 完整固件 | UPFILE、RKFW、RKAF 校验、版本、分区容量、运行包与服务 pin 通过 |
| 槽位回退 | 10 步源码加载探针通过，原始记录随验证包提供 |

完整固件 SHA-256 与上述交付清单一致。rootfs 为 247,259,136 字节，每个系统槽容量 314,572,800 字节；rootfs SHA-256 为 `625a065c5f7bb8c8acb6817b951d5686a7435f2aa9fd49ec25ba5bf70b9b456f`。MCU、硬件标识、分区映射与非 rootfs 分区均与基线一致。

**这是离线验证的预发布版。本轮没有连接、重启或刷写打印机；真实启动、触摸显示、运动、加热、风扇和 ACE 硬件动作尚未验收。** 预览图是原生程序在模拟后端下的 480×320 绘制结果。

## 导入对应运行包

解压运行 ZIP，保持 `.tar.gz` 与 `.tar.gz.manifest.json` 相邻，在包含本次修订的源码 checkout 中执行：

```sh
python3 scripts/native-ui/pin_runtime.py --runtime /path/to/helixscreen-233-native-ui-20261110.4.tar.gz
python3 scripts/native-ui/validate.py
./build-233.sh --plan
```

详细源码重建与整机打包步骤见 [构建说明](native-ui.md)；原问题见 [槽位回退记录](native-ui-slot-rollback-20261110.md) 与 [原三项修复记录](native-ui-review-fixes-20261110.md)。
