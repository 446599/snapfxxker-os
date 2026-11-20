# 4. ACE 自定义固件

这一层只管理 ACE 盒内的 MCU 镜像与补丁。U1 主机的 `ace.py`、读卡逻辑、进退料逻辑属于第 3 层 multiACE。

当前保留仓库已经使用的 **ACE2-Open `1.1.60O`** 补丁，基础镜像为用户提供的 **ACE 2 Pro `1.1.31`**。本轮没有修改 MCU 补丁字节，也没有生成新版本号冒充升级。

```sh
python build.py ace --plan
python build.py ace --base /path/to/ACE2_V1.1.31.bin --output dist/ACE2-Open-1.1.60O.bin
```

构建沿用现有 `ace2_ota.py` 的镜像解析、补丁应用及匹配检查，只输出 MCU `.bin` 和校验和，不打开串口、不刷机。没有原始 `1.1.31` 镜像时无法生成完整 MCU 镜像，仓库不伪造或内置该原厂镜像。

`patches/ace2_open_60_patch.json` 是补丁的唯一源码位置。主机 backend 同名路径为仓库内的相对链接；multiACE 打包时将它展开为普通文件，保留现有 Web 刷写功能。以后改 MCU 补丁只改这一处，并记录版本及实机证据。

来源为 [Simon-CR/ace2-pro-firmware-research](https://github.com/Simon-CR/ace2-pro-firmware-research)；主机 OTA 协议代码沿用文件内保留的 hakimio 等贡献者说明。原有第三方许可随包保留。
