# 原生 reader 记录边界候选（仅离线）

基于 95O `diagnostic95/firmware/native_tag_decoder.c`，唯一产品代码改动见 `native-record-bounds.patch`：把旧的单向 `>=0x20001748` 拒绝条件改为四个精确地址白名单；不改变读卡、认证、IAP 或协议。本目录不是固件镜像，不得单独覆盖 95O 的已链接片段。

反汇编依据（93/95 同源，见 `minimal-repair/build-analysis/stock.dis`）：worker `0xE250` 将基址置为 `0x20001600`，reader 编号限制为 0/1；`0xE31E–328` 生成两条 record 指针 `base + 164*reader + 12` 与 `base + 164*reader + 340`，即 `0x2000160C、0x200016B0、0x20001754、0x200017F8`。这些是两个 reader 各两个候选记录，不能据静态地址直接认定物理槽映射。每项 164 字节，按地址连续；最后一个记录独占末地址 `0x2000189B`，之后 8 字节空隙，再到 ctx 起始 `0x200018A4`。`0xFDFC–FE32` 把 record+24 传入页面读取；`0xE22C–E234` 固定拷贝 140 字节到该目标，24+140=164，刚好在记录内。注意现有 `rawtag_cache_stub.s` 在 Anycubic 原生回退时以返回值 144 从 record+24 复制到 scratch，会额外读取 record 末尾之后 4 字节；最后记录读至 0x2000189F，仍未达到 ctx，但可能读入相邻记录或间隙。Bambu 成功直接走 FE98，不经过该回退 stub；这是原 95 行为，本补丁不修改。内部共享 scratch 与 SELECT/接收容量也属于其他未解决问题，此次只验证 decoder 的写边界，不证明整个 worker 的读取边界、RF 并发或未可信 FIFO 安全。

离线测试：

```
docker run --rm --network none -v "$PWD/ace2-rfid-dev-20261107/native-record-bounds:/work:ro" -w /work ace2-rfid-offline:20261107 sh -c 'cc -std=gnu11 -O0 -Wall -Wextra -Wno-unused-function -Wno-unused-variable -Wno-int-to-pointer-cast test_record_bounds.c -o /tmp/test_record_bounds && /tmp/test_record_bounds'
```

Linux 容器映射假 SRAM 到固件地址，使用假 SELECT/认证和标签块；四个地址都分别测试 Bambu 双读认证与 JSON 正常写入，检查仅声明字段可改变且相邻记录/ctx 不变；非法和未对齐地址在任何读卡前拒绝；每个块读取失败、双读不一致、SELECT UID 不匹配均不提交记录。此为本机 C 假 IO，不是设备或 ARM 执行证明。候选源使用与 95 同样 Cortex-M3 参数编译并使用原 dec.ld 链接通过；链接 text 6533 字节，相比原 6513 多 20 字节，实际整包必须重新链接所有 hook 和独立校验，并非可原位替换。当前不包含/不生成可烧录文件。
