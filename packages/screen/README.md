# 第 2 层：屏幕包

本目录是现有屏幕构建流程的统一入口。屏幕负责显示真实状态、收集用户操作和调用设备接口；进料、退料、换色、停止与恢复状态机由 `packages/multiace/runtime/klipper/extras` 负责。不要在屏幕端复制一套送料状态机，也不要因界面刷新或页面切换发起额外送料。

现有源头保持不变：

| 内容 | 实际位置 |
| --- | --- |
| 固定 HelixScreen 版本、6 个基础补丁与字体来源 | `bundles/native-ui/manifest.json` |
| UI.5 重建补丁顺序 | `bundles/native-ui/reconstruction/series.json` |
| 实际 ARM64 编译 | `scripts/native-ui/build.sh` |
| ARM 运行包和编译来源核对 | `scripts/native-ui/package_runtime.py` |
| 设备服务与启动适配 | `overlays/233-native-ui` |

`build.py` 只封装这些现有入口，不创建第二套编译系统。本次目录整理没有重新编译屏幕二进制，不能把历史 UI.8 运行包称为本轮新构建。

## 查看计划

在仓库根目录运行：

```sh
python3 packages/screen/build.py --plan
python3 packages/screen/build.py --source-only --plan
```

这两个命令只显示输入、输出和实际构建命令，不下载源码、不启动编译，也不生成文件。

## 当前可直接生成的源码输入包

```sh
python3 packages/screen/build.py --source-only --output-dir /tmp/snapfxxker-screen-source
```

输出 `snapfxxker-screen-source-20261120.tar.gz` 及相邻 `.manifest.json`。归档根目录是 `screen-source/`，包含屏幕补丁、固定版本和依赖清单、构建脚本、设备服务源码、启动 overlay、许可及本目录说明。归档内的 `PACKAGE.json` 和外部清单记录逐文件 SHA-256；时间戳、排序与权限采用固定写法，同一输入可重复生成相同归档。

**这是源码与构建输入包，里面没有新的 ARM 可执行程序。** 固定的 HelixScreen 上游完整源码、Git 子模块、CJK 源字体、Debian 交叉编译依赖和 npm 依赖仍由原构建脚本获取。归档保留它们的版本与校验清单，不声称已经包含全部离线编译依赖。

源码模式沿用原 `build_inputs.py` 和重建补丁校验检查本地输入。缺少补丁或设备服务源码会直接说明缺失文件。它不要求提供另一层的 multiACE 历史运行归档，也不会把旧屏幕二进制加入源码包。

## 构建真正的 ARM64 屏幕包

在已有文档指定的 amd64 Debian trixie 构建环境中运行：

```sh
python3 packages/screen/build.py --output-dir /tmp/snapfxxker-screen-arm64
```

该命令实际执行：

```sh
bash scripts/native-ui/build.sh /tmp/snapfxxker-screen-arm64/native-build
```

`native-build` 必须是新的空目录。原脚本负责固定源码和依赖的获取、补丁应用、XML 检查、CJK 字体生成、ARM64 编译和 `package_runtime.py` 打包。缺少主机工具、源码或工具链，或者获取依赖失败时，原错误向上返回；不会改用旧二进制。

成功后输出 `snapfxxker-screen-arm64-20261120.tar.gz` 和清单。归档内有：

- `runtime/helixscreen-233-native-ui.tar.gz`：原打包器刚生成的 ARM64 屏幕运行包。
- `runtime/helixscreen-233-native-ui.tar.gz.manifest.json`：相应文件清单和本次编译来源记录。
- `overlays/233-native-ui/`：配套屏幕启动、界面配置和设备服务源码。
- 相同的构建输入、补丁和许可，供后续维护与重建。

外层归档没有整机分区、原厂固件或者 ACE MCU 镜像，**不能作为 `.bin` 直接刷机**。

## 安装配套与职责

内层 `helixscreen` 归档只包含屏幕程序及其运行资源，不能单独替代系统中的启动和设备服务配置。已有启动适配包括：

- `S57u1-native-ui` 和 `u1-native-ui-setup.py`：准备持久化界面配置。
- `S58u1-device-service`：启动本地设备适配服务，沿用 `127.0.0.1:7136`。
- `etc/hooks/lmd.d/30-helixscreen.sh`：沿用 lmd 的屏幕启动入口。
- Klipper/Moonraker 升级接口和配置段：沿用既有随系统槽位加载的设计。

这些文件依赖现有 PAXX 基础服务；`pre-scripts/10-check-base.sh` 保留了此前已核对基线的要求。本目录不提供绕过基线检查的安装脚本。

向完整固件整合时，先在对应源码仓库中使用原导入工具生成与新运行包匹配的离线包描述，再由第 1 层固件整合已有 overlay：

```sh
python3 scripts/native-ui/pin_runtime.py \
  --runtime /tmp/snapfxxker-screen-arm64/native-build/helixscreen-233-native-ui.tar.gz
```

历史运行包清单、旧安装包描述、旧 `/etc/FULLVERSION` 和已生成的固件共享目录不作为本目录归档输入。导入和安装配套必须使用新构建生成的记录；本次 `--source-only` 打包不意味着已经完成 ARM 编译、安装、触摸显示或真机验证。

## 尚未激活的现场屏幕扩展

`reference/insert_rfid_settings.py` 来源于 Flow.15 现场交接候选，原路径和 SHA-256 记录在 `reference/provenance.json`。它属于屏幕高级设置适配，不属于 multiACE 电机控制运行包。

该文件目前**没有接入当前 overlay 的设备服务，也不会由此包自动安装或导入**。保存它是为后续明确对接参考；不能仅因文件出现在源码包就宣称屏幕已支持相关高级开关。后续接入时先确认现有接口和用户流程，实际送料决策继续放在 multiACE。
