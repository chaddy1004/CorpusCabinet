/* ── State ── */
let projects      = [];
let papers        = [];
let allTags       = [];
let workspaces    = [];
let activeWorkspace = null;
let selectedProj  = null;
let selectedPaper = null;
let activeTag     = null;
let scope         = 'local';

const API = '';  // same origin

/* ── Bootstrap ── */
async function init() {
  await loadWorkspaces();
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

let dragSrcProjIdx = null;
let didDragProj = false;

function renderProjects() {
  document.getElementById('project-list').innerHTML = projects.map((p, idx) => `
    <div class="project-item ${selectedProj?.id === p.id ? 'active' : ''}"
      draggable="true"
      ondragstart="onProjDragStart(event,${idx})"
      ondragover="onProjDragOver(event,${idx})"
      ondragleave="onProjDragLeave(event)"
      ondrop="onProjDrop(event,${idx})"
      ondragend="onProjDragEnd()"
      onclick="if(!didDragProj)selectProject(${p.id})">
      <div class="dot" style="background:${p.color}"></div>
      <span class="project-name" id="proj-name-${p.id}">${p.name}</span>
      <span class="count">${p.paper_count}</span>
      <button class="rename-btn" onclick="startRenameProject(event,${p.id},'${p.name.replace(/'/g, "\\'")}')" title="Rename">✎</button>
    </div>
  `).join('');
}

function onProjDragStart(e, idx) {
  dragSrcProjIdx = idx;
  didDragProj = false;
  e.dataTransfer.effectAllowed = 'move';
  e.currentTarget.classList.add('dragging');
}

function onProjDragOver(e, idx) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  const half = _dragHalf(e);
  document.querySelectorAll('.project-item').forEach((el, i) => {
    el.classList.remove('drag-over-top', 'drag-over-bottom');
    if (i === idx && i !== dragSrcProjIdx) el.classList.add(`drag-over-${half}`);
  });
}

function onProjDragLeave(e) {
  e.currentTarget.classList.remove('drag-over-top', 'drag-over-bottom');
}

function onProjDrop(e, targetIdx) {
  e.preventDefault();
  const half = _dragHalf(e);
  document.querySelectorAll('.project-item').forEach(el => el.classList.remove('drag-over-top', 'drag-over-bottom', 'dragging'));
  if (dragSrcProjIdx === null) return;
  didDragProj = true;
  let insertAt = half === 'bottom' ? targetIdx + 1 : targetIdx;
  if (dragSrcProjIdx < insertAt) insertAt--;
  if (dragSrcProjIdx === insertAt) { dragSrcProjIdx = null; return; }
  const [moved] = projects.splice(dragSrcProjIdx, 1);
  projects.splice(insertAt, 0, moved);
  dragSrcProjIdx = null;
  renderProjects();
  api('PUT', '/projects/reorder', { project_ids: projects.map(p => p.id) });
}

function onProjDragEnd() {
  document.querySelectorAll('.project-item').forEach(el => el.classList.remove('drag-over-top', 'drag-over-bottom', 'dragging'));
  setTimeout(() => { didDragProj = false; }, 0);
}

async function startRenameProject(e, id, currentName) {
  e.stopPropagation();
  const span = document.getElementById(`proj-name-${id}`);
  if (!span) return;
  const input = document.createElement('input');
  input.className = 'rename-input';
  input.value = currentName;
  span.replaceWith(input);
  input.focus();
  input.select();

  async function save() {
    const name = input.value.trim();
    if (name && name !== currentName) {
      try {
        await api('PUT', `/projects/${id}`, { name });
        await loadProjects();
      } catch (err) {
        alert(`Rename failed: ${err.message}`);
        await loadProjects();
      }
    } else {
      await loadProjects();
    }
  }
  input.addEventListener('keydown', e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') loadProjects(); });
  input.addEventListener('blur', save);
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

let dragSrcIdx = null;
let didDrag = false;

function renderPaperList() {
  document.getElementById('paper-count').textContent = `${papers.length} paper${papers.length !== 1 ? 's' : ''}`;
  document.getElementById('paper-list').innerHTML = papers.length
    ? papers.map((p, idx) => `
      <div class="paper-card ${selectedPaper?.id === p.id ? 'active' : ''}"
        draggable="true"
        ondragstart="onPaperDragStart(event,${idx})"
        ondragover="onPaperDragOver(event,${idx})"
        ondragleave="onPaperDragLeave(event)"
        ondrop="onPaperDrop(event,${idx})"
        ondragend="onPaperDragEnd()"
        onclick="if(!didDrag)selectPaperById(${p.id})">
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

function onPaperDragStart(e, idx) {
  dragSrcIdx = idx;
  didDrag = false;
  e.dataTransfer.effectAllowed = 'move';
  e.currentTarget.classList.add('dragging');
}

function _dragHalf(e) {
  const r = e.currentTarget.getBoundingClientRect();
  return e.clientY < r.top + r.height / 2 ? 'top' : 'bottom';
}

function onPaperDragOver(e, idx) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  const half = _dragHalf(e);
  document.querySelectorAll('.paper-card').forEach((el, i) => {
    el.classList.remove('drag-over-top', 'drag-over-bottom');
    if (i === idx && i !== dragSrcIdx) el.classList.add(`drag-over-${half}`);
  });
}

function onPaperDragLeave(e) {
  e.currentTarget.classList.remove('drag-over-top', 'drag-over-bottom');
}

function onPaperDrop(e, targetIdx) {
  e.preventDefault();
  const half = _dragHalf(e);
  document.querySelectorAll('.paper-card').forEach(el => el.classList.remove('drag-over-top', 'drag-over-bottom', 'dragging'));
  if (dragSrcIdx === null) return;
  didDrag = true;
  let insertAt = half === 'bottom' ? targetIdx + 1 : targetIdx;
  if (dragSrcIdx < insertAt) insertAt--;
  if (dragSrcIdx === insertAt) { dragSrcIdx = null; return; }
  const [moved] = papers.splice(dragSrcIdx, 1);
  papers.splice(insertAt, 0, moved);
  dragSrcIdx = null;
  renderPaperList();
  api('PUT', '/papers/reorder', { paper_ids: papers.map(p => p.id) });
}

function onPaperDragEnd() {
  document.querySelectorAll('.paper-card').forEach(el => el.classList.remove('drag-over-top', 'drag-over-bottom', 'dragging'));
  setTimeout(() => { didDrag = false; }, 0);
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
      <div style="display:flex;align-items:flex-start;gap:8px;">
        <h2 style="flex:1">${paper.title}</h2>
        <button class="delete-paper-btn" onclick="deletePaper(${paper.id})" title="Delete paper">🗑</button>
      </div>
      <div class="authors">${paper.authors || 'Authors unknown'}</div>
      <div class="conf-row">
        ${paper.conference ? `<span class="conf-badge" style="background:#EEEDFE;color:#3C3489">${paper.conference}</span>` : ''}
        ${paper.year ? `<span class="ds-tag">${paper.year}</span>` : ''}
      </div>
    </div>
    <div class="detail-tabs">
      <button class="detail-tab active" onclick="switchDetailTab(${paper.id}, 'details', this)">Details</button>
      <button class="detail-tab" onclick="switchDetailTab(${paper.id}, 'pdf', this)">PDF</button>
    </div>
    <iframe class="pdf-viewer" id="pdf-frame-${paper.id}" src="/papers/${paper.id}/pdf" style="display:none"></iframe>
    <div class="detail-body" id="detail-body-${paper.id}">
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
          <button class="copy-btn" onclick="copyBibtex(${paper.id})">Copy</button><pre>${paper.bibtex || 'BibTeX not available'}</pre>
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

async function deletePaper(paperId) {
  if (!confirm('Delete this paper? This cannot be undone.')) return;
  await api('DELETE', `/papers/${paperId}`);
  papers = papers.filter(p => p.id !== paperId);
  selectedPaper = null;
  renderPaperList();
  renderDetail(null);
  await loadProjects(); // Update paper counts
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

/* ── Detail tabs ── */
function switchDetailTab(paperId, tab, btn) {
  const body = document.getElementById(`detail-body-${paperId}`);
  const frame = document.getElementById(`pdf-frame-${paperId}`);
  const tabs = btn.closest('.detail-tabs').querySelectorAll('.detail-tab');
  tabs.forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  if (tab === 'pdf') {
    body.style.display = 'none';
    frame.style.display = 'block';
  } else {
    body.style.display = '';
    frame.style.display = 'none';
  }
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
  const overlay = document.getElementById('processing-overlay');
  const overlayLabel = document.getElementById('processing-label');
  overlayLabel.textContent = `Processing ${file.name}…`;
  overlay.classList.add('visible');

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
    overlay.classList.remove('visible');
  }
}

/* ── Workspaces ── */
async function loadWorkspaces() {
  try {
    const data = await api('GET', '/workspaces');
    workspaces = data.workspaces;
    activeWorkspace = workspaces.find(w => w.is_active);

    // Update selector display
    if (activeWorkspace) {
      document.getElementById('workspace-name').textContent = activeWorkspace.name;
      document.getElementById('workspace-color').style.background = activeWorkspace.color;
    }
  } catch (err) {
    console.error('Failed to load workspaces:', err);
  }
}

async function openWorkspaceSettings() {
  document.getElementById('workspace-modal').classList.add('visible');
  await loadWorkspaces(); // Refresh list
  renderWorkspaceList();
}

function closeWorkspaceSettings() {
  document.getElementById('workspace-modal').classList.remove('visible');
  document.getElementById('new-workspace-name').value = '';
  document.getElementById('new-workspace-path').value = '';
}

function closeWorkspaceSettingsIfBackdrop(event) {
  if (event.target.id === 'workspace-modal') closeWorkspaceSettings();
}

function renderWorkspaceList() {
  const container = document.getElementById('workspace-list');
  container.innerHTML = workspaces.map(w => `
    <div class="workspace-item ${w.is_active ? 'active' : ''}">
      <div class="workspace-item-dot" style="background:${w.color}"></div>
      <div class="workspace-item-info" id="ws-info-${btoa(w.path).replace(/=/g,'')}">
        <div class="workspace-item-name">${w.name}</div>
        <div class="workspace-item-path">${w.path}</div>
      </div>
      <button class="workspace-item-btn" onclick="startRenameWorkspace('${w.path}','${w.name.replace(/'/g, "\\'")}')">Rename</button>
      ${w.is_active
        ? '<span style="font-size:11px;color:var(--accent)">● Active</span>'
        : `<button class="workspace-item-btn" onclick="switchWorkspace('${w.path}')">Switch</button>`
      }
      ${workspaces.length > 1 && !w.is_active
        ? `<button class="workspace-item-btn danger" onclick="deleteWorkspace('${w.path}')">Remove</button>`
        : ''
      }
    </div>
  `).join('');
}

async function startRenameWorkspace(path, currentName) {
  const newName = prompt('Rename workspace:', currentName);
  if (!newName || newName.trim() === currentName) return;
  try {
    const encodedPath = encodeURIComponent(path);
    await api('PUT', `/workspaces/${encodedPath}`, { name: newName.trim() });
    await loadWorkspaces();
    renderWorkspaceList();
  } catch (err) {
    alert(`Rename failed: ${err.message}`);
  }
}

let pendingWorkspace = null;

async function createNewWorkspace() {
  const name = document.getElementById('new-workspace-name').value.trim();
  const path = document.getElementById('new-workspace-path').value.trim();

  if (!name || !path) {
    alert('Please enter both a name and path for the workspace');
    return;
  }

  try {
    // Check if path exists
    const check = await api('POST', '/workspaces/check-path', { name, path });

    if (check.needs_creation) {
      // Show confirmation dialog
      pendingWorkspace = { name, path };
      showConfirmDialog(
        'Create Workspace Folder?',
        `This folder doesn't exist yet. Corpus Cabinet will create it for you:`,
        check.path
      );
    } else if (check.exists && !check.is_directory) {
      alert('This path exists but is not a directory. Please choose a different location.');
    } else {
      // Path exists and is a directory, create workspace directly
      await doCreateWorkspace(name, path);
    }
  } catch (err) {
    alert(`Failed to check path: ${err.message}`);
  }
}

async function doCreateWorkspace(name, path) {
  try {
    await api('POST', '/workspaces', { name, path });
    await loadWorkspaces();
    renderWorkspaceList();
    document.getElementById('new-workspace-name').value = '';
    document.getElementById('new-workspace-path').value = '';
    alert(`Workspace "${name}" created! You can now switch to it.`);
  } catch (err) {
    alert(`Failed to create workspace: ${err.message}`);
  }
}

function showConfirmDialog(title, message, path) {
  document.getElementById('confirm-title').textContent = title;
  document.getElementById('confirm-message').textContent = message;
  document.getElementById('confirm-path').textContent = path;
  document.getElementById('confirm-dialog').classList.add('visible');
}

function closeConfirmDialog(event) {
  if (!event || event.target.id === 'confirm-dialog') {
    document.getElementById('confirm-dialog').classList.remove('visible');
    pendingWorkspace = null;
  }
}

function confirmAction() {
  if (pendingWorkspace) {
    closeConfirmDialog();
    doCreateWorkspace(pendingWorkspace.name, pendingWorkspace.path);
  }
}

async function switchWorkspace(path) {
  if (!confirm('Switch workspace? The page will reload.')) return;

  try {
    const encodedPath = encodeURIComponent(path);
    await api('POST', `/workspaces/${encodedPath}/activate`, {});
    window.location.reload();
  } catch (err) {
    alert(`Failed to switch workspace: ${err.message}`);
  }
}

async function deleteWorkspace(path) {
  if (!confirm('Remove this workspace from the list? (Files will not be deleted)')) return;

  try {
    const encodedPath = encodeURIComponent(path);
    await fetch(`${API}/workspaces/${encodedPath}`, { method: 'DELETE' });
    await loadWorkspaces();
    renderWorkspaceList();
  } catch (err) {
    alert(`Failed to remove workspace: ${err.message}`);
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
