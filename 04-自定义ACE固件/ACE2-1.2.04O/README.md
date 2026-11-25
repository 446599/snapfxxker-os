# ACE 2 Pro V1.2.04O

入口确认拔空后，电机和队列已空闲时立即清除同槽原有的送料故障 129。如果当时还在停机，料仓会在后续查询或重新插入时再检查一次。若同槽开始新送料或固件更新该槽状态，之前的拔出记录就会失效。重新插入时仍未停稳也会放弃这次清除。

清除旧故障只更新料仓状态。随后重新插料时，由 MultiACE 按当前槽位和料路状态启动一次约 100 毫米的短吸。此镜像保留 V1.2.03O 的插料协议。

## 本地构建和测试

从项目根目录运行以下命令。输出目录需为空。

```sh
python3 '04-自定义ACE固件/ACE2-1.2.04O/build.py' \
  --base verification/flow-audit-20261111/artifacts/insert-edit-notify-fix-20261115/upload/ACE2-98O-rollback.bin \
  --reference verification/short-unload-recovery-20261123/runtime/web/backend/firmware/ACE2-1.2.01O-insert-policy.bin \
  --output /tmp/ace-inlet-04-build
python3 '04-自定义ACE固件/ACE2-1.2.04O/test_emulator.py' /tmp/ace-inlet-04-build
python3 '04-自定义ACE固件/ACE2-1.2.04O/test_recovery.py' /tmp/ace-inlet-04-build
```

构建需要 Arm GNU Embedded 工具链，执行测试需要 Python 的 Unicorn 库。
