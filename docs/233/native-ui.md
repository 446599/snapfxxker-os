# 233 原生 UI：源码集成与 ARM64 构建

本批是 `2.1.0-233-dev.3-ui.4` 的源码集成。它扩展现有 233 固件，不更改 HelixScreen 上游仓库，也不更换 multiACE v1.27b、离线 managed 包、喷头映射或现场耗材数据。源码补丁属于 233 固件仓库；已有 HelixScreen 应用标识仅用于复用系统启动和安装接口。

## 提交范围与当前状态

仓库记录固定的 HelixScreen 提交、顺序应用的文本补丁、Debian 交叉工具链清单、字体源哈希、设备服务、启动 overlay 和许可。完整上游源码、编译目录和 ARM64 归档不作为本批源码提交内容。当前分发包的 `runtime-files.json` 与来源清单一并提交，便于核对文件与构建输入。

当前修订的运行包、整包固件和实机验证状态分别记录，不能用上一版的启动记录代替。运行包 manifest 记录已分发的 `233-native-ui-20261110.4` 实际校验和及构建输入指纹；归档需按 [产物索引](native-ui-20261110-artifacts.md) 核对已交付文件后导入，或从相同源码重新构建；随包逐文件清单须与仓库记录一致。整机包的完成状态统一记录在 `233-firmware.json` 与产物索引，避免在运行包内预先记录尚未完成的外层固件状态。

`ui.3` 修复 `df0f5c3` 的三项审查问题：升级前的完整空闲检查与 Klipper 原子控制交接、字体二进制补丁预像、回环请求继承环境代理，见 [原修复记录](native-ui-review-fixes-20261110.md)。本次补上 `1fdd0ac` 的系统槽位回退修复：真实配置段与模块随 rootfs 生效，共享配置只保留允许空匹配的 include；实际固件抽取后的加载链验证见 [回退修复记录](native-ui-slot-rollback-20261110.md)。UI.4 在这些修复之上增加完整屏幕改版，见 [全界面改版记录](native-ui-full-redesign-20261110.md)。

## 本轮落实的界面与操作

`0004-confirmed-controls.patch` 将已确认的交互接到现有 LVGL / C++ 原生模块：

| 区域 | 当前实现 |
| --- | --- |
| 移动 | XY 内圈固定 1 mm、外圈固定 10 mm，中心归零；Z 模式独立；命中、长按与标签使用同一组步长。 |
| 打印速度 | 50%、100%、125%、150% 四档，使用既有速度控制接口；不提供速度滑条。 |
| 风扇 | 可控与自动风扇统一为两列卡片；可控项有独立 ±10% 按钮；自动项只读；未知速度显示 `--%`，不冒充 0%。 |
| ACE 耗材 | 实际装载的来源保持黄色高亮，候选选择单独描边；余量和手动识别保留在 ACE 流程。 |
| 无 ACE | 使用设备报告的四头并行供料状态，主页面、详情、编辑均隐藏余量；断线后清空实时信息并禁用操作。 |
| 喷头详情 | 左侧温度、材料和来源，右侧回抽/挤出 5 mm；左右仅浏览喷头，不隐式切换物理工具；返回耗材页同步浏览对象。 |
| 编辑与保存 | 外置卷先编辑草稿，取消无写入；保存检查并发变化，经设备回读确认后返回来源页；失败保留明确反馈。 |
| 排版 | 480×320 层级采用 16/18 px 主要文字；进度百分比单独放大；返回触区增高；首页热床与打印速度共享两行结构，热床仅显示当前值。 |

UI.4 耗材详情与手动工作台提供显式“激活此喷头”入口，沿用共享换头确认并等待设备回读。手动进退料继续检查当前工具、实际活动挤出机、该喷头温度与设备空闲条件。温度读数可进入原生数字键盘；实际绘制验证使用 480×320 LVGL 程序与隔离模拟后端，机上触摸和硬件动作单独验收。

相关原生回归标签为 `[native_controls]` 和 `[material-workspace]`。集成与来源验证位于 `tests/native-ui/`，设备服务测试与服务源码放在同一 overlay 目录。

## 专用 CI

[233 Native UI](../../.github/workflows/native_ui.yaml) 在 PR 中使用 amd64 Debian trixie 容器。步骤为：

1. 校验源补丁、设备服务、升级互斥模块与启动钩子、现有 multiACE 包及两个子模块的固定提交；先运行启动保留、陈旧包拒绝、补丁链、升级互斥与代理环境回归。
2. 还原固定的 HelixScreen 源码及子模块，依次应用当前 manifest 的全部补丁。
3. 下载并校验记录在 `cross-packages.json` 中的工具链与 ARM64 静态库；检查完整 XML 布局与 schema 同步，重建 35 个 CJK 字体文件。
4. 创建本次编译的源码和构建输入记录，再执行 U1 ARM64 原生编译。
5. 打包并校验 ARM64 ELF、XML 布局、字体、文件权限、逐文件哈希和来源指纹。
6. 仅在成功后上传 `233-native-ui-arm64-<提交>` artifact。日志和来源记录单独保存，便于检查失败原因。

工作流不创建 Release，不上传完整源码目录，不制作整包固件，也不连接打印机。源码或构建规则变化后必须重新运行；下载缓存仍逐个核对 SHA-256。

## 本地重建与导入

本地重建需要与工作流相同的 amd64 Debian trixie 用户空间、Git、Make、Python 3、Node/npm、autotools、CMake 等宿主工具。`build.sh` 会使用记录的交叉编译器和 ARM64 库，并拒绝复用非空输出目录：

```sh
python3 scripts/native-ui/validate.py --source-only
scripts/native-ui/build.sh /tmp/233-native-ui-build
```

产物是 `helixscreen-233-native-ui.tar.gz` 及相邻的 `helixscreen-233-native-ui.tar.gz.manifest.json`。也可以从成功的专用 CI 下载这两个文件；应选择对应当前源码补丁和构建规则的运行记录，保持两个文件相邻。

```sh
python3 scripts/native-ui/pin_runtime.py \
  --runtime /tmp/233-native-ui-build/helixscreen-233-native-ui.tar.gz
python3 scripts/native-ui/validate.py
./build-233.sh --plan
./build-233.sh
```

导入工具校验归档与文件清单、源码补丁和构建指纹，然后生成固件中的本地 package pin、版本文件及 manifest 镜像。运行归档与安装用派生文件继续由本地导入生成；当前分发包的逐文件清单单独记录在仓库中，普通源码提交不包含运行归档。输入清单能够拒绝意外使用旧构建；它不是第三方签名或硬件运行证明。

固件构建需要原有 `codex-233-overlay-tools:20261109` Docker 镜像与已固定 SHA-256 的 rsyncfix 基线，依次应用 `233-dev.2-perms.1`、`233-native-ui`。新的 overlay 脚本支持绝对路径。缺少运行包、输入指纹不一致、归档损坏、ARM 架构不对或 multiACE pin 变化时，入口在启动固件打包前退出；不会回退到旧 UI。

`build-233.sh` 将项目版本通过 `FIRMWARE_VERSION_OVERRIDE` 传给打包脚本，同时更新升级包头的 `UPFILE_VERSION`；它与原生 overlay 的 `/etc/FULLVERSION` 一致。通用打包入口未设置该变量时，继续沿用追加 Git 提交号的原有行为。

`validate.py --source-only --partial-tree` 仅用于临时稀疏目录中的脚本审查，会明确跳过 Git 子模块和既有 multiACE 归档检查。CI 和固件构建禁止用此模式替代完整校验。

## 运行集成

原生程序作为实际 ARM64 ELF 通过现有 lmd hook 绑定到屏幕启动路径，保留 lmd 的进程身份检查。设备服务由独立 init 脚本启动，只监听 `127.0.0.1:7136`，对接已有本地 multiACE 与固件设置接口。真实状态由已安装服务提供；源码中的模拟支持用于开发检查。

首次启用前先检查有效 ARM64 运行包，再备份与初始化界面配置。UI.4 只迁移与 UI.3 已发行默认首页完全一致的布局，替换为四头温度、任务卡、热床和速度；用户改过的布局保留，迁移前备份且重复启动不再重写。已有连接、触摸校准和自定义数据保留；后续启动尊重用户选择的界面及主题。原生界面设置位于 `/oem/printer_data/config/extended/helixscreen`；本 overlay 不替换 multiACE 的现场配置或映射文件。

## 交付界限

本轮的源码、原生绘制、ARM64 运行包和完整固件验证分别记录在 [UI.4 改版记录](native-ui-full-redesign-20261110.md) 与 [产物索引](native-ui-20261110-artifacts.md)。完整固件由原始 rsyncfix 基线依次应用两层 overlay；离线打包保留数值所有权、权限、符号链接和硬链接，并从最终包重新核对内容及回退加载链。构建脚本、探针和日志随验证归档保存。

这些结果不代替设备升级、真实启动、触摸显示、运动、加热、风扇或 ACE 硬件验收。本轮没有连接或刷写打印机；之前 `233-dev.2-perms.1` 的现场验证单独保留。

发布二进制时保留 HelixScreen 原始许可、233 补丁、对应工具链与依赖来源说明，以及 [第三方材料](../../bundles/native-ui/licenses/README.md)。特别是静态 libnl 使用记录的 Debian ARM64 3.7.0-2 provider，其对应源码不是上游树内的 libnl 3.9.0 快照。
