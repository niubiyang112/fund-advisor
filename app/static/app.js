const $ = (s) => document.querySelector(s);
const fmt = (v, d = 2, suffix = '%') => (v === null || v === undefined ? '-' : `${Number(v).toFixed(d)}${suffix}`);
const cls = (v) => (v > 0 ? 'up' : v < 0 ? 'down' : '');
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = (v) => Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 });

const DISC_SHORT = '风险提示：以上内容仅为基于公开历史数据的统计结果，不构成投资建议，不保证收益。基金有风险，投资需谨慎，请自主决策、自担风险。';
const disc = () => `<div class="disc">${DISC_SHORT}</div>`;
const DISC_FULL = `<h3>免责声明与风险提示</h3>
<p>1. 本网站是个人学习、研究用的数据统计工具，仅对公开的基金历史数据做整理和计算。<b>本网站不是基金销售机构，也不是证券投资咨询机构</b>，不提供代客理财、代销基金、代客下单或收益承诺。</p>
<p>2. 网站中的“方案”“买入/卖出/止盈/止损”“赚钱概率”“预期收益”“回测”等，都是按固定规则自动算出的历史统计结果，<b>仅供参考，不构成任何投资建议、要约或推荐</b>。</p>
<p>3. 基金的过往业绩和历史统计不代表未来表现。<b>任何基金都可能亏损，包括债券基金</b>；历史上赚钱概率高或从未亏损，也不代表以后不会亏损。</p>
<p>4. 数据来自第三方公开渠道，可能有延迟、缺失或错误；计算规则也可能有缺陷。本网站不保证数据和结果准确、完整、及时。</p>
<p>5. 是否买卖、买卖哪只基金、买多少，完全由使用者自己判断和决定，<b>投资产生的一切盈亏和风险都由使用者自行承担</b>。本网站及其开发者、运营者对使用本网站内容造成的任何直接或间接损失，均不承担责任。</p>
<p>6. 购买基金前，请阅读基金合同、招募说明书和产品资料概要，了解产品风险等级和费率，并结合自己的风险承受能力决定。如有需要，请咨询具有合法资质的专业机构。</p>
<p>7. 继续使用本网站，即表示你已阅读、理解并同意以上全部内容。</p>`;

async function api(path, opts) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `请求失败 ${r.status}`);
  return body;
}

let rankings = null;
let statusTimer = null;

function uiDialog(msg, { ok = '确定', cancel = null, input = null } = {}) {
  return new Promise((resolve) => {
    const d = document.createElement('div');
    d.className = 'dlg-mask';
    d.innerHTML = `<div class="dlg"><div class="dlg-msg">${esc(msg)}</div>${input !== null ? `<input class="dlg-input" type="number" inputmode="decimal" placeholder="${esc(input)}">` : ''}
      <div class="dlg-btns">${cancel ? `<button class="dlg-cancel">${cancel}</button>` : ''}<button class="primary dlg-ok">${ok}</button></div></div>`;
    document.body.appendChild(d);
    const inp = d.querySelector('.dlg-input');
    if (inp) inp.focus();
    const done = (v) => { d.remove(); resolve(v); };
    d.addEventListener('click', (e) => e.stopPropagation());
    d.querySelector('.dlg-ok').onclick = () => done(inp ? inp.value : true);
    const c = d.querySelector('.dlg-cancel');
    if (c) c.onclick = () => done(inp ? null : false);
  });
}
const uiAlert = (m) => uiDialog(m);
document.getElementById('disc-full').innerHTML = DISC_FULL;
(function disclaimerGate() {
  if (localStorage.getItem('disclaimer_ok')) return;
  const d = document.createElement('div');
  d.className = 'dlg-mask gate';
  d.innerHTML = `<div class="dlg"><div class="gate-text">${DISC_FULL}</div><button class="primary dlg-ok" disabled>请先阅读（5）</button></div>`;
  document.body.appendChild(d);
  const b = d.querySelector('button');
  let n = 5;
  const t = setInterval(() => { n -= 1; b.textContent = n > 0 ? `请先阅读（${n}）` : '我已阅读并同意'; if (n <= 0) { b.disabled = false; clearInterval(t); } }, 1000);
  b.onclick = () => { localStorage.setItem('disclaimer_ok', new Date().toISOString()); d.remove(); };
})();
const uiConfirm = (m) => uiDialog(m, { cancel: '取消' });
const uiPrompt = (m, ph = '') => uiDialog(m, { cancel: '取消', input: ph });

async function loadStatus() {
  try {
    const s = await api('/api/status');
    let text = s.updated_at ? `数据更新：${s.updated_at}（净值日期 ${s.nav_date}）` : '数据尚未生成';
    if (s.refreshing) text += ` · 正在更新：${s.progress}`;
    if (s.error) text += ` · 上次更新失败：${s.error}`;
    $('#status').textContent = text;
    clearTimeout(statusTimer);
    if (s.refreshing || !s.updated_at) statusTimer = setTimeout(async () => { const n = await loadStatus(); if (n?.updated_at && !n.refreshing && !currentPlan) { await loadRankings(); loadPlan(); } }, 4000);
    return s;
  } catch (e) { $('#status').textContent = e.message; }
}

function tag(level, text) { return `<span class="tag ${level}">${esc(text)}</span>`; }

function keyStats(m) {
  const c = [
    ['近1年收益', fmt(m.ann_1y), cls(m.ann_1y)], ['近3年年化', fmt(m.ann_3y), cls(m.ann_3y)], ['每月赚钱比例', fmt(m.pos_ratio_36 ?? m.pos_ratio_12, 0)],
    ['近1年最大回撤', m.mdd_1y != null ? `-${fmt(m.mdd_1y)}` : '-', 'down'], ['近3年最大回撤', m.mdd_3y != null ? `-${fmt(m.mdd_3y)}` : '-', 'down'], ['成立以来最大回撤', m.mdd_all != null ? `-${fmt(m.mdd_all)}` : '-', 'down'],
    ['近3年最低净值', m.min_nav_3y != null ? `${m.min_nav_3y}` : '-'], ['近3年最高净值', m.max_nav_3y != null ? `${m.max_nav_3y}` : '-'], ['离最高点', m.cur_dd ? `-${fmt(m.cur_dd)}` : '0%', m.cur_dd ? 'down' : ''],
    ['近3月上涨天数', m.days_3m ? `${m.up_days_3m}/${m.days_3m}天` : '-'], ['近3月最长连涨', m.days_3m ? `${m.max_streak_3m}天` : '-'], ['现在已连涨', m.days_3m ? `${m.cur_streak}天` : '-'],
    ['近3月收益', fmt(m.r_3m), cls(m.r_3m)], ['近3月最大回撤', m.mdd_3m != null ? `-${fmt(m.mdd_3m)}` : '-', 'down'], ['走势平稳度', m.smooth_3m != null ? `${fmt(m.smooth_3m, 0, '')}分` : '-'],
    ['最差的一个月', fmt(m.worst_month), cls(m.worst_month)], ['平均每月', fmt(m.avg_month), cls(m.avg_month)], ['最新净值', `${m.nav}`],
  ];
  return `<div class="grid stats">${c.map(([k, v, cl]) => `<div>${k}<b class="${cl || ''}">${v}</b></div>`).join('')}</div>`;
}

function fundCard(f, kind) {
  const m = f.metrics;
  const cells = kind === 'stable' ? [] : [['近20日', fmt(m.r_20d), cls(m.r_20d)], ['近60日', fmt(m.r_60d), cls(m.r_60d)], ['近120日', fmt(m.r_120d), cls(m.r_120d)]];
  const st = f.status?.buy && f.status.buy !== '开放申购' ? `<div class="note">申购状态：${esc(f.status.buy)}</div>` : '';
  const risk = f.risk_note ? `<div class="note">⚠ ${esc(f.risk_note)}</div>` : '';
  return `<div class="fund" data-code="${f.code}">
    <div class="top"><div class="name">${esc(f.name)}<span class="code">${f.code}</span></div>${tag(f.signal.level, f.signal.action)}</div>
    ${cells.length ? `<div class="grid">${cells.map(([k, v, c]) => `<div>${k}<b class="${c || ''}">${v}</b></div>`).join('')}</div>` : ''}
    ${keyStats(m)}
    <div class="grid"><div>规模<b>${f.scale ? `${f.scale}亿` : '-'}</b></div><div>止损线<b>-${fmt(f.signal.stop_pct, 1)}</b></div><div>止盈线<b>+${fmt(f.signal.take_pct, 1)}</b></div></div>
    ${risk}${st}</div>`;
}

async function loadRankings() {
  try {
    rankings = await api('/api/rankings');
    $('#stable').innerHTML = rankings.stable.map((f) => fundCard(f, 'stable')).join('') || '<div class="empty">暂无符合条件的基金</div>';
    $('#growth').innerHTML = rankings.growth.map((f) => fundCard(f, 'growth')).join('') || '<div class="empty">暂无符合条件的基金</div>';
    $('#steady').innerHTML = (rankings.steady || []).map((f) => fundCard(f, 'stable')).join('') || '<div class="empty">数据更新后显示</div>';
  } catch (e) {
    $('#stable').innerHTML = $('#growth').innerHTML = $('#steady').innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

let currentPlan = null;
let planSeed = 0;
function planParams() {
  return {
    capital: Number($('#capital').value) || 0,
    monthly: Number($('#monthly').value) || 0,
    risk: $('#risk').value, horizon: $('#horizon').value, goal: $('#goal').value,
    max_loss: $('#max_loss').value, n_funds: $('#n_funds').value,
    types: [...document.querySelectorAll('#types input:checked')].map((i) => i.value).join(','),
  };
}
function restorePlanParams(p) {
  if (!p) return;
  ['capital', 'monthly', 'risk', 'horizon', 'goal', 'max_loss', 'n_funds'].forEach((k) => { if (p[k] !== undefined && $(`#${k}`)) $(`#${k}`).value = p[k]; });
  if (p.types !== undefined) document.querySelectorAll('#types input').forEach((i) => (i.checked = p.types.split(',').includes(i.value)));
}

function signed(v) { return `${v > 0 ? '+' : ''}${money(v)}`; }
function expTable(exp, base, hl) {
  if (!exp || !exp.length) return '<div class="hint small">历史数据不足，暂时算不出预期收益。</div>';
  const cell = (pct) => `<td class="${cls(pct)}">${base ? signed(Math.round(base * pct / 100)) + '<br><span class="pct">' : ''}${fmt(pct)}${base ? '</span>' : ''}</td>`;
  return `<table class="exp"><tr><th>持有</th><th>差的情况</th><th>一般情况</th><th>好的情况</th><th>亏钱概率</th></tr>
    ${exp.map((e) => `<tr${e.days === hl ? ' class="hl"' : ''}><td>${e.label}</td>${cell(e.bad)}${cell(e.mid)}${cell(e.good)}<td>${fmt(e.loss_prob, 0)}</td></tr>`).join('')}</table>`;
}
const EXP_NOTE = '按近 3 年每一天买入、持有对应时间的真实结果统计：“一般”是中间水平，“差/好”是最差/最好的 10% 情况，已剔除一次性异常跳涨。这是历史统计，不是承诺，行情变了结果会不同。';
function alipayBtn(code) { return `<button class="small alipay" data-alipay="${code}">去支付宝买</button>`; }

async function loadPlan(shuffle = false) {
  const params = planParams();
  if (params.capital <= 0) return uiAlert('请输入投入金额');
  if (!params.types) return uiAlert('请至少选一种基金类型');
  localStorage.setItem('plan', JSON.stringify(params));
  planSeed = shuffle ? Math.floor(Math.random() * 1e9) + 1 : 0;
  $('#plan').innerHTML = '<div class="empty">生成中…</div>';
  try {
    const p = await api(`/api/plan?${new URLSearchParams({ ...params, seed: planSeed })}`);
    currentPlan = p;
    const yr = (p.expected || []).find((e) => e.days === 250);
    $('#plan').innerHTML = `
      <div class="card">
        <div class="plan-head"><span>${esc(p.risk)} · ${esc(p.horizon)} · 最多亏 ${p.max_loss}% · 净值日期 ${esc(p.nav_date)}</span><span>${p.items.length} 只</span></div>
        <div class="bar"><i style="width:${p.stable_pct}%;background:#1677ff"></i><i style="width:${p.growth_pct}%;background:#e5383b"></i></div>
        <div class="plan-head"><span>稳健仓位 ${p.stable_pct}%</span><span>进攻仓位 ${p.growth_pct}%</span></div>
        ${p.win !== null && p.win !== undefined ? `<div class="win-big">按近 3 年数据，这个组合持有 ${p.horizon_label} 赚钱的概率约 <b>${fmt(p.win, 0)}</b><div class="todo-why">是历史上的比例，${p.win >= 99 ? '过去没亏过不代表以后不会亏，' : ''}行情变了结果会不同。</div></div>` : ''}
        <div class="grid">
          <div>一次投入<b>${money(p.capital)} 元</b></div>
          <div>组合历史年化<b class="${cls(p.hist_ann)}">${fmt(p.hist_ann)}</b></div>
          <div>持有1年一般能赚<b class="${cls(yr?.mid)}">${yr ? signed(yr.mid_amt) + ' 元' : '-'}</b></div>
          <div>每月定投<b>${p.monthly ? money(p.monthly) + ' 元' : '无'}</b></div>
          <div>历史最大回撤<b>${fmt(p.hist_mdd)}</b></div>
          <div>守止损时预计最大亏<b>${fmt(p.est_loss)}</b></div>
        </div>
        <div class="exp-title">预期收益（一次投入 ${money(p.capital)} 元）</div>
        ${expTable(p.expected, p.capital, p.horizon_days)}
        ${p.dca ? `<div class="steps">每月定投 ${money(p.monthly)} 元，一年共投 ${money(p.dca.invested)} 元，定投部分预计赚 <b class="${cls(p.dca.mid)}">${signed(p.dca.mid)} 元</b>（差 ${signed(p.dca.bad)} ~ 好 ${signed(p.dca.good)} 元）</div>` : ''}
        <div class="hint small">${EXP_NOTE}</div>
        ${p.notes.map((n) => `<div class="note">${esc(n)}</div>`).join('')}
        ${p.items.length ? '<button class="primary" id="plan-add-all" style="margin-top:10px">我已按方案买入，全部加入持仓</button>' : ''}
      </div>
      ${p.items.map((i) => `
        <div class="fund" data-code="${i.code}">
          <div class="top"><div class="name">${esc(i.name)}<span class="code">${i.code} · ${esc(i.category || '')}</span></div>${tag(i.signal.level, i.signal.action)}</div>
          <div class="grid">
            <div>角色<b>${esc(i.role)}</b></div><div>计划金额(${i.weight}%)<b>${money(i.amount)} 元</b></div><div>今天买<b>${money(i.first_buy)} 元</b></div>
            <div>止损净值<b class="down">${i.signal.stop_nav}</b></div><div>止盈净值<b class="up">${i.signal.take_nav}</b></div><div>持有${p.horizon_label}赚钱比例<b class="up">${fmt(i.win, 0)}</b></div><div>一般能赚<b class="${cls(i.hexp.mid)}">${fmt(i.hexp.mid)}</b></div><div>差的情况<b class="${cls(i.hexp.bad)}">${fmt(i.hexp.bad)}</b></div>
          </div>
          ${keyStats(i.metrics)}
          ${i.status?.buy && i.status.buy !== '开放申购' ? `<div class="note">申购状态：${esc(i.status.buy)}</div>` : ''}
          <div class="steps"><b>怎么买：</b>${esc(i.how)}<br><b>什么时候卖：</b>跌到止损净值以下（从最高点回撤 ${fmt(i.signal.stop_pct, 1)}）或信号变成“卖出”就全部卖出；涨到止盈净值先卖一半。每天看“我的持仓”的提醒。</div>
          <div class="actions">${alipayBtn(i.code)}</div>
        </div>`).join('')}
      <div class="hint small">怎么挑的：先按你选的类型、风险和能接受的亏损筛选，${p.goal === 'ann' ? '再按近 3 年年化收益从高到低排（不满 3 年的用近 1 年收益）。年化高的基金历史上跌得也多，注意看每只基金的最大回撤。' : p.goal === 'steady' ? '再按近 3 个月涨得稳不稳排：上涨天数占比、走势平稳度、近 3 个月收益和回撤综合打分。过去 3 个月涨得稳不代表接下来也会涨。' : `再把近 3 年每一天都当作买入日，统计持有 ${p.horizon_label} 后赚钱的比例，比例最高的排最前，差不多的再比赚得多少。`}进攻基金还要求当前信号是“买入”。点基金可以看走势图和详细理由；“换一批”会在${({ ann: '年化收益', steady: '稳步上涨分' })[p.goal] || '赚钱比例'}靠前的基金里重新组合。</div>${disc()}`;
    const addAll = $('#plan-add-all');
    if (addAll) addAll.onclick = async () => {
      if (!await uiConfirm(`把方案里 ${p.items.length} 只基金按“今天买”的金额加入持仓？`)) return;
      p.items.forEach((i) => addBuy(i.code, i.name, today(), i.first_buy, { from_plan: true }, false));
      loadHoldings(); switchTab('hold');
    };
  } catch (e) {
    $('#plan').innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

let charts = [];
async function openFund(code) {
  $('#modal').classList.remove('hidden');
  $('#modal-body').innerHTML = '<div class="empty">加载中…</div>';
  try {
    const f = await api(`/api/fund/${code}`);
    const m = f.metrics, s = f.signal, b = f.backtest;
    $('#modal-body').innerHTML = `
      <div class="card">
        <div class="top" style="display:flex;justify-content:space-between;gap:8px;padding-right:28px"><div class="name" style="font-weight:600">${esc(f.name)} <span class="code">${f.code}</span></div>${tag(s.level, s.action)}</div>
        <div class="grid">
          <div>最新净值<b>${m.nav}</b></div><div>净值日期<b>${m.nav_date}</b></div><div>当日涨跌<b class="${cls(m.r_1d)}">${fmt(m.r_1d)}</b></div>
          <div>规模<b>${f.scale ?? '-'}亿</b></div><div>基金经理<b>${esc(f.manager || '-')}</b></div><div>申购<b>${esc(f.status?.buy || '-')}</b></div>
        </div>
        <ul class="reasons">${s.reasons.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>
        <div class="grid"><div>止损净值<b class="down">${s.stop_nav}</b></div><div>止盈净值<b class="up">${s.take_nav}</b></div><div>申购费率<b>${esc(f.fee_rate || '-')}%</b></div></div>
        <div class="actions">${alipayBtn(f.code)}<button class="small" id="add-this">加入我的持仓（今天买入）</button></div>
      </div>
      <div class="card"><b>预期收益（每投 10000 元）</b>
        ${m.jump ? `<div class="note">这只基金近 3 年有一天异常大涨 ${fmt(m.jump)}（通常是别人大额赎回造成的一次性收益，不会重复），它的历史收益被抬高了，不建议因此买入。下面的预期已剔除这一天。</div>` : ''}
        ${expTable(f.expected, 10000)}<div class="hint small">${EXP_NOTE}</div>${disc()}</div>
      <div class="card"><b>近3年复权净值 & 均线</b><div id="c-nav" class="chart"></div></div>
      <div class="card"><b>每月收益（红=赚，绿=亏）</b><div id="c-month" class="chart small"></div>
        <div class="grid"><div>月度胜率(3年)<b>${fmt(m.pos_ratio_36, 0)}</b></div><div>平均每月<b class="${cls(m.avg_month)}">${fmt(m.avg_month)}</b></div><div>最差月份<b class="${cls(m.worst_month)}">${fmt(m.worst_month)}</b></div></div>
      </div>
      <div class="card"><b>风险收益</b>
        <div class="grid">
          <div>近1年<b class="${cls(m.ann_1y)}">${fmt(m.ann_1y)}</b></div><div>近3年年化<b class="${cls(m.ann_3y)}">${fmt(m.ann_3y)}</b></div><div>年化波动<b>${fmt(m.vol)}</b></div>
          <div>1年最大回撤<b>${fmt(m.mdd_1y)}</b></div><div>3年最大回撤<b>${fmt(m.mdd_3y)}</b></div><div>夏普比率<b>${fmt(m.sharpe, 2, '')}</b></div>
        </div>
      </div>
      ${b ? `<div class="card"><b>按本工具信号操作的回测（${b.start} ~ ${b.end}）</b>
        <table><tr><th></th><th>总收益</th><th>年化</th><th>最大回撤</th></tr>
          <tr><td>按信号买卖</td><td class="${cls(b.strategy_return)}">${fmt(b.strategy_return)}</td><td>${fmt(b.strategy_ann)}</td><td>${fmt(b.strategy_mdd)}</td></tr>
          <tr><td>一直拿着不动</td><td class="${cls(b.hold_return)}">${fmt(b.hold_return)}</td><td>${fmt(b.hold_ann)}</td><td>${fmt(b.hold_mdd)}</td></tr></table>
        <div class="hint small">共 ${b.n_trades} 次完整买卖，胜率 ${fmt(b.win_rate, 0)}。按信号操作通常能减少大跌时的亏损，但在震荡行情里可能少赚甚至来回止损。</div>
      </div>` : ''}`;
    $('#add-this').onclick = async () => {
      const amt = await uiPrompt('买入金额（元），不填也可以', '金额(元)');
      if (amt === null) return;
      addBuy(f.code, f.name, today(), Number(amt) || 0); closeModal(); switchTab('hold');
    };
    drawCharts(f, b);
  } catch (e) {
    $('#modal-body').innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

function drawCharts(f, b) {
  charts.forEach((c) => c.dispose());
  charts = [];
  const c = f.chart;
  const navChart = echarts.init($('#c-nav'));
  const idx = Object.fromEntries(c.dates.map((d, i) => [d, i]));
  const marks = (b?.trades || []).filter((t) => idx[t.date] !== undefined).map((t) => ({
    coord: [t.date, c.adj[idx[t.date]]], value: t.type === 'buy' ? '买' : t.type === 'take' ? '盈' : '卖',
    itemStyle: { color: t.type === 'buy' ? '#e5383b' : t.type === 'take' ? '#d4600b' : '#13a35d' },
    symbolRotate: t.type === 'buy' ? 180 : 0,
  }));
  navChart.setOption({
    grid: { left: 44, right: 10, top: 30, bottom: 50 },
    tooltip: { trigger: 'axis' },
    legend: { data: ['复权净值', '20日均线', '60日均线'], top: 0, textStyle: { fontSize: 11 } },
    xAxis: { type: 'category', data: c.dates, axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 10 } },
    dataZoom: [{ type: 'inside', start: 50 }, { type: 'slider', height: 16, bottom: 8, start: 50 }],
    series: [
      { name: '复权净值', type: 'line', data: c.adj, showSymbol: false, lineStyle: { width: 1.5 }, markPoint: { symbol: 'pin', symbolSize: 28, label: { fontSize: 10 }, data: marks } },
      { name: '20日均线', type: 'line', data: c.ma20, showSymbol: false, lineStyle: { width: 1, type: 'dashed' } },
      { name: '60日均线', type: 'line', data: c.ma60, showSymbol: false, lineStyle: { width: 1 } },
    ],
  });
  const mChart = echarts.init($('#c-month'));
  mChart.setOption({
    grid: { left: 40, right: 10, top: 10, bottom: 30 },
    tooltip: { trigger: 'axis', valueFormatter: (v) => `${v}%` },
    xAxis: { type: 'category', data: c.months, axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', axisLabel: { fontSize: 10, formatter: '{value}%' } },
    series: [{ type: 'bar', data: c.monthly.map((v) => ({ value: v, itemStyle: { color: v >= 0 ? '#e5383b' : '#13a35d' } })) }],
  });
  charts = [navChart, mChart];
}

function closeModal() { $('#modal').classList.add('hidden'); charts.forEach((c) => c.dispose()); charts = []; }

const today = () => { const d = new Date(Date.now() + 8 * 3600e3); return d.toISOString().slice(0, 10); };
const getPositions = () => JSON.parse(localStorage.getItem('positions') || '[]');
const savePositions = (h) => localStorage.setItem('positions', JSON.stringify(h));
const getClosed = () => JSON.parse(localStorage.getItem('closed') || '[]');
const saveClosed = (c) => localStorage.setItem('closed', JSON.stringify(c));
const uid = () => `${Date.now()}${Math.floor(Math.random() * 1e6)}`;
(function migrate() {
  const old = JSON.parse(localStorage.getItem('holdings') || 'null');
  if (!old) return;
  const pos = getPositions();
  old.forEach((h) => {
    let p = pos.find((x) => x.code === h.code);
    if (!p) { p = { id: uid(), code: h.code, name: h.name || h.code, from_plan: !!h.from_plan, trades: [] }; pos.push(p); }
    p.trades.push({ id: uid(), date: h.buy_date, type: 'buy', amount: h.amount || 0 });
  });
  savePositions(pos);
  localStorage.setItem('holdings_bak', JSON.stringify(old));
  localStorage.removeItem('holdings');
})();
function addTrade(code, name, t, opts = {}, reload = true) {
  const all = getPositions();
  let p = all.find((x) => x.code === code);
  if (!p) { p = { id: uid(), code, name: name || code, from_plan: !!opts.from_plan, trades: [] }; all.push(p); }
  if (opts.from_plan) p.from_plan = true;
  p.trades.push({ id: uid(), ...t });
  savePositions(all);
  if (reload) loadHoldings();
}
const addBuy = (code, name, date, amount, opts, reload) => addTrade(code, name, { date, type: 'buy', amount }, opts, reload);
let lastAdvice = {};

function expOf(r, days) { return (r.expected || []).find((e) => e.days === days); }
function holdExp(r) {
  const e = expOf(r, 63);
  if (!e) return '';
  const v = r.no_amount ? 0 : r.value;
  const part = (pct) => (v ? `${signed(Math.round(v * pct / 100))} 元` : fmt(pct));
  return `<div class="steps"><b>接下来3个月预期：</b>一般 <b class="${cls(e.mid)}">${part(e.mid)}</b>，差 ${part(e.bad)} ~ 好 ${part(e.good)}，亏钱概率 ${fmt(e.loss_prob, 0)}</div>`;
}
function sumExp(rows) {
  const lines = [[63, '3个月'], [250, '1年']].map(([d, label]) => {
    let bad = 0, mid = 0, good = 0, n = 0;
    rows.forEach((r) => {
      const e = expOf(r, d);
      if (!e || r.no_amount || !r.value) return;
      const v = r.value;
      bad += v * e.bad / 100; mid += v * e.mid / 100; good += v * e.good / 100; n++;
    });
    return n ? `<tr><td>再拿${label}</td><td class="${cls(bad)}">${signed(Math.round(bad))}</td><td class="${cls(mid)}">${signed(Math.round(mid))}</td><td class="${cls(good)}">${signed(Math.round(good))}</td></tr>` : '';
  }).join('');
  return lines ? `<div class="exp-title">持仓预期收益（元，按当前市值）</div><table class="exp"><tr><th>继续持有</th><th>差的情况</th><th>一般情况</th><th>好的情况</th></tr>${lines}</table>` : '';
}

function renderSummary(rows) {
  const legacy = getClosed();
  rows = rows.filter((r) => !r.no_amount);
  const sum = (k) => rows.reduce((a, r) => a + (r[k] || 0), 0);
  const legacyProfit = legacy.reduce((a, c) => a + c.profit, 0);
  const invested = sum('invested') + legacy.reduce((a, c) => a + c.amount, 0);
  const value = sum('value');
  const floating = sum('floating');
  const realized = sum('realized') + legacyProfit;
  const total = sum('total') + legacyProfit;
  const act = rows.filter((r) => ['sell', 'take'].includes(r.level)).length;
  $('#summary').innerHTML = `<div class="card">
    <div class="plan-head"><span>总收益（含已卖出、已加仓）</span><span>${rows.filter((r) => r.level !== 'closed').length} 只持有中</span></div>
    <div class="sum-big ${cls(total)}">${total >= 0 ? '+' : ''}${total.toFixed(2)} 元</div>
    <div class="grid">
      <div>总收益率<b class="${cls(total)}">${invested ? fmt((total / invested) * 100) : '-'}</b></div>
      <div>累计投入<b>${money(invested)} 元</b></div>
      <div>当前市值<b>${money(value)} 元</b></div>
      <div>持有收益<b class="${cls(floating)}">${floating.toFixed(2)} 元</b></div>
      <div>已卖出收益<b class="${cls(realized)}">${realized.toFixed(2)} 元</b></div>
      <div>需要处理<b class="${act ? 'up' : ''}">${act} 只</b></div>
    </div>
    ${sumExp(rows.filter((r) => r.level !== 'closed'))}
    <div class="hint small">按份额记账：每笔买入按当天净值折成份额，卖出按卖出那天的净值扣份额（先买的先卖）。总收益 = 现在市值 + 卖出拿回的钱 − 累计投入。买入费按基金费率扣，卖出只算了 7 天内 1.5% 的赎回费，和支付宝可能略有差异。</div>
  </div>`;
  $('#closed').innerHTML = legacy.length ? `<div class="card"><b>旧版已卖出记录</b><table><tr><th>基金</th><th>卖出日</th><th>本金</th><th>收益</th></tr>
    ${legacy.slice().reverse().map((c) => `<tr><td>${esc(c.name)}</td><td>${c.date}</td><td>${money(c.amount)}</td><td class="${cls(c.profit)}">${c.profit.toFixed(2)}</td></tr>`).join('')}</table>
    <button class="small danger" id="clear-closed" style="margin-top:8px">清空旧记录</button></div>` : '';
  const cc = $('#clear-closed');
  if (cc) cc.onclick = async () => { if (await uiConfirm('清空旧版已卖出记录？')) { saveClosed([]); loadHoldings(); } };
}

const yuan = (v) => `${v > 0 ? '+' : ''}${Number(v).toFixed(2)} 元`;
function tradeRows(r, p) {
  return `<details class="trades"><summary>交易记录（${r.trades.length} 笔）</summary>
    <table><tr><th>日期</th><th>操作</th><th>金额</th><th>净值</th><th>份额</th><th></th></tr>
    ${r.trades.map((t) => `<tr><td>${t.date}</td><td>${t.type === 'buy' ? '买入' : '卖出'}${t.type === 'sell' ? `<br><span class="${cls(t.profit)}">${yuan(t.profit)}</span>` : ''}</td>
      <td>${r.no_amount ? '-' : money(t.amount)}</td><td>${t.nav}${t.estimated ? '<br><span class="pct">待更新</span>' : ''}</td><td>${r.no_amount ? '-' : money(t.shares)}</td>
      <td><button class="small" data-deltrade="${p.id}|${t.id}">删</button></td></tr>`).join('')}</table>
    <div class="hint small">卖出金额是按当天净值算的到手金额；“待更新”表示当天净值还没公布，先按最新净值估算。</div></details>`;
}
function tradeForm(p) {
  return `<div class="tform hidden" data-form="${p.id}">
    <select data-ttype><option value="buy">加仓（填金额）</option><option value="sell1">卖出全部</option><option value="sell0.5">卖出一半</option><option value="sell0.3333">卖出 1/3</option><option value="sellamt">卖出指定金额</option></select>
    <input type="date" data-tdate value="${today()}"><input type="number" inputmode="decimal" data-tamt placeholder="金额(元)">
    <button class="small primary" data-tsave="${p.id}">保存</button></div>`;
}
function cmpBlock(r) {
  if (r.no_amount || !r.invested || r.cmp_hold === undefined) return '';
  const sig = r.cmp_signal !== undefined ? `<div>严格按方案信号<b class="${cls(r.cmp_signal)}">${yuan(r.cmp_signal)}</b></div>` : '<div>按方案信号<b>-</b></div>';
  return `<div class="steps"><b>同样的买入，三种做法到今天的收益：</b>
    <div class="grid"><div>你的实际操作<b class="${cls(r.total)}">${yuan(r.total)}</b></div><div>一直拿着不动<b class="${cls(r.cmp_hold)}">${yuan(r.cmp_hold)}</b></div>${sig}</div>
    ${r.cmp_signal !== undefined ? `<div class="hint small">“按方案信号”：在同样的日期投同样的钱，之后信号说卖就卖、说买再买，期间操作 ${r.signal_trades} 次，现在${r.signal_in ? '持有中' : '空仓等待'}。</div>` : ''}</div>`;
}

function renderTodo(res, all) {
  const items = res.map((r, i) => ({ r, p: all[i] })).filter(({ r }) => !r.error && r.level !== 'closed');
  if (!items.length) { $('#todo').innerHTML = ''; return; }
  const amt = (r, f) => (r.no_amount ? '' : `，约 ${money(r.value * f)} 元`);
  const row = (cls_, title, r, p, btn) => `<div class="todo-row ${cls_}" data-code="${r.code}"><div><b>${title}</b> ${esc(r.name)} <span class="code">${r.code}</span><div class="todo-why">${esc(r.reasons[0] || '')}</div></div>${btn}</div>`;
  const sells = items.filter(({ r }) => r.level === 'sell');
  const takes = items.filter(({ r }) => r.level === 'take');
  const adds = items.filter(({ r }) => r.level === 'buy');
  const holds = items.filter(({ r }) => r.level === 'hold');
  const html = [
    ...sells.map(({ r, p }) => row('sell', `全部卖出${amt(r, 1)}：`, r, p, `<button class="small danger" data-quick="${p.id}|1">已卖出</button>`)),
    ...takes.map(({ r, p }) => row('take', `卖出一半${amt(r, 0.5)}：`, r, p, `<button class="small" data-quick="${p.id}|0.5">已卖出</button>`)),
    ...adds.map(({ r, p }) => row('buy', '可以加仓：', r, p, alipayBtn(r.code))),
  ].join('');
  $('#todo').innerHTML = `<div class="card todo">
    <div class="plan-head"><span><b>今日操作清单</b>（净值日期 ${esc(items[0].r.nav_date)}）</span><span>${sells.length + takes.length ? `<b class="up">${sells.length + takes.length} 只要操作</b>` : '今天不用卖'}</span></div>
    ${html || '<div class="todo-why">今天没有要卖出或加仓的基金。</div>'}
    ${holds.length ? `<div class="todo-why" style="margin-top:6px">其余 ${holds.length} 只（${holds.map(({ r }) => esc(r.name)).join('、')}）继续拿着，不用动。</div>` : ''}
    <div class="hint small">第二天 15:00 前在支付宝操作，按当天净值成交；卖完点“已卖出”就会自动记账。“可以加仓”只针对还没买满计划金额的基金，按方案分批买的就买下一批。</div>${disc()}
  </div>`;
}

async function loadHoldings() {
  const all = getPositions();
  if (!all.length) {
    $('#todo').innerHTML = '';
    $('#holdings').innerHTML = '<div class="empty">还没有持仓。按“方案”在支付宝买入后，点“全部加入持仓”或在上面手动添加。之后加仓、卖出都在每只基金下面“记一笔”，收益按份额自动算。</div>';
    renderSummary([]);
    return;
  }
  $('#holdings').innerHTML = '<div class="empty">计算中…</div>';
  try {
    const res = await api('/api/positions', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(all.map((p) => ({ code: p.code, trades: p.trades.map((t) => ({ id: t.id, date: t.date, type: t.type, amount: t.amount || 0, frac: t.frac || 0 })) }))) });
    renderSummary(res.filter((r) => !r.error));
    renderTodo(res, all);
    const order = res.map((r, i) => i).sort((a, b) => (res[a].level === 'closed') - (res[b].level === 'closed'));
    $('#holdings').innerHTML = order.map((i) => {
      const r = res[i], p = all[i];
      if (r.error) return `<div class="fund"><div class="top"><div class="name">${esc(p.name || p.code)}</div><button class="small danger" data-delpos="${p.id}">删除</button></div><div class="note">${esc(r.error)}</div></div>`;
      const closed = r.level === 'closed';
      const m = (v) => (r.no_amount ? '-' : `${money(v)} 元`);
      return `<div class="fund${closed ? ' closed' : ''}" data-code="${r.code}">
        <div class="top"><div class="name">${esc(r.name)}<span class="code">${r.code}${p.from_plan ? ' · 方案' : ''}</span></div>${closed ? '<span class="tag">已清仓</span>' : tag(r.level, r.action)}</div>
        <div class="grid">
          <div>累计投入<b>${m(r.invested)}</b></div><div>当前市值<b>${m(r.value)}</b></div><div>总收益<b class="${cls(r.total)}">${r.no_amount ? fmt(r.total_pct) : yuan(r.total)}</b></div>
          <div>总收益率<b class="${cls(r.total_pct)}">${fmt(r.total_pct)}</b></div><div>持有收益<b class="${cls(r.floating)}">${r.no_amount ? '-' : yuan(r.floating)}</b></div><div>已卖出收益<b class="${cls(r.realized)}">${r.no_amount ? '-' : yuan(r.realized)}</b></div>
          ${closed ? '' : `<div>成本价→最新净值<b>${r.cost_nav}→${r.nav}</b></div><div>持有份额<b>${r.no_amount ? '-' : money(r.shares)}</b></div><div>已持有<b>${r.held_days} 天</b></div>
          <div>止损净值<b class="down">${r.stop_nav}</b></div><div>止盈净值<b class="up">${r.take_nav}</b></div><div>距最高点<b>${r.from_peak > 0 ? '-' : ''}${fmt(r.from_peak)}</b></div>`}
        </div>
        ${r.no_amount ? '<div class="note">没填买入金额，只能给买卖建议，不计入总收益。可以删掉这笔重新记录金额。</div>' : ''}
        ${closed ? '' : `<ul class="reasons">${r.reasons.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>${holdExp(r)}`}
        ${cmpBlock(r)}
        ${tradeRows(r, p)}
        ${tradeForm(p)}
        <div class="actions">
          ${r.level === 'take' ? `<button class="small" data-quick="${p.id}|0.5">今天已卖出一半</button>` : ''}
          ${r.level === 'sell' ? `<button class="small danger" data-quick="${p.id}|1">今天已全部卖出</button>` : ''}
          <button class="small" data-openform="${p.id}">记一笔（加仓/卖出）</button>
          ${alipayBtn(r.code)}
          <button class="small" data-delpos="${p.id}">删除</button>
        </div></div>`;
    }).join('');
  } catch (e) { $('#holdings').innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

let picked = null;
let searchTimer;
$('#h-search').addEventListener('input', (e) => {
  picked = null;
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  if (!q) { $('#h-results').innerHTML = ''; return; }
  searchTimer = setTimeout(async () => {
    const list = await api(`/api/search?q=${encodeURIComponent(q)}`).catch(() => []);
    $('#h-results').innerHTML = list.map((f) => `<div data-code="${f.code}" data-name="${esc(f.name)}">${f.code} ${esc(f.name)} <span class="code">${esc(f.type)}</span></div>`).join('');
  }, 250);
});
$('#h-results').addEventListener('click', (e) => {
  const d = e.target.closest('[data-code]');
  if (!d) return;
  picked = { code: d.dataset.code, name: d.dataset.name };
  $('#h-search').value = `${picked.code} ${picked.name}`;
  $('#h-results').innerHTML = '';
});
$('#btn-add').onclick = () => {
  const raw = $('#h-search').value.trim();
  const code = picked?.code || (raw.match(/^\d{6}/) || [])[0];
  if (!code) return uiAlert('请先输入并选择基金');
  addBuy(code, picked?.name || code, $('#h-date').value || today(), Number($('#h-amount').value) || 0);
  $('#h-search').value = ''; $('#h-amount').value = ''; picked = null;
};
function updatePos(pid, fn) { savePositions(getPositions().map((p) => (String(p.id) === String(pid) ? fn(p) : p)).filter(Boolean)); loadHoldings(); }
const holdClick = async (e) => {
  const btn = e.target.closest('button');
  if (btn) {
    e.stopPropagation();
    const d = btn.dataset;
    if (d.openform) document.querySelector(`[data-form="${d.openform}"]`).classList.toggle('hidden');
    if (d.tsave) {
      const f = document.querySelector(`[data-form="${d.tsave}"]`);
      const type = f.querySelector('[data-ttype]').value, date = f.querySelector('[data-tdate]').value || today();
      const amt = Number(f.querySelector('[data-tamt]').value) || 0;
      let t;
      if (type === 'buy') { if (amt <= 0) return uiAlert('请填加仓金额'); t = { date, type: 'buy', amount: amt }; }
      else if (type === 'sellamt') { if (amt <= 0) return uiAlert('请填卖出金额'); t = { date, type: 'sell', amount: amt }; }
      else t = { date, type: 'sell', frac: Number(type.slice(4)) };
      updatePos(d.tsave, (p) => ({ ...p, trades: [...p.trades, { id: uid(), ...t }] }));
    }
    if (d.quick) {
      const [pid, frac] = d.quick.split('|');
      if (await uiConfirm(frac === '1' ? '确认今天已在支付宝全部卖出？' : '确认今天已卖出一半？')) updatePos(pid, (p) => ({ ...p, trades: [...p.trades, { id: uid(), date: today(), type: 'sell', frac: Number(frac) }] }));
    }
    if (d.deltrade && await uiConfirm('删除这笔交易记录？')) {
      const [pid, tid] = d.deltrade.split('|');
      updatePos(pid, (p) => { const trades = p.trades.filter((t) => String(t.id) !== tid); return trades.length ? { ...p, trades } : null; });
    }
    if (d.delpos && await uiConfirm('删除这只基金的全部记录（不再计入收益）？')) updatePos(d.delpos, () => null);
    return;
  }
  if (e.target.closest('.tform, .trades')) return;
  const card = e.target.closest('.fund[data-code]');
  if (card) openFund(card.dataset.code);
};
$('#holdings').addEventListener('click', holdClick);
$('#todo').addEventListener('click', holdClick);

// --- 支付宝 ---
document.addEventListener('click', async (e) => {
  const b = e.target.closest('[data-alipay]');
  if (!b) return;
  e.stopPropagation();
  const code = b.dataset.alipay;
  let copied = false;
  try { await navigator.clipboard.writeText(code); copied = true; } catch (_) { /* clipboard blocked */ }
  if (/iPhone|iPad|Android/i.test(navigator.userAgent)) {
    await uiAlert(`${copied ? '已复制' : '基金代码'} ${code}，即将打开支付宝，在首页搜索框粘贴代码即可找到这只基金。`);
    location.href = 'alipays://platformapi/startapp?appId=20000001';
  } else {
    uiAlert(`${copied ? '已复制' : '基金代码：'} ${code}。请在手机支付宝首页搜索这个代码购买。`);
  }
}, true);

// --- 全部基金 ---
let allPage = 1, allPages = 1;
async function loadAll(reset = true) {
  if (reset) { allPage = 1; $('#all-list').innerHTML = '<div class="empty">加载中…</div>'; }
  const q = new URLSearchParams({ ft: $('#all-ft').value, sort: $('#all-sort').value, order: $('#all-order').value, page: allPage });
  const label = $('#all-sort').selectedOptions[0].text;
  const key = { rzdf: 'r_1d', zzf: 'r_1w', '1yzf': 'r_1m', '3yzf': 'r_3m', '6yzf': 'r_6m', jnzf: 'r_ytd', '1nzf': 'r_1y', '2nzf': 'r_2y', '3nzf': 'r_3y' }[$('#all-sort').value];
  try {
    const res = await api(`/api/all?${q}`);
    allPages = res.pages;
    $('#all-total').textContent = `共 ${res.total} 只基金（同一基金的 A/C 份额分开列出），点击查看详情和买卖信号`;
    const html = res.rows.map((f, i) => `<div class="fund" data-code="${f.code}">
      <div class="row-fund"><div><div class="name">${(allPage - 1) * 30 + i + 1}. ${esc(f.name)}<span class="code">${f.code}</span></div>
        <div class="code" style="margin:4px 0 0">净值 ${f.nav} (${f.date.slice(5)}) · 日 <span class="${cls(f.r_1d)}">${fmt(f.r_1d)}</span> · 近1年 <span class="${cls(f.r_1y)}">${fmt(f.r_1y)}</span></div></div>
        <div class="ret ${cls(f[key])}">${fmt(f[key])}<div class="code" style="font-weight:normal">${label}</div></div></div></div>`).join('');
    if (reset) $('#all-list').innerHTML = html || '<div class="empty">没有数据</div>';
    else $('#all-list').insertAdjacentHTML('beforeend', html);
    $('#all-more').classList.toggle('hidden', allPage >= allPages);
  } catch (e) { $('#all-list').innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
['#all-ft', '#all-sort', '#all-order'].forEach((s) => $(s).addEventListener('change', () => loadAll(true)));
$('#all-more').onclick = () => { allPage += 1; loadAll(false); };
$('#all-list').addEventListener('click', (e) => { const c = e.target.closest('.fund[data-code]'); if (c) openFund(c.dataset.code); });
let allSearchTimer;
$('#all-search').addEventListener('input', (e) => {
  clearTimeout(allSearchTimer);
  const q = e.target.value.trim();
  if (!q) { $('#all-results').innerHTML = ''; return; }
  allSearchTimer = setTimeout(async () => {
    const list = await api(`/api/search?q=${encodeURIComponent(q)}`).catch(() => []);
    $('#all-results').innerHTML = list.map((f) => `<div data-code="${f.code}">${f.code} ${esc(f.name)} <span class="code">${esc(f.type)}</span></div>`).join('') || '<div>没找到</div>';
  }, 250);
});
$('#all-results').addEventListener('click', (e) => { const d = e.target.closest('[data-code]'); if (d) { $('#all-results').innerHTML = ''; openFund(d.dataset.code); } });

document.querySelectorAll('.seg button').forEach((b) => (b.onclick = () => {
  document.querySelectorAll('.seg button').forEach((x) => x.classList.toggle('active', x === b));
  ['stable', 'steady', 'growth'].forEach((k) => $(`#seg-${k}`).classList.toggle('hidden', b.dataset.seg !== k));
}));

['#stable', '#steady', '#growth', '#plan'].forEach((sel) => $(sel).addEventListener('click', (e) => {
  const card = e.target.closest('.fund[data-code]');
  if (card) openFund(card.dataset.code);
}));

function switchTab(name) {
  document.querySelectorAll('.tabs button').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.id === `tab-${name}`));
  if (name === 'hold') loadHoldings();
  if (name === 'all' && !$('#all-list').innerHTML) loadAll(true);
  window.scrollTo(0, 0);
}
document.querySelectorAll('.tabs button').forEach((b) => (b.onclick = () => switchTab(b.dataset.tab)));
$('#modal-close').onclick = closeModal;
$('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') closeModal(); });
$('#btn-plan').onclick = () => loadPlan(false);
$('#btn-shuffle').onclick = () => loadPlan(true);
$('#btn-refresh').onclick = async () => { await api('/api/refresh', { method: 'POST' }); loadStatus(); };

(async () => {
  const saved = JSON.parse(localStorage.getItem('plan') || 'null');
  restorePlanParams(saved);
  $('#h-date').value = today();
  const s = await loadStatus();
  if (s?.updated_at) { await loadRankings(); loadPlan(); }
  else $('#plan').innerHTML = '<div class="empty">第一次使用，正在分析几百只基金，大约需要几分钟…</div>';
})();
