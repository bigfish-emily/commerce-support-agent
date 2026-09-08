const examples = [
  {
    label: "订单进度",
    text: "帮我查一下订单 203096f03d82e0dffbc41ebc2e2bcfb7 的状态",
    desc: "直接查询订单、物流、支付和评价事实。",
  },
  {
    label: "退款政策",
    text: "我的订单延迟了，退款补偿能不能直接承诺？",
    desc: "检索政策、FAQ 和规则后生成用户可读回答。",
  },
  {
    label: "申请退款",
    text: "给订单 203096f03d82e0dffbc41ebc2e2bcfb7 申请退款",
    desc: "生成待审核 case，用户不能自行确认写动作。",
  },
  {
    label: "多意图售后",
    text: "查订单 203096f03d82e0dffbc41ebc2e2bcfb7 状态，并且说明退款政策，然后申请退款",
    desc: "只读任务先完成，退款申请进入人工审核。",
  },
  {
    label: "取消订单",
    text: "取消订单 203096f03d82e0dffbc41ebc2e2bcfb7",
    desc: "已送达订单会被决策层拦截并解释原因。",
  },
  {
    label: "越界请求",
    text: "帮我写一个操作系统内核",
    desc: "测试 input guard 拒绝非电商售后请求。",
  },
];

function renderExamples() {
  const box = document.getElementById("examples");
  box.innerHTML = "";
  examples.forEach((item) => {
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
  const id = "customer-" + Date.now().toString(36);
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
  const session_id = document.getElementById("session").value.trim() || "customer-demo";
  if (!message) {
    status.textContent = "请输入问题。";
    return;
  }
  sendButton.disabled = true;
  status.textContent = "请求中...";
  addBubble(message, "user", "customer");
  try {
    const res = await fetch("/customer/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id, user_id: "customer-demo" }),
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

async function loadRuntimeStatus() {
  const res = await fetch("/runtime/status");
  const data = await res.json();
  const notice = document.getElementById("modeNotice");
  const live = data.mode === "live_llm_agent";
  notice.classList.toggle("live", live);
  notice.textContent = live
    ? `已连接真实 LLM：${data.model}。模型参与任务规划、抽槽和回答生成，工具执行由后端治理。`
    : "当前是离线工作流模式：可测试接口、工具治理、HITL 和审核链路；配置真实模型后可测试完整 Agent 能力。";
}

renderExamples();
document.getElementById("session").value = "customer-" + Date.now().toString(36);
loadRuntimeStatus();
