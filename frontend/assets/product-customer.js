const $ = id => document.getElementById(id);
const session = sessionStorage.getItem('support-session') || crypto.randomUUID();
sessionStorage.setItem('support-session', session);

let selectedOrder = null;
let busy = false;
let lastStatus = '';
let polling = false;
let lastMessageId = 0;
window.customerOrders = [];

const states = {
  pending_review: '申请已受理，等待审核',
  appealed_pending_review: '补充说明已提交，等待复核',
  executed: '申请已处理',
  rejected: '申请未通过审核',
  timeout_canceled: '本次申请已超时关闭',
};

async function api(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error('暂时无法完成操作，请稍后重试。');
  return response.json();
}

function add(message, type = 'agent') {
  const element = document.createElement('div');
  element.className = `message ${type}`;
  element.textContent = message;
  $('chat').append(element);
  $('chat').scrollTop = $('chat').scrollHeight;
}

function formatStatus(status) {
  return ({created: '待付款', approved: '已付款', invoiced: '待发货', processing: '处理中', shipped: '运输中', delivered: '已送达', canceled: '已取消'})[status] || status;
}

function formatCategory(value) {
  return value === 'health_beauty' ? '健康与美容用品' : (value || '商品订单');
}

function chooseOrder(order) {
  selectedOrder = order;
  document.querySelectorAll('.order').forEach(node => node.classList.toggle('selected', node.dataset.orderId === order.order_id));
  $('selectionHint').textContent = `正在查看订单 ${order.order_id.slice(0, 8)}…，可以直接使用下方服务。`;
}

function actionButton(action) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = action.style === 'primary' ? 'action primary' : 'action';
  button.textContent = action.label;
  button.onclick = () => {
    const order = action.order_id ? window.customerOrders.find(item => item.order_id === action.order_id) : selectedOrder;
    if (order) chooseOrder(order);
    sendMessage(action.message || action.label, action.id);
  };
  return button;
}

function renderOrders(payload) {
  window.customerOrders = payload.orders || [];
  const summary = payload.summary || {};
  $('orderCount').textContent = `${summary.order_count || 0} 笔`;
  $('orderSummary').replaceChildren();
  for (const [label, value] of [['在途', summary.active_shipments || 0], ['待留意', summary.attention_count || 0]]) {
    const metric = document.createElement('div');
    metric.innerHTML = `<strong>${value}</strong><span>${label}</span>`;
    $('orderSummary').append(metric);
  }
  $('orders').replaceChildren();
  for (const order of window.customerOrders) {
    const card = document.createElement('article');
    card.className = 'order';
    card.dataset.orderId = order.order_id;
    const top = document.createElement('div');
    top.className = 'order-top';
    top.innerHTML = `<strong>${formatCategory(order.category)}</strong><span class="order-status ${order.needs_attention ? 'attention' : ''}">${formatStatus(order.status)}</span>`;
    const amount = document.createElement('span');
    amount.className = 'price';
    amount.textContent = new Intl.NumberFormat('zh-CN', {style: 'currency', currency: order.currency || 'BRL'}).format(order.amount);
    const meta = document.createElement('small');
    meta.textContent = `订单 ${order.order_id.slice(0, 8)}… · ${String(order.purchased_at || '').slice(0, 10)}`;
    card.append(top, amount, meta);
    if (order.needs_attention) {
      const note = document.createElement('small');
      note.className = 'order-note';
      note.textContent = order.delay_days > 0 ? `预计延迟 ${order.delay_days} 天` : '有售后进度更新';
      card.append(note);
    }
    const actions = document.createElement('div');
    actions.className = 'order-actions';
    (order.actions || []).slice(0, 3).forEach(action => actions.append(actionButton(action)));
    actions.onclick = event => event.stopPropagation();
    card.append(actions);
    $('orders').append(card);
  }
}

function renderFollowUps(actions) {
  if (!actions?.length) return;
  const tray = document.createElement('div');
  tray.className = 'message-actions';
  actions.slice(0, 4).forEach(action => tray.append(actionButton(action)));
  $('chat').append(tray);
  $('chat').scrollTop = $('chat').scrollHeight;
}

async function progress() {
  if (polling || document.hidden) return;
  polling = true;
  try {
    const data = await api(`/customer/sessions/${encodeURIComponent(session)}/progress`);
    if (!data.case) return;
    const current = data.case;
    $('progress').hidden = false;
    $('progress').replaceChildren();
    const title = document.createElement('strong');
    title.textContent = states[current.status] || '申请处理中';
    const detail = document.createElement('small');
    detail.textContent = `服务单 ${current.case_id}`;
    $('progress').append(title, detail);
    if (lastStatus && lastStatus !== current.status) add(states[current.status] || '您的申请状态已更新。', 'update');
    lastStatus = current.status;
  } catch {
    $('status').textContent = '申请进度暂时无法刷新，将自动重试。';
  } finally {
    polling = false;
  }
}

async function sendMessage(message, requestedAction = null) {
  if (!message || busy) return;
  busy = true;
  $('send').disabled = true;
  add(message, 'user');
  $('status').textContent = requestedAction ? '正在准备处理…' : '正在查询…';
  try {
    const data = await api('/customer/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        message,
        session_id: session,
        requested_action: requestedAction,
        page_context: {surface: selectedOrder ? 'order_detail' : 'order_center', selected_order_id: selectedOrder?.order_id || null},
      }),
    });
    add(data.answer);
    renderFollowUps(data.ui_actions);
    $('status').textContent = '';
    await progress();
  } catch (error) {
    $('status').textContent = error.message;
  } finally {
    busy = false;
    $('send').disabled = false;
  }
}

async function send(event) {
  event.preventDefault();
  const message = $('message').value.trim();
  if (!message) return;
  $('message').value = '';
  await sendMessage(message);
}

$('compose').onsubmit = send;
document.querySelectorAll('[data-query]').forEach(button => button.onclick = () => {
  if (button.dataset.action) sendMessage(button.dataset.query, button.dataset.action);
  else {
    $('message').value = button.dataset.query;
    $('message').focus();
  }
});

add('你好，我已经读取了你当前账号的订单。你可以直接问“哪些订单还在路上”，也可以从对应订单卡片发起售后。');
api('/customer/context').then(renderOrders).catch(error => {$('orders').textContent = error.message;});
api('/runtime/status').then(data => {
  if (data.mode !== 'live_llm_agent') {
    $('availability').hidden = false;
    $('availability').textContent = '当前处于基础服务模式，复杂问题会按售后流程继续处理。';
  }
}).catch(() => {});
progress();
setInterval(progress, 6000);

async function receive() {
  if (busy || document.hidden) return;
  const response = await fetch(`/customer/sessions/${encodeURIComponent(session)}/messages`);
  if (!response.ok) return;
  const data = await response.json();
  const last = data.messages.at(-1)?.id || 0;
  if (!last || last === lastMessageId) return;
  $('chat').replaceChildren();
  for (const item of data.messages) add((item.sender === 'staff' ? '人工客服：' : '') + item.content, item.sender === 'customer' ? 'user' : 'agent');
  lastMessageId = last;
}
setInterval(() => receive().catch(() => {}), 4000);
