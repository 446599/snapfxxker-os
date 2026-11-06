# 233固件源码初始化核对

日期：2026-11-06（Asia/Shanghai）。

## 已完成核对

- PAXX：develop 基线 `bb1fd3705ec9608d71122462b07556613dbbbb1f`，主仓库不是 shallow clone；历史文件内容按需获取。
- multiACE：v1.27b / `a1a7b91b1fd297c1486a13ea22c61e01499a196d`，与 PAXX managed 包的版本定义一致。
- screen-apps：`4a81b50d9b6ef7a2c253b45dd00fe6f732c9d203`，与 PAXX 的子模块记录一致。
- 两个子模块均完成初始化且工作区无修改。项目名称、版本、来源与包校验和写入 `233-firmware.json`。
- PAXX 原 README 按原始字节保存在 `README.upstream.md`；PAXX 与 multiACE 的 LICENSE 均保持原样。
- `bash -n build-233.sh` 通过。
- `./build-233.sh --plan` 输出 `GIT_VERSION=2.1.0-233-dev.1`、`PROFILE=extended-multiace`，文件名为 `firmware/U1_233_2.1.0-233-dev.1_upgrade.bin`。
- `make -s overlays PROFILE=extended-multiace` 正确选择 PAXX extended overlay 与 multiACE mod。
- 使用临时 rootfs 目录运行 PAXX 的实际版本写入脚本，确认 `/etc/FULLVERSION` 为 `2.1.0-233-dev.1`、`/etc/BUILD_PROFILE` 为 `extended-multiace`，原版本保留在 `/etc/BASE_VERSION`。临时目录已清理。
- `git diff --check` 通过。

## 验证边界

以上核对覆盖源码、依赖、构建参数与版本写入逻辑，没有运行完整镜像构建、managed 包生成、刷机或硬件回归。没有生成可刷写的 233 固件镜像，也没有把本地 multiACE 改动接入固件安装流程。

初始化时只读查询打印机，看到 Klipper 为 `2.1.0-paxx12-develop-e086afa`、multiACE Web 为 `1.27b+2725a008`。本次没有修改设备。
