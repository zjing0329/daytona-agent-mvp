# Daytona Agent MVP

一个最小可用的 agent sandbox 服务层：创建 Daytona sandbox、上传/下载文件、运行 agent 命令、导出事件轨迹。默认最多 20 个 active sandbox。

## 能力

- `POST /sessions` 创建 sandbox session
- `POST /sessions/{id}/files` 上传一个或多个文件
- `GET /sessions/{id}/files?path=...` 下载单文件
- `GET /sessions/{id}/files/list` 列文件
- `GET /sessions/{id}/archive` 下载整个 workspace tar.gz
- `POST /sessions/{id}/exec` 执行一条命令
- `POST /sessions/{id}/run` 后台运行 agent 命令
- `GET /sessions/{id}/events` SSE 事件流
- `GET /sessions/{id}/trajectory?format=json|jsonl` 导出轨迹
- `DELETE /sessions/{id}` 销毁 sandbox

## 启动

先安装依赖：

```bash
cd /Users/zjing/Documents/git/daytona-agent-mvp
python3 -m pip install -r requirements.txt
```

本地 dry-run，不需要 Daytona：

```bash
MVP_BACKEND=local ./scripts/run_dev.sh
```

真实 Daytona：

```bash
export MVP_BACKEND=daytona
export DAYTONA_API_URL=http://localhost:3000/api
export DAYTONA_API_KEY=你的_key
export DAYTONA_SDK_PATH=/Users/zjing/Documents/git/daytona/libs/sdk-python/src
./scripts/run_dev.sh
```

如果你已经 `pip install daytona`，可以不设 `DAYTONA_SDK_PATH`。

## 快速试用

```bash
curl -s -X POST http://127.0.0.1:8787/sessions \
  -H 'content-type: application/json' \
  -d '{"agent_command":"python /workspace/agent.py"}'
```

把返回的 `session_id` 放进下面命令：

```bash
SID=替换成_session_id

curl -s -X POST "http://127.0.0.1:8787/sessions/$SID/files" \
  -H 'content-type: application/json' \
  -d '{"files":[{"path":"agent.py","content_text":"print(\"hello from agent\")\n"}]}'

curl -s -X POST "http://127.0.0.1:8787/sessions/$SID/run" \
  -H 'content-type: application/json' \
  -d '{}'

curl -s "http://127.0.0.1:8787/sessions/$SID/trajectory"
```

## MVP 边界

这套实现有意保持薄：

- 不做多用户鉴权。
- 不做数据库持久化，session 状态在进程内，轨迹写到 `.state/trajectories`。
- 不做完整 tool protocol，只执行 sandbox 内命令并记录可见轨迹。
- 不导出隐藏推理链，只导出请求、命令、文件操作、输出、时间戳等可观察事件。

后续生产化建议再加 API key 鉴权、SQLite/Postgres session store、TTL scavenger、Daytona create retry/nonce adoption、对象存储 artifact、preview URL、正式 agent tool loop。

