'use strict';
const REAL_ENGINE = 'lifesim_deepseek';
let engineMode = 'lifesim';
let serviceStatus = null;
let busy = false;
let lastFailedRequest = null;
let modelConsent = false;
let consentResolve = null;
let feedbackTarget = null;
const $ = selector => document.querySelector(selector);
const KEY = 'mirra-research-v2';
const VERSION = 'rule-rehearsal-v2';
const FOOD = ['面食', '家常菜', '轻食', '日式'];
const CATALOG = [
  { id: 'demo-noodle', name: '暖暖面屋', cuisine: '面食', price: 32, distance: 800, ingredients: ['鸡蛋', '小麦', '蔬菜'], source: 'fictional_demo' },
  { id: 'demo-green', name: '一碗清爽', cuisine: '轻食', price: 38, distance: 650, ingredients: ['鸡肉', '蔬菜', '花生'], source: 'fictional_demo' },
  { id: 'demo-home', name: '小满食堂', cuisine: '家常菜', price: 48, distance: 1200, ingredients: ['牛肉', '蔬菜', '米饭'], source: 'fictional_demo' },
  { id: 'demo-japan', name: '日和小馆', cuisine: '日式', price: 78, distance: 1500, ingredients: ['海鲜', '米饭', '鸡蛋'], source: 'fictional_demo' }
];
const uuid = () => crypto.randomUUID ? crypto.randomUUID() : 'demo-' + Date.now() + '-' + Math.random().toString(16).slice(2);
const now = () => new Date().toISOString();
const copy = value => JSON.parse(JSON.stringify(value));
function freshSession() {
  return { id: uuid(), title: '新的消费探索', created_at: now(), updated_at: now(), profile: { budget: null, cuisines: null, excluded: null }, context: { hour: 18, budget: null, coupon: 0, cuisines: null, excluded: [] }, phase: 'start', evidence: [], messages: [], runs: [], pendingPreview: false, activeRunId: null, profileVersion: 0, engine:engineMode==='demo'?'rule_based_demo':null };
}
let sessions = [freshSession()];
let activeId = sessions[0].id;
let consent = false;
try {
  const stored = JSON.parse(localStorage.getItem(KEY) || 'null');
  if (stored?.consent && Array.isArray(stored.sessions) && stored.sessions.length &&
      stored.sessions.every(s => s.id && s.profile && s.context && Array.isArray(s.messages) && Array.isArray(s.runs) && Array.isArray(s.evidence))) {
    sessions = stored.sessions; activeId = sessions.some(s => s.id === stored.activeId) ? stored.activeId : sessions[0].id; consent = true;
  }
} catch { /* Local storage is optional. */ }
const current = () => sessions.find(s => s.id === activeId);
const profileComplete = () => {
  const p = current().profile;
  return p.budget !== null && p.cuisines !== null && p.excluded !== null;
};
function enterWorkbench(focus = true) {
  document.body.classList.remove('entry-active');
  $('#entry-screen').hidden = true;
  try { sessionStorage.setItem('mirra-entry-opened', '1'); } catch {}
  if (focus) {
    if (profileComplete()) $('#message-input').focus();
    else showPanel('profile');
  }
}
const entryWasOpened = (() => { try { return sessionStorage.getItem('mirra-entry-opened') === '1'; } catch { return false; } })();
if (entryWasOpened || current().messages.length || new URLSearchParams(location.search).has('session_id')) enterWorkbench(false);
$('#entry-start').addEventListener('click', () => enterWorkbench());
$('#entry-method').addEventListener('click', () => { enterWorkbench(false); showPanel('evaluation'); });
$('#entry-coupon').addEventListener('click', () => { enterWorkbench(false); showPanel('coupon'); });
$('#back-to-entry').addEventListener('click', () => {
  setInspectorOpen(false);
  $('#sidebar').classList.remove('open');
  $('#entry-screen').hidden = false;
  document.body.classList.add('entry-active');
  try { sessionStorage.removeItem('mirra-entry-opened'); } catch {}
  window.scrollTo(0, 0);
  $('#entry-start').focus();
});
let timer;
function node(tag, className, text) { const e = document.createElement(tag); if (className) e.className = className; if (text !== undefined) e.textContent = text; return e; }
function toast(text) { $('#toast').textContent = text; $('#toast').classList.add('visible'); clearTimeout(timer); timer = setTimeout(() => $('#toast').classList.remove('visible'), 3000); }
function persist() {
  if (!consent) return;
  try { localStorage.setItem(KEY, JSON.stringify({ consent: true, activeId, sessions })); } catch { toast('本机存储不可用，本次会话仍可继续。'); }
}
function add(role, text, extra = {}) {
  const s = current(); const message = { id: uuid(), role, text, at: now(), ...extra };
  s.messages.push(message); s.updated_at = now(); return message;
}
function evidence(field, value, messageId, scope = 'profile') {
  const s = current(); s.evidence.push({ field, value: copy(value), message_id: messageId, source: 'user_explicit_demo_input', scope, at: now() });
  if (scope === 'profile') s.profileVersion++;
}
function setFact(field, value, messageId, scope = 'profile') {
  current()[scope][field] = value; evidence(field, value, messageId, scope);
}
function userTurn(text) { return add('user', text); }
function nextQuestion() {
  const s = current();
  if (s.profile.budget === null && s.context.budget === null) {
    s.phase = 'budget'; add('agent', '先从预算开始。你通常愿意为一餐花多少钱？\n你也可以直接写“预算 50 元”，或告诉我这次的预算。', { options: ['35 元', '50 元', '80 元'] }); return;
  }
  if (s.profile.cuisines === null) {
    s.phase = 'cuisine'; add('agent', '记下了。平常你更喜欢哪类口味？\n如果没有特别偏好，也可以告诉我。', { options: ['面食', '家常菜', '轻食', '没有特别偏好'] }); return;
  }
  if (s.profile.excluded === null) {
    s.phase = 'excluded'; add('agent', '有哪些食材需要避开？\n不知道或不想回答，也可以保留未知。', { options: ['没有特别忌口', '不吃牛肉', '避开花生', '暂时跳过'] }); return;
  }
  s.phase = 'ready'; s.title = '餐饮偏好与选择';
  add('agent', '现在已经有了你的第一份偏好画像。你可以继续补充，也可以开始一个消费情境。\n每次预演会先封存规则预测，再请你独立记录会选什么。', { options: ['开始晚餐预演', '再聊一聊我的偏好'] });
  if (s.pendingPreview) { s.pendingPreview = false; makeRun('baseline'); }
}
function startInterview() {
  userTurn('我想先让你了解我的偏好。');
  if (current().phase === 'start') add('agent', '我们可以通过几轮交流逐渐建立画像。你随时可以纠正信息；本次需求和长期偏好会分别记录。');
  nextQuestion(); render();
}
function processText(text) {
  text = text.trim(); if (!text) return;
  const s = current(); const message = userTurn(text); let changed = [];
  if (/^(开始晚餐预演|预演当前情境|预演一顿晚餐)$/.test(text)) { requestPreview(); render(); return; }
  if (/^(再聊一聊我的偏好|认识我的偏好)$/.test(text)) { s.phase = 'budget'; add('agent', '可以继续告诉我你的取舍，例如“通常预算 50 元”“喜欢面食”“不吃牛肉”。也可以用右侧编辑来纠正已知信息。'); render(); return; }
  if (/^(暂时不用|不饿|不想吃|现在不需要)$/.test(text)) { s.phase = 'paused'; s.pendingPreview = false; add('agent', '好，先不进入消费预演。没有需求也是一个有效状态；需要时再告诉我。'); render(); return; }
  if (/^(暂时跳过)$/.test(text) && s.phase === 'excluded') {
    s.phase = 'ready'; s.title = '餐饮偏好与选择';
    add('agent', '忌口保留为未知。这次演示可以继续，但它不是食材或过敏安全核验。', { options: ['开始晚餐预演'] });
    if (s.pendingPreview) { s.pendingPreview = false; makeRun('baseline'); } render(); return;
  }
  const temporary = /今天|这次|本次|今晚|现在/.test(text);
  const explicitBudget = text.match(/(?:预算|不超过|最多|每餐|每顿)\s*(\d+(?:\.\d{1,2})?)/);
  const plainBudget = s.phase === 'budget' ? text.match(/^\s*(\d+(?:\.\d{1,2})?)\s*(?:元)?\s*$/) : null;
  const budget = explicitBudget || plainBudget;
  if (budget) {
    const value = Number(budget[1]);
    if (value > 500) { add('agent', '这个演示支持 0–500 元的餐饮预算。可以调整数值继续。'); render(); return; }
    setFact('budget', value, message.id, temporary ? 'context' : 'profile'); changed.push((temporary ? '本次预算' : '通常每餐预算') + ' ' + value + ' 元');
  }
  if (/没有特别偏好|口味不限|都可以/.test(text)) { setFact('cuisines', [], message.id); changed.push('口味没有特别偏好'); }
  else {
    const cuisines = FOOD.filter(food => text.includes(food) && !new RegExp('(?:不吃|不喜欢|不想吃|避开)\\s*' + food).test(text));
    if (cuisines.length && (s.phase === 'cuisine' || /喜欢|偏好|想吃|吃点/.test(text))) {
      setFact('cuisines', cuisines, message.id, temporary ? 'context' : 'profile'); changed.push((temporary ? '本次想吃' : '口味偏好') + ' ' + cuisines.join('、'));
    }
  }
  if (/没有特别忌口|无忌口/.test(text)) { setFact('excluded', [], message.id); changed.push('没有特别忌口'); }
  else {
    const excludes = [];
    const pattern = /(?:不吃|避开|忌口|不想吃|不能吃|过敏)\s*(花生|牛肉|海鲜|香菜)/g;
    for (const found of text.matchAll(pattern)) excludes.push(found[1]);
    if (excludes.length) {
      const scope = temporary ? 'context' : 'profile';
      setFact('excluded', [...new Set([...(s[scope].excluded || []), ...excludes])], message.id, scope);
      changed.push((temporary ? '本次避开' : '忌口') + ' ' + excludes.join('、'));
    }
  }
  if (/晚上六点|18[:：]00/.test(text)) { setFact('hour', 18, message.id, 'context'); changed.push('本次时间 18:00'); }
  if (changed.length) {
    add('agent', '已按你的表达记录：' + changed.join('；') + '。');
    if (['start', 'budget', 'cuisine', 'excluded'].includes(s.phase)) nextQuestion();
    else add('agent', '你可以继续补充，或用更新后的情境重新预演。已封存的历史预测会保留原来的信息快照。', { options: ['开始晚餐预演'] });
  } else if (/预演|模拟|想吃|饿了|晚餐/.test(text)) requestPreview();
  else {
    add('agent', '这句话已保留在对话中，但我没有把它自动变成确定画像。\n当前是有限规则的交互示例。你可以明确说“预算 50 元”“喜欢面食”“不吃牛肉”，或在右侧编辑偏好。');
  }
  render();
}
function requestPreview() {
  const s = current();
  if (s.profile.budget === null && s.context.budget === null) { s.pendingPreview = true; nextQuestion(); }
  else if (s.profile.cuisines === null || (s.profile.excluded === null && s.phase !== 'ready')) { s.pendingPreview = true; nextQuestion(); }
  else makeRun('baseline');
}
function nameOf(id) { return CATALOG.find(c => c.id === id)?.name || '暂不消费'; }
function makeRun(type) {
  const s = current();
  const pending = s.runs.find(r => !r.observed_choice && !r.skipped);
  if (pending) { add('agent', '还有一份封存预测等你记录选择。请先完成或跳过它，避免后续分支提前透露结果。'); render(); return; }
  const parent = type === 'baseline' ? null : s.runs[0];
  if (type !== 'baseline' && !parent) { add('agent', '先完成一个基线情境，再从它的快照创建条件分支。', { options: ['开始晚餐预演'] }); render(); return; }
  const profileSnapshot = parent ? copy(parent.profile_snapshot) : copy(s.profile);
  const context = parent ? copy(parent.context_snapshot) : copy(s.context);
  if (type === 'budget') context.budget = 30;
  if (type === 'coupon') context.coupon = 20;
  const budget = context.budget ?? profileSnapshot.budget;
  if (budget === null) { s.pendingPreview = true; nextQuestion(); return; }
  const excluded = [...(profileSnapshot.excluded || []), ...(context.excluded || [])];
  const preference = context.cuisines ?? profileSnapshot.cuisines ?? [];
  const candidates = CATALOG.map(c => ({ ...copy(c), effective_price: Math.max(0, c.price - (c.cuisine === '家常菜' ? context.coupon : 0)) }));
  const feasible = candidates.filter(c => c.effective_price <= budget && !c.ingredients.some(i => excluded.includes(i)));
  const ordered = [...feasible].sort((a, b) => Number(preference.includes(b.cuisine)) - Number(preference.includes(a.cuisine)) || a.effective_price - b.effective_price);
  const chosen = ordered[0] || null;
  const prediction = { choice_id: chosen?.id || 'none', ranking: ordered.map(c => c.id), probabilities: null, reason: chosen ? ((preference.includes(chosen.cuisine) ? '匹配已表达的' + chosen.cuisine + '偏好；' : '没有匹配的已知口味偏好，采用演示价格规则；') + '在本次预算内，且未包含已知忌口。相同偏好时按演示价格升序。') : '本次预算与已知忌口下没有可行演示餐厅，规则选择外部选项。', source: 'rule_based_demo' };
  const run = { id: uuid(), sequence: s.runs.length + 1, title: type === 'budget' ? '分支 · 预算 30 元' : type === 'coupon' ? '分支 · 家常菜 20 元券' : '基线 · 餐饮选择', branch_type: type, parent_snapshot_id: parent?.id || null,
    at: now(), prediction_frozen_at: now(), profile_snapshot: profileSnapshot, profile_version: parent ? parent.profile_version : s.profileVersion,
    context_snapshot: context, history_cutoff: parent ? parent.history_cutoff : s.messages.length,
    evidence_snapshot: parent ? copy(parent.evidence_snapshot) : copy(s.evidence),
    candidate_set: candidates, display_order: candidates.map(c => c.id).concat('none'),
    feasible_ids: feasible.map(c => c.id), prediction, observed_choice: null,
    blind_prediction: true, skipped: false, model_version: null, engine_version: VERSION, seed: 0 };
  s.runs.push(run); s.activeRunId = run.id; s.phase = 'rehearsal'; s.title = '晚餐决策预演';
  add('agent', '把这一刻的画像与情境固定下来。\n预算 ' + budget + ' 元 · 时间 ' + context.hour + ':00' + (context.coupon ? ' · 家常菜券 ' + context.coupon + ' 元' : '') + '。\n规则预测已经封存，先不展示。面对下面这组虚构候选，你独立会怎么选？', { run_id: run.id });
  render();
  showPanel('runs', false);
}
function recordChoice(runId, id) {
  const run = current().runs.find(r => r.id === runId);
  if (!run || run.observed_choice || run.skipped || !run.display_order.includes(id)) return;
  run.observed_choice = { id, name: nameOf(id), at: now(), source: 'user_scenario_choice_demo', label_type: 'scenario_intention', prediction_visible_before_choice: false, satisfaction: null };
  userTurn('在这个情境中，我会选：' + nameOf(id) + '。');
  add('twin', '你的选择已经单独记录，可以查看先前封存的规则预测了。\n这只是一次演示情境的比对，不代表实际购买，也不能证明孪生模型准确。', { reveal_run_id: run.id, options: ['我想比较预算变化', '我想比较消费券变化'] });
  current().phase = 'ready'; render();
}
function skipRun(runId) {
  const run = current().runs.find(r => r.id === runId); if (!run || run.observed_choice || run.skipped) return;
  run.skipped = true; userTurn('跳过这次独立选择记录。');
  add('agent', '这次标签保留为空，不会记成拒绝或负例。规则预测可以查看，但该次记录不进入选择一致性评测。', { reveal_run_id: run.id });
  current().phase = 'ready'; render();
}
function action(name) {
  if (name === 'start') startInterview();
  else if (name === 'preview') { userTurn('我想预演当前的消费情境。'); requestPreview(); render(); }
  else if (name === 'branch') { showPanel('runs'); if (!current().runs.length) toast('先完成一个基线预演，再比较条件变化。'); }
  else if (name === 'budget-branch' || name === 'coupon-branch') { userTurn(name === 'budget-branch' ? '保持基线画像与其他条件，预算改为 30 元。' : '保持基线画像与其他条件，加入家常菜 20 元券。'); makeRun(name === 'budget-branch' ? 'budget' : 'coupon'); render(); }
}
function optionReply(text) {
  if (text === '我想比较预算变化') action('budget-branch');
  else if (text === '我想比较消费券变化') action('coupon-branch');
  else processText(text);
}
function renderMessages() {
  const s = current(); $('#welcome').hidden = s.messages.length > 0;
  const result = s.messages.map(message => {
    if (message.role === 'system') return node('div', 'system-line', message.text);
    const row = node('article', 'message ' + message.role);
    if (message.role !== 'user') { const avatar = node('span', 'dot-avatar'); avatar.append(node('i'), node('i')); row.append(avatar); }
    const body = node('div', 'message-body');
    if (message.role !== 'user') { const meta = node('div', 'message-meta'); meta.append(node('strong', '', message.role === 'twin' ? '预演记录' : '助手')); if (message.place_source === 'amap_poi') meta.append(node('span', '', '高德附近地点')); body.append(meta); }
    body.append(node('p', '', message.text));
    if (message.options) {
      const choices = node('div', 'message-options');
      const answered = s.messages.slice(s.messages.indexOf(message) + 1).some(m => m.role === 'user');
      message.options.forEach(text => { const button = node('button', '', text); button.type = 'button'; button.disabled = answered; button.addEventListener('click', () => optionReply(text)); choices.append(button); });
      body.append(choices);
    }
    if (message.run_id) {
      const run = s.runs.find(r => r.id === message.run_id);
      if (run) body.append(freezeCard(run));
    }
    if (message.reveal_run_id) {
      const run = s.runs.find(r => r.id === message.reveal_run_id);
      if (run && (run.observed_choice || run.skipped)) body.append(revealCard(run));
    }
    row.append(body); return row;
  });
  $('#messages').replaceChildren(...result);
}
function freezeCard(run) {
  const card = node('div', 'freeze-card'); card.append(node('div', 'freeze-heading', '◇ 预测已封存 · #' + String(run.sequence).padStart(2, '0')));
  card.append(node('div', 'freeze-caption', '候选按固定目录顺序展示，尚未按预测排序。先记录独立选择，也可以选择不消费。'));
  const list = node('div', 'choice-list');
  run.candidate_set.forEach(c => {
    const button = node('button', 'choice-button' + (run.observed_choice?.id === c.id ? ' chosen' : '')); button.type = 'button';
    const label = node('span', '', c.name); label.append(node('small', '', c.cuisine + ' · ' + c.distance + ' m · ' + c.ingredients.join('、') + ' · 虚构候选'));
    button.append(label, node('span', '', '¥' + c.effective_price + (c.effective_price !== c.price ? '（券后）' : '') + ' / 人'));
    button.disabled = Boolean(run.observed_choice || run.skipped); button.addEventListener('click', () => recordChoice(run.id, c.id)); list.append(button);
  });
  const none = node('button', 'choice-button' + (run.observed_choice?.id === 'none' ? ' chosen' : ''), '暂不消费 / 都不选');
  none.type = 'button'; none.disabled = Boolean(run.observed_choice || run.skipped); none.addEventListener('click', () => recordChoice(run.id, 'none')); list.append(none); card.append(list);
  if (!run.observed_choice && !run.skipped) { const skip = node('button', 'unobserved-label', '跳过记录，保持标签未知'); skip.type = 'button'; skip.addEventListener('click', () => skipRun(run.id)); card.append(skip); }
  return card;
}
function revealCard(run) {
  const box = node('div', 'reveal-box'); box.append(node('span', 'reveal-kicker', run.engine_source === REAL_ENGINE ? 'FROZEN LIFESIM PREDICTION · DEEPSEEK' : 'FROZEN RULE PREDICTION · DEMO'), node('h3', '', runName(run, run.prediction.choice_id)), node('p', '', run.prediction.reason));
  const comparison = node('div', 'reveal-comparison');
  if (run.observed_choice) { comparison.append(node('span', '', '你的情境选择：' + run.observed_choice.name + ' · '), node('strong', '', run.observed_choice.id === run.prediction.choice_id ? '本次一致' : '本次不一致')); }
  else comparison.textContent = '实际选择未知 · 不计入一致性评测';
  box.append(comparison); if (run.engine_source === REAL_ENGINE && run.simulation_dialogue) box.append(modelDialogue(run)); return box;
}
function renderProfile() {
  const s = current(); const p = s.profile;
  const knownCount = [p.budget, p.cuisines, p.excluded].filter(v => v !== null && v !== undefined).length;
  $('#profile-progress').textContent = knownCount + ' / 3 项基础信息';
  $('#profile-next').hidden = knownCount < 3;
  $('#profile-progress-percent').textContent = Math.round(knownCount / 3 * 100) + '%';
  $('#profile-progress-fill').style.width = knownCount / 3 * 100 + '%';
  $('#workspace-progress-value').textContent = Math.round(knownCount / 3 * 100) + '%';
  $('#workspace-progress-status').textContent = knownCount ? `已确认 ${knownCount} / 3 项基础偏好` : '尚未确认基础偏好';
  $('#workspace-progress-fill').style.width = knownCount / 3 * 100 + '%';
  const form = $('#profile-form');
  if (!form.contains(document.activeElement)) {
    form.elements.budget.value = p.budget === null ? '' : p.budget;
    form.elements.cuisine.value = p.cuisines === null ? '' : p.cuisines.length ? p.cuisines[0] : '不限';
    form.elements.excluded.value = p.excluded === null ? '' : p.excluded.length ? p.excluded[0] : '无';
  }
  const facts = [['通常预算', p.budget === null ? null : p.budget + ' 元 / 餐'], ['口味偏好', p.cuisines === null ? null : p.cuisines.join('、') || '没有特别偏好'], ['长期忌口', p.excluded === null ? null : p.excluded.join('、') || '无特别忌口']];
  $('#profile-facts').replaceChildren(...facts.map(([key, value]) => { const line = node('div'); line.append(node('dt', '', key), node('dd', value === null ? 'unknown' : '', value === null ? '尚未了解' : value)); return line; }));
  $('#context-time').textContent = s.context.hour + ':00 · ' + (s.context.hour === 12 ? '午间' : s.context.hour === 18 ? '傍晚' : '夜晚');
  const contextual = [];
  if (s.context.budget !== null) contextual.push('本次预算 ' + s.context.budget + ' 元');
  if (s.context.cuisines?.length) contextual.push('本次想吃 ' + s.context.cuisines.join('、'));
  if (s.context.excluded.length) contextual.push('本次避开 ' + s.context.excluded.join('、'));
  if (s.context.coupon) contextual.push('家常菜券 ' + s.context.coupon + ' 元');
  $('#context-summary').textContent = contextual.join(' · ') || '使用已知偏好；时间不代表确定饥饿。';
  $('#evidence-count').textContent = s.evidence.length;
  if (s.evidence.length) $('#evidence-list').replaceChildren(...s.evidence.slice(-6).reverse().map(e => { const item = node('article', 'evidence-entry'); const idx = s.messages.findIndex(m => m.id === e.message_id); const text = s.messages.find(m => m.id === e.message_id)?.text || '用户确认'; item.append(node('strong', '', (e.scope === 'context' ? '本次状态' : '明确偏好') + ' / ' + ({budget:'预算',cuisines:'口味',excluded:'忌口',allergies:'过敏原',diet_tags:'饮食要求',max_distance:'距离',dinner_time:'晚餐时间',hour:'时间',coupon:'消费券'})[e.field] + ' · 第 ' + (idx + 1) + ' 条消息'), node('p', '', text)); return item; }));
  else $('#evidence-list').replaceChildren(node('p', 'evidence-empty', '还没有对话证据。先聊聊你，再建立画像。'));
}
function renderRuns() {
  const s = current(); $('#run-count').textContent = s.runs.length; $('#run-total').textContent = s.runs.length;
  if (!s.runs.length) { $('#run-list').replaceChildren(node('p', 'evidence-empty', '完成偏好交流后，封存第一份消费预演。')); return; }
  $('#run-list').replaceChildren(...[...s.runs].reverse().map(run => {
    const card = node('article', 'run-card' + (s.activeRunId === run.id ? ' active' : '')); const head = node('header');
    head.append(node('span', '', '#' + String(run.sequence).padStart(2, '0')), node('span', '', run.parent_snapshot_id ? '基线快照分支' : '新快照')); card.append(head, node('h3', '', run.title));
    if (!run.observed_choice && !run.skipped) card.append(node('p', '', '预测已封存，等待独立选择。'), node('span', 'run-status', '选择记录前不展示预测'));
    else {
      card.append(node('p', '', (run.engine_source === REAL_ENGINE ? '孪生预测：' : '规则预测：') + runName(run, run.prediction.choice_id)), node('p', '', '情境选择：' + (run.observed_choice?.name || '未知')));
      card.append(node('span', 'run-status', run.observed_choice ? (run.observed_choice.id === run.prediction.choice_id ? '本次比对一致' : '本次比对不一致') : '标签缺失 · 不计分'));
    }
    return card;
  }));
}
function renderSessions() {
  $('#session-count').textContent = sessions.length;
  $('#session-list').replaceChildren(...[...sessions].reverse().map(s => {
    const button = node('button', 'session-item' + (s.id === activeId ? ' active' : '')); button.type = 'button'; button.append(node('span', '', '◌'), node('span', '', s.title));
    button.disabled = busy;
    button.addEventListener('click', () => { if (busy) return; activeId = s.id; engineMode=s.backendId?'lifesim':s.engine==='rule_based_demo'?'demo':engineMode; clearNearby(); $('#sidebar').classList.remove('open'); render(); updateEngineDisplay(); if (!profileComplete()) showPanel('profile'); else setInspectorOpen(false); }); return button;
  }));
}
function render(scroll = true) {
  renderSessions(); renderProfile(); renderRuns(); renderMessages(); $('#save-consent').checked = consent;
  $('#composer-phase').textContent = ({ start: '多轮探索', budget: '了解预算', cuisine: '了解口味', excluded: '确认忌口', ready: '画像与情境', rehearsal: '独立选择记录', paused: '等待你的需要' })[current().phase] || '多轮探索';
  persist(); if (scroll && current().messages.length) requestAnimationFrame(() => { $('#chat-scroll').scrollTop = $('#chat-scroll').scrollHeight; });
}
function syncWorkspaceTabs(view) {
  document.querySelectorAll('[data-workspace-tab]').forEach(button => {
    const selected = button.dataset.workspaceTab === view;
    button.classList.toggle('active', selected);
    if (selected) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
}
function showPanel(panel, open = true) {
  for (const name of ['profile', 'runs', 'evaluation', 'coupon']) { $('#panel-' + name).hidden = name !== panel; $('#tab-' + name).setAttribute('aria-selected', String(name === panel)); $('#tab-' + name).tabIndex = name === panel ? 0 : -1; }
  $('#inspector-heading').textContent = ({profile:'用户画像', runs:'消费决策预演', evaluation:'评测与数据', coupon:'政府消费券预演'})[panel];
  document.querySelectorAll('.sidebar [data-panel]').forEach(button => button.classList.toggle('active', open && button.dataset.panel === panel));
  if (open) { setInspectorOpen(true); syncWorkspaceTabs(panel); }
  $('#sidebar').classList.remove('open');
}
function setInspectorOpen(open) {
  $('.app-shell').classList.toggle('inspector-collapsed', !open);
  $('#inspector').classList.toggle('open', open);
  $('#context-toggle').setAttribute('aria-expanded', String(open));
  $('#context-toggle').setAttribute('aria-label', open ? '收起研究面板' : '打开研究面板');
  $('#context-toggle').title = open ? '收起研究面板' : '打开研究面板';
  $('#agent-nav').classList.toggle('active', !open);
  if (!open) document.querySelectorAll('.sidebar [data-panel]').forEach(button => button.classList.remove('active'));
  if (!open) syncWorkspaceTabs('chat');
}
document.querySelectorAll('[data-workspace-tab]').forEach(button => button.addEventListener('click', () => {
  const view = button.dataset.workspaceTab;
  if (view === 'chat') { setInspectorOpen(false); $('#message-input').focus(); }
  else showPanel(view);
}));
document.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', () => action(button.dataset.action)));
document.querySelectorAll('[data-panel]').forEach(button => button.addEventListener('click', () => showPanel(button.dataset.panel)));
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
$('#message-form').addEventListener('submit', event => { event.preventDefault(); const text = $('#message-input').value; if (!text.trim()) return; $('#message-input').value = ''; processText(text); });
$('#message-input').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('#message-form').requestSubmit(); } });
$('#new-session').addEventListener('click', () => { const s = freshSession(); sessions.push(s); activeId = s.id; clearNearby(); $('#sidebar').classList.remove('open'); render(false); showPanel('profile'); });
$('#agent-nav').addEventListener('click', () => { setInspectorOpen(false); $('#message-input').focus(); });
$('#menu-button').addEventListener('click', () => { $('#sidebar').classList.toggle('open'); $('#inspector').classList.remove('open'); });
$('#context-toggle').addEventListener('click', () => {
  const opening = $('.app-shell').classList.contains('inspector-collapsed');
  if (opening) showPanel('profile');
  else setInspectorOpen(false);
  $('#sidebar').classList.remove('open');
});
$('#close-inspector').addEventListener('click', () => setInspectorOpen(false));
document.addEventListener('keydown', event => { if (event.key === 'Escape') { $('#sidebar').classList.remove('open'); setInspectorOpen(false); } });
$('.conversation').addEventListener('click', event => { if (!event.target.closest('#menu-button')) $('#sidebar').classList.remove('open'); });
$('#save-consent').addEventListener('change', event => {
  consent = event.target.checked;
  if (!consent) { try { localStorage.removeItem(KEY); } catch {} toast(engineMode === 'lifesim' ? '已关闭浏览器入口保存；后端研究记录仍保存在本机，可通过清除记录删除。' : '已关闭持久保存，当前会话继续留在本次页面。'); }
  else { persist(); toast('当前会话将保存到这个浏览器，可随时清除。'); }
});
$('#clear-data').addEventListener('click', () => { if (engineMode === 'lifesim') { clearRealSessions(); return; } try { localStorage.removeItem(KEY); } catch {} consent = false; sessions = [freshSession()]; activeId = sessions[0].id; showPanel('profile', false); render(false); toast('本地研究 demo 会话已清除。'); });
$('#edit-profile').addEventListener('click', () => { $('#profile-form').scrollIntoView({behavior:'smooth',block:'center'}); $('#profile-form').elements.budget.focus(); });
$('#profile-next').addEventListener('click', () => { setInspectorOpen(false); $('#message-input').focus(); });
function explicitProfileUpdates(form) {
  const data = new FormData(form), previous = current().profile;
  const cuisine = data.get('cuisine'), excluded = data.get('excluded');
  const values = {budget:data.get('budget') === '' ? null : Number(data.get('budget')),
    cuisines:cuisine === '' ? null : cuisine === '不限' ? [] : [cuisine],
    excluded:excluded === '' ? null : excluded === '无' ? [] : [excluded]};
  return Object.fromEntries(Object.entries(values).filter(([key, value]) => value !== null || previous[key] !== null));
}
$('#profile-form').addEventListener('submit', event => {
  if (engineMode === 'lifesim') { submitRealProfile(event); return; }
  event.preventDefault(); const profile = explicitProfileUpdates(event.target);
  if (!Object.keys(profile).length) { toast('先填写至少一项偏好，再保存。'); return; }
  const nextProfile = {...current().profile, ...profile};
  const message = userTurn('我确认的偏好：每餐预算 ' + (nextProfile.budget === null ? '未知' : nextProfile.budget + ' 元') + '；口味 ' + (nextProfile.cuisines === null ? '未知' : nextProfile.cuisines.join('、') || '不限') + '；忌口 ' + (nextProfile.excluded === null ? '未知' : nextProfile.excluded.join('、') || '无') + '。');
  Object.entries(profile).forEach(([key, value]) => setFact(key, value, message.id));
  current().phase = profileComplete() ? 'ready' : 'start'; current().title = '餐饮偏好与选择';
  add('agent', '按你确认的信息更新了画像。已封存预测的原始快照不会改变。', { options: ['开始晚餐预演'] }); render();
});
$('#open-scenario').addEventListener('click', () => { const c = current().context; const f = $('#scenario-form'); f.elements.hour.value = c.hour; f.elements.budget.value = c.budget ?? ''; f.elements.coupon.value = c.coupon; $('#scenario-dialog').showModal(); });
$('#scenario-form').addEventListener('submit', event => {
  if (engineMode === 'lifesim') { submitRealContext(event); return; }
  event.preventDefault(); const data = new FormData(event.target); const values = { hour: Number(data.get('hour')), budget: data.get('budget') === '' ? null : Number(data.get('budget')), coupon: Number(data.get('coupon')) };
  const message = userTurn('调整本次情境：时间 ' + values.hour + ':00；预算 ' + (values.budget === null ? '沿用已知偏好' : values.budget + ' 元') + '；家常菜券 ' + values.coupon + ' 元。');
  Object.entries(values).forEach(([key, value]) => setFact(key, value, message.id, 'context')); $('#scenario-dialog').close();
  add('agent', '仅更新本次情境，长期偏好保持原有记录。可以用这个情境创建新基线。'); render();
});
$('#show-protocol').addEventListener('click', () => $('#protocol-dialog').showModal());
$('#export-session').addEventListener('click', () => {
  if (engineMode === 'lifesim') { exportRealSession(); return; }
  const s = current();
  const record = { schema_version: 2, study_id: 'local-demo', participant_id: s.id, source: 'rule_based_demo', mode: 'elicitation_and_twin_rehearsal_demo', exported_at: now(), engine_version: VERSION, llm_connected: false, consent_to_browser_storage: consent, session: copy(s), note: '虚构候选与有限规则；用户选择是演示情境意向，不是实际交易。本地隐藏只演示流程，正式盲测需服务器隔离预测与标签。' };
  const url = URL.createObjectURL(new Blob([JSON.stringify(record, null, 2)], { type: 'application/json;charset=utf-8' }));
  const link = node('a'); link.href = url; link.download = 'mirra-research-' + s.id.slice(0, 8) + '.json'; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); toast('已导出当前会话、证据与冻结预演记录。');
});
$('#inspector .inspector-tabs').addEventListener('keydown', event => {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault(); const panels = ['profile', 'runs', 'evaluation', 'coupon']; const index = panels.findIndex(p => $('#tab-' + p).getAttribute('aria-selected') === 'true');
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? panels.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : panels.length - 1)) % panels.length; showPanel(panels[next], false); $('#tab-' + panels[next]).focus();
});
showPanel('profile', false);
setInspectorOpen(false);
render(false);
if (!document.body.classList.contains('entry-active') && !profileComplete()) showPanel('profile');

// Real LifeSim bridge. This deliberately never falls back to rules after an API failure.
function runName(run, id) { return run.candidate_set.find(c => c.id === id)?.name || '暂不消费'; }
function modelDialogue(run) {
  const details = node('details', 'model-dialogue');
  details.append(node('summary', '', '查看内部模拟对话 · ' + run.simulation_dialogue.length + ' 条'));
  for (const turn of run.simulation_dialogue) {
    const item = node('article', 'simulation-turn');
    item.append(node('strong', '', turn.role === 'simulated_user' ? '模拟消费者' : '模拟助手'), node('p', '', turn.content));
    details.append(item);
  }
  details.append(node('small', '', '这些话由模型生成，不是真人陈述；不会写入真人反馈记忆。'));
  return details;
}
const localProcessText = processText;
const localAction = action;
const localRecordChoice = recordChoice;
const localSkipRun = skipRun;
const localRenderProfile = renderProfile;
processText = text => engineMode === 'lifesim' ? sendRealMessage(text) : localProcessText(text);
action = name => {
  if (['preview', 'budget-branch', 'coupon-branch'].includes(name) && !profileComplete()) {
    showPanel('profile'); toast('请先填写预算、口味和饮食限制，再开始预演。'); return;
  }
  if (engineMode !== 'lifesim') { localAction(name); return; }
  if (busy) return;
  if (name === 'branch') { showPanel('runs'); return; }
  if (name === 'start') { if (!profileComplete()) showPanel('profile'); else sendRealMessage('我想通过几轮交流，让你逐渐了解我的消费习惯与偏好。'); return; }
  if (name === 'preview') { performRealRequest('/api/research/runs', { branch_type: 'baseline', request_id: uuid() }, '正在进行孪生预演…'); return; }
  if (name === 'budget-branch' || name === 'coupon-branch') {
    performRealRequest('/api/research/runs', { branch_type: name === 'budget-branch' ? 'budget' : 'coupon', request_id: uuid() }, '从基线快照生成条件分支…');
  }
};
optionReply = text => {
  if (text === '我想比较预算变化') action('budget-branch');
  else if (text === '我想比较消费券变化') action('coupon-branch');
  else if (text === '开始晚餐预演') action('preview');
  else processText(text);
};
recordChoice = (runId, id) => {
  if (engineMode !== 'lifesim') { localRecordChoice(runId, id); return; }
  if (busy) return;
  const run = current().runs.find(r => r.id === runId);
  if (!run || run.observed_choice || run.skipped) return;
  feedbackTarget = { runId, id };
  $('#real-feedback-title').textContent = '你的选择：' + runName(run, id);
  $('#real-feedback-form').reset();
  $('#real-feedback-dialog').showModal();
};
skipRun = runId => {
  if (engineMode !== 'lifesim') { localSkipRun(runId); return; }
  performRealRequest('/api/research/feedback', {run_id: runId, skip: true}, '保留未知标签并揭示预演…');
};
renderProfile = () => {
  localRenderProfile();
  if (current().engine === REAL_ENGINE) {
    const extraFacts = [['过敏原', current().profile.allergies], ['饮食要求', current().profile.diet_tags],
      ['距离上限', current().profile.max_distance === null ? null : current().profile.max_distance + ' 米']];
    for (const [name, value] of extraFacts) {
      const line = node('div');
      line.append(node('dt', '', name), node('dd', value === null ? 'unknown' : '', value === null ? '尚未了解' : Array.isArray(value) ? value.join('、') || '用户明确无要求' : value));
      $('#profile-facts').append(line);
    }
  }
  const proposals = current().pending_facts || [];
  const container = $('#pending-facts');
  container.hidden = proposals.length === 0 || engineMode !== 'lifesim';
  container.replaceChildren();
  if (!container.hidden) {
    container.append(node('strong', '', '对话提取 · 等你确认'));
    const labels = {budget:'预算',cuisines:'口味',excluded:'忌口',allergies:'过敏原',diet_tags:'饮食要求',max_distance:'距离上限',dinner_time:'晚餐时间',note:'偏好原话'};
    for (const fact of proposals) {
      const item = node('p', '', (fact.scope === 'context' ? '本次 ' : '长期 ') + labels[fact.field] + '：' + (Array.isArray(fact.value) ? fact.value.join('、') || '无特别要求' : fact.value));
      item.append(node('small', '', '原话：“' + fact.quote + '”'));
      container.append(item);
    }
    const confirm = node('button', '', '确认这些信息 →');
    confirm.disabled = busy;
    confirm.addEventListener('click', () => performRealRequest('/api/research/profile', {proposal_ids: proposals.map(p => p.id)}, '保存已确认画像…'));
    container.append(confirm, node('small', '', '提取不等于事实；需要纠正时可使用画像编辑。'));
  }
  const lastReply = [...current().messages].reverse().find(m => m.engine_source === REAL_ENGINE && m.retrieved_memories?.length);
  if (lastReply) {
    const retrieval = node('article', 'retrieval-evidence');
    retrieval.append(node('strong', '', '本轮记忆检索'));
    for (const memory of lastReply.retrieved_memories) retrieval.append(node('p', '', memory.text));
    $('#evidence-list').prepend(retrieval);
  }
};
async function api(path, body, method = 'POST') {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 240000);
  try {
    const response = await fetch(path, {method, headers: body === undefined ? {} : {'Content-Type':'application/json'},
      body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal});
    const text = await response.text();
    let data;
    try { data = JSON.parse(text); } catch { throw new Error('当前地址没有启动研究后端，请使用“启动网页Demo.cmd”重新启动。'); }
    if (!response.ok) throw new Error(data.error || '研究服务请求失败');
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('模型响应超时。可重试相同请求编号，已完成的预演不会重复创建。');
    if (error instanceof TypeError) throw new Error('本地研究服务无法连接，请重新启动工作台。');
    throw error;
  } finally { clearTimeout(timeout); }
}
function adoptRemote(data) {
  const old = current();
  const value = {...data, backendId: data.id, engine: REAL_ENGINE};
  if (old.backendId || !old.messages.length) sessions[sessions.findIndex(s => s.id === old.id)] = value;
  else sessions.push(value);
  activeId = value.id;
  render();
  if (data.pending_facts?.length) showPanel('profile');
  if (data.runs?.length && data.phase === 'rehearsal') showPanel('runs', false);
  updateEngineDisplay();
}
async function ensureRealSession() {
  if (current().backendId) {
    const data = await api('/api/research/session?session_id=' + encodeURIComponent(current().backendId), undefined, 'GET');
    adoptRemote(data);
    modelConsent = true;
    return current().backendId;
  }
  if (!modelConsent) {
    await new Promise((resolve, reject) => {
      consentResolve = {resolve, reject};
      $('#connect-dialog').showModal();
    });
  }
  const data = await api('/api/research/sessions', {consent:true});
  adoptRemote(data);
  return data.id;
}
function setBusy(value, label = '') {
  busy = value;
  document.querySelectorAll('#message-form button, #chat-nearby-trigger, [data-action], #new-session, .session-item, #edit-profile, #open-scenario, #export-session, #clear-data, .pending-facts button').forEach(button => { button.disabled = value; });
  $('#engine-mode').disabled = value;
  $('#message-input').disabled = value;
  $('.composer').classList.toggle('loading', value);
  if (value) {
    $('#composer-phase').textContent = label;
    $('#engine-status').textContent = '模型正在处理';
  } else updateEngineDisplay();
}
function mountPendingTurn(text) {
  $('#welcome').hidden = true;
  const sent = node('article', 'message user pending-user');
  sent.append(node('div', 'message-body', text));
  const reply = node('article', 'message agent pending-reply');
  const avatar = node('span', 'dot-avatar'); avatar.append(node('i'), node('i'));
  const body = node('div', 'message-body');
  const meta = node('div', 'message-meta'); meta.append(node('strong', '', '助手'));
  const words = node('p', '', '正在理解这句话…');
  body.append(meta, words, node('span', 'pending-cursor'));
  reply.append(avatar, body);
  $('#messages').append(sent, reply);
  $('#chat-scroll').scrollTop = $('#chat-scroll').scrollHeight;
  let hasDelta = false;
  return {
    event(event) {
      if (event.type === 'phase') {
        $('#composer-phase').textContent = event.value;
        if (!hasDelta) words.textContent = event.value;
      } else if (event.type === 'delta') {
        if (!hasDelta) { hasDelta = true; words.textContent = ''; }
        words.textContent += event.value;
        $('#chat-scroll').scrollTop = $('#chat-scroll').scrollHeight;
      }
    },
    failed() { words.textContent = '本轮回复中断，请使用下方按钮重试。'; reply.classList.remove('pending-reply'); }
  };
}
async function streamRealChat(payload, onEvent) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 240000);
  try {
    const response = await fetch('/api/research/chat/stream', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload), signal:controller.signal});
    if (!response.ok) {
      let data = {}; try { data = await response.json(); } catch {}
      throw new Error(data.error || '对话服务请求失败');
    }
    if (!response.body) throw new Error('当前浏览器无法逐步接收模型回复');
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let buffer = '', completed = null;
    function parse(line) {
      if (!line.trim()) return;
      const event = JSON.parse(line);
      if (event.type === 'error') throw new Error(event.value);
      if (event.type === 'session') completed = event.value;
      else onEvent(event);
    }
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream:true});
      while (buffer.includes('\n')) {
        const index = buffer.indexOf('\n'); parse(buffer.slice(0,index)); buffer = buffer.slice(index+1);
      }
    }
    buffer += decoder.decode(); parse(buffer);
    if (!completed) throw new Error('回复未完整保存，可重试同一请求');
    return completed;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('模型响应超时，可重试本轮消息。');
    if (error instanceof TypeError) throw new Error('本地对话服务断开，请检查工作台是否还在运行。');
    throw error;
  } finally { clearTimeout(timeout); }
}
async function performRealRequest(endpoint, payload, label) {
  if (busy) return;
  setBusy(true, label); $('#request-error').hidden = true;
  let pending = null;
  try {
    const sid = await ensureRealSession();
    setBusy(true, label);
    if (endpoint === '/api/research/chat') pending = mountPendingTurn(payload.text);
    const body = {...payload, session_id:sid};
    const result = pending ? await streamRealChat(body, event => pending.event(event)) : await api(endpoint, body);
    adoptRemote(result);
    lastFailedRequest = null;
  } catch (error) {
    pending?.failed();
    const expiredPlaces = error.message.includes('附近地点结果已过期');
    if (expiredPlaces) clearNearby();
    if (error.message === '已取消模型授权') toast('未调用模型，可以继续选择规则示例。');
    else {
      $('#request-error-text').textContent = error.message;
      $('#request-error').hidden = false;
      $('#retry-request').hidden = expiredPlaces;
      lastFailedRequest = expiredPlaces ? null : {endpoint, payload, label};
    }
  } finally { setBusy(false); }
}
function sendRealMessage(text) {
  text = text.trim(); if (!text || busy) return;
  return performRealRequest('/api/research/chat', {text, request_id:uuid(),
    nearby_search_id:nearbyResults?.search_id || null}, '正在理解并回应…');
}
function updateEngineDisplay() {
  if (!$('#engine-status')) return;
  $('#engine-mode').value = engineMode;
  if (engineMode === 'demo') {
    $('#engine-status').textContent = '本地规则示例';
    $('#engine-description').textContent = '规则示例 · 虚构候选';
  } else {
    const verified = current().messages.some(m => m.engine_source === REAL_ENGINE && m.model) || current().runs.some(r => r.engine_source === REAL_ENGINE);
    $('#engine-status').textContent = verified ? '对话已响应' : serviceStatus?.status === 'ready' ? '研究后端就绪' : '研究后端待连接';
    $('#engine-description').textContent = (current().purpose === 'integration_probe' ? '集成测试记录 · ' : '') + '示例候选 · 未校准的行为模拟 · 记录保存在本机';
    if (current().memory_status?.warning) $('#engine-description').textContent += ' · ' + current().memory_status.warning;
  }
}
$('#connect-form').addEventListener('submit', event => {
  event.preventDefault(); modelConsent = true; consent = true; $('#save-consent').checked = true;
  consentResolve?.resolve(); consentResolve = null; $('#connect-dialog').close();
});
$('#connect-dialog').addEventListener('close', () => {
  if (consentResolve) { consentResolve.reject(new Error('已取消模型授权')); consentResolve = null; }
});
$('#real-feedback-form').addEventListener('submit', event => {
  event.preventDefault(); if (!feedbackTarget) return;
  const data = new FormData(event.target);
  const payload = {run_id:feedbackTarget.runId, selected_id:feedbackTarget.id,
    satisfaction:data.get('satisfaction') === '' ? null : Number(data.get('satisfaction')), comment:String(data.get('comment')).trim()};
  $('#real-feedback-dialog').close(); feedbackTarget = null;
  performRealRequest('/api/research/feedback', payload, '保存独立选择与反馈…');
});
async function submitRealProfile(event) {
  event.preventDefault();
  const updates = explicitProfileUpdates(event.target);
  if (!Object.keys(updates).length) { toast('先填写至少一项偏好，再保存。'); return; }
  await performRealRequest('/api/research/profile', {updates}, '正在更新画像…');
}
async function submitRealContext(event) {
  event.preventDefault(); const data = new FormData(event.target);
  $('#scenario-dialog').close();
  await performRealRequest('/api/research/context', {context:{hour:Number(data.get('hour')), budget:data.get('budget') === '' ? null : Number(data.get('budget')), coupon:Number(data.get('coupon'))}}, '保存本次情境…');
}
async function exportRealSession() {
  if (busy) return;
  setBusy(true, '导出研究记录…');
  try {
    const sid = await ensureRealSession();
    const record = await api('/api/research/export?session_id=' + encodeURIComponent(sid), undefined, 'GET');
    const url = URL.createObjectURL(new Blob([JSON.stringify(record,null,2)],{type:'application/json;charset=utf-8'}));
    const link = node('a'); link.href=url; link.download='lifesim-research-'+sid.slice(0,8)+'.json'; document.body.append(link); link.click(); link.remove();
    setTimeout(()=>URL.revokeObjectURL(url),1000); toast('已导出研究记录；未揭示预测仍保持隐藏。');
  } catch(error) { toast(error.message); } finally { setBusy(false); }
}
async function clearRealSessions() {
  if (busy) return;
  setBusy(true,'清除本机研究会话…');
  try {
    for (const s of sessions.filter(s=>s.backendId)) {
      await api('/api/research/session',{session_id:s.backendId},'DELETE');
    }
    try { localStorage.removeItem(KEY); } catch {}
    sessions=[freshSession()]; activeId=sessions[0].id; consent=false; render(false); showPanel('profile',false);
    toast('本机会话、画像与原生记忆已清除。');
  } catch(error) { toast(error.message); } finally { setBusy(false); }
}
$('#retry-request').addEventListener('click',()=>{if(lastFailedRequest)performRealRequest(lastFailedRequest.endpoint,lastFailedRequest.payload,lastFailedRequest.label);});
$('#engine-mode').addEventListener('change',event=>{
  engineMode=event.target.value; $('#request-error').hidden=true;
  if ((engineMode==='demo' && current().backendId) || (engineMode==='lifesim' && !current().backendId && current().messages.length)) {
    const s=freshSession(); s.engine=engineMode==='demo'?'rule_based_demo':null; sessions.push(s); activeId=s.id;
  }
  render(false); updateEngineDisplay();
});
async function checkResearchBackend() {
  try { serviceStatus=await api('/api/research/status',undefined,'GET'); }
  catch { serviceStatus={status:'offline'}; }
  // A local research record can be reopened without exposing sealed predictions.
  const sessionId = new URLSearchParams(location.search).get('session_id');
  if (sessionId) {
    try {
      const restored = await api('/api/research/session?session_id=' + encodeURIComponent(sessionId), undefined, 'GET');
      engineMode = 'lifesim'; adoptRemote(restored);
    } catch(error) { toast(error.message); }
  }
  updateEngineDisplay();
}
let nearbyResults = null;
let nearbyLoading = false;
function clearNearby() {
  nearbyResults = null;
  $('#nearby-list').replaceChildren();
  $('#nearby-status').textContent = '搜索范围：当前位置约 2 公里。';
  $('#chat-nearby-status').textContent = '';
}
function renderNearby() {
  const list = $('#nearby-list');
  list.replaceChildren();
  if (!nearbyResults) return;
  const preferences = current().profile.cuisines || [];
  const budget = nearbyResults.category === 'restaurant' ? (current().context.budget ?? current().profile.budget) : null;
  const matches = item => preferences.some(value => item.name.includes(value) || item.type.includes(value));
  const ordered = [...nearbyResults.items].sort((a, b) => Number(matches(b)) - Number(matches(a)) ||
    (a.distance_meters ?? Infinity) - (b.distance_meters ?? Infinity));
  if (!ordered.length) { list.append(node('p', 'nearby-empty', '这个范围内没有查到该类地点，可以换一个位置再试。')); return; }
  for (const item of ordered) {
    const card = node('article', 'nearby-place');
    const heading = node('div', 'nearby-place-heading');
    heading.append(node('strong', '', item.name), node('span', '', item.distance_meters === null ? '距离未知' : Math.round(item.distance_meters) + ' 米'));
    card.append(heading, node('p', '', item.address));
    const details = node('div', 'nearby-place-details');
    if (matches(item)) details.append(node('span', 'nearby-match', '匹配已确认口味'));
    if (item.average_cost_cny !== null) {
      details.append(node('span', '', '参考人均约 ' + item.average_cost_cny + ' 元'));
      if (budget !== null && item.average_cost_cny > budget) details.append(node('span', '', '参考人均高于预算'));
    } else if (nearbyResults.category === 'restaurant') details.append(node('span', '', '人均价未知'));
    if (item.type) details.append(node('span', '', item.type.split(';').slice(0, 2).join(' · ')));
    card.append(details); list.append(card);
  }
}
async function findNearby({recommend = false} = {}) {
  if (nearbyLoading || busy) return;
  const chatStatus = $('#chat-nearby-status');
  const showStatus = message => { $('#nearby-status').textContent = message; chatStatus.textContent = message; };
  if (!serviceStatus) {
    try { serviceStatus = await api('/api/research/status', undefined, 'GET'); }
    catch { showStatus('本地地点服务未连接，请重新启动工作台。'); return; }
  }
  if (!serviceStatus.configured?.places) {
    showStatus('请先在本机配置高德 Web 服务 Key，然后重启工作台。');
    return;
  }
  if (!window.isSecureContext || !navigator.geolocation) {
    showStatus('当前浏览器无法申请定位；请在本机 localhost 打开工作台。');
    return;
  }
  nearbyLoading = true; $('#nearby-locate').disabled = true; $('#chat-nearby-trigger').disabled = true;
  showStatus('等待浏览器定位授权…');
  try {
    const position = await new Promise((resolve, reject) => navigator.geolocation.getCurrentPosition(resolve, reject,
      {enableHighAccuracy:false, timeout:12000, maximumAge:300000}));
    showStatus('正在向高德查询附近地点…');
    const data = await api('/api/research/nearby', {longitude:position.coords.longitude,
      latitude:position.coords.latitude, radius:2000, category:recommend ? 'restaurant' : $('#nearby-category').value});
    nearbyResults = data;
    renderNearby();
    $('#nearby-status').textContent = `找到 ${data.items.length} 家地点 · 定位精度约 ${Math.round(position.coords.accuracy)} 米 · 仅本次查询`;
    chatStatus.textContent = data.items.length ? `已连接 ${data.items.length} 家附近${data.category === 'restaurant' ? '餐厅' : '店铺'}` : '附近没有查到这类地点';
    if (recommend && data.items.length) {
      setInspectorOpen(false);
      sendRealMessage('请根据我已经确认的画像和预算，从刚查询到的真实附近餐厅中推荐适合的几家，说明距离与价格信息的可靠程度。');
    }
  } catch (error) {
    const locationError = error.code === 1 ? '你未授权定位；没有向高德发送位置。' :
      error.code === 2 ? '设备暂时无法取得位置。' : error.code === 3 ? '定位超时，请再试一次。' : null;
    showStatus(locationError || error.message || '附近地点查询失败');
  } finally { nearbyLoading = false; $('#nearby-locate').disabled = false; $('#chat-nearby-trigger').disabled = busy; }
}
$('#nearby-locate').addEventListener('click', () => findNearby());
$('#chat-nearby-trigger').addEventListener('click', () => findNearby({recommend:true}));
render(false);
updateEngineDisplay();
checkResearchBackend();
