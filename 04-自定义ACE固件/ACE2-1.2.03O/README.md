# ACE 2 Pro V1.2.03O

本版修复同一槽已经拔空却持续上报送料故障 129 的问题。只有拔料边沿到达时已存在的 129 才能自动清除。入口持续为空，原生工作队列、电机均已空闲后，通过现有插料状态查询确认。重新插入、同槽开始新动作或任务再次报告故障会撤销这次清错。清错不转动电机。

主机配套版本为 MultiACE `.15.93`。料仓处理旧故障，主机处理暂停期间重新插入后的 100 毫米短吸。

## 复现构建

使用 Python 3 和 Arm GNU Embedded 工具链。两个输入均由构建脚本校验。以下命令从工作区根目录运行，输出目录需为空。

```sh
python3 '04-自定义ACE固件/ACE2-1.2.03O/build.py' \
  --base verification/flow-audit-20261111/artifacts/insert-edit-notify-fix-20261115/upload/ACE2-98O-rollback.bin \
  --reference verification/short-unload-recovery-20261123/runtime/web/backend/firmware/ACE2-1.2.01O-insert-policy.bin \
  --output /tmp/ace-inlet-03-build
```

输出 `ACE2-1.2.03O-insert-policy.bin` 的 SHA-256 为 `0c877266f4d8b6bc54dc1541cef736c967cbeab6039996e3f7bce7982b3d81d0`。在装有 Unicorn 的 Python 环境中，运行 `test_emulator.py` 和 `test_recovery.py` 可复查原有行为和拔空清错。
