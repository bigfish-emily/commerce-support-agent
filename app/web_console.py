# ruff: noqa: E501

WEB_CONSOLE_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>E-Commerce After-Sales Agent</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f6f8;
      --surface: #ffffff;
      --surface-soft: #f8fafb;
      --line: #d7dee7;
      --ink: #111827;
      --muted: #5b6472;
      --accent: #0f766e;
      --accent-dark: #115e59;
      --warn-bg: #fff7ed;
      --warn-line: #fdba74;
      --warn-ink: #9a3412;
      --ok-bg: #ecfdf5;
      --ok-line: #86efac;
      --ok-ink: #166534;
      --code-bg: #eef2f6;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
    }
    header {
      background: var(--surface);
      border-bottom: 1px solid var(--line);
      padding: 18px 24px;
    }
    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      max-width: 1440px;
      margin: 0 auto;
    }
    .title-block { min-width: 260px; }
    h1 { margin: 0; font-size: 21px; line-height: 1.2; letter-spacing: 0; }
    .subtitle { margin-top: 6px; color: var(--muted); font-size: 13px; }
    .runtime {
      display: flex;
      align-items: center;
      gap: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface-soft);
      padding: 10px 12px;
      min-width: 360px;
    }
    .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--warn-ink); }
    .dot.live { background: var(--ok-ink); }
    .runtime strong { display: block; font-size: 13px; }
    .runtime span { display: block; margin-top: 2px; color: var(--muted); font-size: 12px; }
    main {
      max-width: 1440px;
      margin: 0 auto;
      padding: 16px 24px 24px;
      display: grid;
      grid-template-columns: 300px minmax(420px, 1fr) 380px;
      gap: 16px;
    }
    section, aside {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .panel-head {
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    h2 { margin: 0; font-size: 14px; line-height: 1.3; }
    .panel-body { padding: 14px 16px; }
    .stack { display: flex; flex-direction: column; gap: 10px; }
    .example {
      width: 100%;
      border: 1px solid var(--line);
      background: var(--surface);
      border-radius: 7px;
      padding: 10px;
      cursor: pointer;
      text-align: left;
    }
    .example:hover { border-color: var(--accent); }
    .example strong { display: block; font-size: 13px; margin-bottom: 4px; }
    .example span { display: block; color: var(--muted); font-size: 12px; line-height: 1.45; }
    label { display: block; color: var(--muted); font-size: 12px; margin-bottom: 5px; }
    input, textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 10px 11px;
      font: inherit;
      background: #fff;
      color: var(--ink);
    }
    textarea { min-height: 118px; resize: vertical; line-height: 1.5; }
    input:focus, textarea:focus {
      outline: 2px solid rgba(15, 118, 110, 0.16);
      border-color: var(--accent);
    }
    .toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    button {
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      border-radius: 7px;
      padding: 9px 12px;
      font: inherit;
      cursor: pointer;
    }
    button:hover { border-color: var(--accent); color: var(--accent-dark); }
    button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    button.primary:hover { background: var(--accent-dark); color: #fff; }
    button.secondary { color: var(--muted); }
    button:disabled { opacity: 0.55; cursor: not-allowed; }
    .notice {
      border: 1px solid var(--warn-line);
      background: var(--warn-bg);
      color: var(--warn-ink);
      border-radius: 7px;
      padding: 10px;
      font-size: 12px;
      line-height: 1.45;
    }
    .notice.live {
      border-color: var(--ok-line);
      background: var(--ok-bg);
      color: var(--ok-ink);
    }
    .chat {
      display: flex;
      flex-direction: column;
      gap: 12px;
      min-height: 360px;
      max-height: calc(100vh - 390px);
      overflow: auto;
      padding-right: 4px;
    }
    .bubble {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 11px 12px;
      white-space: pre-wrap;
      line-height: 1.55;
      font-size: 14px;
    }
    .bubble.user { background: #eef8f7; border-color: #c7e7e2; margin-left: 30px; }
    .bubble.agent { background: var(--surface-soft); margin-right: 30px; }
    .bubble.system { background: var(--warn-bg); border-color: var(--warn-line); color: var(--warn-ink); }
    .bubble-label { color: var(--muted); font-size: 11px; margin-bottom: 5px; text-transform: uppercase; }
    .status { color: var(--muted); font-size: 12px; min-height: 18px; }
    pre {
      margin: 0;
      white-space: pre-wrap;
      overflow: auto;
      background: var(--code-bg);
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 11px;
      max-height: calc(100vh - 260px);
      font-size: 12px;
      line-height: 1.45;
    }
    .quick-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .small { font-size: 12px; color: var(--muted); }
    @media (max-width: 1120px) {
      main { grid-template-columns: 1fr; }
      .runtime { min-width: 0; width: 100%; }
      .topbar { align-items: flex-start; flex-direction: column; }
      .chat { max-height: none; }
    }
  </style>
</head>
<body>
  <header>
    <div class="topbar">
      <div class="title-block">
        <h1>E-Commerce After-Sales Case Agent</h1>
        <div class="subtitle">面向客服与售后的 Agent 控制台：case 决策、工具调用、RAG、HITL、轨迹回放</div>
      </div>
      <div class="runtime" aria-live="polite">
        <div class="dot" id="runtimeDot"></div>
        <div>
          <strong id="runtimeTitle">检测运行模式中...</strong>
          <span id="runtimeMeta">读取 /runtime/status</span>
        </div>
      </div>
    </div>
  </header>
  <main>
    <aside>
      <div class="panel-head"><h2>业务任务模板</h2></div>
      <div class="panel-body stack" id="examples"></div>
    </aside>
    <section>
      <div class="panel-head">
        <h2>对话执行</h2>
        <div class="toolbar">
          <button class="secondary" onclick="newSession()">新 session</button>
          <button class="secondary" onclick="clearChat()">清空</button>
        </div>
      </div>
      <div class="panel-body stack">
        <div id="modeNotice" class="notice">当前如果没有配置 OPENAI_API_KEY，只能展示确定性工作流，不代表真正 LLM Agent。</div>
        <div>
          <label for="session">Session ID</label>
          <input id="session" value="" autocomplete="off" />
        </div>
        <div>
          <label for="message">用户输入</label>
          <textarea id="message">health beauty 类目有什么运营风险？</textarea>
        </div>
        <div class="toolbar">
          <button class="primary" id="sendButton" onclick="sendMessage()">发送请求</button>
          <button onclick="fillMessage('yes')">确认 yes</button>
          <button onclick="fillMessage('no')">取消 no</button>
        </div>
        <div class="status" id="status"></div>
        <div class="chat" id="chat" aria-live="polite"></div>
      </div>
    </section>
    <aside>
      <div class="panel-head"><h2>Trace 与观测</h2></div>
      <div class="panel-body stack">
        <div class="quick-grid">
          <button onclick="loadSummary()">Summary</button>
          <button onclick="loadTraces()">Session Trace</button>
        </div>
        <div class="small">Trace 展示 route intent、状态、延迟和 trajectory_json，用来判断 Agent 到底做了什么。</div>
        <pre id="observability"></pre>
      </div>
    </aside>
  </main>
  <script>
    const examples = [
      { label: "类目运营风险", text: "health beauty 类目有什么运营风险？", desc: "检索策略：query rewrite 后命中 health_beauty 统计。" },
      { label: "订单事实查询", text: "帮我查一下订单 203096f03d82e0dffbc41ebc2e2bcfb7 的状态", desc: "确定性订单工具：状态、延迟、支付、评价。" },
      { label: "政策问答", text: "退款补偿能不能直接承诺？", desc: "政策 KB 检索和 grounded answer。" },
      { label: "多意图 + HITL", text: "查订单 203096f03d82e0dffbc41ebc2e2bcfb7 状态，并且说明退款政策，然后申请退款", desc: "只读任务先执行，售后 case 决策后进入确认门。" },
      { label: "退款 Case", text: "给订单 203096f03d82e0dffbc41ebc2e2bcfb7 申请退款", desc: "生成结构化决策和客户回复草稿，确认后返回 REFUND 工具结果。" },
      { label: "取消拦截", text: "取消订单 203096f03d82e0dffbc41ebc2e2bcfb7", desc: "已送达订单会被决策层拒绝，不进入副作用执行。" },
      { label: "越界请求", text: "帮我写一个操作系统内核", desc: "测试 input guard 拒绝非业务请求。" }
    ];
    function renderExamples() {
      const box = document.getElementById("examples");
      box.innerHTML = "";
      examples.forEach(item => {
        const btn = document.createElement("button");
        btn.className = "example";
        btn.innerHTML = `<strong>${item.label}</strong><span>${item.desc}</span>`;
        btn.onclick = () => fillMessage(item.text);
        box.appendChild(btn);
      });
    }
    function fillMessage(text) {
      document.getElementById("message").value = text;
      document.getElementById("message").focus();
    }
    function addBubble(text, cls, label) {
      const div = document.createElement("div");
      div.className = "bubble " + cls;
      const head = document.createElement("div");
      head.className = "bubble-label";
      head.textContent = label;
      const body = document.createElement("div");
      body.textContent = text;
      div.appendChild(head);
      div.appendChild(body);
      document.getElementById("chat").appendChild(div);
      div.scrollIntoView({ block: "end" });
    }
    function newSession() {
      const id = "manual-" + Date.now().toString(36);
      document.getElementById("session").value = id;
      addBubble("已切换到新 session：" + id, "system", "system");
    }
    function clearChat() {
      document.getElementById("chat").innerHTML = "";
      document.getElementById("status").textContent = "";
    }
    async function sendMessage() {
      const status = document.getElementById("status");
      const sendButton = document.getElementById("sendButton");
      const message = document.getElementById("message").value.trim();
      const session_id = document.getElementById("session").value.trim() || "manual-demo";
      if (!message) {
        status.textContent = "请输入任务内容。";
        return;
      }
      sendButton.disabled = true;
      status.textContent = "请求中...";
      addBubble(message, "user", "user");
      try {
        const res = await fetch("/chat", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({message, session_id})
        });
        const data = await res.json();
        const sourceText = (data.sources || []).length ? " · sources: " + data.sources.join(", ") : "";
        status.textContent = "HTTP " + res.status + sourceText;
        addBubble(data.answer || JSON.stringify(data), "agent", "agent");
      } catch (error) {
        status.textContent = "请求失败：" + error;
      } finally {
        sendButton.disabled = false;
      }
    }
    async function loadSummary() {
      const res = await fetch("/observability/summary");
      document.getElementById("observability").textContent = JSON.stringify(await res.json(), null, 2);
    }
    async function loadTraces() {
      const session = document.getElementById("session").value.trim() || "manual-demo";
      const res = await fetch("/observability/traces/" + encodeURIComponent(session) + "?limit=20");
      document.getElementById("observability").textContent = JSON.stringify(await res.json(), null, 2);
    }
    async function loadRuntimeStatus() {
      const res = await fetch("/runtime/status");
      const data = await res.json();
      const live = data.mode === "live_llm_agent";
      document.getElementById("runtimeDot").classList.toggle("live", live);
      document.getElementById("runtimeTitle").textContent = live ? "Live LLM Agent 模式" : "Offline Workflow Demo 模式";
      document.getElementById("runtimeMeta").textContent = data.model + " · " + data.base_url;
      const notice = document.getElementById("modeNotice");
      notice.classList.toggle("live", live);
      notice.textContent = live
        ? "当前已连接真实 LLM：模型会参与任务规划、抽槽和回答生成；工具执行仍由后端确定性系统治理。"
        : "当前没有配置真实 LLM key：系统只展示确定性工作流和本地 fallback，不能作为完整 Agent 能力展示。";
    }
    renderExamples();
    document.getElementById("session").value = "manual-" + Date.now().toString(36);
    loadRuntimeStatus();
  </script>
</body>
</html>
"""
