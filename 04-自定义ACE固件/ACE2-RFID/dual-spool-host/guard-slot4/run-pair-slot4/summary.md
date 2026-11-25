# 同读头顺序插入实测

97O；静态 UI3 粉卷 before/after 槽记录均 SME5DDA105，count 2，原生长度 288873 mm。UI4 蓝卷自然预装后槽记录 SM55F328E6，受控身份对照符合预期；UI4 count 0，长度 0，余量 pending。首次参考 317.6894 mm，后续参考 NaN；不能据此确定缺样原因。

active 约 38.6 s，四头未触发，READY 且编码器稳定后监督 STOP；最大 tick 间隔 54.6 ms，两次读超时恢复。停止后目标 decoder 三次均 351；末阶段坐标不能推算总进料。恢复原配置后 97O 同 UID、四槽 ready、四头 false，所有 hold false；停机后恢复前 reader flags [1,1]。
