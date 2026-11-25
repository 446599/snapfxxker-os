# 96O 离线候选

该镜像基于 95O 的完整构建流程，仅修改后台解码器允许的四个 reader 候选记录地址，并将内部版本改为 V1.1.96O。未烧录、未部署；同 reader 双卷归属和物理隔离没有验证。

审查入口：`native-decoder.diff`、`version.diff`、`manifest.json`；独立地址/写边界测试见 `../native-record-bounds/README.md`。双构建、生成补丁回放、stock 向量及尾标一致，Bambu 双读和 RC522、已链接 Thumb hook/地址门禁回归通过；逐项结果见 `test-*-output.txt`。构建和回归均为离线操作，不证明设备启动或实际光学行为。

95O 既有 Anycubic 回退路径仍可能从 164 字节记录多读 4 字节；共享 RFID scratch 和无界 SPI 问题本版本未修改。该文件不可单独作为上线或烧录授权。
