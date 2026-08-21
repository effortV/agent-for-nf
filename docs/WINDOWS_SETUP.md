# Windows + WSL 2 + Anaconda 启动说明

## 1. 安装 WSL 2（只做一次）

以管理员身份打开 PowerShell：

```powershell
wsl --install -d Ubuntu
wsl --update
```

按提示重启 Windows。首次打开 Ubuntu 时会创建一个新的 Linux 用户名和密码；这个密码由你现场设置，不一定等于 Windows 登录密码。输入 Linux 密码时屏幕不显示字符是正常现象。

## 2. 使用 Ubuntu-24.04 中已经安装的 Docker Engine

你的 Docker 已安装在 `\\wsl.localhost\Ubuntu-24.04` 对应的 WSL 发行版中，不需要再安装 Docker Desktop。普通 PowerShell 验证：

```powershell
wsl -d Ubuntu-24.04 -- docker --version
wsl -d Ubuntu-24.04 -- docker compose version
```

也可以打开 Ubuntu-24.04 终端，在 `/mnt/d/桌面/agent for NF/gpt` 下直接执行 Docker 命令。

## 3. 配置 API 凭据

在普通 PowerShell 中：

```powershell
cd "D:\桌面\agent for NF\gpt"
powershell -ExecutionPolicy Bypass -File scripts\configure_env.ps1
```

脚本会隐藏密钥输入并写入 Git 已忽略的 `.env`。

可在项目根目录验证外部 API（会向硅基流动发出一次极短模型请求）：

```bat
conda run -n nf-agent python -m scripts.check_external_apis
```

## 4. 启动 Docker 后端

```powershell
cd "D:\桌面\agent for NF\gpt"
powershell -ExecutionPolicy Bypass -File scripts\start_backend.ps1
```

默认启动 PostgreSQL、Redis、Neo4j、MinIO、Chroma、GROBID、FastAPI 和 Worker，不启动容器内的 Streamlit，避免占用 Anaconda Streamlit 的 8501 端口。

`start_backend.ps1` 会优先使用 Windows Docker；找不到时自动把项目路径映射到 Ubuntu-24.04 并调用其中的 Docker Engine。
它还会建立一个隐藏的 WSL 保活进程，避免 PowerShell 命令结束后 Ubuntu 自动停机、导致 API 的 8000 端口突然消失。

需要停止整套后端时运行下面的命令。它不会删除 PostgreSQL、Neo4j、Chroma、MinIO 等持久化数据卷：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_backend.ps1
```

## Elsevier insttoken 获取方式

Elsevier 默认根据请求来源的机构 IP 判断 ScienceDirect 订阅权限。API key 能通过开发者身份认证，但不保证具备订阅全文权限。如果在家庭网络、校外网络或学校代理后面调用失败：

1. 登录 Elsevier Developer Portal，确认 API key 已启用 ScienceDirect API。
2. 联系学校图书馆电子资源/API 管理员，确认学校订阅允许该 TDM 用途。
3. 通过 Elsevier Research Products APIs Support Center 提交申请，说明机构名称、API key 对应应用、ScienceDirect TDM 用途、部署网络位置及所需内容范围，请求 `Institutional Token`。
4. Elsevier 批准后会提供与 API key 配套的 insttoken。运行 `scripts\configure_env.ps1 -KeepExisting`，把它保存到 `ELSEVIER_INSTTOKEN`。

官方说明：<https://dev.elsevier.com/tecdoc_api_authentication.html>。insttoken 代表机构订阅权限，只能保存在服务器端 `.env`，不能放入浏览器代码、仓库或 URL。

## 5. 在 Anaconda Prompt 启动 Streamlit

```bat
conda activate nf-agent
cd /d "D:\桌面\agent for NF\gpt"
streamlit run ui\streamlit_app.py
```

也可直接运行：

```bat
cd /d "D:\桌面\agent for NF\gpt"
scripts\start_streamlit_anaconda.cmd
```

浏览器打开 <http://localhost:8501>。页面包含三个主模块：

- “对话 Agent”：保留上下文，可选择跨文献知识发现，并在回答中分开显示论文事实与尚未验证的 AI 推断。
- “文献读取与知识库”：自动采集、公开网址/DOI 直读、摘要文献补全文、合法订阅全文上传。
- “知识发现图谱”：查看规律、矛盾、新假设、支持/反对证据和验证计划，并进行人工审核。

API 文档位于 <http://localhost:8000/docs>。

日常关闭后再次使用，只需先在普通 PowerShell 启动后端，再在 Anaconda Prompt 启动 Streamlit；不需要重新安装 Docker，也不会清空原来的 PostgreSQL、Neo4j、Chroma、MinIO 或对话数据。

如希望 Streamlit 也在 Docker 内运行：

```powershell
docker compose --profile docker-ui up --build -d
```
