# 报销制度问答系统 · 技术架构方案

> 角色：资深 Python 后端架构师视角
> 约束：FastAPI（异步，Python 3.11+）· LLM 经 API 调用（可替换供应商）· 制度知识库需向量化检索 · 双角色（员工问答 / 管理员维护+看记录）

---

## 0. 选型原则（贯穿全文）

**三层"可替换"是核心设计目标：**

- **LLM 供应商可换**：用 OpenAI 兼容接口统一 DeepSeek / 通义千问 / GLM，仅通过 `base_url` + `api_key` 切换。
- **向量库可换**：开发期用本地 numpy 余弦，生产期切 pgvector，业务代码不变。
- **嵌入模型可换**：与 LLM 同层抽象，可本地或云端。

**Week 1 默认 `LLM_PROVIDER=mock` + 本地向量库，零外部依赖即可跑通**，之后靠环境变量切真模型。开发用 SQLite，生产用 Postgres（同一套 SQLAlchemy 模型）。

---

## 1. 四层架构图（文字版）

```
┌─────────────────────────────────────────────────────────────┐
│  入口层  Entry / Gateway Layer  (app/api)                     │
│  · FastAPI Router (/api/v1)   · JWT 鉴权中间件(角色:员工/管理员) │
│  · Pydantic v2 请求校验      · CORS / 限流 / 静态文件(uploads)  │
│  · 依赖注入 deps: get_current_user / require_admin            │
└───────────────────────────┬─────────────────────────────────┘
                            │ 调用
┌───────────────────────────▼─────────────────────────────────┐
│  服务层  Service Layer  (app/services)                        │
│  AuthService     登录/发令牌/密码哈希                          │
│  ChatService     会话与消息编排、调用编排层、存库               │
│  KnowledgeService 文档摄取(解析→分块→向量化→入库)              │
│  RetrievalService 向量召回 + 重排 + 来源拼装                   │
│  LLMService      供应商抽象、chat/embed 调用                   │
│  AdminService    用户管理 / 文档管理 / 记录查看                │
│  UploadService   文件接收、类型识别、落盘                       │
└───────────────────────────┬─────────────────────────────────┘
                            │ 调度
┌───────────────────────────▼─────────────────────────────────┐
│  智能体编排层  Agent Orchestration  (app/agents)               │
│  Orchestrator  有界多步推理(Planner→Tool→Reflect, ≤3步)      │
│  Tools: retrieval_tool / clarify_tool / business_tool         │
│  prompts.py    系统提示词(员工只问、管理员可维护)              │
│  guardrails.py 不许编造、必须带引用、不确定时反问               │
│  audit         每步写 agent_logs(可追溯, 管理员"看记录"用)     │
└───────────────────────────┬─────────────────────────────────┘
                            │ 读写
┌───────────────────────────▼─────────────────────────────────┐
│  数据模型层  Data Model  (app/models + app/vectorstore)        │
│  · 关系库(SQLAlchemy 2.0 async): users/sessions/messages/     │
│    policy_documents/policy_chunks/reimbursement_drafts/        │
│    agent_logs         (开发:SQLite  生产:Postgres)            │
│  · 向量库(可换): 开发 local(numpy余弦)  生产 pgvector         │
│  · 文件存储: uploads/ 原始制度文档与图片                        │
└─────────────────────────────────────────────────────────────┘
```

**要点**：入口层只做"收请求+验身份"；服务层做业务编排但**不碰 LLM 细节**；编排层做"怎么想/调哪些工具"；数据层统一收敛所有存储。管理员"看记录"= 读 `sessions` / `messages` / `agent_logs`，员工越权访问这些接口在入口层就被 `require_admin` 拦掉。

---

## 2. 推荐目录结构（tree）

```
reimbursement-qa/
├── app/
│   ├── main.py                  # FastAPI 入口, 挂载 router
│   ├── core/
│   │   ├── config.py            # pydantic-settings 读 .env
│   │   ├── security.py          # JWT 签发/校验, bcrypt
│   │   ├── database.py          # async engine + session 工厂
│   │   └── logger.py
│   ├── models/                  # SQLAlchemy ORM
│   │   ├── user.py
│   │   ├── session.py
│   │   ├── message.py
│   │   ├── policy_document.py   # 含 policy_chunks
│   │   ├── reimbursement_draft.py
│   │   └── agent_log.py
│   ├── schemas/                 # Pydantic 出入参
│   │   ├── auth.py  chat.py  policy.py  admin.py
│   ├── services/
│   │   ├── auth_service.py
│   │   ├── chat_service.py
│   │   ├── knowledge_service.py
│   │   ├── retrieval_service.py
│   │   ├── llm_service.py
│   │   └── admin_service.py
│   ├── agents/
│   │   ├── orchestrator.py      # deep agent 主循环
│   │   ├── prompts.py
│   │   ├── guardrails.py
│   │   └── tools/
│   │       ├── retrieval_tool.py
│   │       ├── clarify_tool.py
│   │       └── business_tool.py
│   ├── llm/
│   │   ├── base.py              # LLMProvider 抽象
│   │   ├── openai_compat.py     # DeepSeek/通义/GLM 共用
│   │   ├── mock.py              # 离线假模型(开发/测试)
│   │   └── factory.py           # 按 env 选供应商
│   ├── vectorstore/
│   │   ├── base.py
│   │   ├── local_store.py       # numpy 余弦(开发)
│   │   └── pgvector_store.py    # 生产
│   ├── api/
│   │   ├── deps.py              # 依赖注入
│   │   └── routes/
│   │       ├── auth.py  chat.py  upload.py
│   │       ├── knowledge.py  admin.py
│   └── static/uploads/          # 上传原始文件
├── migrations/                  # alembic
├── tests/                       # pytest(含 mock LLM 的 e2e)
├── data/                        # 本地向量索引/sqlite(被 gitignore)
├── .env.example
├── requirements.txt
├── pyproject.toml
├── Dockerfile
└── README.md
```

---

## 3. 数据库表设计

> 主表（用户点名）：`users` / `sessions` / `messages` / `policy_documents` / `reimbursement_drafts`
> 支撑表：`policy_chunks`（向量检索必需）、`agent_logs`（管理员看记录 / 可追溯）

### users — 双角色
```sql
users(
  id            BIGINT PK,
  username      VARCHAR(50) UNIQUE NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  role          VARCHAR(10) NOT NULL DEFAULT 'employee', -- employee | admin
  is_active     BOOLEAN DEFAULT TRUE,
  created_at    TIMESTAMP DEFAULT now()
)
```

### sessions — 一次问答会话
```sql
sessions(
  id         BIGINT PK,
  user_id    BIGINT FK -> users.id,
  title      VARCHAR(200),
  status     VARCHAR(10) DEFAULT 'open',  -- open | closed
  created_at TIMESTAMP,
  updated_at TIMESTAMP
)
```

### messages — 会话内消息
```sql
messages(
  id           BIGINT PK,
  session_id   BIGINT FK -> sessions.id,
  role         VARCHAR(10) NOT NULL,      -- user | assistant | system
  content      TEXT NOT NULL,
  citations    JSON,                       -- [{doc_id, chunk_id, snippet}]
  token_usage  JSON,                       -- {prompt, completion}
  created_at   TIMESTAMP
)
```

### policy_documents — 报销制度文档（管理员维护）
```sql
policy_documents(
  id          BIGINT PK,
  title       VARCHAR(200) NOT NULL,
  source_path VARCHAR(500),                -- static/uploads 下的原文件
  content     TEXT,                        -- 全文(便于回溯)
  status      VARCHAR(10) DEFAULT 'draft', -- draft | published | archived
  version     INT DEFAULT 1,
  created_by  BIGINT FK -> users.id,
  created_at  TIMESTAMP,
  updated_at  TIMESTAMP
)
```

### policy_chunks — 向量分片（检索单元）
```sql
policy_chunks(
  id         BIGINT PK,
  doc_id     BIGINT FK -> policy_documents.id,
  chunk_idx  INT,
  chunk_text TEXT NOT NULL,
  embedding  VECTOR(1536),                 -- pgvector; 本地存为 JSON/numpy
  meta       JSON,                         -- 页码/章节等
  created_at TIMESTAMP
)
```

### reimbursement_drafts — 报销单草稿（员工在对话中生成/暂存）
```sql
reimbursement_drafts(
  id         BIGINT PK,
  user_id    BIGINT FK -> users.id,
  session_id BIGINT FK -> sessions.id NULL,-- 可关联来源会话
  title      VARCHAR(200),
  category   VARCHAR(50),                  -- 差旅|招待|办公...
  amount     NUMERIC(12,2),
  items      JSON,                         -- [{name, amount, note}]
  status     VARCHAR(10) DEFAULT 'draft',  -- draft | submitted
  created_at TIMESTAMP,
  updated_at TIMESTAMP
)
```

### agent_logs — 智能体步骤审计（管理员"看记录"核心）
```sql
agent_logs(
  id          BIGINT PK,
  session_id  BIGINT FK -> sessions.id,
  message_id  BIGINT FK -> messages.id NULL,
  step_type   VARCHAR(20),                 -- retrieval | llm | tool | guardrail
  detail      JSON,                        -- 输入/输出/命中来源/耗时
  created_at  TIMESTAMP
)
```

**设计取舍**：向量字段在开发期用 SQLite + numpy 近似存储（`embedding` 存 JSON 数组），生产切 pgvector 改成 `VECTOR` 类型即可，业务代码不动。

---

## 4. 第一周最小验证目标（MVP 验收标准）

**一句话：证明"员工问制度 → 系统检索 → LLM 回答带引用 → 记录可查"这条主链路在真实代码里跑通，且零外部依赖。**

Week 1 交付清单（全部可本地 `uvicorn` 起）：

1. 工程脚手架按上面目录建好，四层都有，依赖装得动。
2. **鉴权**：员工/管理员两个种子账号，JWT 登录；员工访问管理员接口返回 403。
3. **知识摄取**：管理员上传 1 份 txt/markdown 制度 → 分块 → 嵌入(mock 或本地) → 写入 `policy_chunks`。
4. **向量检索**：给定问题，从 chunks 召回 top-k，拼成上下文。
5. **RAG 问答**：员工 POST 一个问题 → 编排层走 retrieval+llm(mock 可确定性回复) → 返回答案 + `citations` 来源。
6. **记录落库**：会话/消息/agent_logs 全部入库；管理员能列出会话与步骤日志。
7. **可替换验证**：`LLM_PROVIDER=mock` 跑通后，切 `openai_compat` 配一个真 key 再跑一遍，证明供应商切换只改 env。
8. **测试**：2 个 pytest——(a) 双角色鉴权，(b) mock 下的"问→答带引用"端到端。

**Week 1 不做**（防范围蔓延）：真实 PDF/OCR、报销单前端表单、流式输出、重排模型、多租户、权限细粒度。留到第 2–3 周。

---

## 5. 技术栈速查

| 层 | 选型 | 备注 |
|---|---|---|
| Web | FastAPI + Uvicorn | 异步，Pydantic v2 |
| ORM | SQLAlchemy 2.0 (async) | Alembic 迁移 |
| DB(开发) | SQLite | 零依赖 |
| DB(生产) | Postgres + pgvector | 关系+向量统一 |
| 鉴权 | JWT (PyJWT) + bcrypt | 角色 claim |
| LLM | OpenAI 兼容抽象 + mock | DeepSeek/通义/GLM 可换 |
| 向量 | numpy(本地) / pgvector(生产) | 抽象接口统一 |
| 测试 | pytest + httpx(AsyncClient) | mock LLM 离线跑 |
