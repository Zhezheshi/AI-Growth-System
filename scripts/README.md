# 买家名单试点评测

在子项目目录启动工作台后，选用与页面相同的国家、品类、角色或关键词冻结本次候选。下面的国家、品类和目录名均为示例。

```sh
python3 scripts/pilot.py freeze runtime/pilot-01 --country 印尼 --product drum
python3 scripts/pilot.py report runtime/pilot-01
```

可重复添加 `--exclude-company "校准企业全名"` 排除已用于调试的正反例。可传 `--role workshop`、`--query truck`、`--port 8794`。先把已知客户导入工作台，新候选将按本轮检索顺序去重，最多保留十家。企业历史沿用页面标识；尚未取得的客户历史记为待核。每轮选新目录，复测读取原名单快照。

销售在 Excel 中打开 UTF-8 的 `review.csv`，保留前六列，仅填写企业或团队的复核列。实际送给该组复核时填“是”。完成后填复核人和“值得联系 / 待核实 / 不匹配”中的一项，注明原因，记录真实计时的分钟数。未做的判断、未测的耗时留空。保存为 UTF-8 CSV。人工和辅助计时均从打开资料到作出该项判断。比较用时应选业务难度相近的样本，交叉分配方式。

`report` 将每组实际送审数用作其分母。“待核实”和已送审未复核都计入分母。通过“送审后未复核”查看该比例是否仍在变化。未实际送审时比例为 null。时长分别报告有计时的样本数和中位数，任一组缺少计时即留空该项差值。企业与团队各自统计。可用 `python3 scripts/pilot.py report runtime/pilot-01 > runtime/pilot-01/report.json` 保存结果。
