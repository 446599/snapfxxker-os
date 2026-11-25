# 97c 单槽原生预装实测

ACE0 UID 808780566/17445170/942880330，97O，内部槽2。用户先退出并收好松线，再重新插入。97b 的DISCOVER无回复发生在 ARM 前，未运动，已独立保留；97c 完成一次原生预装。未主动 FEED、加热或写标签。

本次前 native context count0/remaining0；后 count2（原生样本插入数，不等同严格圈数），+12=288873mm，CMD13 field11同值；SKU SME5DDA105，PLA Basic。上下限10000和330000mm未触及。按线径1.75mm、密度1.24g/cm³换算861.578g，模型估算未称重校准；标签标称1000g不是当前余量。槽位归属本次单槽证据不覆盖同读头双卷和碰撞。

自然结束监督 STOP confirmed；追加 fresh STOP code0，三组四槽ready、四head false、flags[1,1]，最后回退阶段显式decoder −746mm三次一致，不能当作正向累计长度。临时holds恢复false，原cfg SHA 6e5686a83c178a9be3c8b66cf2da8250fda7bc835dc99e321dc15230125abb7d，原协议 SHA 749d1487e3a404c66a881822c1bcfbfb0cd0d6fcf7e6e231e52581a6d86e93ca，临时extra/include已移除。最终fresh同UID/97O、printer/四槽ready、四head false、两reader状态[0,0]、hold全false。

原始数据见 `guard-driver-result.json`、`guard-states.jsonl`、`final-stop.json`、`restored-state.json`。日常自动接入、网页状态及同读头/并发仍未完成。
