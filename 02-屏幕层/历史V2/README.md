# U1 机器端界面

原生 LVGL 触屏实现，分辨率为 480×320。项目位于 `helixscreen`，分支为 `codex/u1-native-filament-ui`，上游基线为 `cf2e80ab6ccbf44b5ee389ffd0d68b4858ea9fd1`。

## 已实现

- multi 模式按喷头查看各 ACE 的同号槽位，支持 1 至 4 台 ACE。
- 当前供料从引擎 `head_source` 读取，点击候选耗材单独切换查看状态。
- 每台 ACE 的温度、湿度和离线状态。
- 每卷耗材的颜色、材质、估算克数与竖向余量。比例采用 `weight_g / weight_initial_g`，未知数量显示未知。
- 底部独立居中的 U1 正面 2D 喷头。
- 左侧耗材导航直接进入自定义料路页。
- 新的耗材操作页，提供装料、换料、退料、槽位编辑与可见的烘干入口。
- 本地耗材库的新建、编辑、颜色与余量修正，线盘绑定与解绑，以及 Spoolman 同步。
- RFID 读取、OpenSpool 写卡与 PA 标定入口。
- 自动烘干温度、启停湿度与跟随策略，自动续料、PA 同步、空打检测、取头清洁和冲刷矩阵设置。
- 设置中提供独立的本地升级入口。
- 本机 U1 完整固件包的本地选择、完整性校验、升级确认、执行日志与重启后版本核对。
- 480×320 的正文为 16 像素，辅助文字为 14 像素，标题为 20 像素。
- 位置待确认提示、动作拒绝和待执行进料取消。
- U1 Warm 浅色与深色主题。

## 本地预览

在本目录运行 `./preview-machine.sh 4`。最后一个参数为 ACE 数量。预览使用独立配置与模拟打印机。

进入耗材页可点击首页耗材组件，也可在另一个终端执行以下命令。

```sh
cd helixscreen
./build/bin/helix-screen ctl -s /tmp/helix-u1-native-preview.sock navigate filament
```

## 构建

macOS 使用 GNU Make。主机模拟器命令为 `gmake SKIP_OPTIONAL_DEPS=1`，U1 构建命令为 `gmake snapmaker-u1-docker`。子模块需要先运行 `git submodule update --init --recursive`。

同目录切换主机与交叉构建后，libhv 的生成配置需要随目标刷新。返回 macOS 前先执行 `gmake libhv-build`。返回 ARM 构建前，在 Docker 工具链中先执行 `make PLATFORM_TARGET=snapmaker-u1 libhv-build`，再进行常规构建。

U1 目标采用 ARM64、DRM 显示与 evdev 触摸输入，运行库采用静态链接。目标产物位于 `helixscreen/build/snapmaker-u1/bin/helix-screen`。

## 机器接入

本机 233 固件已有 lmd 的 HelixScreen 集成钩子，使用 `/oem/apps/helixscreen/latest` 的二进制与资源。安装时需沿用该钩子，并保留当前配置。

机器运行参数包含 `HELIX_DATA_DIR`、`HELIX_SUPERVISED=1`、`HELIX_DRM_DEVICE=/dev/dri/card0`、`HELIX_CONFIG_DIR` 和 `HELIX_REMOTE_SCREEN_FB0=/dev/fb0`。资源包需与二进制配套。

启用本版视觉时，配置 `/language` 为 `zh`、`/dark_mode` 为 `false`、`/display/theme` 为 `u1-warm`。中文语言选项会加载配套的中文字库。

本轮交付包含本地模拟与构建验证。真机安装、DRM 显示、触摸和实际状态同步仍待设备测试窗口验收，安装前需与设备测试任务协调。

新增耗材页只在有效的 multiACE API 1 且 multi 模式下成为默认入口。其余模式使用框架已有的页面。


## V2 接入范围

新增的耗材库、设置与本地升级需要运行配套的 [本机服务](device-service/README.md)。该服务包含在 V2 安装包中。升级范围为用户选择的本机 U1 打印机。

网页版的完整配置编辑器、耗材库导入导出、Spoolman 服务器配置、路径校准向导、预检和第三方插件页面仍由网页版提供。机器端当前覆盖日常耗材管理与上述设置入口。

本地验证包括原生编译、ARM64 交叉编译、隔离模拟器界面流转与升级服务测试。实机显示、触摸、联机操作和真实升级需要设备验收。
