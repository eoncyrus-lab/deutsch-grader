# 验收说明

## 已执行

~~~bash
cd /Users/bilibili/deutsch-grader
.venv/bin/python -m compileall -q app eval tests
node --check app/web/app.js
.venv/bin/pytest -q
~~~

结果：43 个测试全部通过；Python 编译和前端语法检查通过。

覆盖范围：

- 分句、缩写、数字、连字符、撇号、脱敏占位符和跨度定位。
- 题型配置校验、错误码与类型一致性、bands 兜底、caps 排序。
- 语言档位、重复错误递减、单 code 上限、动词结构按句上限。
- 内容缺项映射、结构矛盾、可懂度上限/下限、封顶后扣分、Decimal 取整。
- 最小改动：合法词尾/介词/助动词修改、纯词序调整、实义词替换、扩写、多备选。
- S2 跨度丢弃、语义替换降级、S2b 缺失 verdict、S3 证据 unknown、S5 引文和德语长度。
- 离线 S1→确认→S2/S2b/S3→S4→S5 端到端；演示作文 1 得 9.5，作文 2 得 4.0。
- 教师驳回错误后 reviewed 分重算，终审写入 final_total，学生版导出。
- CSRF 缺失返回 403；教师访问质量看板返回 403；量规负责人可访问；跨租户读取返回 404。
- 队列租约回收、失败退避和 dead；额度预留、结算、释放和耗尽。
- 配置无 pass 报告不能激活；fail 和 demo 不能激活。
- 评测指标、QWK、bootstrap 区间和 release 门禁。
- 对象加密、租户前缀和跨租户读取拒绝。

## 离线演示

~~~bash
DG_DB_PATH=/tmp/dg-demo.db DG_STORAGE_DIR=/tmp/dg-demo-objects \
  .venv/bin/python -m app.bootstrap
~~~

输出包含两篇演示提交：

- travel-1：待确认转写。确认后跑完整流程，最终 9.5 分，语言 4.5、内容 3、结构 2。
- travel-2：自动批改完成，4.0 分，语言 4.5、内容 2、结构 1.5，22 词触发 30 词以下封顶。

## 前端

已用 TestClient 验证：

- / 返回教师端 HTML。
- /static/app.js 和 /static/styles.css 返回 200。
- /healthz 返回 ok。
- 登录成功，响应包含 CSRF。
- 安全响应头包含 CSP、nosniff、DENY frame 和 no-referrer。

未在真实浏览器里做视觉回归；布局按 320 px 起的窄屏和使用系统深浅色做了 CSS 约束。

## 评测门禁

~~~bash
python -m eval.run --set eval/datasets/synthetic-demo.jsonl --out reports/eval.json
python -m eval.gate reports/eval.json --release
~~~

合成集报告会得到较高的 QWK 和 MAE，但 release 门禁仍会失败，原因是：

- synthetic dataset cannot approve a release；
- 样本少于 50；
- 稳定性只有 75%。

这是预期行为：合成数据只验证流程，不能作为发布依据。真实发布前需要教师独立标注的 blind/regression 集。

## 未验证

- 真实模型供应商的 JSON 稳定性、token 账单和限流行为。
- Postgres/RLS 迁移草案未在真实 Postgres 执行。
- 自动视觉脱敏未实现；当前要求教师先确认脱敏副本。
- 真实教师双评一致率和 QWK 未生成。
- 学生辅导 agent、多校 SSO 和移动端未实现。
