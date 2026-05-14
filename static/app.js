// Frontend interactions: sync buttons, iteration checkboxes.

document.addEventListener('DOMContentLoaded', () => {
  // Global "sync today" on dashboard
  const syncAll = document.getElementById('sync-today-btn');
  if (syncAll) {
    syncAll.addEventListener('click', async () => {
      const originalText = syncAll.textContent;
      syncAll.disabled = true;
      syncAll.textContent = '同步中…';
      try {
        const r = await fetch(syncAll.dataset.url, { method: 'POST' });
        const data = await r.json();
        const msg = `${data.synced} 个项目已同步，${data.skipped} 个项目今天没有改动`;
        syncAll.textContent = '✓ ' + msg;
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
        const r = await fetch(syncOne.dataset.url, { method: 'POST' });
        const data = await r.json();
        if (data.skipped) {
          syncOne.textContent = '✓ 今天没有改动';
          syncOne.disabled = false;
          setTimeout(() => { syncOne.textContent = originalText; }, 2200);
        } else {
          syncOne.textContent = `✓ ${data.commits} 条 commit`;
          setTimeout(() => { window.location.reload(); }, 800);
        }
      } catch (e) {
        syncOne.textContent = '同步失败';
        syncOne.disabled = false;
        setTimeout(() => { syncOne.textContent = originalText; }, 2500);
      }
    });
  }

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

  // Iteration checkboxes — toggle via fetch
  document.querySelectorAll('input[type=checkbox][data-toggle-url]').forEach(cb => {
    cb.addEventListener('change', async () => {
      try {
        const fd = new FormData();
        fd.append('field', cb.dataset.field);
        const r = await fetch(cb.dataset.toggleUrl, { method: 'POST', body: fd });
        const data = await r.json();
        if (!data.ok) cb.checked = !cb.checked;
      } catch (e) {
        cb.checked = !cb.checked;
      }
    });
  });
});
