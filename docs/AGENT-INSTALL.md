# 给 AI Agent 的安装、验收与交付说明

这份文档是给其他 AI Agent 执行的，不是营销页。目标是让 Agent 从公开仓库完成部署、安装 Cindy Skill，并把教师使用方式讲清楚。执行时以实际命令输出为准，不要编造成功结果。

## 1 Agent 执行契约

~~~yaml
repository: https://github.com/eoncyrus-lab/deutsch-grader
preferred_runtime: Docker Compose
fallback_runtime: Python 3.12 + uv
web_entrypoint: http://127.0.0.1:8000
health_check: /healthz
skill_source: .agents/skills/deutsch-grader
skill_target: ~/.agents/skills/deutsch-grader
skill_installer: ./scripts/install-skill.sh
demo_login: teacher@demo.local / Demo2026!
supported_uploads: jpg, jpeg, png, webp, pdf, docx, txt, md
acceptance: health 200, login 200, submission list 200, skill file exists
~~~

## 2 安装

### 2.1 克隆

~~~bash
git clone https://github.com/eoncyrus-lab/deutsch-grader.git
cd deutsch-grader
~~~

### 2.2 启动

macOS / Linux：

~~~bash
./scripts/install.sh
~~~

Windows PowerShell：

~~~powershell
.\scripts\install.ps1
~~~

脚本会自动创建 .env、生成会话密钥、下载依赖、构建/启动容器，或者在 Docker 不可用时创建本机 Python 环境，并在可用时自动把 Cindy Skill 复制到 ~/.agents/skills/deutsch-grader。不要手工跳过依赖安装。

验证：

~~~bash
docker compose ps
curl http://127.0.0.1:8000/healthz
~~~

必须看到容器 healthy 和 JSON 里的 ok 为 true。

### 2.3 安装 Cindy Skill

~~~bash
./scripts/install-skill.sh
~~~

安装后的位置：

~~~text
~/.agents/skills/deutsch-grader/SKILL.md
~/.agents/skills/deutsch-grader/scripts/grade.py
~~~

Cindy 会在新会话中加载该 Skill。若当前会话没有出现 deutsch-grader，先新开一个会话，不要重复复制目录。

## 3 验证流程

### 3.1 登录

~~~bash
curl -sS -c /tmp/dg-cookies.txt \
  -H 'Content-Type: application/json' \
  -d '{"email":"teacher@demo.local","password":"Demo2026!"}' \
  http://127.0.0.1:8000/api/auth/login
~~~

预期返回 user 和 csrf。不要在日志或最终报告里输出密码。

### 3.2 看作业列表

~~~bash
curl -sS -b /tmp/dg-cookies.txt http://127.0.0.1:8000/api/submissions
~~~

演示数据应有 S-001 和 S-002；S-002 通常已经是 ready_for_review。

### 3.3 跑测试

源码运行且安装了开发依赖时：

~~~bash
.venv/bin/pytest -q
~~~

预期全部通过。当前基线是 47 个测试。

## 4 配置真实模型

编辑 .env，不要用聊天窗口传递 key：

~~~text
DG_LLM_BASE_URL=https://provider-a.example/v1
DG_LLM_API_KEY=由部署者填写
DG_LLM_B_BASE_URL=https://provider-b.example/v1
DG_LLM_B_API_KEY=由部署者填写
~~~

重启：

~~~bash
docker compose restart
~~~

S2 找错和 S2b 复核必须使用不同模型家族。没有 key 时演示数据仍可查看，新提交的真实作文会进入 needs_manual，这是预期行为，不要把它报告成成功批改。

## 5 正式学校初始化

演示模式默认使用 demo-tenant。正式使用前：

1. 在 .env 设置 DG_ENV=prod、DG_AUTO_BOOTSTRAP=0、DG_COOKIE_SECURE=true。
2. 重新启动。
3. 创建租户、班级、题型配置和管理员：

~~~bash
docker compose exec deutsch-grader python -m app.admin init-school \
  --tenant my-school --tenant-name "我的学校" \
  --class-code G8-A --grade 初二 --term 2026-秋 \
  --admin-email admin@my-school.example --admin-name "管理员"
~~~

4. 添加学生假名：

~~~bash
docker compose exec deutsch-grader python -m app.admin add-student \
  --tenant my-school --class-code G8-A --pseudonym S-001 --birth-year 2012
~~~

不要把真实姓名、身份证号或照片直接写进假名。监护同意和学校的数据授权流程必须在导入真实作文前完成；add-student 只创建假名，不替你完成合规授权。

修改演示密码：

~~~bash
docker compose exec deutsch-grader python -m app.admin reset-password \
  --tenant demo-tenant --email teacher@demo.local
~~~

## 6 老师怎么用

### 新建作业

1. 登录教师端。
2. 左侧点“新建”。
3. 选学生。
4. 三选一：
   - 文本录入：粘贴或输入德语作文，点“提交并批改”；
   - 图片：上传 jpg/png/webp，逐张上传遮住姓名/班级/学校区域的副本，再点“开始转写”；
   - 文档：上传 PDF、Word（.docx）、TXT 或 Markdown，系统提取文字后直接排队批改。
5. 文档一次一个；图片可以多选。旧版 .doc 不支持，请另存为 .docx 或 PDF。
6. 扫描版 PDF 往往没有可提取文字，改用图片上传。

### 确认转写

转写结果会先停在“待确认转写”。检查学生原有拼写、标点和 [?]，确认后才进入 S2/S2b/S3/S4/S5。不要在没有确认转写的情况下把分数发出去。

### 审核和终审

1. 打开“待终审”作业。
2. 逐条看确定错误；必要时点“AI 改错”“改法过度”或“修正改法”。
3. 看要素核对；有“证据未通过”的字段先修正。
4. 选择改分原因，完成终审。
5. 点“学生版反馈”导出 HTML。

### 语音朗读

详情页可以逐句朗读原文，错误卡可以朗读正确表达，评语区可以朗读德语鼓励。学生版反馈也带朗读按钮。使用浏览器本地德语语音，不把作文额外发送给第三方 TTS；设备需要安装 de-DE 语音包。

## 7 用 Cindy Skill

~~~bash
export DG_API_URL=http://127.0.0.1:8000
export DG_API_EMAIL=teacher@demo.local
export DG_API_PASSWORD=你的密码
~~~

提交文本：

~~~bash
python ~/.agents/skills/deutsch-grader/scripts/grade.py \
  --text "Meine beste Reise war im Sommer nach Qingdao." \
  --student S-001 \
  --html /tmp/report.html
~~~

提交文本文件：

~~~bash
python ~/.agents/skills/deutsch-grader/scripts/grade.py \
  --file essay.txt --student S-001 --html /tmp/report.html
~~~

读取已有作业并生成页面：

~~~bash
python ~/.agents/skills/deutsch-grader/scripts/grade.py \
  --submission-id 提交ID \
  --html /tmp/graded-report.html
~~~

Skill 会输出 JSON 和 HTML 路径。HTML 包含 AI 草稿、复核/最终分数、确定错误、建议核对、要素状态和评语。Skill 不能替代教师终审；如果状态是 needs_manual，报告要明确说明。

Skill 的 --file 参数读取文本文件；PDF/Word 请在网页上传入口使用，或先用可靠工具转成文本再提交。

## 8 常见故障

### 页面打不开

~~~bash
docker compose ps
docker compose logs -f
curl http://127.0.0.1:8000/healthz
~~~

### 作业一直排队

worker 没启动。Docker 下看日志；本机模式检查 worker.log 和 .worker.pid。

### 提示 model provider is not configured

.env 没有模型 key 或没有重启。演示作业不受影响。

### 文件上传 400

检查扩展名、文件大小（8 MB 上限）和 PDF 是否有可提取文字。扫描版 PDF 请走图片上传。

### 朗读没有声音

系统缺少德语语音包，或浏览器没有获得音频权限。先检查系统 de-DE 语音；Chrome/Edge/Safari 通常会自动使用。

### Skill 登录失败

检查 DG_API_URL 是否是正在运行的实例、账号是否是教师账号、密码是否已重置。不要把密码写进仓库。

## 9 Agent 最终交付格式

安装完成后，向用户报告：

- Web 地址和容器/进程状态；
- 使用的是演示模式还是正式租户；
- 健康检查、登录、作业列表和 Skill 的实际结果；
- Skill 安装路径；
- 测试结果；
- 需要用户自己填写的模型 key 和语音包事项；
- 任何未完成项，不要把未跑过的真实模型结果说成已验收。
