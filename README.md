# 上金所黄金价格监控

macOS 后台监控上海黄金交易所 `Au99.99`，价格单位为人民币元/克，通过 Bark 推送。

- 每 60 秒检查一次，每天在日盘及夜盘时段检查，不按星期几跳过（包括周末特殊交易日）。
- 价格 **≤835 元/克**时推送一次购入价位提醒。发送状态持久化，跨日和重启不会重复提醒；不因其它涨跌、接近目标或跌幅触发。
- 价格 **≥880 元/克**时推送一次高位提醒，与低位提醒独立记录，跨日和重启不会重复提醒。
- 实际交易日北京时间 **09:30** 后取得有效行情时发送一次早报，包含日盘开盘价及现价。最晚补发到 11:30。
- 周末如上金所实际开市，照常发送早报及高低位提醒；仅依据中国大陆上金所行情判断是否可推送，不套用海外市场休市安排。休市、过期或异常行情不推送；没有晚报。
- 校验官方时间戳、当前分钟和数据有效性，不读取时间轴上的未来占位价格。超过 15 分钟未更新或时间不一致的行情不会推送。
- Bark key 仅存在本地忽略的配置文件中；权限为 `600`，不写入日志或仓库。

## 安装

需要 macOS、Framework 版 Python 3.9+ 和 Xcode Command Line Tools（`cc`）；无第三方 Python 依赖。

```sh
cp gold_monitor.config.example.json gold_monitor.config.json
# 编辑本地配置，填写自己的 Bark URL
python3 -m unittest -v
python3 gold_monitor.py --dry-run
python3 gold_monitor.py --test-push
python3 install.py
```

`install.py` 自动适配当前用户和 Python 路径，编译名为 `gold-monitor` 的原生可执行文件；后台进程名称为 `gold-monitor`，在进程内加载 Python 运行时，不启动 `python3` 子进程。重复执行可升级。安装会替换同名 `com.gold-monitor` LaunchAgent，保留历史状态。

实际配置：

```json
{
  "bark_url": "https://api.day.app/YOUR_BARK_KEY/",
  "price_threshold": 835,
  "high_price_threshold": 880
}
```

任务文件位于 `~/Library/LaunchAgents/com.gold-monitor.plist`；运行文件及私有配置位于 `~/Library/Application Support/gold-monitor/`；状态文件位于该目录的 `.state/gold_monitor_state.json`；日志位于 `~/Library/Logs/gold-monitor/`。

```sh
launchctl print "gui/$(id -u)/com.gold-monitor"
tail -n 30 "$HOME/Library/Logs/gold-monitor/gold_monitor.err.log"
```

`--dry-run` 不发送消息、不修改状态；`--test-push` 单独测试 Bark，不依赖行情可用性。收到 Bark 服务端成功确认后才记录发送状态。

## 运行边界

这是本机 LaunchAgent：登录后自动启动；电脑关机、休眠或断网时不能检查或推送。恢复后会继续运行；若恢复时已超过早报窗口，不补发当天早报。需要全天候运行时，应将监控迁移至常开服务器。

休市通过官方行情是否及时更新判断，没有独立维护节假日日历；官方接口不可用、时间异常或超过 15 分钟未更新时保持静默并记录错误。因此不收到早报也可能是行情故障或电脑离线，不能据此确认休市。行情本身可能存在延迟，60 秒轮询不等于成交所实时推送。

低位和高位提醒每个目标价各只发送一次；需要重新启用相同目标时，从安装目录状态文件分别删除 `notified_threshold` 或 `notified_high_threshold` 字段。`high_price_threshold` 省略或设置为 `null` 时关闭高位提醒。调整仓库配置后重新执行 `python3 install.py`。

数据来源：[上海黄金交易所官网](https://www.sge.com.cn/)，官方分钟行情接口 `/graph/quotations`。该品种价格不等于零售金饰价格。
