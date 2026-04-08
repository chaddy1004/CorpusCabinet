/* ── State ── */
let projects      = [];
let papers        = [];
let allTags       = [];
let selectedProj  = null;
let selectedPaper = null;
let activeTag     = null;
let scope         = 'local';

const API = '';  // same origin

/* ── Bootstrap ── */
async function init() {
  await loadProjects();
  await loadAllTags();
  setupDropZone();
}

/* ── Projects ── */
async function loadProjects() {
  projects = await api('GET', '/projects');
  renderProjects();
  if (projects.length > 0 && !selectedProj) {
    selectProject(projects[0]);
  }
}

function renderProjects() {
  document.getElementById('project-list').innerHTML = projects.map(p => `
    <div class="project-item ${selectedProj?.id === p.id ? 'active' : ''}" onclick="selectProject(${p.id})">
      <div class="dot" style="background:${p.color}"></div>
      <span>${p.name}</span>
      <span class="count">${p.paper_count}</span>
    </div>
  `).join('');
}

function selectProject(projOrId) {
  selectedProj = typeof projOrId === 'object' ? projOrId : projects.find(p => p.id === projOrId);
  selectedPaper = null;
  activeTag = null;
  clearTagFilter(false);
  document.getElementById('list-title').textContent = scope === 'local' ? selectedProj.name : 'All projects';
  renderProjects();
  loadPapers();
  renderDetail(null);
}

async function promptNewProject() {
  const name = prompt('Project name:');
  if (!name) return;
  const color = ['#7F77DD','#1D9E75','#D85A30','#378ADD','#D4537E','#BA7517'][projects.length % 6];
  await api('POST', '/projects', { name, color });
  await loadProjects();
}

/* ── Scope ── */
function setScope(s) {
  scope = s;
  document.getElementById('btn-local').classList.toggle('active', s === 'local');
  document.getElementById('btn-global').classList.toggle('active', s === 'global');
  document.getElementById('list-title').textContent = s === 'local' ? (selectedProj?.name || '') : 'All projects';
  loadPapers();
}

/* ── Papers ── */
async function loadPapers() {
  if (!selectedProj && scope === 'local') return;
  const params = new URLSearchParams({ scope });
  if (scope === 'local' && selectedProj) params.set('project_id', selectedProj.id);
  if (activeTag) params.set('tag', activeTag);
  const q = document.getElementById('search-input').value.trim();
  if (q) params.set('q', q);

  papers = await api('GET', `/papers?${params}`);
  renderPaperList();
}

function renderPaperList() {
  document.getElementById('paper-count').textContent = `${papers.length} paper${papers.length !== 1 ? 's' : ''}`;
  document.getElementById('paper-list').innerHTML = papers.length
    ? papers.map(p => `
      <div class="paper-card ${selectedPaper?.id === p.id ? 'active' : ''}" onclick="selectPaperById(${p.id})">
        <div class="paper-title">${p.title}</div>
        <div class="paper-meta">
          <span>${p.conference || 'Unknown venue'}</span>
          ${scope === 'global' ? `<span>· ${p.project}</span>` : ''}
        </div>
        <div class="paper-tag-row">
          ${p.tags.map(t => `<span class="tag-chip" style="background:${t.color};color:${t.text_color}">${t.name}</span>`).join('')}
        </div>
      </div>`).join('')
    : '<div class="empty-state">No papers match.</div>';
}

function selectPaperById(id) {
  selectedPaper = papers.find(p => p.id === id);
  renderPaperList();
  renderDetail(selectedPaper);
}

/* ── Detail panel ── */
function renderDetail(paper) {
  const el = document.getElementById('detail-panel');
  if (!paper) {
    el.innerHTML = '<div class="empty-state" style="margin:auto;">Select a paper to view details</div>';
    return;
  }
  el.innerHTML = `
    <div class="detail-header">
      <h2>${paper.title}</h2>
      <div class="authors">${paper.authors || 'Authors unknown'}</div>
      <div class="conf-row">
        ${paper.conference ? `<span class="conf-badge" style="background:#EEEDFE;color:#3C3489">${paper.conference}</span>` : ''}
        ${paper.year ? `<span class="ds-tag">${paper.year}</span>` : ''}
      </div>
    </div>
    <div class="detail-body">
      <div>
        <div class="section-label">AI summary</div>
        <div class="summary-box">
          ${paper.task ? `<strong>Task:</strong> ${paper.task}<br><br>` : ''}
          ${paper.methodology ? `<strong>Methodology:</strong> ${paper.methodology}` : ''}
          ${!paper.task && !paper.methodology ? '<span style="color:var(--text3)">Summary not yet generated.</span>' : ''}
        </div>
      </div>
      <div>
        <div class="section-label">Tags</div>
        <div class="tag-editor" id="tag-editor-${paper.id}"></div>
        <div id="tag-input-wrap-${paper.id}" style="display:none;margin-top:8px;">
          <input class="tag-input" id="tag-input-${paper.id}" placeholder="Type a tag..." oninput="renderTagSuggestions(${paper.id})" onkeydown="handleTagKey(event,${paper.id})">
          <div class="tag-suggestions" id="tag-suggestions-${paper.id}"></div>
        </div>
      </div>
      <div>
        <div class="section-label">BibTeX</div>
        <div class="bibtex-box" id="bibtex-${paper.id}">
          <button class="copy-btn" onclick="copyBibtex(${paper.id})">Copy</button>${paper.bibtex || 'BibTeX not available'}
        </div>
      </div>
      ${paper.datasets?.length ? `
      <div>
        <div class="section-label">Datasets &amp; Metrics</div>
        <div class="datasets-row">
          ${paper.datasets.map(d => `<span class="ds-tag">${d}</span>`).join('')}
          ${paper.metrics.map(m => `<span class="ds-tag" style="border-style:dashed">${m}</span>`).join('')}
        </div>
      </div>` : ''}
    </div>`;

  renderTagEditor(paper);
}

/* ── Tags ── */
async function loadAllTags() {
  allTags = await api('GET', '/tags');
  renderTagFilters();
}

function renderTagFilters() {
  const counts = {};
  if (scope === 'local' && selectedProj) {
    papers.forEach(p => p.tags.forEach(t => { counts[t.name] = (counts[t.name] || 0) + 1; }));
  } else {
    allTags.forEach(t => { counts[t.name] = t.count; });
  }
  document.getElementById('tag-filters').innerHTML = Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .map(([name, count]) => {
      const t = allTags.find(x => x.name === name) || { color: '#F1EFE8', text_color: '#444441' };
      return `<div class="tag-filter-item ${activeTag === name ? 'active' : ''}" onclick="setTagFilter('${name}')">
        <div class="tag-dot" style="background:${t.color};border:1px solid ${t.text_color}30"></div>
        <span>${name}</span>
        <span class="count">${count}</span>
      </div>`;
    }).join('');
}

function setTagFilter(tag) {
  activeTag = activeTag === tag ? null : tag;
  const filterEl  = document.getElementById('active-filter');
  const chipEl    = document.getElementById('filter-chip-label');
  if (activeTag) {
    const t = allTags.find(x => x.name === activeTag) || { color:'#eee', text_color:'#333' };
    filterEl.classList.add('visible');
    chipEl.textContent = activeTag;
    chipEl.style.cssText = `background:${t.color};color:${t.text_color}`;
  } else {
    filterEl.classList.remove('visible');
  }
  renderTagFilters();
  loadPapers();
}

function clearTagFilter(reload = true) {
  activeTag = null;
  document.getElementById('active-filter').classList.remove('visible');
  renderTagFilters();
  if (reload) loadPapers();
}

function renderTagEditor(paper) {
  const el = document.getElementById(`tag-editor-${paper.id}`);
  if (!el) return;
  el.innerHTML = paper.tags.map(t => `
    <span class="tag-removable" style="background:${t.color};color:${t.text_color}">
      ${t.name}
      <span class="remove" onclick="removeTag(${paper.id},'${t.name}')">✕</span>
    </span>`).join('') +
    `<button class="add-tag-btn" onclick="toggleTagInput(${paper.id})">+ Add tag</button>`;
}

function toggleTagInput(paperId) {
  const wrap = document.getElementById(`tag-input-wrap-${paperId}`);
  wrap.style.display = wrap.style.display === 'none' ? 'block' : 'none';
  if (wrap.style.display === 'block') {
    document.getElementById(`tag-input-${paperId}`).focus();
    renderTagSuggestions(paperId);
  }
}

function renderTagSuggestions(paperId) {
  const paper = selectedPaper;
  const q = document.getElementById(`tag-input-${paperId}`).value.toLowerCase();
  const existing = new Set(paper.tags.map(t => t.name));
  const suggestions = allTags.filter(t => !existing.has(t.name) && t.name.includes(q)).slice(0, 8);
  document.getElementById(`tag-suggestions-${paperId}`).innerHTML = suggestions.map(t =>
    `<span class="tag-suggestion tag-chip" style="background:${t.color};color:${t.text_color}" onclick="addTag(${paperId},'${t.name}')">${t.name}</span>`
  ).join('');
}

function handleTagKey(e, paperId) {
  if (e.key === 'Enter') {
    const val = e.target.value.trim().toLowerCase().replace(/\s+/g, '-');
    if (val) addTag(paperId, val);
  }
  if (e.key === 'Escape') document.getElementById(`tag-input-wrap-${paperId}`).style.display = 'none';
}

async function addTag(paperId, name) {
  const tag = await api('POST', `/papers/${paperId}/tags`, { name });
  // Update local state
  if (selectedPaper?.id === paperId) {
    if (!selectedPaper.tags.find(t => t.name === name)) {
      selectedPaper.tags.push(tag);
    }
    renderTagEditor(selectedPaper);
    document.getElementById(`tag-input-${paperId}`).value = '';
  }
  await loadAllTags();
  await loadPapers();
}

async function removeTag(paperId, tagName) {
  await api('DELETE', `/papers/${paperId}/tags/${tagName}`);
  if (selectedPaper?.id === paperId) {
    selectedPaper.tags = selectedPaper.tags.filter(t => t.name !== tagName);
    renderTagEditor(selectedPaper);
  }
  await loadAllTags();
  await loadPapers();
}

/* ── BibTeX copy ── */
function copyBibtex(paperId) {
  const paper = papers.find(p => p.id === paperId);
  if (paper?.bibtex) navigator.clipboard.writeText(paper.bibtex);
  const btn = document.querySelector(`#bibtex-${paperId} .copy-btn`);
  if (btn) { btn.textContent = 'Copied!'; setTimeout(() => btn.textContent = 'Copy', 1500); }
}

/* ── Drop zone ── */
function setupDropZone() {
  const dz = document.getElementById('drop-zone');

  dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('dragover'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('dragover'));
  dz.addEventListener('drop', async e => {
    e.preventDefault();
    dz.classList.remove('dragover');
    const files = [...e.dataTransfer.files].filter(f => f.name.endsWith('.pdf'));
    if (!files.length) return alert('Please drop a PDF file.');
    if (!selectedProj) return alert('Select a project first.');
    for (const file of files) await uploadPdf(file);
  });

  dz.addEventListener('click', () => {
    const input = document.createElement('input');
    input.type = 'file'; input.accept = '.pdf'; input.multiple = true;
    input.onchange = async e => {
      for (const file of e.target.files) await uploadPdf(file);
    };
    input.click();
  });
}

async function uploadPdf(file) {
  const label = document.getElementById('drop-label');
  label.textContent = `Processing ${file.name}...`;

  const form = new FormData();
  form.append('file', file);
  form.append('project_id', selectedProj.id);

  try {
    const resp = await fetch(`${API}/papers/upload`, { method: 'POST', body: form });
    if (!resp.ok) throw new Error(await resp.text());
    const paper = await resp.json();
    papers.unshift(paper);
    renderPaperList();
    selectPaperById(paper.id);
    await loadProjects();
    await loadAllTags();
  } catch (err) {
    alert(`Upload failed: ${err.message}`);
  } finally {
    label.textContent = 'Drop PDF here to add paper';
  }
}

/* ── API helper ── */
async function api(method, path, body = null) {
  const opts = { method, headers: {} };
  if (body) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const resp = await fetch(`${API}${path}`, opts);
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json();
}

init();
