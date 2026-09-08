function currentSession() {
  return document.getElementById("session").value.trim() || "customer-demo";
}

function setStatus(text) {
  document.getElementById("status").textContent = text;
}

function writeJson(value) {
  document.getElementById("observability").textContent = JSON.stringify(value, null, 2);
}

function reviewHeaders() {
  const token = document.getElementById("reviewToken").value.trim() || "local-review-demo";
  return {
    "Content-Type": "application/json",
    "X-Review-Token": token,
  };
}

function renderCaseSummary(data) {
  const box = document.getElementById("caseSummary");
  const draft = data.escalation_draft || {};
  const decision = draft.decision || {};
  const verification = draft.verification || {};
  const rows = [
    ["状态", data.has_pending ? "待审核" : "无待审核 case"],
    ["动作", draft.action_type || "-"],
    ["订单", draft.order_id || "-"],
    ["决策", decision.outcome || "-"],
    ["风险等级", decision.risk_level || "-"],
    ["下一步", verification.required_next_step || "-"],
  ];
  box.innerHTML = rows
    .map(([key, value]) => `<div class="metric-row"><span>${key}</span><strong>${value}</strong></div>`)
    .join("");
}

async function loadReview() {
  const session = currentSession();
  const res = await fetch("/review/sessions/" + encodeURIComponent(session), {
    headers: reviewHeaders(),
  });
  const data = await res.json();
  setStatus(data.has_pending ? "已读取待审核 case。" : "当前 session 没有待审核 case。");
  renderCaseSummary(data);
  writeJson(data);
}

async function approveReview() {
  const session = currentSession();
  const res = await fetch("/review/sessions/" + encodeURIComponent(session) + "/approve", {
    method: "POST",
    headers: reviewHeaders(),
    body: JSON.stringify({
      reviewer_id: "after-sales-demo",
      role: "after_sales_operator",
      auth_scopes: ["after_sales:write"],
    }),
  });
  const data = await res.json();
  setStatus("审核通过请求已提交。");
  writeJson(data);
  await loadReview();
}

async function rejectReview() {
  const session = currentSession();
  const res = await fetch("/review/sessions/" + encodeURIComponent(session) + "/reject", {
    method: "POST",
    headers: reviewHeaders(),
    body: JSON.stringify({
      reviewer_id: "after-sales-demo",
      role: "after_sales_operator",
      auth_scopes: ["after_sales:write"],
    }),
  });
  const data = await res.json();
  setStatus("审核驳回请求已提交。");
  writeJson(data);
  await loadReview();
}

async function loadSummary() {
  const res = await fetch("/observability/summary");
  writeJson(await res.json());
}

async function loadCaseMetrics() {
  const res = await fetch("/observability/case-metrics");
  writeJson(await res.json());
}

async function loadTraces() {
  const session = currentSession();
  const res = await fetch("/observability/traces/" + encodeURIComponent(session) + "?limit=20", {
    headers: reviewHeaders(),
  });
  writeJson(await res.json());
}

document.getElementById("session").value = "customer-" + Date.now().toString(36);
loadCaseMetrics();
