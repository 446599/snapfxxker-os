# 04 自定义 ACE 固件

[ACE2 RFID 开发仓库](ACE2-RFID/) 是 **ACE 2 Pro 料盒端**固件研究与补丁工程，独立于 U1 系统和 MultiACE。分支 `codex/ace2-rfid-resume`，整理时提交 `17c1da7d931bbaef9192ea3a3dc7e4b1c31ac5d5`。

- [仓库基线说明](ACE2-RFID/baseline/README.md) 和 [源码说明](ACE2-RFID/source/README.md)：固件来源、补丁结构和构建边界。
- `diagnostic92/` 至 `diagnostic97/` 是分阶段实验。料盒端补丁、C/汇编和构建脚本在各阶段的 `firmware/`，例如 [97O 料盒端源码](ACE2-RFID/diagnostic97/firmware/)；[97O 构建清单](ACE2-RFID/diagnostic97/manifest.json) 记录离线候选。
- [原生几何](ACE2-RFID/native-geometry/)、[记录边界](ACE2-RFID/native-record-bounds/) 和 [双卷](ACE2-RFID/dual-spool/) 是实验代码。[双卷主机端](ACE2-RFID/dual-spool-host/) 与 [预装配套](ACE2-RFID/preload-feature/) 在 U1 侧运行，不是刷入料盒的镜像。

含设备身份的进度记录、现场备份、镜像和日志留在各自原路径，未并入这次源码提交。候选清单和历史单卷记录用于追溯，不证明当前设备版本，也不代表双卷、余量或 OTA 已验收。
