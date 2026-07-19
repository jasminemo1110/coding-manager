// Frontend interactions: sync buttons, iteration checkboxes.

// 同步在后台线程跑：POST 只负责启动，之后轮询 /sync/status 直到结束。
// onTick 在每次拿到状态时被调，用来更新按钮上的进度文案。
async function pollSync(statusUrl, onTick) {
  for (;;) {
    await new Promise(res => setTimeout(res, 1200));
    const r = await fetch(statusUrl);
    const s = await r.json();
    if (onTick) onTick(s);
    if (!s.running) return s;
  }
}

async function startSync(startUrl) {
  const r = await fetch(startUrl, { method: 'POST' });
  const data = await r.json();
  if (!data.ok) {
    throw new Error(data.reason === 'already running' ? '已有同步在进行中' : (data.reason || '启动失败'));
  }
  return data;
}

document.addEventListener('DOMContentLoaded', () => {
  // Global "sync today" on dashboard
  const syncAll = document.getElementById('sync-today-btn');
  if (syncAll) {
    syncAll.addEventListener('click', async () => {
      const originalText = syncAll.textContent;
      syncAll.disabled = true;
      syncAll.textContent = '同步中…';
      try {
        await startSync(syncAll.dataset.url);
        const final = await pollSync(syncAll.dataset.statusUrl, (s) => {
          if (s.running) {
            syncAll.textContent = `同步中 ${s.done}/${s.total}${s.current ? '：' + s.current : ''}…`;
          }
        });
        syncAll.textContent = `✓ ${final.synced} 个项目已同步到最新，${final.skipped} 个项目无新改动`;
        setTimeout(() => { window.location.reload(); }, 1200);
      } catch (e) {
        syncAll.textContent = '同步失败：' + e.message;
        syncAll.disabled = false;
        setTimeout(() => { syncAll.textContent = originalText; }, 3000);
      }
    });
  }

  // Per-project "sync today"
  const syncOne = document.getElementById('project-sync-btn');
  if (syncOne) {
    syncOne.addEventListener('click', async () => {
      const originalText = syncOne.textContent;
      syncOne.disabled = true;
      syncOne.textContent = '同步中…';
      try {
        await startSync(syncOne.dataset.url);
        const final = await pollSync(syncOne.dataset.statusUrl);
        const res = (final.results || [])[0] || {};
        if (res.skipped) {
          syncOne.textContent = '✓ 没有新改动';
          syncOne.disabled = false;
          setTimeout(() => { syncOne.textContent = originalText; }, 2200);
        } else if (res.ok) {
          const dayPart = res.days > 1 ? `${res.days} 天 / ` : '';
          syncOne.textContent = `✓ ${dayPart}${res.commits} 条 commit`;
          setTimeout(() => { window.location.reload(); }, 800);
        } else {
          throw new Error(res.reason || '未知原因');
        }
      } catch (e) {
        syncOne.textContent = '同步失败';
        syncOne.disabled = false;
        setTimeout(() => { syncOne.textContent = originalText; }, 2500);
      }
    });
  }

  // History import buttons (dashboard + project pages)
  ['sync-history-btn', 'project-history-btn'].forEach(id => {
    const btn = document.getElementById(id);
    if (!btn) return;
    btn.addEventListener('click', async () => {
      if (!confirm('将所有项目（或当前项目）的全部历史 commit 按日期导入。仅存 commit 列表，不调 AI。继续？')) return;
      const originalText = btn.textContent;
      btn.disabled = true;
      btn.textContent = '导入中…';
      try {
        const r = await fetch(btn.dataset.url, { method: 'POST' });
        const data = await r.json();
        if (data.ok) {
          btn.textContent = `✓ 已导入 ${data.days || data.log_entries || 0} 天`;
          setTimeout(() => { window.location.reload(); }, 1200);
        } else {
          btn.textContent = '失败：' + (data.reason || '');
          btn.disabled = false;
        }
      } catch (e) {
        btn.textContent = '失败：' + e.message;
        btn.disabled = false;
        setTimeout(() => { btn.textContent = originalText; }, 2500);
      }
    });
  });

  // Per-day AI regenerate
  document.querySelectorAll('.regen-summary').forEach(btn => {
    btn.addEventListener('click', async () => {
      const original = btn.textContent;
      btn.disabled = true;
      btn.textContent = '生成中…';
      try {
        const r = await fetch(btn.dataset.url, { method: 'POST' });
        const data = await r.json();
        if (data.ok && data.summary) {
          const div = document.createElement('div');
          div.className = 'auto-summary';
          div.textContent = data.summary;
          btn.replaceWith(div);
        } else {
          btn.textContent = '失败：' + (data.reason || '');
          btn.disabled = false;
          setTimeout(() => { btn.textContent = original; }, 2500);
        }
      } catch (e) {
        btn.textContent = '失败';
        btn.disabled = false;
        setTimeout(() => { btn.textContent = original; }, 2500);
      }
    });
  });

  // Inline stage select on dashboard cards
  document.querySelectorAll('select.stage-select[data-url]').forEach(sel => {
    sel.addEventListener('change', async () => {
      const previous = sel.dataset.previous || sel.options[sel.selectedIndex].value;
      const newStage = sel.value;
      try {
        const fd = new FormData();
        fd.append('stage', newStage);
        const r = await fetch(sel.dataset.url, { method: 'POST', body: fd });
        const data = await r.json();
        if (data.ok) {
          // Swap visual color class
          sel.className = 'stage-select stage-' + newStage;
          sel.dataset.previous = newStage;
        } else {
          sel.value = previous;
        }
      } catch (e) {
        sel.value = previous;
      }
    });
    sel.dataset.previous = sel.value;
  });

  // Project / global todo checkboxes (project columns + top panel)
  document.querySelectorAll('input[type=checkbox][data-todo-toggle]').forEach(cb => {
    cb.addEventListener('change', async () => {
      const row = cb.closest('.todo-row, .panel-todo-row');
      try {
        const r = await fetch(cb.dataset.todoToggle, { method: 'POST' });
        const data = await r.json();
        if (data.ok) {
          // 首页项目待办列：勾选完成后直接移除该行（已完成的留在项目详情页）
          if (data.done && cb.hasAttribute('data-hide-when-done')) {
            if (row) row.remove();
          } else if (row) {
            row.classList.toggle('done', !!data.done);
          }
        } else {
          cb.checked = !cb.checked;
        }
      } catch (e) {
        cb.checked = !cb.checked;
      }
    });
  });

  // Todo "important" toggle (highlight in place + sync to top panel)
  document.querySelectorAll('.todo-important-btn[data-url]').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      try {
        const r = await fetch(btn.dataset.url, { method: 'POST' });
        const data = await r.json();
        if (data.ok) {
          const on = !!data.important;
          btn.classList.toggle('on', on);
          const row = btn.closest('.todo-row, .panel-todo-row');
          if (row) row.classList.toggle('important', on);
        }
      } catch (e) { /* ignore */ }
    });
  });

  // 首页清单提醒的「忽略」：静音这一条，移除该行
  document.querySelectorAll('.reminder-ignore[data-ignore-url]').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      e.preventDefault();
      const row = btn.closest('.checklist-reminder');
      try {
        const r = await fetch(btn.dataset.ignoreUrl, { method: 'POST' });
        const data = await r.json();
        if (data.ok && row) row.remove();
      } catch (e) { /* ignore */ }
    });
  });

  // Todo inline text edit (✎)
  document.querySelectorAll('.todo-edit-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      e.preventDefault();
      const row = btn.closest('.todo-row');
      if (!row) return;
      const span = row.querySelector('.todo-text');
      if (!span || row.querySelector('.todo-edit-input')) return;
      const input = document.createElement('input');
      input.type = 'text';
      input.className = 'todo-edit-input';
      input.value = span.textContent;
      span.style.display = 'none';
      span.parentNode.insertBefore(input, span.nextSibling);
      input.focus();
      input.select();
      let settled = false;
      const finish = async (commit) => {
        if (settled) return;
        settled = true;
        const text = input.value.trim();
        if (commit && text && text !== span.textContent) {
          try {
            const fd = new FormData();
            fd.append('text', text);
            const r = await fetch(span.dataset.editUrl, { method: 'POST', body: fd });
            const data = await r.json();
            if (data.ok) span.textContent = data.text;
          } catch (err) { /* ignore */ }
        }
        input.remove();
        span.style.display = '';
      };
      input.addEventListener('click', (ev) => ev.stopPropagation());
      input.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') { ev.preventDefault(); finish(true); }
        else if (ev.key === 'Escape') { ev.preventDefault(); finish(false); }
      });
      input.addEventListener('blur', () => finish(true));
    });
  });

  // Media chip star toggle
  document.querySelectorAll('.media-star[data-url]').forEach(star => {
    star.addEventListener('click', async (e) => {
      e.stopPropagation();
      try {
        const r = await fetch(star.dataset.url, { method: 'POST' });
        const data = await r.json();
        if (data.ok) {
          const on = !!data.starred;
          star.classList.toggle('on', on);
          star.closest('.media-chip')?.classList.toggle('starred', on);
        }
      } catch (e) { /* ignore */ }
    });
  });

  // Daily-log checklist: remove (×) / re-add (＋) an item for that day
  document.querySelectorAll('.check-remove[data-url], .check-readd[data-url]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const fd = new FormData();
      fd.append('field', btn.dataset.field);
      try {
        const r = await fetch(btn.dataset.url, { method: 'POST', body: fd });
        const data = await r.json();
        if (data.ok) window.location.reload();
      } catch (e) { /* ignore */ }
    });
  });

  // Refresh GitHub visibility (project page)
  const refreshGh = document.getElementById('refresh-github-btn');
  if (refreshGh) {
    refreshGh.addEventListener('click', async () => {
      const original = refreshGh.textContent;
      refreshGh.disabled = true;
      refreshGh.textContent = '检测中…';
      try {
        const r = await fetch(refreshGh.dataset.url, { method: 'POST' });
        const data = await r.json();
        if (data.ok) {
          const label = data.visibility === 'public' ? '公开' : (data.visibility === 'private' ? '私有' : '未知');
          refreshGh.textContent = '✓ ' + label;
          setTimeout(() => { window.location.reload(); }, 800);
        } else {
          refreshGh.textContent = '检测失败';
          refreshGh.disabled = false;
          setTimeout(() => { refreshGh.textContent = original; }, 2500);
        }
      } catch (e) {
        refreshGh.textContent = '检测失败';
        refreshGh.disabled = false;
        setTimeout(() => { refreshGh.textContent = original; }, 2500);
      }
    });
  }

  // Note expand modal — click a clipped note to read it full-size
  const noteModal = document.getElementById('note-modal');
  if (noteModal) {
    document.querySelectorAll('.note-clickable').forEach(el => {
      el.addEventListener('click', (e) => {
        if (e.target.closest('a, button, input, textarea, summary, details')) return;
        document.getElementById('note-modal-title').textContent = el.dataset.noteTitle || '';
        document.getElementById('note-modal-body').textContent = el.dataset.noteBody || '(无正文)';
        noteModal.showModal();
      });
    });
  }
  // mark clipped note bodies so the "…点击展开" hint only shows when actually truncated
  document.querySelectorAll('.note-body-clip').forEach(el => {
    if (el.scrollHeight > el.clientHeight + 2) el.classList.add('is-clipped');
  });

  // Auto-growing textareas
  document.querySelectorAll('textarea.autosize').forEach(ta => {
    const grow = () => {
      ta.style.height = 'auto';
      ta.style.height = ta.scrollHeight + 'px';
    };
    grow();
    ta.addEventListener('input', grow);
  });

  // Reference form: "＋ 加一条链接" clones the last name/url row
  document.querySelectorAll('.add-link-row').forEach(btn => {
    btn.addEventListener('click', () => {
      const wrap = btn.parentElement.querySelector('.ref-links-edit');
      if (!wrap) return;
      const rows = wrap.querySelectorAll('.ref-link-row');
      const last = rows[rows.length - 1];
      const fresh = last.cloneNode(true);
      fresh.querySelectorAll('input').forEach(i => { i.value = ''; });
      wrap.appendChild(fresh);
      fresh.querySelector('input')?.focus();
    });
  });

  // Card navigation (card is a div now) — navigate unless click hit an interactive child
  document.querySelectorAll('.card[data-href]').forEach(card => {
    card.addEventListener('click', (e) => {
      if (e.target.closest('.stage-select, .star-btn, .category-picker, .badge[data-href]')) return;
      window.location.href = card.dataset.href;
    });
  });

  // Star toggle
  document.querySelectorAll('.star-btn[data-url]').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      try {
        const r = await fetch(btn.dataset.url, { method: 'POST' });
        const data = await r.json();
        if (data.ok) {
          const on = !!data.starred;
          btn.classList.toggle('starred', on);
          const card = btn.closest('.card');
          if (card) card.classList.toggle('starred', on);
        }
      } catch (e) { /* ignore */ }
    });
  });

  // Multi-category picker on dashboard cards
  document.querySelectorAll('.category-picker').forEach(picker => {
    const btn = picker.querySelector('.category-btn');
    const pop = picker.querySelector('.category-popover');
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const wasHidden = pop.hidden;
      document.querySelectorAll('.category-popover').forEach(p => p.hidden = true);
      document.querySelectorAll('.card.popover-open').forEach(c => c.classList.remove('popover-open'));
      pop.hidden = !wasHidden;
      if (!pop.hidden) picker.closest('.card')?.classList.add('popover-open');
    });
    pop.addEventListener('click', (e) => e.stopPropagation());
    picker.querySelectorAll('input[data-cat-toggle]').forEach(cb => {
      cb.addEventListener('change', async () => {
        const fd = new FormData();
        fd.append('category_id', cb.value);
        try {
          const r = await fetch(cb.dataset.url, { method: 'POST', body: fd });
          const data = await r.json();
          if (!data.ok) { cb.checked = !cb.checked; return; }
        } catch (e) { cb.checked = !cb.checked; return; }
        // rebuild button display
        const checked = Array.from(picker.querySelectorAll('input[data-cat-toggle]:checked'));
        btn.textContent = '';
        if (checked.length) {
          checked.forEach(c => {
            const s = document.createElement('span');
            s.className = 'cat-chip';
            s.textContent = c.parentElement.querySelector('span').textContent;
            btn.appendChild(s);
          });
        } else {
          const s = document.createElement('span');
          s.className = 'cat-placeholder';
          s.textContent = '＋ 分类';
          btn.appendChild(s);
        }
      });
    });
  });
  // close category popovers on outside click
  document.addEventListener('click', () => {
    document.querySelectorAll('.category-popover').forEach(p => p.hidden = true);
    document.querySelectorAll('.card.popover-open').forEach(c => c.classList.remove('popover-open'));
  });

  // Media add buttons on dashboard — open shared dialog with project context
  const mediaDialog = document.getElementById('add-media-dialog');
  document.querySelectorAll('.media-add-btn[data-project-id]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (!mediaDialog) return;
      document.getElementById('media-dialog-pid').value = btn.dataset.projectId;
      document.getElementById('media-dialog-project').textContent = btn.dataset.projectName;
      mediaDialog.showModal();
    });
  });

  // Media chip click — open edit dialog populated from data attributes
  const mediaEditDialog = document.getElementById('media-edit-dialog');
  document.querySelectorAll('.media-chip[data-media-id]').forEach(chip => {
    chip.addEventListener('click', (e) => {
      e.stopPropagation();
      if (!mediaEditDialog) return;
      const id = chip.dataset.mediaId;
      document.getElementById('media-edit-form').action = '/media/' + id + '/edit';
      document.getElementById('media-delete-form').action = '/media/' + id + '/delete';
      document.getElementById('me-title').value = chip.dataset.title;
      document.getElementById('me-type').value = chip.dataset.type;
      document.getElementById('me-status').value = chip.dataset.status;
      document.getElementById('me-notes').value = chip.dataset.notes;
      document.getElementById('me-publish-date').value = chip.dataset.publishDate || '';
      mediaEditDialog.showModal();
    });
  });

  // Daily-log checklist checkboxes
  document.querySelectorAll('input[type=checkbox][data-log-check]').forEach(cb => {
    cb.addEventListener('change', async () => {
      try {
        const fd = new FormData();
        fd.append('field', cb.dataset.field);
        const r = await fetch(cb.dataset.logCheck, { method: 'POST', body: fd });
        const data = await r.json();
        if (!data.ok) cb.checked = !cb.checked;
      } catch (e) {
        cb.checked = !cb.checked;
      }
    });
  });

  // Daily-log checklist: select-all / deselect-all
  document.querySelectorAll('.log-check-all').forEach(btn => {
    btn.addEventListener('click', async () => {
      const checklist = btn.closest('.log-checklist');
      const boxes = Array.from(checklist.querySelectorAll('input[data-log-check]'));
      const target = !boxes.every(b => b.checked);  // if all checked -> uncheck; else -> check
      btn.disabled = true;
      for (const cb of boxes) {
        if (cb.checked === target) continue;
        cb.checked = target;
        try {
          const fd = new FormData();
          fd.append('field', cb.dataset.field);
          const r = await fetch(cb.dataset.logCheck, { method: 'POST', body: fd });
          const data = await r.json();
          if (!data.ok) cb.checked = !target;
        } catch (e) {
          cb.checked = !target;
        }
      }
      btn.textContent = target ? '全不选' : '全选';
      btn.disabled = false;
    });
  });

  // Project one-line description: edit + AI generate
  const descBox = document.getElementById('project-desc');
  if (descBox) {
    const descText = document.getElementById('desc-text');
    const editBtn = document.getElementById('desc-edit-btn');
    const genBtn = document.getElementById('desc-gen-btn');

    const setText = (val) => {
      if (val) {
        descText.textContent = val;
        descText.classList.remove('empty');
      } else {
        descText.textContent = '一句话简介…';
        descText.classList.add('empty');
      }
    };

    editBtn.addEventListener('click', () => {
      const current = descText.classList.contains('empty') ? '' : descText.textContent;
      const input = document.createElement('input');
      input.type = 'text';
      input.className = 'desc-edit-input';
      input.value = current;
      input.maxLength = 60;
      descText.replaceWith(input);
      input.focus();
      let done = false;
      const commit = async () => {
        if (done) return;
        done = true;
        const val = input.value.trim();
        const fd = new FormData();
        fd.append('description', val);
        fd.append('ajax', '1');
        try {
          await fetch(descBox.dataset.setUrl, { method: 'POST', body: fd });
        } catch (e) { /* ignore */ }
        input.replaceWith(descText);
        setText(val);
      };
      input.addEventListener('blur', commit);
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); commit(); }
        if (e.key === 'Escape') { done = true; input.replaceWith(descText); }
      });
    });

    genBtn.addEventListener('click', async () => {
      const original = genBtn.textContent;
      genBtn.textContent = '⏳';
      genBtn.disabled = true;
      try {
        const r = await fetch(descBox.dataset.genUrl, { method: 'POST' });
        const data = await r.json();
        if (data.ok && data.description) {
          setText(data.description);
        } else {
          alert('生成失败：' + (data.reason === 'no api key' ? '未设置 Claude API Key' : (data.reason || '无可用信息')));
        }
      } catch (e) {
        alert('生成失败：' + e.message);
      }
      genBtn.textContent = original;
      genBtn.disabled = false;
    });
  }
});
