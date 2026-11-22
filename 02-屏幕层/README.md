# 02 屏幕层

- [当前维护的 233 screen](233-screen/)：`codex/u1-ace-head-unload-20261120`，整理时提交 `33b76d35c1d48db91796d568f26f10f4c1873df7`。以后改当前屏幕界面从这里进。
- [屏幕重构配套服务](device-service/)：与重构工作树配套的 Python 服务和测试。233 集成包里的服务另见 [系统 overlay](../01-原厂固件/233-系统集成/overlays/233-native-ui/root/usr/local/lib/u1-device-service/)，两者不要视为同一版本。
- [旧 V2 屏幕](历史V2/README.md)：旧原生界面、配套服务和 V2 本地交付。`历史V2/helixscreen` 的 `codex/u1-native-filament-ui` 已保存为 `956fe83ab6de69a62d91344cf7d66ce7cfc62f64`。旧版提交检查未通过，仅为保存现场而跳过该次钩子，未合入当前分支。
- [旧网页界面汉化](旧网页汉化/使用说明.md) 和 [旧显示层接口记录](../ui-interfaces/U1旧显示层可用接口.md) 属于早期实现，不是当前触屏源码。

屏幕源码常用构建方式：macOS 本机用 `gmake SKIP_OPTIONAL_DEPS=1`，U1 ARM64 用 `gmake snapmaker-u1-docker`。以各自项目的构建说明和固定子模块为准。当前屏幕树中带 ` 2` 后缀的字体、头文件等旁支文件原地保留，未纳入提交。

“打印时烘干”开关存在于旧 V2 的配置改动中；这次未移植到当前屏幕分支。11 月 22 日的现场差量另列在 [MultiACE 层](../03-MultiACE层/README.md)。
