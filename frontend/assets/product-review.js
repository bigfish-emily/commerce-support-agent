const $ = id => document.getElementById(id);
const names = {refund_request: '退款申请', cancel_order: '取消订单', change_address: '修改收货地址', invoice_request: '发票申请', complaint_escalation: '投诉处理', open_support_case: '售后跟进'};
const statusNames = {delivered: '已送达', shipped: '配送中', canceled: '已取消', created: '待处理', approved: '已确认'};
const senderNames = {customer: '客户', assistant: '智能客服', staff: '人工客服'};
let token = '';
let selected = null;
let loadingConversation = false;

async function api(path, options = {}) {
  const headers = {'X-Review-Token': token, ...(options.headers || {})};
  if (options.body) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, {...options, headers});
  if (!response.ok) throw new Error(response.status === 403 ? '访问凭证无效或权限不足。' : '操作未完成，请刷新后重试。');
  return response.json();
}

function setStatus(text) {$('notice').textContent = text;}
function addFact(label, value) {
  const row = document.createElement('div');
  const term = document.createElement('dt');
  const detail = document.createElement('dd');
  term.textContent = label;
  detail.textContent = value == null || value === '' ? '未提供' : String(value);
  row.append(term, detail);
  $('facts').append(row);
}
function fillList(id, values) {
  $(id).replaceChildren();
  for (const value of values?.length ? values : ['暂无依据']) {
    const item = document.createElement('li');
    item.textContent = value;
    $(id).append(item);
  }
}
function formatTimestamp(value) {return value ? value.slice(0, 16).replace('T', ' ') : '刚刚';}
function pct(value) {return `${Math.round((value || 0) * 10000) / 100}%`;}

function metricCard(title, value, note) {
  const card = document.createElement('div');
  card.className = 'metric-card';
  const heading = document.createElement('strong');
  const number = document.createElement('b');
  const caption = document.createElement('small');
  heading.textContent = title;
  number.textContent = value;
  caption.textContent = note;
  card.append(heading, number, caption);
  return card;
}

async function renderMetrics() {
  const panel = $('quality');
  try {
    const data = await api('/review/metrics');
    const product = data.product_evaluation || {};
    const core = product.core || {};
    const planner = product.llm_planner || {};
    const ablation = product.ablation || {};
    panel.replaceChildren();
    const title = document.createElement('h2');
    title.textContent = '质量概览';
    const grid = document.createElement('div');
    grid.className = 'metric-grid';
    grid.append(
      metricCard('售后主链路', core.case_oracle_pass || '-', `转审核 P/R ${core.handoff_precision || '-'}/${core.handoff_recall || '-'}`),
      metricCard('真实 LLM 规划', planner.exact_task_plan || '-', `${planner.live_planner_turns || '-'} 真实规划 · P95 ${planner.p95_latency_ms || '-'}ms`),
      metricCard('风险门禁', core.forbidden_cancellation || '-', `禁取消写入 · 安全自动 ${core.safe_auto_resolution || '-'}`),
      metricCard('消融对照', (ablation.execute_on_intent || {}).forbidden_cancellation || '-', '识别即写会错误取消；完整门禁为 0/1'),
    );
    const scope = document.createElement('p');
    scope.className = 'metric-scope';
    scope.textContent = product.scope || '本地 trace 仅用于排障，不与业务 KPI 混算。';
    panel.append(title, grid, scope);
    panel.hidden = false;
  } catch (_error) {
    panel.hidden = true;
  }
}

async function renderConversation() {
  if (!selected || loadingConversation || document.hidden) return;
  loadingConversation = true;
  const id = selected.session_id;
  try {
    const data = await api(`/review/sessions/${encodeURIComponent(id)}/messages`);
    if (!selected || selected.session_id !== id) return;
    $('conversation').replaceChildren();
    for (const message of data.messages) {
      const row = document.createElement('div');
      row.className = 'desk-message';
      const label = document.createElement('strong');
      const content = document.createElement('p');
      label.textContent = senderNames[message.sender] || '系统';
      content.textContent = message.content;
      row.append(label, content);
      $('conversation').append(row);
    }
    $('conversation').scrollTop = $('conversation').scrollHeight;
    $('takeover').disabled = data.human;
    $('automatic').disabled = !data.human;
  } catch (error) {
    $('replyStatus').textContent = error.message;
  } finally {
    loadingConversation = false;
  }
}

async function openConversation(record) {
  selected = record;
  $('detail').hidden = false;
  $('result').textContent = '';
  $('replyStatus').textContent = '';
  $('approve').disabled = true;
  $('reject').disabled = true;
  document.querySelectorAll('.queue-item').forEach(button => button.classList.toggle('selected', button.dataset.session === record.session_id));
  try {
    const data = await api(`/review/sessions/${encodeURIComponent(record.session_id)}`);
    const caseRecord = data.after_sales_cases?.at(-1) || {};
    const draft = data.escalation_draft || {};
    const decision = draft.decision || caseRecord.decision || {};
    const facts = data.order_facts || {};
    const isCase = data.after_sales_cases?.length || data.has_pending;
    $('caseTitle').textContent = isCase ? (names[record.action_type] || '售后申请') : '客户咨询';
    $('caseStatus').textContent = data.has_pending ? '待审核' : (isCase ? '已处理' : '咨询中');
    $('request').textContent = caseRecord.user_request || (isCase ? '售后申请正在生成处理依据。' : '尚未创建售后申请，请先了解客户的具体诉求。');
    $('facts').replaceChildren();
    addFact('订单号', facts.order_id || record.order_id);
    addFact('订单状态', statusNames[facts.order_status] || facts.order_status);
    addFact('支付金额', facts.payment_value == null ? null : `R$ ${facts.payment_value}`);
    addFact('配送延迟', facts.delay_days == null ? null : `${facts.delay_days} 天`);
    fillList('evidence', decision.customer_message_points || decision.evidence || []);
    fillList('policies', decision.policy_refs || []);
    $('approve').disabled = !data.has_pending;
    $('reject').disabled = !data.has_pending;
    if (isCase && !data.has_pending) $('result').textContent = '该申请已经处理完成，没有待恢复的审核流程。';
    setStatus('');
    await renderConversation();
  } catch (error) {
    setStatus(error.message);
  }
}

async function refreshQueue() {
  const data = await api('/review/conversations');
  $('queue').replaceChildren();
  $('count').textContent = data.cases.length;
  setStatus(data.cases.length ? '选择一个客户会话以查看详情。' : '当前没有客户会话。');
  for (const record of data.cases) {
    const button = document.createElement('button');
    const meta = document.createElement('small');
    button.type = 'button';
    button.className = 'queue-item';
    button.dataset.session = record.session_id;
    button.textContent = names[record.action_type] || '客户咨询';
    meta.textContent = formatTimestamp(record.updated_at);
    button.append(meta);
    button.onclick = () => openConversation(record);
    $('queue').append(button);
  }
}

async function updateHandoff(human) {
  if (!selected) return;
  try {
    await api(`/review/sessions/${encodeURIComponent(selected.session_id)}/handoff`, {method: 'POST', body: JSON.stringify({human})});
    $('replyStatus').textContent = human ? '已接手会话，智能客服将暂停自动回复。' : '已恢复智能客服。';
    await renderConversation();
  } catch (error) {$('replyStatus').textContent = error.message;}
}

async function sendReply(event) {
  event.preventDefault();
  if (!selected) return;
  const content = $('reply').value.trim();
  if (!content) return;
  $('replySend').disabled = true;
  try {
    await api(`/review/sessions/${encodeURIComponent(selected.session_id)}/messages`, {method: 'POST', body: JSON.stringify({content})});
    $('reply').value = '';
    $('replyStatus').textContent = '已发送给客户。';
    await renderConversation();
  } catch (error) {$('replyStatus').textContent = error.message;} finally {$('replySend').disabled = false;}
}

async function suggestReply() {
  if (!selected) return;
  $('suggest').disabled = true;
  $('replyStatus').textContent = '正在生成建议…';
  try {
    const data = await api(`/review/sessions/${encodeURIComponent(selected.session_id)}/suggestion`, {method: 'POST'});
    $('reply').value = data.draft;
    $('attention').textContent = `${data.attention}${data.signals.length ? `；本轮用词：${data.signals.join('、')}` : ''}。${data.signal_method}`;
    $('replyStatus').textContent = '建议已填入草稿，请核对后发送。';
  } catch (error) {$('replyStatus').textContent = error.message;} finally {$('suggest').disabled = false;}
}

async function decide(action) {
  if (!selected) return;
  $('approve').disabled = true;
  $('reject').disabled = true;
  try {
    await api(`/review/sessions/${encodeURIComponent(selected.session_id)}/${action}`, {method: 'POST', body: JSON.stringify({reviewer_id: 'local-reviewer', role: 'after_sales_operator', auth_scopes: ['after_sales:write']})});
    $('caseStatus').textContent = '已处理';
    $('result').textContent = action === 'approve' ? '已批准申请，系统将继续处理。' : '已驳回申请，客户会看到处理结果。';
    await refreshQueue();
    await renderMetrics();
  } catch (error) {$('result').textContent = error.message;}
}

$('login').onsubmit = async event => {
  event.preventDefault();
  token = $('token').value.trim();
  try {
    await refreshQueue();
    await renderMetrics();
    $('login').hidden = true;
    $('refresh').hidden = false;
  } catch (error) {setStatus(error.message);}
};
$('refresh').onclick = () => refreshQueue().catch(error => setStatus(error.message));
$('takeover').onclick = () => updateHandoff(true);
$('automatic').onclick = () => updateHandoff(false);
$('replyForm').onsubmit = sendReply;
$('suggest').onclick = suggestReply;
$('approve').onclick = () => decide('approve');
$('reject').onclick = () => decide('reject');

const replyOptions = document.createElement('div');
replyOptions.className = 'shortcuts';
for (const [label, content] of [
  ['确认收货', '请问您是否已经收到包裹？商品本身是否完好？'],
  ['了解退款原因', '我来协助您处理。请问您希望退款的具体原因是什么？'],
  ['补充问题描述', '为了准确核查，麻烦您说明商品出现了什么问题，以及发现问题的时间。'],
]) {
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = label;
  button.onclick = () => {$('reply').value = content; $('reply').focus();};
  replyOptions.append(button);
}
$('replyForm').before(replyOptions);
setInterval(() => renderConversation(), 5000);
