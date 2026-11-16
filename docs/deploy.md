# 部署指南（给第一次部署的人）

这套系统默认是“一位老师或一个学校一套部署”，数据存在本机目录。按下面的步骤做，不需要先懂 Python 或 Docker。

## 方案 A：Docker，推荐

### 1. 安装 Docker Desktop

macOS 或 Windows 安装 Docker Desktop，打开后等左下角显示 Docker is running。

检查：

~~~bash
docker --version
docker compose version
~~~

### 2. 下载代码

有 Git 时：

~~~bash
git clone https://github.com/eoncyrus-lab/deutsch-grader.git
cd deutsch-grader
~~~

没有 Git 就在 GitHub 页面点 Code → Download ZIP，解压后进入目录。

### 3. 一键启动

macOS / Linux：

~~~bash
./scripts/install.sh
~~~

Windows PowerShell：

~~~powershell
.\scripts\install.ps1
~~~

脚本会自动：

- 复制 .env.example 为 .env；
- 生成随机会话密钥；
- 构建镜像并下载 Python 依赖；
- 启动 Web 和批改 worker；
- 等待健康检查通过。

看到 Ready: http://127.0.0.1:8000 后，用浏览器打开这个地址。

演示账号：

~~~text
teacher@demo.local / Demo2026!
rubric@demo.local  / Demo2026!
admin@demo.local   / Demo2026!
dpo@demo.local     / Demo2026!
~~~

## 方案 B：不用 Docker，本机 Python

需要 macOS 或 Linux，Python 3.12。脚本会尝试自动安装 uv 和依赖。

~~~bash
./scripts/install.sh
~~~

如果机器上没有 Docker，脚本会自动走本机模式：

- 安装 uv（已有则跳过）；
- 创建 .venv；
- 安装项目依赖；
- 初始化演示数据；
- 后台启动 Web 和 worker。

停止：

~~~bash
kill "$(cat .server.pid)" "$(cat .worker.pid)"
~~~

日志在 server.log 和 worker.log。

## 第一次使用

1. 登录教师账号。
2. 点左侧“新建”。
3. 选择学生，输入标题和作文正文。
4. 点“提交并批改”。
5. 等待状态变成“待终审”。
6. 逐条确认错误，必要时点“AI 改错”“改法过度”或“修正改法”。
7. 选择改分原因，完成终审。
8. 点“学生版反馈”导出 HTML。

没有配置模型时，新建的真实作文会停在“需人工”，错误信息会提示 model provider is not configured。演示数据仍然可以看完整流程。

## 配置模型

编辑项目根目录的 .env：

~~~bash
DG_LLM_BASE_URL=https://你的服务商/v1
DG_LLM_API_KEY=你的key
DG_LLM_B_BASE_URL=https://第二个服务商/v1
DG_LLM_B_API_KEY=第二个key
~~~

S2 找错和 S2b 复核必须来自不同模型家族；只用一家服务商时，S2b 会被拒绝，这是设计约束。模型名在 configs/models/routes.yaml 里改。

改完重启：

~~~bash
docker compose restart
~~~

本机模式则重启 uvicorn 和 grading-worker。

## 修改密码和创建账号

Docker：

~~~bash
docker compose exec deutsch-grader python -m app.admin reset-password \
  --tenant demo-tenant --email teacher@demo.local
~~~

本机：

~~~bash
.venv/bin/python -m app.admin reset-password \
  --tenant demo-tenant --email teacher@demo.local
~~~

创建新老师：

~~~bash
docker compose exec deutsch-grader python -m app.admin create-user \
  --tenant demo-tenant --email laoshi@school.example \
  --name "李老师" --role teacher
~~~

查看账号：

~~~bash
docker compose exec deutsch-grader python -m app.admin list-users --tenant demo-tenant
~~~

一套部署默认使用 demo-tenant。多所学校建议各自部署一套，或先接学校统一身份后再共享部署。

如果要从演示模式切到正式环境：

1. 在 .env 设置 DG_ENV=prod、DG_AUTO_BOOTSTRAP=0、DG_COOKIE_SECURE=true。
2. 执行 docker compose up -d --build。
3. 先创建自己的租户、班级、题型和管理员账号：

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

正式环境的学生名单导入目前由命令行完成；管理页覆盖日常批改和终审。

## 数据、备份和恢复

数据在 ./data：

- data/app.db：业务库；
- data/objects：加密后的作文图片。

备份：

~~~bash
./scripts/backup.sh
~~~

文件写入 backups/。恢复时停止服务，把备份解压回 data，再启动。

## 更新版本

~~~bash
git pull
docker compose up -d --build
~~~

更新前先执行 ./scripts/backup.sh。

## 常见问题

### 页面打不开

确认服务在跑：

~~~bash
docker compose ps
curl http://127.0.0.1:8000/healthz
~~~

端口被占用时，改 docker-compose.yml 左边的端口，例如 8001:8000，再打开 http://127.0.0.1:8001。

### 一直显示排队中

worker 没启动。Docker 查看：

~~~bash
docker compose logs -f
~~~

本机查看 worker.log；确认 .venv/bin/grading-worker 在运行。

### 提示 model provider is not configured

.env 还没有模型 key，或者改完没有重启。演示数据不受影响。

### 登录提示 invalid credentials

确认使用的是演示账号，或在容器里重置密码。密码区分大小写。

### Docker 构建失败

先确认 Docker Desktop 正在运行，再执行：

~~~bash
docker compose build --no-cache
~~~

网络受限时，给 Docker 配置代理后重试。

## 上线前的安全清单

- 修改所有演示账号密码，或删掉演示账号。
- 修改 .env 里的 DG_SECRET_KEY，使用随机长字符串。
- 不要直接把 8000 端口暴露到公网；前面加 HTTPS 反向代理。
- 设置 DG_COOKIE_SECURE=true 并启用 HTTPS。
- 备份 data 目录，设置定期备份。
- 在模型服务商后台核对条款是否允许后端 API 调用。
- 真实学生数据上线前完成监护同意、保留期限和删除流程。
