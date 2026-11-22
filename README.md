# 冠英外贸工作台

浙江冠英汽车零部件有限公司的独立销售工具。把渠道询盘、商用车制动片选型、报价依据，以及 2026 上海法兰克福汽配展邀约放在一起。原获客产品继续独立运行。

## 启动和演示

**使用本机 Codex CLI**　已登录 ChatGPT 的机器可在本子目录运行 `./dev.sh --demo --codex`，Windows 使用 `.\dev.ps1 -Demo -Codex`。页面显示「本机 Codex CLI」后，在买家卡上点「用 AI 核查证据与建议首问」，或在收件箱点「AI 辅助核查」。点「查型号」会带入客户原话，可以继续点「AI 提取字段」并确认查询。每次点击使用本机 CLI 的账号调用模型服务，传入当前证据摘录或询盘原文。选型及核价沿用工作台的目录、适配和价表规则。手动启动 uvicorn 时设置 `DESK_AI_PROVIDER=codex` 即可选用相同方式。

macOS / Linux 在本子目录运行 `./dev.sh --demo`，然后打开 [海外买家发现](http://127.0.0.1:8794/static/discovery.html)。脚本准备本项目虚拟环境，使用独立演示库。日常业务可运行 `./dev.sh`。比赛演示顺序、实测结果及数据口径见[试点演示与验收记录](PILOT.md)。

需要 Python 3.11+。首次运行 `python -m pip install -r requirements.txt`。

```powershell
powershell -ExecutionPolicy Bypass -File .\dev.ps1 -Demo -DocsDir 'D:\量子'
```

打开 <http://127.0.0.1:8794>。不带 `-Demo` 使用独立业务库。macOS / Linux 可从项目目录运行 `DESK_DEMO=1 DESK_DATABASE=runtime/demo.db python -m uvicorn desk.main:app --host 127.0.0.1 --port 8794`。

1. **WVA 19032**。英文目录第 6 页命中 GYS-007，展示尺寸、孔数和来源。
2. **JH6 同码双候选**。冠隆目录第 17 页的同一参考码列出后 A、后 B。加入 `177×197×15.5/18.5` 和 16 孔后可缩小到后 A；仍需现车桥和材料复核。
3. **订单成本**。点击候选上的「按订单核算」带入型号和已填条件。根据市场标准、订制厚度、配方、重量、附件材质和指定港口核算本单。虚构型号 DEMO-D01 还保留历史阶梯价演示。
4. **询盘收件箱**。六渠道演示，显示原文、身份线索、待处理私信、超过 8 小时标记和按账号筛选。点击「查型号」带入客户原话。
5. **上海汽配展**。导入买家 CSV，搜索或按 A/B/C 过滤，保存跟进状态并生成英文邀约草稿。展期 2026 年 12 月 2–5 日，资料报送展位 1.1N59。展前邀约、展中会谈、湖州参观与展后跟进可分别记录。

## 企业数据

仓库的 `data/catalog.json` 是经目录页面校对的首批结构化条目。它保留目录原名和页码。将三份原 PDF 放入 `DESK_DOCS_DIR`，即可从候选和资料页面打开原件。维护入口在「企业知识库」，表头位于 `examples/`。CSV 使用 UTF-8，可带 BOM。

| 数据 | 关联方式 |
| --- | --- |
| 产品与交叉编码 | 以企业型号为键。参考编码带体系及来源，允许同一码指向多条记录。车型、车桥分别存储。 |
| 工艺与适配 | 导入车桥、安装位置、材料、复核证据与记录号。仅由技术人员复核后在数据中设 `verified=1`。 |
| 试验报告 | 型号＋材料、报告号、来源、结论。企业介绍中的通用试验能力只用于背景，具体报价需产品级报告。 |
| 孚盟 CRM / 售后 / 进销存 | 将已授权导出映射到经验、适配和价格模板。导入以类型、来源和记录号去重，更新会保留买家跟进。 |
| 海关和展会买家 | 企业输入的品类编码用于候选检索。把授权导出映射到 `examples/buyers.csv`，保留交易期间、来源、单位和记录编号。交易与公开在售证据分别记录。 |

图面尺寸与下方表格有冲突时，以原目录表格为录入依据。目录码和 WVA、BFMC、车型习惯称呼都可用于找候选。已审核的历史阶梯价只在型号候选唯一、车桥与材料已复核、同材料试验合格且市场、币种、贸易条款、数量匹配时显示。

定制成本按 `(每套净重 × 配方材料成本/kg + 各附件每套数量 × 各自单件成本之和 + 单套其他成本) × 订购套数 + 整单贸易费用` 计算。贸易费用由工作人员依据指定港口或地点填写，费用为零时显式填 0。拟议价格可选，用于查看与成本的差额。核算稿供技术与业务复核本单厚度、市场标准、配方、成本和商务政策。算式以销售输入的统一币种计算。

PDF、数据库、买家名录、价格和凭据留在本地运行目录。可用 `DESK_BUYER_CSV` 指向初始买家 CSV、`DESK_BUYER_WORKBOOK` 指向下载的 Excel，默认读取 `runtime/buyers.csv` 与 `runtime/buyers.xlsx`。

## 渠道

| 渠道 | 工作方式 |
| --- | --- |
| 小红书、抖音 | 复用旧工具已持久化的评论。小红书可额外读取所选账号槽位的入站文字私信。连接使用 SQLite 只读模式。 |
| Instagram | 授权商业账号，接收签名 Webhook 的文字私信和评论。设置 `DESK_META_VERIFY_TOKEN`、`DESK_META_APP_SECRET`，新建账号时填平台 ID。 |
| YouTube | 填频道 ID，配置 `DESK_YOUTUBE_KEY_<工作台账号 ID 大写>`。同步最新视频顶层评论，单次最多 500 条。 |
| X | 填 OAuth 用户 ID，配置 `DESK_X_KEY_<工作台账号 ID 大写>` 的用户访问令牌。同步入站文字私信，单次最多 500 条。 |
| TikTok | 通过获得 TikTok Business 权限的上游接入端转换事件，或者手动登记。 |

接原获客工具时设置 `DESK_LEGACY_DB`、`DESK_LEGACY_ORG`，按需要指定明确归属的 `DESK_LEGACY_PRODUCT` 或逗号分隔的 `DESK_LEGACY_DM_SLOTS`（默认槽位名 `default`）。工作台支持手动同步，`DESK_SYNC_SECONDS` 设置后台同步间隔。同步处理既有系统已经实际收到的内容。冠英现有的个人社媒账号可先走手动登记与已授权的原系统导入，平台接口按账号资质接入。

所有渠道也可用带账号令牌的 `POST /ingest/<account_id>`。添加账号时显示一次令牌。请求包含 `event_id`、`kind`（`dm` / `comment`）、`body`，及可选的 `sender`、`url`、`occurred_at`（带时区 ISO 时间）。相同账号和事件 ID 可重放。

可选 `DESK_AI_BASE_URL`、`DESK_AI_MODEL`、`DESK_AI_API_KEY` 开启询盘辅助核查和查询字段提取。模型只接收销售主动提交的当前文本。型号决定和报价基于结构化规则。跨网部署时设置 `DESK_PUBLIC_HOST` 和 `DESK_OPERATOR_PASSWORD`，通过 HTTPS 反向代理访问。操作员名为 `operator`。试用默认只在本机监听。

## 验证

### 复现跨班询盘交接

在询盘卡的「关联已有企业」选择企业并点「保存跟进」。多条询盘可以归到同一家企业。打开「海外买家发现」并检索该企业，可查看最近跟进的时间、负责人、状态及下一步，展开「相关询盘」可读客户原话。选另一家或「未关联企业」并保存即可调整归属。单独更改关联不会刷新跟进时间。

1. 在「渠道与账号」创建两个中国制造网账号，名称标注为演练。现阶段可登记或导入该平台的授权消息，实际平台回复时限需由企业确认。
2. 在「询盘收件箱」展开「导入账号的询盘 CSV」，对第一个账号导入 `examples/inbox.csv`。应显示新增 3 条、重复 1 条。重放同一文件时新增为 0。
3. 在第二个账号手工登记一条消息，编号填 `sample-100`。两账号合计 4 条。按「待认领」找到夜间询价，保存负责人及下一步，可直接用原文「查型号」。
4. 在平台实际回复后，由销售填写实际回复时间。仅保存跟进状态时仍保留原有回复记录。多人同时编辑产生版本冲突时刷新后再操作。

工作台将时间保存为 UTC，在收件卡上显示北京时间。手工时间选择器使用设备所在时区，CSV 的 `occurred_at` 须带明确时区。待回复筛选包含进行中且尚无回复登记的事项。8 小时用于队列提醒。

后台和手工同步均按来源记录最近尝试、最近成功读取、错误和待续读状态。YouTube 和 X 每轮读取最新页及待续读的历史分页，单次最多 5 页。暂时失败可再次同步，已入库事件按账号与平台编号去重。上游分页过期后从头扫描，需对照平台可见历史核实缺口。

```sh
python -m pip install httpx
python -m unittest discover -s tests -v
node --check desk/static/desk.js
```

- [主办方展期、买家配对](https://automechanika-shanghai.hk.messefrankfurt.com/shanghai/en/planning-preparation/visitors.html)
- [Instagram Messaging](https://developers.facebook.com/documentation/business-messaging/instagram-messaging)
- [YouTube 评论 API](https://developers.google.com/youtube/data/reference/rest/v3/commentThreads/list)
- [X 私信事件 API](https://docs.x.com/x-api/direct-messages/get-dm-events)
- [TikTok Business Messaging](https://ads.tiktok.com/resources/help/article/marketing-api?lang=en&redirected=2)
