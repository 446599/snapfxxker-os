# U1 原生 UI：2026-11-10 构建产物

固件版本：`2.1.0-233-dev.3-ui.2`。配套源码分支：`codex/u1-native-ui-20261110`。

完整升级包已经生成并通过离线完整性校验。文件入口如下；需要登录保存这些文件的 ChatGPT 账号。这里记录下载链接与校验值，文件并非 GitHub Release 附件。

## 文件下载

| 文件 | 字节数 | 内容 |
| --- | ---: | --- |
| [U1_233_2.1.0-233-dev.3-ui.2_upgrade.bin](https://chatgpt.com/library?file_id=libfile_fde3_f-8f23aede74188191b001071e32e22cbf_25260bb8fed081918d26cd0aacc1e06f&account_id=personal) | 286,698,010 | 完整整机升级包 |
| [U1-native-ui-20261110.1-runtime.zip](https://chatgpt.com/library?file_id=libfile_fde3_f-b625a4b01e488191b18e1bab7c955bbd_25260bb8fed081918d26cd0aacc1e06f&account_id=personal) | 36,635,513 | ARM64 运行归档、相邻 manifest、调试符号及构建记录 |
| [U1-native-ui-20261110.1-source-overlay.zip](https://chatgpt.com/library?file_id=libfile_fde3_f-3058c97c450081918d264feeb9f270e7_25260bb8fed081918d26cd0aacc1e06f&account_id=personal) | 760,210 | 本次 58 个源码接入文件及清单 |
| [U1_233_2.1.0-233-dev.3-ui.2-verification.zip](https://chatgpt.com/library?file_id=libfile_fde3_f-519992244c648191a1017a94b25261e1_25260bb8fed081918d26cd0aacc1e06f&account_id=personal) | 25,509 | 完整校验报告、构建结果、辅助验证脚本及 SHA256SUMS |

## SHA-256

```text
1e64571be3b3d7553d21ffc5a2917c91dbde54330b83b85ad79903c346ca6b04  U1_233_2.1.0-233-dev.3-ui.2_upgrade.bin
f855bdec909f12dbf5f0e41a20ee4abdf8b7036c4b937a905be5bf605560b678  U1-native-ui-20261110.1-runtime.zip
b61ba21ae9b83b6cbaf7e50c6235d876b35627a118abf1583e6e4e90f8183242  U1-native-ui-20261110.1-source-overlay.zip
698c506e1e6ecf082e7a1fcb1b2501a4e6be6dcd6655dffba70035ad02eac0dd  U1_233_2.1.0-233-dev.3-ui.2-verification.zip
```

## 构建与验证范围

- ARM64 主界面、启动程序和守护程序已编译；运行包清单包含 1,082 个文件、484 个 XML 布局、35 个中文字体文件。
- 17 项安装、集成及来源验证测试与 19 项设备服务测试通过，共 36 项。
- 最终 `.bin` 的 12 类独立完整性检查通过，包括 UPFILE、RKFW、RKAF 校验、硬件标识、分区映射、版本和内嵌包校验。
- 包头版本与系统内 `/etc/FULLVERSION` 均为 `2.1.0-233-dev.3-ui.2`。
- 根文件系统为 247,123,968 字节；每个 system 分区容量 314,572,800 字节，剩余 67,448,832 字节。
- MCU 与 10 项非 rootfs 分区载荷保持原样。13,158 个未改路径的内容、UID/GID、权限与时间戳保持一致；221 组硬链接及 493 个别名保留。

验证 ZIP 中保留独立固件验证报告、完整 rootfs 语义审计和辅助脚本。归档内的 GitHub 上传状态记录生成于源码推送之前，后续提交状态以本分支的 Git 历史为准。

## 已知限制

本构建尚未刷入真实 U1，未完成实际启动、屏幕显示、触摸、运动、加热、风扇或 ACE 进退料验收。模拟环境的 socket 交互验证受到环境权限限制。

原生导航仍为左侧五项，预览中的底部四项导航尚未完全迁移。本次首页默认布局包含四喷头、打印速度、热床及打印状态；外置料卷页面隐藏余量，ACE 页面保留余量信息。

## 运行包来源与导入

源码接入基于 `446599/snapfxxker-os` 的 `e901b4af2ca10c4e85fb6ebc6d687be983de937e`。源补丁和构建输入指纹为：

```text
ec25d9810f80eb0e3c1c5ea5ea94d659cf72c9d1559e19b7116cf63fda9d17df
```

运行 ZIP 内的 `helixscreen-233-native-ui-20261110.1.tar.gz` 为 33,163,101 字节，SHA-256：

```text
7fc01b1c52b17d51002a9e82ac4035d99a733df6fb4a82edde5ecc09f5c25fb1
```

源码仓库中的初始运行包 pin 保持为空。下载并解压运行 ZIP，使 `.tar.gz` 与 `.tar.gz.manifest.json` 保持相邻，然后在对应源码检出目录导入：

```sh
python3 scripts/native-ui/pin_runtime.py \
  --runtime /实际路径/helixscreen-233-native-ui-20261110.1.tar.gz
python3 scripts/native-ui/validate.py
```

后续构建步骤、工具链与依赖来源见 [原生 UI 构建说明](native-ui.md)。本次完整固件使用标准 SquashFS pseudo-file 导出和重建保留数字文件归属及元数据，再使用仓库封装工具生成升级包；所用辅助脚本在验证 ZIP 中。
