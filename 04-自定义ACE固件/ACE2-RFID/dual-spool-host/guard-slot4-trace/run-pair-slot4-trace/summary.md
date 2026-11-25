# UI4 追踪实测

97O，UI3 粉卷 before/after 槽记录均为 SME5DDA105、count 2、原生模型长度 288873 mm；静态槽记录保持不变。

UI4 蓝卷在 before 已有缓存 SKU SM55F328E6、ctx0 317.6894、count 0；after SKU 仍相同，ctx0/1 均 NaN、count 0、长度 0。本轮 SKU 为既存槽缓存延续，不能证明本次重新读卡成功；余量 pending。

追踪 1840 事件、无记录 gap；目标 CMD76 152 次。前向坐标 1→536，随后字段缺省形成断点，之后 -5→-121；无法仅凭这些值区分回退和重置或可靠累计总出料。READY 与编码器稳定后监督 STOP，未触发 head。结束后3组全槽ready、四头false、decoder -121稳定；RF flags [1,1]，各端口 hold false。临时 guard 保持 confirmed。
