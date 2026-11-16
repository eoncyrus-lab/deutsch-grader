# 在 Cindy 里使用批改系统

项目自带一个 Cindy Skill，位置是 .agents/skills/deutsch-grader。它调用已部署服务的教师 API，适合让 Cindy 帮你提交作文、等待批改并汇总结果。

## 安装

~~~bash
./scripts/install-skill.sh
~~~

安装目标：

~~~text
~/.agents/skills/deutsch-grader
~~~

新开一个 Cindy 会话后生效。

## 配置环境变量

~~~bash
export DG_API_URL=http://127.0.0.1:8000
export DG_API_EMAIL=teacher@demo.local
export DG_API_PASSWORD=你的密码
~~~

不要把密码写进对话或提交到仓库。使用教师账号，不要用管理员或数据保护账号。

## 直接运行脚本

~~~bash
python .agents/skills/deutsch-grader/scripts/grade.py \
  --text "Meine beste Reise war im Sommer nach Qingdao." \
  --title "Meine beste Reise" \
  --student S-001
~~~

或者从文件读取：

~~~bash
python .agents/skills/deutsch-grader/scripts/grade.py \
  --file essay.txt --student S-001
~~~

脚本会：

1. 登录并拿 CSRF；
2. 查询学生假名；
3. 提交文本作业；
4. 轮询到 ready_for_review、finalized 或 needs_manual；
5. 输出 AI 分数、有效错误、未解决要素和评语。

## 生成独立批改页面

脚本可以把结果渲染成一个单独的 HTML 页面，不依赖前端服务：

~~~bash
python ~/.agents/skills/deutsch-grader/scripts/grade.py \
  --submission-id 你的提交ID \
  --html ~/Downloads/graded-report.html
~~~

页面包含：

- AI 草稿、教师复核、最终成绩三组分数；
- 确定错误和建议核对错误；
- 要素核对状态；
- 中文评语和德语鼓励语；
- 教师终审提醒。

页面是审核材料，不是最终成绩单；正式成绩仍以教师终审为准。

## 在 Cindy 对话里怎么用

可以直接说：

~~~text
用 deutsch-grader 这个 skill 批改这篇德语作文，学生假名 S-001。
~~~

Skill 会把结果当作 AI 草稿，提醒你终审；它不会把 AI 分数说成最终成绩。

## 边界

- 只支持文本提交；图片需要先在教师端上传、确认脱敏副本并转写。
- 没有模型 key 时，真实作文会进入 needs_manual；演示数据仍可在网页里看完整流程。
- 一套部署默认一个租户；多校场景需要先做统一身份或分学校部署。
