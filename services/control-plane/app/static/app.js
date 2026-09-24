const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const state = {page: 'overview', tenant: 'demo', storages: [], groups: [], objects: [], selectedGroup: null};
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const timeAgo = value => value ? new Date(value).toLocaleString() : '—';
const fmtBytes = value => { let n = Number(value || 0); if (n < 1024) return `${n} B`; for (const u of ['KB','MB','GB','TB','PB']) { n /= 1024; if (n < 1024) return `${n.toFixed(n < 10 ? 1 : 0)} ${u}`; } return `${n.toFixed(1)} EB`; };
const pill = value => { const v = String(value || '—'); let c = 'neutral'; if (/READY|COMPLETE|ONLINE|ACTIVE|VERIFIED/.test(v)) c = 'good'; if (/FAILED|MISSING|ERROR|TOMBSTONED|DECOMMISSIONED/.test(v)) c = 'bad'; if (/PENDING|WARNING|RUNNING|FROZEN|ARCHIVED/.test(v)) c = 'warn'; return `<span class="pill ${c}">${esc(v)}</span>`; };

async function api(path, options = {}) {
  const response = await fetch(path, {headers: {'content-type': 'application/json', ...(options.headers || {})}, ...options});
  if (!response.ok) { const body = await response.json().catch(() => ({detail: response.statusText})); throw new Error(body.detail || 'Request failed'); }
  return response.status === 204 ? null : response.json();
}
function toast(message) { const target = $('#toast'); target.textContent = message; target.classList.add('show'); setTimeout(() => target.classList.remove('show'), 2600); }
function setTitle(title, subtitle) { $('#page-title').textContent = title; $('#page-subtitle').textContent = subtitle; }
function openModal(html) { $('#modal-content').innerHTML = html; $('#modal').classList.remove('hidden'); }
function closeModal() { $('#modal').classList.add('hidden'); }
window.closeModal = closeModal;

async function loadGroups() {
  state.groups = await api(`/api/v1/catalogue-groups?tenant_id=${state.tenant}`);
  if (!state.selectedGroup && state.groups.length) state.selectedGroup = state.groups[0].id;
}
const groupOptions = () => state.groups.map(g => `<option value="${g.id}">${esc(g.name)} · ${esc(g.storage_name)} / ${esc(g.container_name)}</option>`).join('');

async function renderOverview() {
  setTitle('Overview', 'Connector ingestion, indexing, search and governance assurance');
  const d = await api('/api/v1/overview');
  $('#view').innerHTML = `<div class="hero"><div><h2>Storage intelligence without a gateway</h2><p>AMP connects to existing object stores, extracts content and metadata, builds searchable indexes and reconciles them with authoritative storage.</p></div></div>
  <div class="grid metrics">
    <div class="metric"><div class="metric-top"><span>Storage scopes</span></div><div class="metric-value">${d.catalogue_groups}</div><div class="metric-foot">${d.active_catalogue_groups} active</div></div>
    <div class="metric"><div class="metric-top"><span>Observed objects</span></div><div class="metric-value">${d.catalogue_objects}</div><div class="metric-foot">${fmtBytes(d.bytes)}</div></div>
    <div class="metric"><div class="metric-top"><span>Search coverage</span></div><div class="metric-value">${d.index_pct}%</div><div class="metric-foot">${d.indexed} indexed objects</div></div>
    <div class="metric"><div class="metric-top"><span>Recon findings</span></div><div class="metric-value">${d.findings}</div><div class="metric-foot">Require review</div></div>
  </div>`;
}

async function renderCatalogue() {
  setTitle('Storage catalogue', 'Read-only inventory of connected object-storage scopes');
  await loadGroups();
  $('#view').innerHTML = `<div class="section-title"><div><h2>Catalogue groups</h2><p>Each account, bucket, container or namespace is independently discoverable and reconcilable.</p></div><button class="btn primary" onclick="newCatalogue()">+ Add scope</button></div>
  <div class="catalogue-grid">${state.groups.map(g => `<button class="catalogue-card ${state.selectedGroup === g.id ? 'selected' : ''}" onclick="selectGroup('${g.id}')"><h3>${esc(g.name)}</h3><p>${esc(g.storage_name)} · ${esc(g.container_name)}</p><div class="catalogue-card-stats"><span><b>${g.active_objects}</b> active</span><span><b>${g.physical_shard_count}</b> shards</span></div></button>`).join('') || '<div class="empty">No storage scopes configured</div>'}</div><div id="catalogue-detail" style="margin-top:16px"></div>`;
  if (state.selectedGroup) await showGroup(state.selectedGroup);
}
async function showGroup(id) {
  const group = await api(`/api/v1/catalogue-groups/${id}`);
  state.objects = await api(`/api/v1/catalogue-objects?catalogue_group_id=${id}&limit=300`);
  $('#catalogue-detail').innerHTML = `<div class="panel"><div class="panel-head"><div><h3>${esc(group.name)}</h3><div class="panel-sub">${esc(group.storage_name)} / ${esc(group.container_name)}</div></div>${pill(group.state)}</div>
  <table class="table"><thead><tr><th>Object</th><th>Recon ID</th><th>Size</th><th>State</th><th>Last seen</th></tr></thead><tbody>${state.objects.map(o => `<tr><td><b>${esc(o.logical_name)}</b><br><span class="mono">${esc(o.object_key)}</span></td><td class="mono">${esc(o.recon_id.slice(0,16))}…</td><td>${fmtBytes(o.size_bytes)}</td><td>${pill(o.lifecycle_state)}</td><td>${timeAgo(o.last_seen_at)}</td></tr>`).join('')}</tbody></table></div>`;
}
window.selectGroup = async id => { state.selectedGroup = id; await renderCatalogue(); };

async function renderDiscovery() {
  setTitle('Connector discovery', 'Inventory and index an existing storage scope');
  await loadGroups();
  $('#view').innerHTML = `<div class="panel"><form id="discover-form" class="form-grid"><div class="field full"><label>Storage scope</label><select name="catalogue_group_id" class="select">${groupOptions()}</select></div><div class="field full"><label>Optional prefix</label><input name="prefix" class="input"/></div><div class="field full"><label><input name="auto_index" type="checkbox" checked/> Extract and index after discovery</label></div><div class="field full"><button class="btn primary">Run discovery</button></div></form></div>`;
  $('#discover-form').onsubmit = async event => { event.preventDefault(); const f = new FormData(event.target); const result = await api('/api/v1/discovery/run', {method: 'POST', body: JSON.stringify({tenant_id: state.tenant, catalogue_group_id: f.get('catalogue_group_id'), prefix: f.get('prefix') || '', auto_index: f.get('auto_index') === 'on'})}); toast(`Discovery complete: ${result.scanned} scanned`); };
}

async function renderSearch() {
  setTitle('Search', 'Search indexed content and metadata');
  $('#view').innerHTML = `<div class="panel"><form id="search-form" class="form-grid"><div class="field full"><label>Query</label><input name="q" class="input" placeholder="contract AND jurisdiction:UK"/></div><div class="field full"><button class="btn primary">Search</button></div></form><div id="search-results"></div></div>`;
  $('#search-form').onsubmit = async event => { event.preventDefault(); const q = new FormData(event.target).get('q') || ''; const rows = await api(`/api/v1/search?q=${encodeURIComponent(q)}&tenant_id=${state.tenant}`); $('#search-results').innerHTML = `<pre>${esc(JSON.stringify(rows, null, 2))}</pre>`; };
}

async function renderReconcile() {
  setTitle('Reconciliation', 'Prove storage and index completeness');
  await loadGroups();
  $('#view').innerHTML = `<div class="panel"><form id="recon-form" class="form-grid"><div class="field full"><label>Storage scope</label><select name="catalogue_group_id" class="select">${groupOptions()}</select></div><div class="field"><label>Target</label><select name="target" class="select"><option>ALL</option><option>STORAGE</option><option>INDEX</option></select></div><div class="field"><label>Mode</label><select name="storage_mode" class="select"><option>TARGETED</option><option>TALLY</option><option>FULL</option></select></div><div class="field full"><button class="btn primary">Run reconciliation</button></div></form></div>`;
  $('#recon-form').onsubmit = async event => { event.preventDefault(); const f = new FormData(event.target); const result = await api('/api/v1/reconciliation/run', {method: 'POST', body: JSON.stringify({tenant_id: state.tenant, catalogue_group_id: f.get('catalogue_group_id'), target: f.get('target'), storage_mode: f.get('storage_mode'), verify_package_members: false, limit: 10000})}); toast(`${result.findings} reconciliation findings`); };
}

async function renderPlatform() {
  setTitle('Connectors', 'AWS S3, Azure Blob, HCP and VSP One Object');
  state.storages = await api(`/api/v1/storage-systems?tenant_id=${state.tenant}`);
  $('#view').innerHTML = `<div class="section-title"><div><h2>Storage connectors</h2><p>AMP reads native objects, metadata, tags, events and governance state. It does not provide a client storage gateway.</p></div><button class="btn primary" onclick="newStorage()">+ Connector</button></div><div class="storage-list">${state.storages.map(s => `<div class="storage-card"><div class="storage-left"><div class="storage-badge">▣</div><div><b>${esc(s.name)}</b><small>${esc(s.kind)} · ${esc(s.endpoint || s.root_path || '')}</small></div></div>${pill(s.status)}</div>`).join('')}</div>`;
}
async function renderDatasets() { setTitle('Analytics', 'Configurable Solr facets and statistics'); $('#view').innerHTML = '<div class="panel"><h3>Analytics foundation</h3><p>Age, size, type, duplicate and governance dashboards will be driven by indexed fields, Solr facets and statistics.</p></div>'; }

window.newStorage = () => { openModal(`<h2>Add storage connector</h2><form id="storage-form" class="form-grid"><div class="field"><label>Name</label><input name="name" class="input" required/></div><div class="field"><label>Type</label><select name="kind" class="select"><option>AWS_S3</option><option>AZURE_BLOB</option><option>HCP</option><option>HCP_S3</option><option>VSP_ONE_OBJECT</option><option>LOCAL</option></select></div><div class="field full"><label>Endpoint / local root</label><input name="endpoint" class="input"/></div><div class="field full right"><button class="btn primary">Connect</button></div></form>`); $('#storage-form').onsubmit = async e => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)); await api('/api/v1/storage-systems', {method:'POST', body:JSON.stringify({tenant_id:state.tenant,name:f.name,kind:f.kind,role:'EXTERNAL',endpoint:f.kind === 'LOCAL' ? null : f.endpoint,root_path:f.kind === 'LOCAL' ? f.endpoint : null,options:{}})}); closeModal(); toast('Connector added'); renderPlatform(); }; };
window.newCatalogue = async () => { state.storages = await api('/api/v1/storage-systems'); openModal(`<h2>Add storage scope</h2><form id="catalogue-form" class="form-grid"><div class="field full"><label>Connector</label><select name="storage_id" class="select">${state.storages.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join('')}</select></div><div class="field"><label>Name</label><input name="name" class="input"/></div><div class="field"><label>Scope type</label><select name="container_type" class="select"><option>S3_BUCKET</option><option>AZURE_CONTAINER</option><option>HCP_NAMESPACE</option></select></div><div class="field full"><label>Bucket / container / namespace</label><input name="container_name" class="input" required/></div><div class="field full right"><button class="btn primary">Add scope</button></div></form>`); $('#catalogue-form').onsubmit = async e => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)); await api('/api/v1/catalogue-groups', {method:'POST', body:JSON.stringify({tenant_id:state.tenant,storage_id:f.storage_id,name:f.name || null,container_type:f.container_type,container_name:f.container_name,physical_shards:4,virtual_shards:1024})}); closeModal(); state.selectedGroup = null; toast('Storage scope added'); renderCatalogue(); }; };

function navigate(page) { state.page = page; $$('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.page === page)); render().catch(e => { toast(e.message); console.error(e); }); }
async function render() { const pages = {overview:renderOverview,catalogue:renderCatalogue,discovery:renderDiscovery,datasets:renderDatasets,search:renderSearch,reconcile:renderReconcile,platform:renderPlatform}; await (pages[state.page] || renderOverview)(); }
$$('.nav-item').forEach(button => button.onclick = () => navigate(button.dataset.page));
window.navigate = navigate;
render().catch(error => toast(error.message));
