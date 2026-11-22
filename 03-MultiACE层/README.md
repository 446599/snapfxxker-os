# 03 MultiACE 层

这一层在 **U1 打印机端**运行，负责向 ACE 发送指令、跟踪耗材位置、进退料、换料和烘干。不在 ACE 料盒 MCU 上运行。

- [MultiACE 源码](multiACE/)：由 `233-firmware` 直接跟踪，不再是独立子模块。维护分支 `codex/233-firmware`，整理时提交 `3c44e71e190d0e38c9e513c515aa1261f182f3b6`。
- [Klipper 主实现](multiACE/multiace/klipper/extras/ace.py)、[ACE 配置](multiACE/multiace/config/extended/ace.cfg)、[测试](multiACE/tests/)。
- [11 月 22 日边烘边打差量](print-drying-20261122.patch)：针对当时的现场基线，不是上述分支的完整运行包。完整现场记录在本地 `verification/print-drying-deploy-20261122/`。

在 `233-firmware` 运行专项测试，使用已安装 `pyserial` 的 Python：

```sh
python -B providers/multiACE/tests/print_drying_selfcheck.py
```

233 整包构建可能使用 [固定的离线包](../01-原厂固件/233-系统集成/bundles/233-dev.2/manifest.json)。改了 provider 后，要核对实际打包来源，不能假设修改自动进入镜像。
