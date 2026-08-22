# 测试契约与实现规格(对话式会话与长期记忆模块)

本文档是 `backend/tests/` 下全部测试固定的**实现契约**。测试先于实现编写(TDD),
当前全部为红;实现必须让测试逐批变绿。**禁止通过修改测试迁就实现**——若契约确需
变更,先同时更新测试与本文档。

对应需求:`feature.md`(FR-01~09);实施计划:`plan.md`(Step 1~5,后端部分)。

---

## 0. 运行方式

```bash
cd backend
../.venv/Scripts/python.exe -m pytest -q          # 全量
../.venv/Scripts/python.exe -m pytest tests/test_memory_store.py -q   # 单文件
```

- pytest 配置:`backend/pytest.ini`(`pythonpath=.`、`asyncio_mode=auto`)
- 依赖已写入 `requirements.txt`(pytest / pytest-asyncio / httpx),已装入 `.venv`
- 测试环境隔离见 `conftest.py`:临时 sqlite、临时 artifacts 目录、
  `USE_MOCK_AGENT=1`、`VLM_API_KEY=""`。**所有 env 覆盖发生在任何 `app.*`
  导入之前**,因此被测模块读取环境变量的时机必须是导入时或运行时,不得在
  测试内部再要求重新设置。

---

## 1. 新增/改动模块总览

| 模块 | 动作 | 契约测试 |
|---|---|---|
| `app/storage/models.py` | 删 Task/TaskStep,新增 4 表 | engine/api 测试 |
| `app/storage/db.py` | 新增 `init_db()` | test_conversations_api |
| `app/storage/artifact_store.py` | `save_screenshot` 改签名 | 引擎测试间接覆盖 |
| `app/memory/store.py` | 新建 MemoryStore | test_memory_store |
| `app/memory/retriever.py` | 新建 | test_memory_store |
| `app/memory/extractor.py` | 新建 | test_memory_store |
| `app/agent/schemas.py` | 加 `ACTION_ASK`、AgentInput 3 字段 | test_chat_engine |
| `app/agent/gui_agent.py` | MockGuiAgent 对话脚本重写 | test_chat_engine |
| `app/agent/chat_agent.py` | 新建 ChatGuiAgent(测试未直接覆盖,见 §3.3) | — |
| `app/agent/{router,escalation,react_agent,simple_agent,vlm_agent}.py` | 删除 | import 失败即红 |
| `app/runtime/session.py` | 重写为 ConversationSession | test_chat_engine |
| `app/runtime/events.py` | 新事件常量 + `WSEvent.conversation_id` | test_chat_engine |
| `app/runtime/engine.py` | 重写为 ConversationEngine | test_chat_engine |
| `app/ws/task_stream.py` → `app/ws/conversation_stream.py` | 改路径 | API mock 链路间接覆盖 |
| `app/api/conversations.py` | 新建 | test_conversations_api |
| `app/api/memory.py` | 新建(必须暴露模块级 `get_store`) | test_conversations_api |
| `app/api/tasks.py` | 删除 | test_conversations_api |
| `app/main.py` | 路由注册更新、StaticFiles、lifespan | test_conversations_api |

---

## 2. 存储层

### 2.1 `app/storage/models.py`

删除 `Task` / `TaskStep`,新增(字段名与测试访问的字段严格一致):

```python
class Conversation(Base):
    __tablename__ = "conversations"
    id: str (pk, uuid)          # title 可空;device_id 可空(=无设备,mock 链路)
    title: str | None
    device_id: str | None
    status: str                 # 建议 "idle";API 测试允许 idle/active/pending
    created_at / updated_at: datetime

class Turn(Base):
    __tablename__ = "turns"
    id: str (pk, uuid)
    conversation_id: FK(conversations.id)
    user_message_id: str | None     # 测试断言完成后非空
    status: str                     # pending/running/waiting_ask/waiting_confirm/
                                    # finished/failed/stopped
    max_steps: int = 20             # 引擎创建时取 settings.MAX_STEPS(运行时读!)
    error: Text | None
    created_at: datetime
    finished_at: datetime | None

class Message(Base):
    __tablename__ = "messages"
    id, conversation_id FK, turn_id FK nullable
    role: str        # "user" | "assistant"
    kind: str        # "text" | "ask" | "risk" | "summary"
    content: Text
    extra: Text|None # JSON(options 等)
    created_at       # 默认 utcnow,微秒精度——测试依赖时间排序稳定性

class TurnStep(Base):
    __tablename__ = "turn_steps"
    id, turn_id FK, step_index: int
    action: str|None, parameters: Text(JSON), status: str = "completed"
    screenshot_path: str|None, raw_output: Text|None
    risk_level: str = "safe", created_at
    # 保留 set_parameters/get_parameters 辅助(与旧 TaskStep 同风格)
```

### 2.2 `app/storage/db.py`

```python
def init_db() -> None
```

- 幂等:`DROP TABLE IF EXISTS tasks`、`task_steps`,再 `create_all`
- `init_db` 内部延迟 import `app.storage.models`(避免循环导入)
- 测试手工建旧表 → 调 `init_db()` → 断言旧表消失、`conversations`/`messages` 存在

### 2.3 `app/storage/artifact_store.py`

```python
def save_screenshot(conversation_id: str, turn_id: str,
                    step_index: int, image: PIL.Image.Image) -> str
```

- 落盘到 `{ARTIFACTS_DIR}/conversations/{conversation_id}/turns/{turn_id}/step_{i}.png`
- **返回相对 ARTIFACTS_DIR 的 posix 相对路径**(如
  `conversations/<cid>/turns/<tid>/step_0.png`),供前端拼 `/artifacts/` URL;
  不要返回 Windows 绝对路径

---

## 3. 记忆模块 `app/memory/`

### 3.1 `store.py` — MemoryStore

```python
class MemoryStore:
    def __init__(self, base_dir: str | Path)          # 目录可注入(测试用 tmp_path)
    def ensure_files(self) -> None                    # 不存在则创建三个空 md
    def add(self, kind: str, content: str,
            task_type: str = "", date: str | None = None) -> None
    def update(self, match: str, content: str) -> bool
    def delete(self, file: str, index: int) -> bool   # file ∈ {"preferences","paths"}
    def clear(self) -> None
    def preferences(self) -> list[str]                # 内容列表(不含日期)
    def paths(self) -> dict[str, list[str]]           # task_type -> 内容列表,保持文件顺序
    def rebuild_index(self) -> None                   # 重新生成 MEMORY.md
    def apply(self, ops: list[dict]) -> None          # 执行 extractor 输出
```

文件与格式(编码 utf-8,位于 `base_dir` 下):

- `preferences.md`:每行一条 `- [YYYY-MM-DD] 内容`
- `paths.md`:`## {task_type}` 小节 + 条目行(同上格式)
- `MEMORY.md`:索引,`rebuild_index()` 重生成;**必须包含全部条目内容与
  task_type 关键字**(测试断言索引文本里能找到「用户常用高德地图」「查航班」)

语义规则:

- `add` 同内容已存在 → 不新增条目(仅刷新日期),条目数不变(去重不累积)
- `update` 按**条目原文完全匹配**在三个文件中查找并替换 content,返回是否命中
- `delete` 按 0-based index 删除指定文件条目,越界返回 False
- 并发安全:模块级 `threading.Lock` 包裹所有读写

`apply(ops)` 的 op 结构(extractor `parse_ops` 的输出):

```python
{"op": "add", "kind": "preference"|"path", "content": str, "task_type": str}
{"op": "update", "match": str, "content": str}
```

另需模块级默认单例:

```python
def get_store() -> MemoryStore   # 惰性创建 MemoryStore(Path("data/memory"))
```

### 3.2 `retriever.py`

```python
def build_memory_context(instruction: str,
                         store: MemoryStore | None = None) -> str
```

- `store=None` 时用 `get_store()`;先 `ensure_files()`
- 注入内容:preferences **全量** + paths 按 `task_type in instruction` 关键词命中的
  小节;无任何命中时取**最近 3 个小节**(文件顺序的最后 3 个,最旧不注入)
- **总长度硬上限 4000 字符**(测试断言 `len(ctx) <= 4000`)
- 第二参数 `store` 供测试注入,签名必须带默认值 None

### 3.3 `extractor.py`

```python
def parse_ops(raw: str) -> list[dict]
def extract_turn_memory(instruction: str, actions: list, summary: str,
                        ask_qa: str = "") -> list[dict]
def detect_explicit_memory(text: str) -> str | None
```

- `parse_ops`:JSON 容错——接受裸 JSON 与 ```json 围栏;解析失败/空串/`{}` → `[]`;
  输出统一加 `"op"` 键(add/update,字段同 §3.1)
- `extract_turn_memory`:无 `VLM_API_KEY` 或调用失败 → **静默返回 `[]`,不抛异常**
  (VLM 调用本身测试不覆盖,monkeypatch 掉)
- `detect_explicit_memory`:正则识别「记住…」「以后都…」「记住我喜欢…」等句式,
  返回提取的记忆内容文本;普通指令(「帮我打开设置」「导航去机场」)→ `None`

> `app/agent/chat_agent.py`(ChatGuiAgent)无直接单测:按 plan Step 2 实现
> (VlmGuiAgent 演进 + ASK 分支 + summarize_turn),其行为通过引擎集成测试与
> Step 8 真机冒烟验证。

---

## 4. Agent 层

### 4.1 `app/agent/schemas.py`

```python
ACTION_ASK = "ASK"        # 追加进 ALL_ACTIONS

@dataclass AgentInput:
    ...                    # 现有字段不变
    conversation_context: List[Dict] = field(default_factory=list)
    memory_text: str = ""
    ask_reply: str = ""
```

ASK 参数 schema:`{"question": str, "options": List[str] (可选)}`。
删除 `ROUTE_*` 常量(路由体系移除)。

### 4.2 `app/agent/gui_agent.py` — MockGuiAgent 对话脚本

| step_count | 输出 |
|---|---|
| 0 | `CLICK {"point":[500,500]}` |
| 1 | `ASK {"question": 非空, "options": [...]}` |
| 2 | 非 ASK 的普通动作(收到 ask_reply 后继续) |
| ≥3 | `COMPLETE {}` |

- `summarize_turn(input_data) -> str`:返回非空固定文案
- 保留 `reset()`、`last_ui_state`、`act(input) -> AgentOutput` 签名
- ASK 步 risk_level 必须为默认 safe(不得触发风险等待)

---

## 5. 运行时

### 5.1 `app/runtime/session.py`

```python
class ConversationSession:
    def __init__(self, conversation_id: str)
```

| 属性 | 初始值 |
|---|---|
| `conversation_id` | 构造参数 |
| `status` | `"pending"` |
| `stop_requested` | `False` |
| `waiting_ask` / `waiting_confirm` | `False` / `False` |
| `ask_event` / `confirm_event` | `asyncio.Event()`(未 set) |
| `ask_reply` | `""` |
| `confirm_approved` | `False` |

不再有 route/escalation/pause 字段。

### 5.2 `app/runtime/events.py`

```python
MESSAGE_CREATED = "message.created"      TURN_STARTED  = "turn.started"
TURN_FINISHED  = "turn.finished"         TURN_FAILED   = "turn.failed"
TURN_STOPPED   = "turn.stopped"          STEP_STARTED  = "step.started"
STEP_COMPLETED = "step.completed"        ASK_REQUESTED = "ask.requested"
ASK_ANSWERED   = "ask.answered"          RISK_DETECTED = "risk.detected"
RISK_OBSERVED  = "risk.observed"         MEMORY_UPDATED = "memory.updated"

class WSEvent(BaseModel):
    event: str
    conversation_id: str          # 原 task_id 字段改名,所有事件必带
    data: Dict[str, Any] = {}
    timestamp: str
```

### 5.3 `app/runtime/engine.py` — ConversationEngine

```python
class ConversationEngine:
    def __init__(self, agent_factory=None, memory_store=None): ...
    async def start_turn(self, conversation_id: str, text: str) -> Turn
    async def reply_ask(self, conversation_id: str, text: str) -> None
    def confirm(self, conversation_id: str, approved: bool) -> None   # 同步
    def stop_turn(self, conversation_id: str) -> None                 # 同步
```

**模块级导入(可 monkeypatch 点,签名不得变)**:

```python
from app.memory.extractor import detect_explicit_memory, extract_turn_memory
from app.memory.retriever import build_memory_context
```

引擎通过这三个名字调用记忆逻辑(测试在 `app.runtime.engine` 命名空间 patch)。

**方法语义**:

- `start_turn`:
  - 同会话已有活动 Turn(session 表中存在)→ `raise ValueError`(API 转 400)
  - 创建 user Message(kind=text)+ Turn(`user_message_id` 回填,
    `max_steps=settings.MAX_STEPS` **运行时读取**——测试会临时 patch 成 2)
  - 广播 `message.created`、`turn.started`,启动后台 `_run_turn` 协程
  - 返回创建的 Turn 对象(含 `.id`)
- `reply_ask`:无活动 session 或非 `waiting_ask` → `ValueError`;
  否则写 user Message(text)、广播 `message.created` + `ask.answered`、
  `session.ask_reply = text`、`ask_event.set()`
- `confirm`:无活动 session 或非 `waiting_confirm` → `ValueError`;
  否则 `confirm_approved = approved`、`confirm_event.set()`
- `stop_turn`:无活动 session → `ValueError`;否则 `stop_requested=True` 且
  **`ask_event` 与 `confirm_event` 都必须 set**(防 ASK 悬挂——有专门测试)

**`_run_turn` 循环语义**(沿用旧 `_run_task` 骨架):

1. `conversation_context`:从 Message 表取该会话最近 6 条(创建序),
   `[{"role","kind","content"}]`,轮次开始时构建一次
2. `memory_text = build_memory_context(text, store=self._memory_store)`,一次性
3. **每个 Turn 调 `agent_factory()` 一次新建 agent**(不跨 Turn 复用,防 subgoals 污染);
   `agent_factory=None` 时按 `USE_MOCK_AGENT` 选 MockGuiAgent/ChatGuiAgent
4. 每步:截图(复用 stable_image / controller / placeholder)→
   `AgentInput(..., conversation_context=…, memory_text=…, ask_reply=session.ask_reply)` →
   `agent.act()`(executor 线程)
5. 输出 `ASK`:存 TurnStep + assistant Message(kind=ask,content=question)、
   Turn 置 `waiting_ask`、广播 `ask.requested`、`await ask_event.wait()`;
   恢复后**步进继续前进不回退**(下一 input 的 `step_count` 递增,`ask_reply` 注入)
6. 风险:`assess_output` 保留;medium → `risk.observed`;high → Turn 置
   `waiting_confirm`、广播 `risk.detected`、`await confirm_event.wait()`;
   `approved=False` → 轮次终止(status=`stopped`),**会话必须仍可开新 Turn**
7. **每个 agent 输出各存一条 TurnStep(含 COMPLETE、含 ASK)**——旧引擎风格
8. `COMPLETE` 或 max_steps 耗尽 → 轮次结束,status=`finished`
9. 收尾(仅 finished):`agent.summarize_turn(input)`(executor;异常降级为
   progress 拼接文案)→ assistant Message(kind=summary)→ 广播 `turn.finished`;
   stop/取消 → status=`stopped` + `turn.stopped`;异常 → `failed` + `turn.failed`
10. 记忆写入(收尾时):`detect_explicit_memory(text)` 命中 → `store.add("preference", …)`;
    `extract_turn_memory(...)` 返回 ops → `store.apply(ops)`;任一写入发生 →
    广播 `memory.updated`。所有记忆调用 try/except 静默
11. finally:关闭 db session、从 session 表移除该会话

WS 广播继续走 `app.ws.connection_manager.manager.broadcast(key, dict)`
(测试 monkeypatch 实例方法捕获事件)。

---

## 6. WS 与 REST

### 6.1 `app/ws/conversation_stream.py`(替代 task_stream.py)

`WS /ws/conversations/{conversation_id}`,逻辑与旧 task_stream 相同
(connect/收文本/disconnect)。connection_manager 仅变量改名,广播接口不变。

### 6.2 `app/api/conversations.py`

| 方法 | 路径 | 请求体 | 成功响应 | 错误 |
|---|---|---|---|---|
| POST | `/api/conversations` | `{title?, device_id?}` | 200 `{id,title,device_id,status,created_at,updated_at}` | |
| GET | `/api/conversations` | — | 200 `[{…同上, last_message?}]` | |
| GET | `/api/conversations/{id}` | — | 200 `{…, messages:[Message], turns:[Turn]}` | 404 不存在 |
| DELETE | `/api/conversations/{id}` | — | 200;同时停止活动 Turn;级联删消息/轮次/步骤 | |
| POST | `/api/conversations/{id}/messages` | `{text}` | 200(建议 `{turn_id}`) | 404 / 400 互斥 |
| POST | `/api/conversations/{id}/reply` | `{text}` | 200 | 404 / 400 无 pending ask |
| POST | `/api/conversations/{id}/confirm` | `{approved: bool}` | 200 | 404 / 400 无 pending risk |
| POST | `/api/conversations/{id}/stop` | — | 200 | 404 / 400 无活动 turn |

- Message 响应字段至少含 `id/role/kind/content/created_at`(测试按 `kind` 过滤)
- Turn 响应字段至少含 `id/status/created_at`;detail 的 turns 建议附带各自 steps
- `parameters` 文本 JSON 解析沿用 `field_validator` 模式
- 引擎 `ValueError` → HTTP 400
- 引擎单例:`get_engine()`(与旧风格一致)

### 6.3 `app/api/memory.py`

```python
def get_store() -> MemoryStore   # 模块级函数;测试 monkeypatch 此名字注入测试 store
```

| 方法 | 路径 | 请求体 | 响应 |
|---|---|---|---|
| GET | `/api/memory` | — | `{"preferences": [str], "paths": {task_type: [str]}}` |
| DELETE | `/api/memory/entries` | `{"file": "preferences"|"paths", "index": int}` | 200;条目删除 |
| DELETE | `/api/memory` | — | 200;全部清空 |

### 6.4 `app/main.py`

- 注册 conversations/memory/devices/ws 路由;**不再注册 tasks**
- lifespan:`init_db()`(替代原 create_all)+ 启动恢复(遗留
  `running/waiting_*` Turn 置 `stopped`)+ 建 artifacts 目录
- `app.mount("/artifacts", StaticFiles(directory=settings.ARTIFACTS_DIR, check_dir=False))`
  (import 时目录可能不存在,必须 `check_dir=False`)
- 旧 `/api/tasks` 任何方法 → 404(测试断言)

---

## 7. 测试用例 ↔ 契约映射

### test_memory_store.py(15 用例)

| 用例 | 验证 |
|---|---|
| ensure_files / add_preference / add_path / entries_survive_reopen | §3.1 文件与条目格式、解析 |
| add_same_preference_twice | 去重不累积 |
| update_replaces / update_without_match | §3.1 update 语义 |
| delete_entry_by_index / clear | §3.1 删除语义 |
| rebuild_index_lists_entries | 索引含条目内容与 task_type |
| apply_ops_add_and_update | op 结构执行 |
| context_includes_all_preferences / matches_path_by_keyword / without_keyword_hit_uses_recent_sections / respects_budget_limit | §3.2 检索注入四规则 |
| parse_ops_* ×3 | §3.3 JSON 容错 |
| extract_turn_memory_without_vlm_key | 无 key 降级 `[]` |
| detect_explicit_memory_* ×2 | §3.3 正则识别 |

### test_chat_engine.py(15 用例)

| 用例 | 验证 |
|---|---|
| TestConversationSession::initial_state | §5.1 字段与初始值 |
| TestSchemas ×2 | `ACTION_ASK`、AgentInput 三字段默认值 |
| TestMockGuiAgentScript ×4 | §4.2 脚本表 |
| turn_completes_and_persists | §5.3-4/7/9:user message、summary、TurnStep 数、四事件、conversation_id |
| max_steps_bounds_turn | §5.3 start_turn 运行时读 settings.MAX_STEPS |
| multi_turn_context_carried_over(AC-01) | conversation_context 含前轮 user 消息与 summary |
| memory_text_injected | build_memory_context 注入 AgentInput |
| ask_pauses_then_resumes(AC-02) | §5.3-5:waiting_ask、ask.requested、kind=ask 消息、reply 注入、步进前进 |
| stop_while_waiting_ask_releases_turn | §5.3 stop_turn 双 event |
| reply_ask_without_active_ask_raises | reply_ask ValueError |
| high_risk_requires_confirm(AC-03) | waiting_confirm、risk.detected、批准后 finished、step risk_level |
| risk_cancel_terminates_but_conversation_reusable | 取消 → stopped,会话可开新轮 |
| confirm_without_pending_risk_raises | confirm ValueError |
| same_conversation_rejects_concurrent_turn | 会话互斥 |
| explicit_memory_saved_and_broadcast(AC-04) / auto_extracted_memory_applied(AC-05) | §5.3-10 记忆写入 + memory.updated |

### test_conversations_api.py(12 用例)

| 用例 | 验证 |
|---|---|
| CRUD ×5 | §6.2 前四行 |
| full_mock_flow_ask_reply_summary(AC-08) | 无设备 Mock 全链路:message→ask 卡片→reply→summary→turn finished |
| reply_without_pending_ask_400 / stop_without_active_turn_400 | ValueError→400 映射 |
| tasks_api_gone(AC-09) | 旧接口 404 |
| init_db_drops_legacy_tables | §2.2 |
| memory api ×3(AC-06) | §6.3 |

---

## 8. 实现顺序(分批变绿)

1. **Step 1** models/db/artifact_store → API 测试中 init_db 用例转绿(其余仍红)
2. **Step 3** `app/memory/` → test_memory_store.py 全绿(独立,无依赖)
3. **Step 2** schemas + MockGuiAgent → TestSchemas、TestMockGuiAgentScript 转绿
4. **Step 4** session/events/engine → test_chat_engine.py 全绿
5. **Step 5** ws/api/main → test_conversations_api.py 全绿
6. 全量 `pytest -q` → **42 passed**(红阶段基线:1 failed + 14 errors)

---

## 9. conftest 调试参考

- `db`:每测试 drop_all + create_all(临时库)
- `memory_store`:注入 `tmp_path/memory` 的 MemoryStore
- `captured_events`:捕获 `manager.broadcast` 的所有事件(list[dict])
- `make_engine(factory)`:`ConversationEngine(agent_factory=factory,
  memory_store=memory_store)`,并把 `engine.build_memory_context` 置空;
  测试可再次 monkeypatch `app.runtime.engine` 命名空间覆盖显式/自动提取
- 轮询 helper:`wait_for`(async)/`wait_for_sync`(同步)/`get_turn(turn_id)`
