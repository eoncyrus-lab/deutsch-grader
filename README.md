# 德语作文 AI 批改台

一个可运行的教师端批改系统，按“固定工作流、代码计分、配置版本化”实现。模型只负责转写、找错、要素核对和评语；分数、封顶、去重和教师改判后的重算全部由确定性代码完成。

当前仓库是 P0–P2 试点实现：教师确认转写、分批找错与复核、要素核对、代码计分、教师改错与终审、学生版反馈、队列、额度账本、租户隔离、评测门禁和离线演示数据都已打通。

### 语音朗读

学生详情页和学生版反馈页都提供德语朗读按钮：可以逐句听原文、听正确表达、听德语鼓励。实现使用浏览器自带语音（de-DE），不把作文额外发送给第三方语音服务；如果系统没有德语语音包，页面会提示安装。

## 一键启动

给第一次部署的人：安装 Docker Desktop，然后运行：

~~~bash
git clone https://github.com/eoncyrus-lab/deutsch-grader.git
cd deutsch-grader
./scripts/install.sh
~~~

脚本会自动下载依赖、初始化演示数据、启动 Web 和 worker，并等待健康检查通过。Windows 用 scripts/install.ps1。完整步骤见 [部署指南](docs/deploy.md)；其他 AI Agent 按 [安装与交付说明](docs/AGENT-INSTALL.md) 执行。

开发模式手动启动：

~~~bash
uv venv --python 3.12
uv pip install -e '.[dev]'
.venv/bin/python -m app.bootstrap
.venv/bin/uvicorn app.main:app --reload
.venv/bin/grading-worker
~~~

打开 http://127.0.0.1:8000 。

演示账号：

- 教师：teacher@demo.local / Demo2026!
- 教研组长：rubric@demo.local / Demo2026!
- 管理员：admin@demo.local / Demo2026!
- 数据保护：dpo@demo.local / Demo2026!

教师端点左侧“新建”可以选学生、输入作文，或上传图片、PDF、Word、TXT。图片要逐张上传遮住姓名区域的副本后才会转写；文档会先提取文字再进入批改。之后在页面里逐条处理错误、改分和终审。演示作文 1 是“待确认转写”，确认后会跑完整五步；演示作文 2 已经完成批改，分数为 4.0，用来展示字数封顶和教师终审。演示数据全部标记为“离线演示”，不会伪装成真实模型效果。

## 目录

~~~text
app/
  config/        题型 YAML、规则表达式、发布门禁
  grading/       分句、词数、最小改动、schema 校验、确定性计分
  model/         OpenAI-compatible 网关、离线 fixture provider
  prompts/       每个步骤独立版本
  pipeline.py    S1 → 教师确认 → S2/S2b/S3 → S4 → S5
  quota.py       预留—结算—释放的额度账本
  queue.py       带租约和幂等键的数据库队列
  services/      教师复核、重算、画像、导出
  api/           FastAPI 路由
  web/           教师端单页界面
  sql/           SQLite 试点 schema 与 Postgres/RLS 迁移草案
configs/
  tasks/         题型、量规、错误码、提示词版本和模型路由
  models/        模型来源与家族
  quota/         套餐、按量兜底和额度窗口
demo/            两篇离线样例的转写与阶段输出
eval/            指标、报告、门禁和合成演示集
tests/           47 个单元/集成测试
docs/            v3 方案、评审记录、验收说明
~~~

## 核心设计

### 固定工作流

1. S1 转写：只做逐字转写，保留学生错误和 [?]。
2. 教师确认：确认后才产生固定转写版本和分句。
3. S2 找错：按受控错误码输出候选修改。
4. S2b 复核：另一个模型家族逐条判“通过 / 改法需调整 / 不成立”，最多两轮。
5. S3 要素核对：布尔、计数、引文和逐句可懂度。
6. S4 计分：纯代码，读题型 YAML。
7. S5 评语：只写表达，不改分；引文和德语长度由代码校验。

任何一步失败都不会产生“看起来完整”的总分；整篇进入需人工，保留已完成阶段产物用于排查。

### 配置驱动

题型文件是唯一的量规来源。语言档位、内容缺项映射、结构判定、字数封顶、标题扣分、错误码权重、去重规则和模型路由都在 YAML 中。计分器用安全的规则表达式求值，不执行任意代码，也不把叙事题的 5/3/2 写死在 Python 里。

新增题型的最低工作量是：复制一份题型 YAML，改写 checklist、content/structure 规则、错误码、prompt 版本和 route，准备该题型的回归集。真正需要新代码语义的题型应显式登记为引擎扩展，不再宣称“只写 YAML”。

### 计分顺序

已发布顺序固定为：

~~~text
维度分求和 → 字数封顶 → 标题等固定扣分 → 上下限钳制 → 最后一次性四舍五入
~~~

权重以 0.5 为增量；内部用 Decimal 做档位比较和最终取整。词数器把 E-Mail、geht's 各算一个词，把 2024、[NAME]、[?Wand] 各算一个词。29/30 词、39/40 词、封顶加无标题这些边界都有测试。

### 错误码与去重

模型输出受控 error_code（如 AUX_MOVE、CASE_PREP），type 由错误码决定。pattern 只作展示。同一 code 全篇按次数计点，第二处起按 repeat_weight 递减，单 code 有上限；同一片段的多个重叠标签只保留影响最大的主类型，避免一处修改被罚两次。

### 教师改判会重算

教师标“AI 改错”“改法过度”“修正改法”或补录漏错后，系统会生成新的 reviewed 分数版本，而不是只改展示。终审必须选择原因（量规理解不同 / AI 漏错 / AI 误判 / 转写问题 / 其他），并写入不可变审计记录。AI 分、复核分、最终分三个口径同时保留。

### 要素证据不确定时不自动扣分

S3 的 true 必须带原文子串。证据校验失败时该字段变成 unknown 并标黄；如果它参与计分，整篇进入需人工，教师修正后重算。系统不会把“模型少抄一个字”直接当成“学生缺地点、缺感受”。

### 数据与隔离

- 所有业务查询都带 tenant_id；对象 key 带租户前缀；跨租户读取返回 404。
- 原图加密落盘，S1 只接受教师确认过脱敏副本的图片；没有确认脱敏副本时转写被拒绝。
- 同意书带 scope、notice_version、purpose_version、到期和撤回时间；撤回后删除画像和正向证据。
- 阶段产物保留哈希和状态，正文按保留策略清理；原图在转写确认后到期删除。

## 配置模型

在 .env 中配置 OpenAI-compatible 网关：

~~~bash
DG_LLM_BASE_URL=https://your-provider.example/v1
DG_LLM_API_KEY=...
DG_LLM_B_BASE_URL=https://second-family.example/v1
DG_LLM_B_API_KEY=...
~~~

S2 和 S2b 必须在不同 family；启动真实批改时会校验。configs/models/routes.yaml 固定模型名和来源，模型版本变化应走同一回归门禁。演示数据不需要 key，使用 demo/ 下的固定阶段输出。

## 测试与评测

~~~bash
.venv/bin/pytest -q
python -m eval.run --set eval/datasets/synthetic-demo.jsonl --out reports/eval.json
python -m eval.gate reports/eval.json --release
~~~

评测报告包含完全一致率、±0.5 一致率、MAE、偏差、QWK、错误精确率/召回率、稳定性和最小改动违规。合成数据只能验证流程，release 门禁会拒绝；真实发布要求至少 50 篇独立标注，建议 QWK 扩到 100 篇，报告成对 bootstrap 置信区间。

## 已知边界

- 试点存储是 SQLite；Postgres/RLS 迁移草案在 app/sql/schema.postgres.sql，尚未在真实 Postgres 上执行。
- 自动裁掉姓名区域需要部署侧接入视觉脱敏；当前实现把“教师确认脱敏副本”作为 S1 的硬门禁。
- 额度账本已做预留、结算和释放，但服务商账单对账、请求 ID 回填和并发压测仍需接真实供应商。
- 真实模型的一致率没有在本仓库中生成；演示分数只来自固定样例，不能当作验收结果。
- 学生辅导 agent 和多校统一身份尚未实现；画像与练习推荐的接口只做到画像聚合和教师正向证据。

## Cindy Skill

项目内提供一个 Cindy Skill，用来从对话里调用已部署的批改服务：提交文本、轮询状态、汇总分数和错误。
也可以传入 --submission-id 和 --html，把已有批改结果渲染成独立 HTML 页面。

安装到本机 Cindy 的共享 Skill 目录：

~~~bash
./scripts/install-skill.sh
~~~

然后新开一个 Cindy 会话，让它读取 .agents/skills/deutsch-grader/SKILL.md。详细说明见 [Cindy 使用](docs/cindy-skill.md)。
